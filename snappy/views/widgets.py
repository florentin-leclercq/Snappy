"""Reusable widgets: icons, clip gallery, notification, drag-and-drop sharing."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QMimeData, QModelIndex, QObject, QPoint, QPointF, QPropertyAnimation, QRect,
                            QRectF, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDrag, QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QSplitter, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QPushButton,
                               QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

from ..i18n import tr
from ..platforms import system
from . import theme

# ---------------------------------------------------------------------------
# Icons: vector drawings (24 px grid, 2 px rounded stroke), in the spirit of Lucide.
# Thicker and easier to read than the Segoe glyphs, and sharp at every size.
# ---------------------------------------------------------------------------
_SPEAKER = '<path d="M11 4.7 6.4 8.5H3.5a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h2.9l4.6 3.8Z"/>'
_STAR = ('<path d="M12 2.8l2.85 5.78 6.38.93-4.62 4.5 1.09 6.35L12 17.35l-5.7 3 1.09-6.35-4.62-4.5 '
         '6.38-.93Z"{}/>')
ICONS = {
    "play": '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.4-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5Z" fill="C"/>',
    "pause": '<rect x="6" y="4.5" width="4" height="15" rx="1" fill="C"/>'
             '<rect x="14" y="4.5" width="4" height="15" rx="1" fill="C"/>',
    "star": _STAR.format(""),
    "star_filled": _STAR.format(' fill="C"'),
    "delete": '<path d="M3.5 6h17"/><path d="M18.5 6v13a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6"/>'
              '<path d="M8.5 6V4.5a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5V6"/>'
              '<path d="M10 11v5.5"/><path d="M14 11v5.5"/>',
    "folder": '<path d="M20 19.5a2 2 0 0 0 2-2V8.5a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3.5H4'
              'a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2Z"/>',
    "cut": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9"/>'
           '<path d="M14.5 14.5 20 20"/><path d="M8.1 8.1 12 12"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/>'
            '<path d="M4 16a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2"/>',
    "edit": '<path d="M16.9 3.6a2.4 2.4 0 0 1 3.5 3.5L7.5 20 3 21l1-4.5Z"/><path d="m14.5 6 3.5 3.5"/>',
    "settings": '<path d="M12.2 2h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-1 1.7l-.4.3a2 2 0 0 1-2 0l-.2-.1a2 2 0 0 0-2.7.7'
                'l-.2.4a2 2 0 0 0 .7 2.7l.2.1a2 2 0 0 1 1 1.7v.5a2 2 0 0 1-1 1.7l-.2.1a2 2 0 0 0-.7 2.7l.2.4'
                'a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2 0l.4.3a2 2 0 0 1 1 1.7v.2a2 2 0 0 0 2 2h.4a2 2 0 0 0 2-2v-.2'
                'a2 2 0 0 1 1-1.7l.4-.3a2 2 0 0 1 2 0l.2.1a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7l-.2-.1'
                'a2 2 0 0 1-1-1.7v-.5a2 2 0 0 1 1-1.7l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1'
                'a2 2 0 0 1-2 0l-.4-.3a2 2 0 0 1-1-1.7V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/>',
    "video": '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4Z"/>',
    "back": '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    "volume": _SPEAKER + '<path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.9 5.1a10 10 0 0 1 0 13.8"/>',
    "mute": _SPEAKER + '<path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
    "speaker": _SPEAKER + '<path d="M15.5 8.5a5 5 0 0 1 0 7"/>',
    "record": '<circle cx="12" cy="12" r="7" fill="C"/>',
    "library": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
               '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "apps": '<path d="M12 2.5 2.5 7.5 12 12.5l9.5-5Z"/><path d="m2.5 12 9.5 5 9.5-5"/>'
            '<path d="m2.5 16.5 9.5 5 9.5-5"/>',
    "search": '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.6-4.6"/>',
    "fullscreen": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/>'
                  '<path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>'
             '<path d="m8.6 13.5 6.8 4"/><path d="m15.4 6.5-6.8 4"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/>'
            '<path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3Z"/>'
              '<circle cx="12" cy="13" r="3.5"/>',
    "prev": '<path d="M18.5 19.2 9 12l9.5-7.2Z" fill="C"/><path d="M5.5 5v14"/>',
    "next": '<path d="M5.5 4.8 15 12l-9.5 7.2Z" fill="C"/><path d="M18.5 5v14"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "loop": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/>'
            '<path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "game": '<path d="M6 11h4"/><path d="M8 9v4"/><path d="M15 12h.01"/><path d="M18 10h.01"/>'
            '<path d="M17.3 5H6.7a4 4 0 0 0-4 3.6C2.6 9.4 2 14.5 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.4-1.4'
            'A2 2 0 0 1 9.8 16h4.4a2 2 0 0 1 1.4.6L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.5-.6-6.6-.7-7.3'
            'A4 4 0 0 0 17.3 5Z"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="C"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "warning": '<path d="m21.7 18-8-14a2 2 0 0 0-3.5 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3"/>'
               '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "add": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/>'
                '<path d="M3 10h18"/>',
    "mic": '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/>'
           '<path d="M12 19v3"/>',
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
    "drag": "".join(f'<circle cx="{x}" cy="{y}" r="1.5" fill="C" stroke="none"/>'
                    for x in (9, 15) for y in (5, 12, 19)),
    "erase": '<path d="m7 21-4.3-4.3a2.4 2.4 0 0 1 0-3.4l9.6-9.6a2.4 2.4 0 0 1 3.4 0l5.6 5.6a2.4 2.4 0 0 1 0 3.4'
             'L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
    "select": '<path d="M4 4l7 17 2.5-7.5L21 11Z"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
}
_icon_cache: dict[tuple[str, str], QIcon] = {}


def icon_svg(name: str, color: str) -> str:
    """`color`: "#rrggbb" or "#rrggbb;opacity"."""
    color, _, alpha = color.partition(";")
    body = ICONS.get(name, ICONS["close"]).replace('"C"', f'"{color}"')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
            f'opacity="{alpha or 1}" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def glyph_icon(name: str, color: str = theme.TEXT, size: int = 32) -> QIcon:
    """Vector icon, sharp at every size and screen zoom (`size` is no longer needed: Qt draws it at
    the size it is shown)."""
    c = QColor(color)
    color = c.name() if c.alpha() == 255 else f"{c.name()};{c.alphaF():.2f}"
    icon = _icon_cache.get((name, color))
    if icon is None:
        icon = _icon_cache[(name, color)] = QIcon(_svg_file(name, color))
    return icon


def _svg_file(name: str, color: str) -> str:
    """The icon goes through an .svg file: QIcon then picks its vector engine."""
    from ..models.settings import cache_dir

    svg = icon_svg(name, color)
    path = cache_dir() / "ui" / "icons" / f"{name}_{hashlib.sha1(svg.encode()).hexdigest()[:10]}.svg"
    try:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8")
    except OSError:
        return ""
    return str(path)


class PersistentSplitter(QSplitter):
    """Resizable splitter whose position is kept from one session to the next (ui_layout.json file,
    separate from the settings: the Settings page does not touch it)."""

    def __init__(self, orientation, key: str, parent=None):
        super().__init__(orientation, parent)
        self.key = key
        self.setChildrenCollapsible(False)
        self.setHandleWidth(7)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save)
        self.splitterMoved.connect(lambda *_: self._save_timer.start())

    @staticmethod
    def _file() -> Path:
        from ..models.settings import app_data_dir
        return app_data_dir() / "ui_layout.json"

    @classmethod
    def _load_all(cls) -> dict:
        try:
            import json
            data = json.loads(cls._file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def restore(self, default_sizes: list[int]) -> bool:
        """Restores the saved position, otherwise applies `default_sizes`. True if restored."""
        state = self._load_all().get(self.key)
        if state:
            try:
                from PySide6.QtCore import QByteArray
                if self.restoreState(QByteArray.fromHex(state.encode())):
                    return True
            except Exception:
                pass
        self.setSizes(default_sizes)
        return False

    def _save(self) -> None:
        import json
        data = self._load_all()
        data[self.key] = bytes(self.saveState().toHex()).decode()
        try:
            self._file().write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


def app_icon(size: int = 256) -> QIcon:
    """Snappy's icon: orange rounded square + stylized shutter."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#ff7a3d"))
    grad.setColorAt(1, QColor("#ff3d1f"))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), size * 0.24, size * 0.24)
    # "Shutter button" circle
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("white"), size * 0.09))
    r = size * 0.27
    c = QPointF(size / 2, size / 2)
    p.drawEllipse(c, r, r)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    p.drawEllipse(c, r * 0.42, r * 0.42)
    p.end()
    return QIcon(pm)


def nav_button(text: str, glyph: str) -> QPushButton:
    b = QPushButton(f"  {text}")
    b.setObjectName("navButton")
    b.setCheckable(True)
    b.setIcon(glyph_icon(glyph, theme.TEXT_DIM, 32))
    b.setIconSize(QSize(20, 20))
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(glyph: str, tooltip: str, object_name: str = "iconButton") -> QPushButton:
    b = QPushButton()
    b.setObjectName(object_name)
    b.setIcon(glyph_icon(glyph, theme.TEXT, 32))
    b.setIconSize(QSize(20, 20))
    b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_time_precise(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:05.2f}"


def fmt_size(num: int) -> str:
    units = (tr("B"), tr("KB"), tr("MB"), tr("GB"))
    for i, unit in enumerate(units):
        if num < 1024 or i == len(units) - 1:
            return f"{num:.0f} {unit}" if i == 0 else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} {units[-1]}"


def fmt_relative(dt: datetime) -> str:
    now = datetime.now()
    delta = now - dt
    if delta.total_seconds() < 60:
        return tr("Just now")
    if delta.total_seconds() < 3600:
        return tr("{n} min ago", n=int(delta.total_seconds() // 60))
    if dt.date() == now.date():
        return tr("Today, {time}", time=f"{dt:%H:%M}")
    if (now.date() - dt.date()).days == 1:
        return tr("Yesterday, {time}", time=f"{dt:%H:%M}")
    return tr("{date:%Y-%m-%d, %H:%M}", date=dt)


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------
ROLE_CLIP = Qt.UserRole + 1
ROLE_HEADER = Qt.UserRole + 2

CARD_TEXT_H = 64
HEADER_H = 46


class ClipDelegate(QStyledItemDelegate):
    """Draws a card (16:9 thumbnail, length, game, favorite, selection box) or a section title.
    On hover, the clip plays by itself (small preview video, no sound); over the progress bar, the
    preview follows the mouse through the clip (precomputed frames)."""

    def __init__(self, view: "GalleryView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_HEADER):
            return QSize(self.view.layout_width(), HEADER_H)
        w = self.view.card_width()
        return QSize(w, int((w - 16) * 9 / 16) + 16 + CARD_TEXT_H)

    @staticmethod
    def thumb_rect(rect: QRect) -> QRectF:
        r = QRectF(rect).adjusted(8, 8, -8, -8)
        return QRectF(r.left(), r.top(), r.width(), r.width() * 9 / 16)

    BUTTON = 34   # round buttons at the top right of the thumbnail: favorite then trash

    @staticmethod
    def trash_rect(tile: QRectF) -> QRectF:
        b = ClipDelegate.BUTTON
        return QRectF(tile.right() - b - 8, tile.top() + 8, b, b)

    @staticmethod
    def star_rect(tile: QRectF) -> QRectF:
        b = ClipDelegate.BUTTON
        return QRectF(tile.right() - 2 * b - 14, tile.top() + 8, b, b)

    @staticmethod
    def on_button(tile: QRectF, pos) -> bool:
        pt = QPointF(pos)
        return any(r.contains(pt) for r in (ClipDelegate.star_rect(tile), ClipDelegate.trash_rect(tile),
                                             ClipDelegate.check_rect(tile)))

    BAR_ZONE = 18     # bottom of the thumbnail: hovering this strip = seeking in the clip

    @staticmethod
    def bar_zone(tile: QRectF) -> QRectF:
        return QRectF(tile.left(), tile.bottom() - ClipDelegate.BAR_ZONE, tile.width(), ClipDelegate.BAR_ZONE)

    @staticmethod
    def check_rect(tile: QRectF) -> QRectF:
        return QRectF(tile.left() + 8, tile.top() + 8, 24, 24)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        header = index.data(ROLE_HEADER)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if header:
            title, count = header
            f = QFont(option.font)
            f.setPixelSize(17)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT))
            r = QRectF(option.rect).adjusted(10, 14, -10, 0)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, title)
            tw = p.fontMetrics().horizontalAdvance(title)
            f.setPixelSize(12)
            f.setWeight(QFont.Normal)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(r.adjusted(tw + 12, 2, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, count)
            p.restore()
            return
        clip = index.data(ROLE_CLIP)
        if clip is None:
            p.restore()
            return
        view = self.view
        hovered = view.hover_index == index.row()
        selected = view.is_selected(clip)
        r = QRectF(option.rect).adjusted(4, 4, -4, -4)
        card = QPainterPath()
        card.addRoundedRect(r, 12, 12)
        p.fillPath(card, QColor(theme.SURFACE_3 if hovered else theme.SURFACE))
        if selected:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawPath(card)

        tile = self.thumb_rect(option.rect)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(tile, 9, 9)
        p.setClipPath(clip_path)
        thumb = view.thumbs.get(clip.file)
        sprite = view.sprites.get(clip.file) if hovered and view.hover_frac is not None else None
        frame = view.hover_player.image if hovered and view.hover_frac is None \
            and view.hover_player.file == clip.file else None
        if frame is not None and not frame.isNull():
            p.drawImage(tile, frame, QRectF(frame.rect()))
        elif sprite and not sprite.isNull():
            from ..models.library import SPRITE_FRAMES

            fw = sprite.width() / SPRITE_FRAMES
            i = min(SPRITE_FRAMES - 1, int(view.hover_frac * SPRITE_FRAMES))
            src = QRectF(i * fw, 0, fw, sprite.height())
            p.drawPixmap(tile, sprite, src)
        elif thumb and not thumb.isNull():
            scaled = thumb.scaled(tile.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(tile.topLeft(), scaled, QRectF((scaled.width() - tile.width()) / 2,
                                                      (scaled.height() - tile.height()) / 2, tile.width(), tile.height()))
        else:
            p.fillRect(tile, QColor(theme.SURFACE_2))
            p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(tile, Qt.AlignCenter, tr("Generating the preview…"))
        if hovered:
            # Progress bar: playback position, or the mouse position when hovering it.
            frac = view.hover_frac if view.hover_frac is not None else view.hover_player.progress(clip.file)
            on_bar = view.hover_frac is not None
            bh = 6 if on_bar else 3
            p.fillRect(QRectF(tile.left(), tile.bottom() - bh, tile.width(), bh), QColor(255, 255, 255, 70))
            p.fillRect(QRectF(tile.left(), tile.bottom() - bh, tile.width() * frac, bh), QColor(theme.ACCENT))
        p.setClipping(False)

        small = QFont(option.font)
        small.setPixelSize(11)
        small.setBold(True)
        p.setFont(small)
        # Length (bottom right)
        dur = fmt_duration(clip.duration) if clip.duration else "--:--"
        bw = p.fontMetrics().horizontalAdvance(dur) + 14
        br = QRectF(tile.right() - bw - 8, tile.bottom() - 30, bw, 19)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 185))
        p.drawRoundedRect(br, 5, 5)
        p.setPen(QColor("white"))
        p.drawText(br, Qt.AlignCenter, dur)
        # Game / application (bottom left)
        if clip.group and view.show_group_chip:
            icon = view.group_icon(clip)
            label = p.fontMetrics().elidedText(clip.group, Qt.ElideRight, int(tile.width() * 0.55))
            cw = p.fontMetrics().horizontalAdvance(label) + (30 if icon else 14)
            cr = QRectF(tile.left() + 8, tile.bottom() - 30, cw, 19)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 185))
            p.drawRoundedRect(cr, 5, 5)
            if icon:
                icon.paint(p, QRect(int(cr.left()) + 3, int(cr.top()) + 2, 15, 15))
            p.setPen(QColor("white"))
            p.drawText(cr.adjusted(22 if icon else 7, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, label)
        # Badges: screens, tracks
        badges = []
        if clip.n_screens > 1:
            badges.append(tr("{n} screens", n=clip.n_screens))
        if len(clip.tracks) > 1:
            badges.append(tr("{n} tracks", n=len(clip.tracks)))
        x = tile.right() - 8
        y = tile.top() + 8 if not (hovered or clip.favorite) else tile.top() + 8 + self.BUTTON + 6
        for b in badges:
            w = p.fontMetrics().horizontalAdvance(b) + 12
            rr = QRectF(x - w, y, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(rr, 5, 5)
            p.setPen(QColor("white"))
            p.drawText(rr, Qt.AlignCenter, b)
            y += 22
        # Favorite then trash (top right): on hover; the star stays visible if the clip is a favorite
        hover_btn = view.hover_button if hovered else ""
        if clip.favorite or hovered:
            sr = self.star_rect(tile)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 215 if hover_btn == "star" else 150))
            p.drawEllipse(sr)
            icon = glyph_icon("star_filled" if clip.favorite else "star",
                              theme.FAVORITE if clip.favorite else "white", 48)
            icon.paint(p, sr.adjusted(7, 7, -7, -7).toRect())
        if hovered:
            dr = self.trash_rect(tile)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.DANGER) if hover_btn == "trash" else QColor(0, 0, 0, 150))
            p.drawEllipse(dr)
            glyph_icon("delete", "white", 48).paint(p, dr.adjusted(8, 8, -8, -8).toRect())
        # Selection (top left): visible on hover, in selection mode or when checked
        if hovered or selected or view.selecting():
            cr = self.check_rect(tile)
            p.setPen(QPen(QColor("white"), 1.6))
            p.setBrush(QColor(theme.ACCENT) if selected else QColor(0, 0, 0, 120))
            p.drawRoundedRect(cr, 6, 6)
            if selected:
                glyph_icon("check", "white", 32).paint(p, cr.adjusted(4, 4, -4, -4).toRect())
        # Texts
        text_r = QRectF(r.left() + 8, tile.bottom() + 8, r.width() - 16, 20)
        title_font = QFont(option.font)
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.DemiBold)
        p.setFont(title_font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(text_r, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(clip.title, Qt.ElideRight, int(text_r.width())))
        meta_font = QFont(option.font)
        meta_font.setPixelSize(12)
        p.setFont(meta_font)
        p.setPen(QColor(theme.TEXT_DIM))
        parts = [clip.created_dt.strftime("%H:%M")]
        if clip.height:
            parts.append(f"{clip.height}p{clip.fps}")
        parts.append(fmt_size(clip.size))
        meta = p.fontMetrics().elidedText("  ·  ".join(parts), Qt.ElideRight, int(text_r.width()))
        p.drawText(text_r.translated(0, 21), Qt.AlignLeft | Qt.AlignVCenter, meta)
        p.restore()


class HoverPlayer(QObject):
    """Loops the preview video of the hovered card, without sound (one at a time)."""

    frame = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

        self.player = QMediaPlayer(self)
        self.player.setLoops(QMediaPlayer.Infinite)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.file = ""
        self.image = None
        self._start = QTimer(self, singleShot=True, interval=180, timeout=self._play)
        self._pending: tuple[str, str] | None = None

    def show(self, file: str, path: str) -> None:
        """Starts the preview of `file` (after a short delay: nothing plays when the mouse just passes by)."""
        if file == self.file and self.player.source().isValid():
            return
        self.stop()
        self._pending = (file, path)
        self._start.start()

    def _play(self) -> None:
        if not self._pending:
            return
        self.file, path = self._pending
        self._pending = None
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def stop(self) -> None:
        self._start.stop()
        self._pending = None
        if self.file or self.player.source().isValid():
            self.player.stop()
            self.player.setSource(QUrl())   # releases the file (trash, rename)
        self.file, self.image = "", None

    def progress(self, file: str) -> float:
        if file != self.file or self.player.duration() <= 0:
            return 0.0
        return max(0.0, min(1.0, self.player.position() / self.player.duration()))

    def _on_frame(self, frame) -> None:
        if self.file:
            self.image = frame.toImage()
            self.frame.emit(self.file)


class GalleryView(QListView):
    """Grid of cards grouped by day. Click: open; Ctrl/Shift + click or box: select; dragging a card
    (or the selection): drops the files into Discord, the file manager..."""

    clipActivated = Signal(object)
    contextRequested = Signal(object, QPoint)
    favoriteToggled = Signal(object)
    trashRequested = Signal(object)
    selectionChanged_ = Signal(int)
    spriteNeeded = Signal(object)
    dragStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(ClipDelegate(self))
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setSelectionMode(QListView.NoSelection)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context)
        self.thumbs: dict[str, QPixmap] = {}
        self.sprites: dict[str, QPixmap] = {}
        self.selected: set[str] = set()
        self.thumb_size = 300
        self.hover_index = -1
        self.hover_frac: float | None = None
        self.hover_button = ""          # "star" / "trash" under the mouse
        self.previews: dict[str, str] = {}
        self.hover_player = HoverPlayer(self)
        self.hover_player.frame.connect(self._on_hover_frame)
        self.show_group_chip = True
        self.folder = ""
        self._icons: dict[str, QIcon | None] = {}
        self._press: tuple[QPoint, object] | None = None

    # ------------------------------------------------------------ data
    def layout_width(self) -> int:
        """Width available for the grid, as if the scroll bar were always there: measured on the
        viewport, its appearance changes the card width, hence their height, hence the need to
        scroll... and the grid oscillates endlessly at some sizes."""
        bar = self.verticalScrollBar()
        reserved = 0 if bar.isVisible() else bar.sizeHint().width()
        return max(200, self.viewport().width() - reserved - 12)

    def card_width(self) -> int:
        vw = self.layout_width()
        cols = max(1, vw // max(self.thumb_size, 1))
        return vw // cols

    def set_clips(self, clips: list, group_by_day: bool = True) -> None:
        self.hover_player.stop()
        self.model_.clear()
        self.hover_index = -1
        last = None
        days = {}
        for c in clips:
            days.setdefault(c.created_dt.date(), 0)
            days[c.created_dt.date()] += 1
        for clip in clips:
            day = clip.created_dt.date()
            if group_by_day and day != last:
                last = day
                head = QStandardItem()
                head.setEditable(False)
                head.setSelectable(False)
                n = days[day]
                head.setData((day_label(clip.created_dt), tr("{n} clip", n=n) if n == 1 else tr("{n} clips", n=n)),
                             ROLE_HEADER)
                self.model_.appendRow(head)
            item = QStandardItem()
            item.setEditable(False)
            item.setData(clip, ROLE_CLIP)
            item.setToolTip(f"{clip.title}\n{clip.file}")
            self.model_.appendRow(item)
        self.selected &= {c.file for c in clips}
        self.selectionChanged_.emit(len(self.selected))

    def set_thumb(self, file: str, pixmap: QPixmap) -> None:
        self.thumbs[file] = pixmap
        self.viewport().update()

    def set_sprite(self, file: str, pixmap: QPixmap) -> None:
        self.sprites[file] = pixmap
        self.viewport().update()

    def set_preview(self, file: str, path: str) -> None:
        self.previews[file] = path
        clip = self._hover_clip()
        if clip is not None and clip.file == file:
            self.hover_player.show(file, path)

    def _hover_clip(self):
        if self.hover_index < 0:
            return None
        item = self.model_.item(self.hover_index)
        return item.data(ROLE_CLIP) if item is not None else None

    def _on_hover_frame(self, file: str) -> None:
        clip = self._hover_clip()
        if clip is not None and clip.file == file and self.hover_frac is None:
            self.viewport().update(self.visualRect(self.model_.index(self.hover_index, 0)))

    def set_thumb_size(self, w: int) -> None:
        self.thumb_size = w
        self.doItemsLayout()

    def clips(self) -> list:
        return [self.model_.item(r).data(ROLE_CLIP) for r in range(self.model_.rowCount())
                if self.model_.item(r).data(ROLE_CLIP) is not None]

    def group_icon(self, clip) -> QIcon | None:
        key = clip.group
        if key not in self._icons:
            self._icons[key] = group_icon(clip.appid, clip.app_exe)
        return self._icons[key]

    # ------------------------------------------------------------ selection
    def selecting(self) -> bool:
        return bool(self.selected)

    def is_selected(self, clip) -> bool:
        return clip.file in self.selected

    def toggle_selected(self, clip) -> None:
        self.selected ^= {clip.file}
        self.viewport().update()
        self.selectionChanged_.emit(len(self.selected))

    def select_all(self) -> None:
        self.selected = {c.file for c in self.clips()}
        self.viewport().update()
        self.selectionChanged_.emit(len(self.selected))

    def clear_selection(self) -> None:
        self.selected.clear()
        self.viewport().update()
        self.selectionChanged_.emit(0)

    def selected_clips(self) -> list:
        return [c for c in self.clips() if c.file in self.selected]

    # ------------------------------------------------------------ mouse
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.doItemsLayout()

    def _clip_at(self, pos: QPoint):
        idx = self.indexAt(pos)
        return (idx, idx.data(ROLE_CLIP)) if idx.isValid() else (idx, None)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._press and (pos - self._press[0]).manhattanLength() >= QApplication.startDragDistance():
            clip = self._press[1]
            self._press = None
            self._start_drag(clip)
            return
        idx, clip = self._clip_at(pos)
        row = idx.row() if clip is not None else -1
        frac = None
        if clip is not None:
            tile = ClipDelegate.thumb_rect(self.visualRect(idx))
            button = ("star" if ClipDelegate.star_rect(tile).contains(QPointF(pos)) else
                      "trash" if ClipDelegate.trash_rect(tile).contains(QPointF(pos)) else "")
            if button != self.hover_button:
                self.hover_button = button
                self.viewport().update()
            if ClipDelegate.bar_zone(tile).contains(QPointF(pos)):
                frac = max(0.0, min(0.999, (pos.x() - tile.left()) / tile.width()))
            if clip.file not in self.sprites or clip.file not in self.previews:
                self.spriteNeeded.emit(clip)        # hover bar + preview video, once
            if clip.file in self.previews:
                self.hover_player.show(clip.file, self.previews[clip.file])
            elif self.hover_player.file != clip.file:
                self.hover_player.stop()
        else:
            self.hover_player.stop()
        if row != self.hover_index or frac != self.hover_frac:
            self.hover_index, self.hover_frac = row, frac
            self.viewport().update()
        self.setCursor(Qt.PointingHandCursor if clip is not None else Qt.ArrowCursor)

    def leaveEvent(self, e):
        self.hover_player.stop()
        self.hover_index, self.hover_frac = -1, None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        idx, clip = self._clip_at(pos)
        if clip is None or e.button() != Qt.LeftButton:
            super().mousePressEvent(e)
            return
        tile = ClipDelegate.thumb_rect(self.visualRect(idx))
        if ClipDelegate.star_rect(tile).contains(QPointF(pos)):
            self.favoriteToggled.emit(clip)
            return
        if ClipDelegate.trash_rect(tile).contains(QPointF(pos)):
            self.hover_player.stop()
            self.trashRequested.emit(clip)
            return
        if ClipDelegate.check_rect(tile).contains(QPointF(pos)) or e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            if e.modifiers() & Qt.ShiftModifier and self.selected:
                self._select_range(clip)
            else:
                self.toggle_selected(clip)
            return
        self._press = (pos, clip)

    def mouseReleaseEvent(self, e):
        press, self._press = self._press, None
        if press and e.button() == Qt.LeftButton:
            clip = press[1]
            if self.selecting():
                self.toggle_selected(clip)
            else:
                self.clipActivated.emit(clip)

    def _select_range(self, clip) -> None:
        clips = self.clips()
        files = [c.file for c in clips]
        anchor = max((files.index(f) for f in self.selected if f in files), default=0)
        i = files.index(clip.file)
        lo, hi = sorted((anchor, i))
        self.selected |= set(files[lo:hi + 1])
        self.viewport().update()
        self.selectionChanged_.emit(len(self.selected))

    def _start_drag(self, clip) -> None:
        clips = self.selected_clips() if clip.file in self.selected else [clip]
        self.dragStarted.emit()
        paths = [Path(self.folder) / c.file for c in clips]
        start_file_drag(self, paths, self.thumbs.get(clip.file))

    def _on_context(self, pos: QPoint) -> None:
        idx, clip = self._clip_at(pos)
        if clip is not None:
            self.contextRequested.emit(clip, self.viewport().mapToGlobal(pos))


def day_label(dt: datetime) -> str:
    months = [tr("January"), tr("February"), tr("March"), tr("April"), tr("May"), tr("June"), tr("July"),
              tr("August"), tr("September"), tr("October"), tr("November"), tr("December")]
    weekdays = [tr("Monday"), tr("Tuesday"), tr("Wednesday"), tr("Thursday"), tr("Friday"), tr("Saturday"),
                tr("Sunday")]
    days = (datetime.now().date() - dt.date()).days
    if days == 0:
        return tr("Today")
    if days == 1:
        return tr("Yesterday")
    date = tr("{month} {day}", month=months[dt.month - 1], day=dt.day)
    if days < 7:
        return tr("{weekday} {date}", weekday=weekdays[dt.weekday()], date=date)
    return date if dt.year == datetime.now().year else tr("{date}, {year}", date=date, year=dt.year)


def group_icon(appid: str = "", exe: str = "") -> QIcon | None:
    """Icon of a game (Steam cache) or of an application (icon of its executable)."""
    if appid:
        from ..models import games

        path = games.game_art(appid).get("icon")
        if path:
            return QIcon(path)
    if exe and Path(exe).exists():
        from PySide6.QtCore import QFileInfo
        from PySide6.QtWidgets import QFileIconProvider

        icon = QFileIconProvider().icon(QFileInfo(exe))
        if not icon.isNull():
            return icon
    return None


# ---------------------------------------------------------------------------
# "Clip saved" notification (bottom right corner, above windowed games)
# ---------------------------------------------------------------------------
class Toast(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(340, 86)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(96, 54)
        self.thumb.setStyleSheet(f"background:{theme.SURFACE_3}; border-radius:6px;")
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight:600; font-size:14px; background:transparent;")
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px; background:transparent;")
        col.addStretch()
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        col.addStretch()
        lay.addLayout(col, 1)
        self._timer = QTimer(self, singleShot=True, timeout=self._fade_out)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._after_fade)   # only hides at the end of a fade-out
        self._ok = True

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(theme.SURFACE))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRoundedRect(r, 12, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if self._ok else theme.DANGER))
        p.drawRoundedRect(QRectF(r.left(), r.top() + 14, 4, r.height() - 28), 2, 2)

    def show_message(self, title: str, subtitle: str, thumb: QPixmap | None = None,
                     ok: bool = True, ms: int = 3500, screen=None) -> None:
        self._ok = ok
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        if thumb and not thumb.isNull():
            self.thumb.setPixmap(thumb.scaled(96, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            self.thumb.show()
        else:
            self.thumb.hide()
        screen = (screen or QApplication.primaryScreen()).availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)
        self._anim.stop()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)
        self.update()

    def _fade_out(self):
        self._anim.stop()
        self._anim.setDuration(350)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _after_fade(self):
        if self.windowOpacity() < 0.05:
            self.hide()

    def mousePressEvent(self, _):
        self.hide()
        self.clicked.emit()


# ---------------------------------------------------------------------------
# Sharing: file drag-and-drop (Discord, file manager, browser...)
# ---------------------------------------------------------------------------
def file_mime(paths: list) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def start_file_drag(source: QWidget, paths: list, pixmap: QPixmap | None = None) -> None:
    """Dragging files out of Snappy: Windows receives a real file drop (CF_HDROP)."""
    if not paths:
        return
    drag = QDrag(source)
    drag.setMimeData(file_mime(paths))
    if pixmap and not pixmap.isNull():
        drag.setPixmap(pixmap.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.setHotSpot(QPoint(20, 20))
    drag.exec(Qt.CopyAction)


def copy_files(paths: list) -> None:
    QApplication.clipboard().setMimeData(file_mime(paths))


class ShareTile(QFrame):
    """File ready to send: drag it anywhere, or copy it (Ctrl+V in Discord)."""

    copied = Signal(str)
    revealRequested = Signal(str)

    def __init__(self, title: str, glyph: str, parent=None):
        super().__init__(parent)
        self.setObjectName("shareTile")
        self.setCursor(Qt.OpenHandCursor)
        self.path: Path | None = None
        self.resolve = None            # callable -> path (file prepared on demand)
        self._press: QPoint | None = None
        self._pixmap: QPixmap | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(64, 36)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background:{theme.SURFACE_3}; border-radius:5px;")
        self.glyph = glyph
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight:600; background:transparent;")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("faint")
        self.subtitle.setStyleSheet("background:transparent;")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.copy_btn = icon_button("copy", tr("Copy the file (then Ctrl+V in Discord, an email…)"), "ghost")
        self.copy_btn.clicked.connect(self._copy)
        self.folder_btn = icon_button("folder", system.TEXT["reveal"], "ghost")
        self.folder_btn.clicked.connect(lambda: self.path and self.revealRequested.emit(str(self.path)))
        for b in (self.copy_btn, self.folder_btn):
            b.setFixedSize(30, 30)
            lay.addWidget(b)
        self.set_file(None, "")

    def set_file(self, path, subtitle: str, pixmap: QPixmap | None = None, resolve=None, stale: bool = False) -> None:
        self.path = Path(path) if path else None
        self.resolve = resolve
        available = bool(path or resolve)
        self.subtitle.setText(subtitle)
        self.setEnabled(available)
        self._pixmap = pixmap
        if pixmap and not pixmap.isNull():
            self.thumb.setPixmap(pixmap.scaled(64, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            self.thumb.setPixmap(glyph_icon(self.glyph, theme.TEXT_DIM, 40).pixmap(22, 22))
        self.setProperty("stale", stale)
        self.setStyleSheet(f"QFrame#shareTile {{ background:{theme.SURFACE_2}; border-radius:10px; border:1px solid "
                           f"{'#e66100' if stale else theme.BORDER}; }} QFrame#shareTile:hover {{ border-color:{theme.ACCENT}; }}")
        self.setToolTip(tr("Drag this file into Discord, a folder, a browser…") if available else "")

    def _file(self) -> Path | None:
        if self.path is None and self.resolve:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.path = Path(self.resolve())
            except Exception:
                self.path = None
            finally:
                QApplication.restoreOverrideCursor()
        return self.path if self.path and self.path.exists() else None

    def _copy(self) -> None:
        f = self._file()
        if f:
            copy_files([f])
            self.copied.emit(f.name)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._press is None or (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        f = self._file()
        if f:
            self.setCursor(Qt.ClosedHandCursor)
            start_file_drag(self, [f], self._pixmap)
            self.setCursor(Qt.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        self._press = None


class Segmented(QWidget):
    """Exclusive buttons side by side (presets, period filters...)."""

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(items):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            pos = "first" if i == 0 else "last" if i == len(items) - 1 else "mid"
            b.setObjectName(f"seg_{pos}")
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)

    def set_current(self, key: str | None) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)
        else:
            self.group.setExclusive(False)
            for b in self.buttons.values():
                b.setChecked(False)
            self.group.setExclusive(True)

    def current(self) -> str | None:
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)


class InAppToast(QFrame):
    """Message at the bottom of the window, with an optional button ("Undo")."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("inAppToast")
        self.setStyleSheet(f"QFrame#inAppToast {{ background:{theme.SURFACE_3}; border:1px solid {theme.BORDER};"
                           "border-radius:10px; } QLabel { background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 8, 8)
        self.label = QLabel()
        lay.addWidget(self.label, 1)
        self.button = QPushButton()
        self.button.setObjectName("ghost")
        self.button.setStyleSheet(f"color:{theme.ACCENT}; font-weight:600;")
        lay.addWidget(self.button)
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._callback = None
        self.button.clicked.connect(self._clicked)
        self.hide()

    def show_message(self, text: str, button: str = "", callback=None, ms: int = 3500) -> None:
        self.label.setText(text)
        self._callback = callback
        self.button.setText(button)
        self.button.setVisible(bool(button))
        self.adjustSize()
        w = min(max(self.sizeHint().width(), 320), self.parentWidget().width() - 40)
        self.resize(w, self.sizeHint().height())
        self.move((self.parentWidget().width() - w) // 2, self.parentWidget().height() - self.height() - 24)
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _clicked(self):
        self.hide()
        if self._callback:
            self._callback()


class GamePickerDialog(QDialog):
    """Picks the game of one or more clips: games already seen, installed Steam games, free name or none."""

    def __init__(self, known: dict, current: str = "", count: int = 1, parent=None):
        super().__init__(parent)
        from ..models import games

        self.setWindowTitle(tr("Game of the clip") if count == 1 else tr("Game of {n} clips", n=count))
        self.resize(420, 520)
        self.result_value: tuple[str, str] | None = None
        self.known = dict(known)
        for name, appid in games.installed_games():
            self.known.setdefault(name, appid)
        self.current = current
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search or type a game name"))
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setStyleSheet(f"QListWidget {{ background:{theme.SURFACE_2}; border-radius:8px; }}"
                                f"QListWidget::item {{ padding:6px; }} QListWidget::item:selected {{ background:{theme.ACCENT_DIM}; }}")
        lay.addWidget(self.list, 1)
        self.search.textChanged.connect(self._fill)
        self.search.returnPressed.connect(self._accept_text)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        self._fill()

    def _fill(self, *_):
        from ..models import games

        self.list.clear()
        q = self.search.text().strip()
        if q and q.lower() not in (k.lower() for k in self.known):
            it = QListWidgetItem(glyph_icon("add"), tr('Use "{name}"', name=q))
            it.setData(Qt.UserRole, (q, ""))
            self.list.addItem(it)
        if not q:
            it = QListWidgetItem(glyph_icon("close"), tr("No game"))
            it.setData(Qt.UserRole, ("", ""))
            self.list.addItem(it)
        for name in sorted(self.known, key=str.lower):
            if q and q.lower() not in name.lower():
                continue
            icon_path = games.game_art(self.known[name]).get("icon")
            icon = QIcon(icon_path) if icon_path else glyph_icon("game")
            it = QListWidgetItem(icon, name + ("  ✓" if name == self.current else ""))
            it.setData(Qt.UserRole, (name, self.known[name] or ""))
            self.list.addItem(it)

    def _accept_text(self):
        q = self.search.text().strip()
        if q:
            match = next((k for k in self.known if k.lower() == q.lower()), None)
            self.result_value = (match or q, (self.known.get(match) or "") if match else "")
            self.accept()

    def _pick(self, item):
        self.result_value = item.data(Qt.UserRole)
        self.accept()
