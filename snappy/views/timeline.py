"""Editor timeline: filmstrip (multicam edit), start/end, removed passages, screens (previews)
and audio tracks.

Each track is set directly on the timeline: its volume line (0 % at the bottom, 200 % at the top)
is dragged vertically, a click on the line adds a point to turn it into a curve. The waveform
follows the volume that was set. Mute / solo in the name column.
Ctrl + wheel (or pinch on the touchpad): zoom around the mouse; Shift + wheel or horizontal
scrolling: move; Alt + wheel: track height.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from ..i18n import tr
from ..models.edit import angle_at, merge_ranges
from . import theme

LABEL_W = 118       # name column (screens, tracks)
HANDLE_W = 10
RULER_H = 20
STRIP_H = 58
HEADER_H = RULER_H + STRIP_H    # ruler + "Multicam" row: always visible at the top
MIN_CUT = 0.1
POINT_R = 4
LINE_GRAB = 6       # distance (px) at which the volume line is grabbed
MAX_GAIN = 2.0

KIND_COLORS = {"game": "#3685e3", "chat": "#9161eb", "mic": "#2ec27e", "other": "#e6a60a", "mix": "#9aa1ad"}
APP_COLORS = ["#f57800", "#e03d8c", "#1cb3bf", "#edc221", "#9e734d"]
WAVE_PITCH = 6.0          # gap between two waveform bars (px)
WAVE_FLOOR_DB = 54.0      # level (dB below full scale) under which the waveform is flat
SCREEN_COLORS = ["#ff5a1f", "#3685e3", "#2ec27e", "#9161eb", "#edc221", "#e03d8c"]


def fmt_clock(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def lane_levels(lane: dict, ts) -> np.ndarray:
    """Effective volume (gain × curve) of a track at the times ts."""
    ts = np.asarray(ts, dtype=np.float64)
    pts = sorted(lane.get("points") or [])
    env = np.interp(ts, [p[0] for p in pts], [p[1] for p in pts]) if pts else np.ones_like(ts)
    return lane.get("gain", 1.0) * env


class EditorTimeline(QWidget):
    seekRequested = Signal(float)
    scrubbing = Signal(bool)
    rangeChanged = Signal(float, float)
    cutsChanged = Signal(list)
    anglesChanged = Signal(list)
    mixChanged = Signal()          # volume, curve, mute or solo of a track
    editCommitted = Signal()       # end of a gesture: step for Undo
    heightChanged = Signal()       # tracks added or made taller

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.duration = 0.0
        self.position = 0.0
        self.t_in = 0.0
        self.t_out = 0.0
        self.cuts: list[list[float]] = []
        self.angles: list[list] = [[0.0, 0]]
        self.screen_labels: list[str] = []
        self.screen_frames: list[list[QPixmap]] = []   # frames of each screen, spread over the clip
        self.screen_order: list[int] = []
        self.lanes: list[dict] = []       # {label, color, gain, points, muted, solo}
        self.peak_source = None           # callable(index, buckets) -> np.ndarray
        self._peaks_cache: dict[tuple[int, int], np.ndarray] = {}
        self.lane_h = 80
        self.screen_h = 50
        self.view_start = 0.0               # zoom: start and length of the visible part
        self.view_len = 0.0                 # 0 = the whole clip
        self.pin = 0                        # vertical scrolling: the header is drawn at this height
        self.pending_cut: float | None = None
        self._drag: dict | None = None
        self._hover: dict | None = None   # {lane, x, value}: volume bubble under the mouse
        self._cache: QPixmap | None = None
        self._cache_key = None
        self._rev = 0                     # frames or waveforms reloaded
        self._update_height()

    # ------------------------------------------------------------ API
    def set_clip(self, duration: float, screen_labels: list[str], lanes: list[dict],
                 screen_order: list[int] | None = None) -> None:
        """`screen_order`: video streams in the display order of the screen rows."""
        self.duration = max(duration, 0.01)
        self.t_in, self.t_out = 0.0, self.duration
        self.position = 0.0
        self.cuts, self.angles = [], [[0.0, 0]]
        self.screen_labels = screen_labels if len(screen_labels) > 1 else []
        order = [k for k in (screen_order or []) if 0 <= k < len(self.screen_labels)]
        self.screen_order = order if sorted(order) == list(range(len(self.screen_labels)))             else list(range(len(self.screen_labels)))
        self.lanes = lanes
        self.screen_frames = []
        self._peaks_cache.clear()
        self._hover = None
        self.view_start, self.view_len = 0.0, 0.0
        self._update_height()
        self.update()

    def set_frames(self, per_screen: list[list[QPixmap]]) -> None:
        self.screen_frames = per_screen
        self._rev += 1
        self.update()

    def refresh_peaks(self) -> None:
        self._peaks_cache.clear()
        self._rev += 1
        self.update()

    def set_position(self, t: float) -> None:
        if t == self.position:
            return                       # paused: nothing to redraw
        self.position = t
        # Zoomed in, the view follows playback (next page when the playhead leaves the screen).
        if self.view_len and self._drag is None and not (self.view_start <= t <= self.view_start + self.view_len):
            self._set_view(t - self.view_len * 0.1, self.view_len)
        self.update()

    # ------------------------------------------------------------ zoom
    @property
    def vlen(self) -> float:
        return self.view_len or self.duration

    MIN_VIEW_S = 1.0          # maximum zoom: 1 s over the whole width

    def _min_view(self) -> float:
        return min(self.duration, self.MIN_VIEW_S)

    def _set_view(self, start: float, length: float) -> None:
        length = max(self._min_view(), min(self.duration, length))
        if length >= self.duration - 1e-6:
            self.view_start, self.view_len = 0.0, 0.0
        else:
            self.view_start, self.view_len = max(0.0, min(self.duration - length, start)), length
        self.update()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        """factor > 1: zoom in. The point under the mouse (or the playhead) stays in place."""
        if self.duration <= 0:
            return
        if anchor_x is None:
            anchor_x = self.t_to_x(self.position)
        frac = max(0.0, min(1.0, (anchor_x - self.x0) / self.w))
        anchor_t = self.view_start + frac * self.vlen
        new_len = max(self._min_view(), min(self.duration, self.vlen / factor))
        if abs(new_len - self.vlen) < 1e-9:
            return                       # already at maximum (or minimum) zoom: the view does not move
        self._set_view(anchor_t - frac * new_len, new_len)

    def pan(self, seconds: float) -> None:
        if self.view_len:
            self._set_view(self.view_start + seconds, self.view_len)

    def set_edit(self, t_in: float, t_out: float, cuts: list, angles: list) -> None:
        self.t_in, self.t_out = t_in, t_out
        self.cuts = [list(c) for c in cuts]
        self.angles = [list(a) for a in angles] or [[0.0, 0]]
        self.update()

    def _update_height(self) -> None:
        h = RULER_H + STRIP_H + len(self.screen_labels) * self.screen_h + len(self.lanes) * self.lane_h + 6
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        self.heightChanged.emit()

    # ------------------------------------------------------------ geometry
    @property
    def x0(self) -> float:
        return LABEL_W + HANDLE_W

    @property
    def w(self) -> float:
        return max(1.0, self.width() - self.x0 - HANDLE_W)

    def t_to_x(self, t: float) -> float:
        return self.x0 + ((t - self.view_start) / self.vlen) * self.w if self.duration else self.x0

    def x_to_t(self, x: float) -> float:
        if not self.duration:
            return 0.0
        return max(0.0, min(self.duration, self.view_start + (x - self.x0) / self.w * self.vlen))

    def _rows(self) -> list[tuple[str, int, float, float]]:
        """(type, index, y, height) of each row."""
        rows = [("ruler", 0, 0, RULER_H), ("strip", 0, RULER_H, STRIP_H)]
        y = RULER_H + STRIP_H
        for k in self.screen_order:
            rows.append(("screen", k, y, self.screen_h))
            y += self.screen_h
        for i in range(len(self.lanes)):
            rows.append(("lane", i, y, self.lane_h))
            y += self.lane_h
        return rows

    def set_pin(self, offset: int) -> None:
        """Scrolling of the area that holds the timeline: the ruler and the "Multicam" row stay at
        the top of the visible part, over the rows that scroll underneath."""
        self.pin = max(0, int(offset))
        self.update()

    def _unpin(self, y: float) -> float:
        """Y in the timeline of a point on screen (the pinned header maps back to itself)."""
        if self.pin and self.pin <= y < self.pin + HEADER_H:
            return y - self.pin
        return y

    def _row_at(self, y: float):
        for row in self._rows():
            if row[2] <= y < row[2] + row[3]:
                return row
        return None

    def _lane_rect(self, idx: int) -> QRectF:
        row = next(r for r in self._rows() if r[0] == "lane" and r[1] == idx)
        return QRectF(self.x0, row[2] + 3, self.w, row[3] - 6)

    def _chip_rects(self, y: float) -> tuple[QRectF, QRectF]:
        """Mute / solo buttons of a track, in the name column."""
        top = y + 24
        return QRectF(12, top, 22, 18), QRectF(38, top, 22, 18)

    @staticmethod
    def _v_to_y(v: float, r: QRectF) -> float:
        return r.bottom() - (v / MAX_GAIN) * r.height()

    @staticmethod
    def _y_to_v(y: float, r: QRectF) -> float:
        return round(max(0.0, min(MAX_GAIN, (r.bottom() - y) / r.height() * MAX_GAIN)), 3)

    def _audible(self, lane: dict) -> bool:
        solo = any(l.get("solo") for l in self.lanes)
        return not lane.get("muted") and (not solo or lane.get("solo"))

    # ------------------------------------------------------------ painting
    def _static_key(self) -> tuple:
        """Everything that changes the timeline background (except the playhead and the volume bubble)."""
        lanes = tuple((l.get("gain"), tuple(map(tuple, l.get("points") or [])), l.get("muted"), l.get("solo"))
                      for l in self.lanes)
        return (self.width(), self.height(), self.devicePixelRatioF(), self.duration, self.view_start,
                self.view_len, self.t_in, self.t_out, tuple(map(tuple, self.cuts)),
                tuple(map(tuple, self.angles)), self.pending_cut, lanes, (self._hover or {}).get("lane"),
                (self._drag or {}).get("lane"), self.lane_h, self.screen_h, tuple(self.screen_order), self._rev)

    def paintEvent(self, _):
        # The background (filmstrip, screens, waveforms...) is drawn once then cached: during
        # playback, only the playhead moves (refreshed every 25 ms).
        key = self._static_key()
        if key != self._cache_key or self._cache is None:
            dpr = self.devicePixelRatioF()
            self._cache = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            self._cache.setDevicePixelRatio(dpr)
            self._cache.fill(Qt.transparent)
            cp = QPainter(self._cache)
            self._paint_static(cp)
            cp.end()
            self._cache_key = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self.pin:
            # Pinned header: the ruler and the filmstrip copied at the top of the visible part.
            dpr = self._cache.devicePixelRatio()
            p.fillRect(QRectF(0, self.pin, self.width(), HEADER_H), QColor(theme.BG))
            p.drawPixmap(QRectF(0, self.pin, self.width(), HEADER_H), self._cache,
                         QRectF(0, 0, self.width() * dpr, HEADER_H * dpr))
            grad = QLinearGradient(0, self.pin + HEADER_H, 0, self.pin + HEADER_H + 8)
            grad.setColorAt(0, QColor(0, 0, 0, 120))
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, self.pin + HEADER_H, self.width(), 8), grad)
        if self.duration <= 0:
            return
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        rows = self._rows()
        bottom = rows[-1][2] + rows[-1][3]
        p.save()
        p.setClipRect(QRectF(LABEL_W, 0, self.width() - LABEL_W, bottom + 1))
        xp = self.t_to_x(self.position)
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(QPointF(xp, 2), QPointF(xp, bottom))
        p.setBrush(QColor("white"))
        p.setPen(Qt.NoPen)
        top = self.pin
        p.drawPolygon([QPointF(xp - 6, top), QPointF(xp + 6, top), QPointF(xp, top + 8)])
        p.restore()
        self._paint_bubble(p)

    def _paint_static(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        rows = self._rows()
        bottom = rows[-1][2] + rows[-1][3]
        for kind, idx, y, h in rows:
            p.save()
            if kind == "ruler":
                self._paint_ruler(p, y, h)
            elif kind == "strip":
                self._paint_strip(p, y, h)
            elif kind == "screen":
                self._paint_screen(p, idx, y, h)
            else:
                self._paint_lane(p, idx, y, h)
            p.restore()
        if self.duration <= 0:
            return
        p.save()
        p.setClipRect(QRectF(LABEL_W, 0, self.width() - LABEL_W, bottom + 1))
        # Removed zones (outside start/end, cut passages): darkened over the whole height.
        shade = QColor(0, 0, 0, 150)
        top = RULER_H
        xs, xe = self.t_to_x(self.t_in), self.t_to_x(self.t_out)
        p.fillRect(QRectF(self.x0, top, xs - self.x0, bottom - top), shade)
        p.fillRect(QRectF(xe, top, self.x0 + self.w - xe, bottom - top), shade)
        for a, b in self.cuts:
            r = QRectF(self.t_to_x(a), top, self.t_to_x(b) - self.t_to_x(a), bottom - top)
            p.fillRect(r, QColor(120, 10, 20, 150))
            p.fillRect(r, QBrush(QColor(255, 77, 94, 70), Qt.BDiagPattern))
            p.setPen(QPen(QColor(theme.DANGER), 1.5))
            p.drawLine(QPointF(r.left(), top), QPointF(r.left(), bottom))
            p.drawLine(QPointF(r.right(), top), QPointF(r.right(), bottom))
        if self.pending_cut is not None:
            x = self.t_to_x(self.pending_cut)
            p.setPen(QPen(QColor(theme.DANGER), 2, Qt.DashLine))
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
        # Start / end handles on the filmstrip.
        sy, sh = RULER_H, STRIP_H
        p.setPen(QPen(QColor(theme.ACCENT), 3))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(xs, sy + 1, xe - xs, sh - 2), 4, 4)
        for x, left in ((xs, True), (xe, False)):
            hr = QRectF(x - HANDLE_W if left else x, sy + 1, HANDLE_W, sh - 2)
            path = QPainterPath()
            path.addRoundedRect(hr, 3, 3)
            p.fillPath(path, QColor(theme.ACCENT))
            p.setPen(QPen(QColor("white"), 2))
            p.drawLine(QPointF(hr.center().x(), hr.center().y() - 8), QPointF(hr.center().x(), hr.center().y() + 8))
        p.restore()

    def _content_clip(self, p: QPainter, y: float, h: float) -> None:
        p.setClipRect(QRectF(self.x0, y, self.w, h))

    def _label(self, p: QPainter, y: float, h: float, text: str, color: str | None = None, dim=False,
               top: bool = False) -> None:
        r = QRectF(8, y + 4, LABEL_W - 14, 18) if top else QRectF(8, y, LABEL_W - 14, h)
        if color:
            p.fillRect(QRectF(8, r.center().y() - 7, 4, 14), QColor(color))
            r.adjust(10, 0, 0, 0)
        p.setPen(QColor(theme.TEXT_FAINT if dim else theme.TEXT_DIM))
        p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, p.fontMetrics().elidedText(text, Qt.ElideRight, int(r.width())))

    def _paint_ruler(self, p: QPainter, y: float, h: float) -> None:
        if self.duration <= 0:
            return
        if self.view_len:   # overview of the visible part, above the ruler
            p.fillRect(QRectF(self.x0, y, self.w, 3), QColor(theme.SURFACE_2))
            p.fillRect(QRectF(self.x0 + self.view_start / self.duration * self.w, y,
                              max(4.0, self.view_len / self.duration * self.w), 3), QColor(theme.TEXT_FAINT))
        self._content_clip(p, y, h)
        step = next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300)
                     if self.w / (self.vlen / s) >= 70), 600)
        p.setPen(QColor(theme.TEXT_FAINT))
        t = int(self.view_start / step) * step
        end = self.view_start + self.vlen
        while t <= end + 1e-6:
            x = self.t_to_x(t)
            p.drawLine(QPointF(x, y + h - 6), QPointF(x, y + h))
            label = f"{int(t // 60)}:{int(round(t, 3) % 60):02d}" if step >= 1 else fmt_clock(t)
            if step < 0.5:
                label = f"{int(t // 60)}:{t % 60:05.2f}"
            p.drawText(QRectF(x + 3, y, 70, h - 4), Qt.AlignLeft | Qt.AlignVCenter, label)
            t += step

    def _draw_frames(self, p: QPainter, r: QRectF, pick) -> None:
        """Thumbnails aligned on time in r; pick(i, n) -> QPixmap of cell i (or None)."""
        n = max((len(f) for f in self.screen_frames), default=0)
        if not n:
            return
        span = self.duration / n
        for i in range(n):
            x1, x2 = self.t_to_x(i * span), self.t_to_x((i + 1) * span)
            if x2 < r.left() or x1 > r.right():
                continue
            fr = pick(i, n)
            if fr is None or fr.isNull():
                continue
            # Only the visible part of the cell is drawn, cropped in the original image (no full
            # rescale for each frame: very slow once zoomed in).
            target = QRectF(x1, r.top(), x2 - x1 + 1, r.height())
            vis = target.intersected(r)
            if vis.isEmpty():
                continue
            scale = max(target.width() / fr.width(), target.height() / fr.height())
            ox = (fr.width() * scale - target.width()) / 2
            oy = (fr.height() * scale - target.height()) / 2
            src = QRectF((vis.left() - target.left() + ox) / scale, (vis.top() - target.top() + oy) / scale,
                         vis.width() / scale, vis.height() / scale)
            p.drawPixmap(vis, fr, src)

    @staticmethod
    def _frame(frames: list[QPixmap], i: int) -> QPixmap | None:
        return frames[min(i, len(frames) - 1)] if frames else None

    def _paint_strip(self, p: QPainter, y: float, h: float) -> None:
        self._label(p, y, h, tr("Multicam") if self.screen_labels else tr("Video"))
        self._content_clip(p, y, h)
        r = QRectF(self.x0, y + 1, self.w, h - 2)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.fillPath(path, QColor(theme.SURFACE_2))
        if not self.screen_frames:
            return
        p.save()
        p.setClipPath(path, Qt.IntersectClip)

        def pick(i, n):   # the screen chosen by the multicam edit in the middle of the cell
            k = angle_at(self.angles, (i + 0.5) / n * self.duration) if len(self.screen_frames) > 1 else 0
            return self._frame(self.screen_frames[min(k, len(self.screen_frames) - 1)], i)

        self._draw_frames(p, r, pick)
        p.restore()

    def _paint_screen(self, p: QPainter, k: int, y: float, h: float) -> None:
        color = SCREEN_COLORS[k % len(SCREEN_COLORS)]
        self._label(p, y, h, self.screen_labels[k], color)
        self._content_clip(p, y, h)
        track = QRectF(self.x0, y + 3, self.w, h - 6)
        path = QPainterPath()
        path.addRoundedRect(track, 4, 4)
        p.fillPath(path, QColor(theme.SURFACE_2))
        p.save()
        p.setClipPath(path, Qt.IntersectClip)
        if k < len(self.screen_frames):
            self._draw_frames(p, track, lambda i, _n: self._frame(self.screen_frames[k], i))
        # Passages where this screen is not shown: veiled; those where it is: colored outline.
        switches = sorted(self.angles)
        bounds = [t for t, _ in switches] + [self.duration]
        for (t, screen), end in zip(switches, bounds[1:]):
            seg = QRectF(self.t_to_x(t), track.top(), self.t_to_x(end) - self.t_to_x(t), track.height())
            if screen == k:
                p.fillRect(QRectF(seg.left(), seg.top(), seg.width(), 3), QColor(color))
                p.fillRect(QRectF(seg.left(), seg.bottom() - 3, seg.width(), 3), QColor(color))
            else:
                p.fillRect(seg, QColor(14, 15, 18, 185))
        p.restore()
        p.setPen(QPen(QColor("white"), 1.5))
        for t, _ in switches[1:]:
            x = self.t_to_x(t)
            p.drawLine(QPointF(x, track.top() - 2), QPointF(x, track.bottom() + 2))

    def _lane_peaks(self, idx: int, buckets: int) -> np.ndarray:
        key = (idx, buckets)
        if key not in self._peaks_cache:
            self._peaks_cache[key] = self.peak_source(idx, buckets) if self.peak_source else np.zeros(buckets)
        return self._peaks_cache[key]

    def _paint_lane(self, p: QPainter, idx: int, y: float, h: float) -> None:
        lane = self.lanes[idx]
        audible = self._audible(lane)
        self._label(p, y, h, lane["label"], lane["color"], dim=not audible, top=True)
        mute_r, solo_r = self._chip_rects(y)
        for r, on, text, on_color in ((mute_r, lane.get("muted"), "M", theme.DANGER),
                                      (solo_r, lane.get("solo"), "S", "#edc221")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(on_color) if on else QColor(theme.SURFACE_2))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QColor("#111" if on and text == "S" else "white" if on else theme.TEXT_DIM))
            p.drawText(r, Qt.AlignCenter, text)
        p.setPen(QColor(theme.TEXT_FAINT))
        level = tr("curve") if lane.get("points") else f"{round(lane.get('gain', 1.0) * 100)} %"
        p.drawText(QRectF(64, mute_r.top(), LABEL_W - 66, mute_r.height()), Qt.AlignVCenter | Qt.AlignLeft, level)

        self._content_clip(p, y, h)
        r = QRectF(self.x0, y + 3, self.w, h - 6)
        p.fillRect(r, QColor(theme.SURFACE))
        # Waveform scaled to the volume that was set (what will be heard), as spaced rounded bars.
        # A bar sums up several cells (average and peak blended, smoothed with the neighbors): a
        # calm drawing, which only touches the edge when the sound really clips; the part beyond a
        # 100 % peak is then shown in red.
        # Zoomed in: more cells over the whole clip, only the visible ones are drawn.
        # (rounded to a power of 2: a few cached levels, not a computation per zoom step)
        need = max(1, int(r.width() / 2 * self.duration / self.vlen))
        buckets = min(1 << 18, 1 << (need - 1).bit_length())
        pk = self._lane_peaks(idx, buckets)
        if len(pk):
            bt = self.duration / len(pk)
            bucket_px = self.w * bt / self.vlen
            group = max(1, round(WAVE_PITCH / bucket_px))     # cells per bar
            # Bars aligned on the cell grid: they do not "flicker" while scrolling.
            g0 = max(0, int(self.view_start / bt) // group - 2)
            g1 = min(-(-len(pk) // group), int((self.view_start + self.vlen) / bt) // group + 3)
            if g1 > g0:
                seg = np.asarray(pk[g0 * group:g1 * group], dtype=np.float64)
                seg = np.pad(seg, (0, (g1 - g0) * group - len(seg)))
                seg = seg.reshape(g1 - g0, group)
                val = 0.55 * seg.mean(axis=1) + 0.45 * seg.max(axis=1)
                if len(val) > 2:
                    val = np.convolve(np.pad(val, 1, mode="edge"), [0.2, 0.6, 0.2], mode="valid")
                ts = (np.arange(g0, g1) + 0.5) * group * bt
                lv = lane_levels(lane, ts)
                amp = val * lv
                peak = seg.max(axis=1) * lv
                mid, half = r.center().y(), r.height() / 2 - 2
                pitch = bucket_px * group
                bar_w = max(1.5, min(pitch * 0.66, 6.0))
                xs = self.x0 + (np.arange(g0, g1) * group * bt - self.view_start) / self.vlen * self.w +                     (pitch - bar_w) / 2
                # Decibel scale (like the ear): a normal voice fills half of the track, a faint sound
                # stays visible, and only 0 dBFS reaches the edge.
                db = 20 * np.log10(np.maximum(np.minimum(amp, 1.0), 1e-6))
                a_px = np.clip((db + WAVE_FLOOR_DB) / WAVE_FLOOR_DB, 0.0, 1.0) * half * 0.94
                shown = a_px >= 1.0                  # below this: silence, the center line is enough
                a_px = np.maximum(a_px, bar_w / 2)
                over = (peak > 1.0) & audible
                rad = bar_w / 2
                body, red = QPainterPath(), QPainterPath()
                for x, a, o, s in zip(xs.tolist(), a_px.tolist(), over.tolist(), shown.tolist()):
                    if s:
                        (red if o else body).addRoundedRect(QRectF(x, mid - a, bar_w, 2 * a), rad, rad)
                color = QColor(lane["color"]) if audible else QColor(theme.TEXT_FAINT)
                color.setAlpha(215 if audible else 70)
                p.save()
                p.setRenderHint(QPainter.Antialiasing, True)
                base = QColor(color)
                base.setAlpha(60 if audible else 30)
                p.setPen(QPen(base, 1.5))
                p.drawLine(QPointF(r.left(), mid), QPointF(r.right(), mid))
                p.setPen(Qt.NoPen)
                p.fillPath(body, color)
                if not red.isEmpty():
                    p.fillPath(red, QColor(255, 77, 94, 220))
                p.restore()
        # Volume line: discreet, more visible when the mouse is over the track.
        hovered = (self._hover or {}).get("lane") == idx or (self._drag or {}).get("lane") == idx
        p.setPen(QPen(QColor(255, 255, 255, 28), 1, Qt.DotLine))
        p.drawLine(QPointF(r.left(), self._v_to_y(1.0, r)), QPointF(r.right(), self._v_to_y(1.0, r)))
        pts = sorted(lane.get("points") or [])
        gain = lane.get("gain", 1.0)
        line = [(0.0, gain * (pts[0][1] if pts else 1.0))] + [(t, gain * v) for t, v in pts] + \
               [(self.duration, gain * (pts[-1][1] if pts else 1.0))]
        path = QPainterPath()
        for i, (t, v) in enumerate(line):
            pt = QPointF(self.t_to_x(t), self._v_to_y(v, r))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor(255, 255, 255, 210 if hovered else 105), 1.6 if hovered else 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(QColor(255, 255, 255, 200 if hovered else 120), 1))
        p.setBrush(QColor(lane["color"]))
        for t, v in pts:
            p.drawEllipse(QPointF(self.t_to_x(t), self._v_to_y(gain * v, r)), POINT_R, POINT_R)

    def _paint_bubble(self, p: QPainter) -> None:
        """Volume percentage next to the mouse (hovering or dragging a track line)."""
        info = self._hover
        if not info or info["lane"] >= len(self.lanes):
            return
        r = self._lane_rect(info["lane"])
        y = self._v_to_y(info["value"], r)
        x = info["x"]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(x, y), 3, 3)
        text = f"{round(info['value'] * 100)} %"
        tw = p.fontMetrics().horizontalAdvance(text) + 12
        box = QRectF(x + 8, y - 22, tw, 18)
        if box.right() > self.width() - 2:
            box.moveRight(x - 8)
        box.moveTop(max(r.top(), min(box.top(), r.bottom() - box.height())))
        p.setBrush(QColor(20, 22, 27, 230))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignCenter, text)

    # ------------------------------------------------------------ mouse
    def _hit(self, x: float, y: float) -> dict:
        y = self._unpin(y)
        row = self._row_at(y)
        if not row or self.duration <= 0:
            return {"kind": "none"}
        kind, idx, ry, rh = row
        if kind in ("ruler", "strip"):
            if kind == "strip":
                if abs(x - (self.t_to_x(self.t_in) - HANDLE_W / 2)) <= HANDLE_W:
                    return {"kind": "in"}
                if abs(x - (self.t_to_x(self.t_out) + HANDLE_W / 2)) <= HANDLE_W:
                    return {"kind": "out"}
            for i, (a, b) in enumerate(self.cuts):
                if abs(x - self.t_to_x(a)) <= 5:
                    return {"kind": "cut_a", "i": i}
                if abs(x - self.t_to_x(b)) <= 5:
                    return {"kind": "cut_b", "i": i}
            return {"kind": "seek"}
        if kind == "screen":
            if x < self.x0:
                return {"kind": "none"}
            for i, (t, _k) in enumerate(sorted(self.angles)):
                if i and abs(x - self.t_to_x(t)) <= 5:
                    return {"kind": "angle", "i": i}
            return {"kind": "screen", "k": idx}
        if kind == "lane":
            if x < self.x0:
                mute_r, solo_r = self._chip_rects(ry)
                if mute_r.adjusted(-2, -2, 2, 2).contains(QPointF(x, y)):
                    return {"kind": "mute", "lane": idx}
                if solo_r.adjusted(-2, -2, 2, 2).contains(QPointF(x, y)):
                    return {"kind": "solo", "lane": idx}
                return {"kind": "lane_label", "lane": idx}
            r = QRectF(self.x0, ry + 3, self.w, rh - 6)
            lane = self.lanes[idx]
            gain = lane.get("gain", 1.0)
            pts = sorted(lane.get("points") or [])
            for i, (t, v) in enumerate(pts):
                if abs(x - self.t_to_x(t)) <= POINT_R + 3 and abs(y - self._v_to_y(gain * v, r)) <= POINT_R + 3:
                    return {"kind": "point", "lane": idx, "i": i, "rect": r}
            level = float(lane_levels(lane, [self.x_to_t(x)])[0])
            if abs(y - self._v_to_y(level, r)) <= LINE_GRAB:
                return {"kind": "line", "lane": idx, "rect": r}
            return {"kind": "lane", "lane": idx, "rect": r}
        return {"kind": "none"}

    def _set_hover(self, x: float, hit: dict) -> None:
        if hit.get("kind") in ("point", "line", "lane") and x >= self.x0:
            lane = self.lanes[hit["lane"]]
            if hit["kind"] == "point":
                t, v = sorted(lane["points"])[hit["i"]]
                x, value = self.t_to_x(t), lane.get("gain", 1.0) * v
            else:
                value = float(lane_levels(lane, [self.x_to_t(x)])[0])
            self._hover = {"lane": hit["lane"], "x": x, "value": value}
        else:
            self._hover = None

    def mousePressEvent(self, e):
        if self.duration <= 0:
            return
        x, y = e.position().x(), e.position().y()
        hit = self._hit(x, y)
        t = self.x_to_t(x)
        if e.button() == Qt.RightButton:
            self._context_menu(hit, t, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        kind = hit["kind"]
        if kind == "seek" and e.modifiers() & Qt.ShiftModifier and RULER_H <= self._unpin(y) < HEADER_H:
            self._drag = {"kind": "new_cut", "t0": t}
            self.cuts.append([t, t])
            return
        if kind == "screen":
            self.set_angle_at(t, hit["k"])
            self.editCommitted.emit()
            return
        if kind in ("mute", "solo"):
            lane = self.lanes[hit["lane"]]
            key = "muted" if kind == "mute" else "solo"
            lane[key] = not lane.get(key)
            self.mixChanged.emit()
            if key == "muted":
                self.editCommitted.emit()
            self.update()
            return
        if kind == "line":
            lane = self.lanes[hit["lane"]]
            self._drag = {"kind": "line", "lane": hit["lane"], "rect": hit["rect"], "x": x, "y": y, "moved": False,
                          "gain": lane.get("gain", 1.0), "points": list(lane.get("points") or [])}
            return
        if kind == "lane":
            kind = hit["kind"] = "seek"       # click in the track, far from the line: move the playhead
        if kind in ("none", "lane_label"):
            return
        self._drag = dict(hit)
        if kind == "seek":
            self.scrubbing.emit(True)
            self._apply_drag(x, y)

    def mouseDoubleClickEvent(self, e):
        if self._unpin(e.position().y()) < RULER_H:     # double-click on the ruler: the whole clip
            self._set_view(0.0, 0.0)
            return
        hit = self._hit(e.position().x(), e.position().y())
        if hit["kind"] == "point":
            self._delete_point(hit["lane"], hit["i"])

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._drag is None:
            hit = self._hit(x, y)
            kind = hit["kind"]
            cursor = {"in": Qt.SizeHorCursor, "out": Qt.SizeHorCursor, "cut_a": Qt.SizeHorCursor,
                      "cut_b": Qt.SizeHorCursor, "angle": Qt.SizeHorCursor, "point": Qt.SizeAllCursor,
                      "line": Qt.SizeVerCursor, "mute": Qt.PointingHandCursor, "solo": Qt.PointingHandCursor,
                      "lane_label": Qt.ArrowCursor, "screen": Qt.PointingHandCursor}.get(kind, Qt.PointingHandCursor)
            self.setCursor(cursor)
            self._set_hover(x, hit)
            self.update()
            return
        self._apply_drag(x, y)

    def leaveEvent(self, _e):
        if self._hover is not None:
            self._hover = None
            self.update()

    def mouseReleaseEvent(self, e):
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag["kind"] == "seek":
            self.scrubbing.emit(False)
            return
        if drag["kind"] == "line" and not drag["moved"]:
            self._add_point(drag["lane"], self.x_to_t(drag["x"]))
        if drag["kind"] in ("new_cut", "cut_a", "cut_b"):
            self.cuts = [list(c) for c in merge_ranges([tuple(c) for c in self.cuts], MIN_CUT)]
            self.cutsChanged.emit([tuple(c) for c in self.cuts])
        self._set_hover(e.position().x(), self._hit(e.position().x(), e.position().y()))
        self.update()
        self.editCommitted.emit()

    def _apply_drag(self, x: float, y: float) -> None:
        d = self._drag
        t = self.x_to_t(x)
        kind = d["kind"]
        min_len = min(0.5, self.duration)
        if kind == "seek":
            self.position = t
            self.seekRequested.emit(t)
        elif kind == "in":
            self.t_in = min(self.x_to_t(x + HANDLE_W / 2), self.t_out - min_len)
            self.rangeChanged.emit(self.t_in, self.t_out)
            self.seekRequested.emit(self.t_in)
        elif kind == "out":
            self.t_out = max(self.x_to_t(x - HANDLE_W / 2), self.t_in + min_len)
            self.rangeChanged.emit(self.t_in, self.t_out)
            self.seekRequested.emit(self.t_out)
        elif kind == "new_cut":
            self.cuts[-1] = [min(d["t0"], t), max(d["t0"], t)]
            self.seekRequested.emit(t)
        elif kind in ("cut_a", "cut_b"):
            a, b = self.cuts[d["i"]]
            self.cuts[d["i"]] = [min(t, b - MIN_CUT), b] if kind == "cut_a" else [a, max(t, a + MIN_CUT)]
            self.cutsChanged.emit([tuple(c) for c in self.cuts])
            self.seekRequested.emit(t)
        elif kind == "angle":
            switches = sorted(self.angles)
            lo = switches[d["i"] - 1][0] + 0.05
            hi = switches[d["i"] + 1][0] - 0.05 if d["i"] + 1 < len(switches) else self.duration
            switches[d["i"]][0] = round(max(lo, min(hi, t)), 3)
            self.angles = switches
            self.anglesChanged.emit([list(a) for a in self.angles])
        elif kind == "line":
            # The whole line goes up or down (the track volume, or its entire curve).
            if not d["moved"] and abs(y - d["y"]) < 3:
                return
            d["moved"] = True
            lane = self.lanes[d["lane"]]
            r = d["rect"]
            dv = (d["y"] - y) / r.height() * MAX_GAIN
            if d["points"]:
                lane["points"] = [(pt, round(max(0.0, min(MAX_GAIN, v + dv / max(d["gain"], 1e-3))), 3))
                                  for pt, v in d["points"]]
            else:
                lane["gain"] = round(max(0.0, min(MAX_GAIN, d["gain"] + dv)), 3)
            self._hover = {"lane": d["lane"], "x": d["x"],
                           "value": float(lane_levels(lane, [self.x_to_t(d["x"])])[0])}
            self.mixChanged.emit()
        elif kind == "point":
            lane = self.lanes[d["lane"]]
            gain = max(lane.get("gain", 1.0), 1e-3)
            pts = sorted(lane["points"])
            pts[d["i"]] = (round(t, 3), round(min(MAX_GAIN, self._y_to_v(y, d["rect"]) / gain), 3))
            moved = pts[d["i"]]
            pts.sort()
            d["i"] = pts.index(moved)
            lane["points"] = pts
            self._hover = {"lane": d["lane"], "x": self.t_to_x(moved[0]), "value": gain * moved[1]}
            self.mixChanged.emit()
        self.update()

    # ------------------------------------------------------------ volume
    def _add_point(self, idx: int, t: float) -> None:
        """Point on the line at time t: the line becomes an adjustable curve."""
        lane = self.lanes[idx]
        pts = sorted(lane.get("points") or [])
        if not pts:
            # The fixed volume of the track becomes the curve: the point starts at the current level.
            level, lane["gain"] = lane.get("gain", 1.0), 1.0
        else:
            level = float(np.interp(t, [q[0] for q in pts], [q[1] for q in pts]))
        pts.append((round(t, 3), round(float(level), 3)))
        lane["points"] = sorted(pts)
        self.mixChanged.emit()
        self.update()

    def _delete_point(self, lane_i: int, i: int) -> None:
        lane = self.lanes[lane_i]
        pts = sorted(lane["points"])
        last = pts.pop(i)
        if not pts:   # no more curve: the track keeps the level of the last point
            lane["gain"] = round(lane.get("gain", 1.0) * last[1], 3)
        lane["points"] = pts
        self.mixChanged.emit()
        self.editCommitted.emit()
        self.update()

    def _reset_lane(self, idx: int, level: float = 1.0) -> None:
        lane = self.lanes[idx]
        lane["points"], lane["gain"] = [], level
        self.mixChanged.emit()
        self.editCommitted.emit()
        self.update()

    # ------------------------------------------------------------ multicam
    def set_angle_at(self, t: float, k: int) -> None:
        """Switches to screen k at time t (until the next switch)."""
        switches = [s for s in sorted(self.angles) if abs(s[0] - t) > 0.05 or s[0] == 0.0]
        if t < 0.05:
            switches[0] = [0.0, k]
        else:
            switches.append([round(t, 3), k])
        switches.sort()
        clean = []
        for s in switches:   # two switches in a row to the same screen: the 2nd one is useless
            if not clean or clean[-1][1] != s[1]:
                clean.append(s)
        self.angles = clean
        self.anglesChanged.emit([list(a) for a in self.angles])
        self.update()

    def _context_menu(self, hit: dict, t: float, global_pos) -> None:
        menu = QMenu(self)
        kind = hit["kind"]
        cut_i = next((i for i, (a, b) in enumerate(self.cuts) if a <= t <= b), None)

        def act(label, fn):
            a = QAction(label, menu)
            a.triggered.connect(fn)
            menu.addAction(a)

        if kind == "point":
            act(tr("Delete this point"), lambda: self._delete_point(hit["lane"], hit["i"]))
        elif kind == "angle":
            def drop():
                switches = sorted(self.angles)
                del switches[hit["i"]]
                self.angles = switches
                self.set_angle_at(0.0, switches[0][1]) if switches else None
                self.editCommitted.emit()
            act(tr("Delete this switch"), drop)
        if "lane" in hit:
            i = hit["lane"]
            lane = self.lanes[i]
            if kind in ("line", "lane"):
                act(tr("Add a point here"), lambda: (self._add_point(i, t), self.editCommitted.emit()))
            if lane.get("points"):
                act(tr("Clear the curve (keep a fixed volume)"),
                    lambda: self._reset_lane(i, round(float(lane_levels(lane, [t])[0]), 3)))
            act(tr("Back to 100 %"), lambda: self._reset_lane(i, 1.0))
            menu.addSeparator()

            def toggle(key):
                lane[key] = not lane.get(key)
                self.mixChanged.emit()
                self.editCommitted.emit()
                self.update()
            act(tr("Unmute the track") if lane.get("muted") else tr("Mute the track (M)"), lambda: toggle("muted"))
            act(tr("Leave solo") if lane.get("solo") else tr("Solo (S)"), lambda: toggle("solo"))
        if cut_i is not None:
            def restore():
                del self.cuts[cut_i]
                self.cutsChanged.emit([tuple(c) for c in self.cuts])
                self.editCommitted.emit()
                self.update()
            act(tr("Put this passage back"), restore)
        elif kind in ("seek", "in", "out", "cut_a", "cut_b"):
            def add_cut():
                a, b = max(self.t_in, t - 1.0), min(self.t_out, t + 1.0)
                self.cuts = [list(c) for c in merge_ranges([tuple(c) for c in self.cuts] + [(a, b)], MIN_CUT)]
                self.cutsChanged.emit([tuple(c) for c in self.cuts])
                self.editCommitted.emit()
                self.update()
            act(tr("Remove a passage here (2 s)"), add_cut)

            def set_in():
                self.t_in = min(t, self.t_out - 0.5)
                self.rangeChanged.emit(self.t_in, self.t_out)
                self.editCommitted.emit()
                self.update()

            def set_out():
                self.t_out = max(t, self.t_in + 0.5)
                self.rangeChanged.emit(self.t_in, self.t_out)
                self.editCommitted.emit()
                self.update()
            act(tr("Start here"), set_in)
            act(tr("End here"), set_out)
        if not menu.isEmpty():
            menu.exec(global_pos)

    def wheelEvent(self, e):
        """Ctrl + wheel (or pinch on the touchpad): zoom around the mouse. Shift + wheel or horizontal
        scrolling: move in time. Alt + wheel: height of the audio tracks (or of the screen rows under
        the mouse). Wheel alone: page scrolling."""
        if self.duration <= 0:
            return
        mods = e.modifiers()
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        if mods & Qt.AltModifier:          # Qt turns Alt + wheel into horizontal scrolling
            delta = 8 if (dy or dx) > 0 else -8
            row = self._row_at(self._unpin(e.position().y()))
            if row and row[0] == "screen":
                self.screen_h = max(30, min(140, self.screen_h + delta))
            elif self.lanes:
                self.lane_h = max(48, min(200, self.lane_h + delta))
            self._update_height()
            self.update()
        elif mods & Qt.ControlModifier:
            if dy:
                self.zoom(1.25 ** (dy / 120), e.position().x())
        elif mods & Qt.ShiftModifier or abs(dx) > abs(dy):
            if not self.view_len:
                e.ignore()
                return
            steps = (dx or dy) / 120
            self.pan(-steps * self.vlen * 0.1)
        else:
            e.ignore()                   # the timeline scroll area takes care of it
            return
        e.accept()
