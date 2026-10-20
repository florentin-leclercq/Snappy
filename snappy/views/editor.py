"""Editor view: playback, trimming (start/end + removed passages), multi-screen editing, separate
audio tracks (volume and curves set on the timeline), export settings and sharing.

The view shows the clip and the current edit and emits signals; `controllers.editor` saves the
edit, renames, exports and provides the timeline frames."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from ..models import edit as E
from ..models.library import Clip
from ..models.settings import CODECS, Settings
from . import theme
from ..services.audio_preview import AudioPreview
from .timeline import APP_COLORS, KIND_COLORS, EditorTimeline, fmt_clock
from .widgets import (GamePickerDialog, PersistentSplitter, Segmented, ShareTile, fmt_size, fmt_time_precise, glyph_icon, icon_button)

QUALITY_STEPS = [(16, tr("Maximum")), (20, tr("Very high")), (23, tr("High")), (27, tr("Medium")), (31, tr("Low"))]


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    return frame, lay


def _subheading(text: str, glyph: str) -> QWidget:
    """Subheading of a card: small icon + label in capitals."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    icon = QLabel()
    icon.setPixmap(glyph_icon(glyph, theme.TEXT_FAINT).pixmap(15, 15))
    lay.addWidget(icon)
    label = QLabel(text.upper())
    label.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:11px; font-weight:600; letter-spacing:1px;")
    lay.addWidget(label)
    lay.addStretch()
    return w


class _TimelineScroll(QScrollArea):
    """Scroll area of the timeline: it takes the timeline's height while the page has room, then
    scrolls (mouse wheel) when there are many tracks."""

    def __init__(self, timeline: QWidget):
        super().__init__()
        self.setWidget(timeline)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.verticalScrollBar().setSingleStep(24)
        # The ruler and the "Multicam" row stay at the top while the tracks scroll.
        self.verticalScrollBar().valueChanged.connect(timeline.set_pin)

    def sizeHint(self) -> QSize:
        return QSize(400, self.widget().minimumHeight() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, min(self.widget().minimumHeight() + 2, 180))


class TitleEdit(QLineEdit):
    """Clip title, editable in place: click to edit, Enter to confirm, Esc to cancel."""

    committed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleEdit")
        self.setReadOnly(True)
        self.setCursor(Qt.IBeamCursor)
        self.setToolTip(tr("Click to rename the clip (also the name of the shared file)"))
        self._original = ""
        self.returnPressed.connect(self._commit)

    def set_title(self, text: str) -> None:
        self._original = text
        self.setReadOnly(True)
        self.setText(text)
        self.setCursorPosition(0)
        self._fit()

    def _fit(self) -> None:
        # Theme font (22 px bold): the widget's own font is only applied when shown.
        font = QFont(self.font())
        font.setPixelSize(22)
        font.setWeight(QFont.Bold)
        self.setFixedWidth(min(760, QFontMetrics(font).horizontalAdvance(self.text() or " ") + 24))

    def start_edit(self) -> None:
        # A click elsewhere (video, timeline... which do not take the focus) confirms the title too.
        QApplication.instance().installEventFilter(self)
        self.setReadOnly(False)
        self.setProperty("editing", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setFocus()
        self.selectAll()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseButtonPress and not self.isReadOnly():
            pos = self.mapFromGlobal(e.globalPosition().toPoint())
            if not self.rect().contains(pos):
                self._commit()
        return False

    def _stop_edit(self) -> None:
        QApplication.instance().removeEventFilter(self)
        self.setReadOnly(True)
        self.setProperty("editing", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.deselect()
        self.setCursorPosition(0)

    def _commit(self) -> None:
        if self.isReadOnly():
            return
        text = self.text().strip()
        self._stop_edit()
        self.clearFocus()
        if text and text != self._original:
            self.committed.emit(text)
        else:
            self.set_title(self._original)

    def event(self, e):
        # While typing, keys go to the title and not to the editor shortcuts (Space, Esc...).
        if e.type() == QEvent.ShortcutOverride and not self.isReadOnly():
            e.accept()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        if self.isReadOnly():
            self.start_edit()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and not self.isReadOnly():
            self._stop_edit()
            self.set_title(self._original)
            self.clearFocus()
            return
        super().keyPressEvent(e)
        self._fit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._commit()


class EditorPage(QWidget):
    backRequested = Signal()
    trashRequested = Signal(object)
    copyRequested = Signal(object)
    revealRequested = Signal(object)
    revealPath = Signal(str)
    message = Signal(str)
    # To the controller
    saveEditRequested = Signal(object, object)     # edit, screen switches
    renameRequested = Signal(str)
    favoriteToggled = Signal()
    gamePickRequested = Signal()
    exportRequested = Signal(object)               # export request (see `export`)
    exportCancelRequested = Signal()
    normalizeChanged = Signal(bool)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.clip: Clip | None = None
        self.path: Path | None = None
        self.exporting = False
        self._latest_export: Path | None = None    # latest video export (for "up to date?")
        self._codecs: dict[str, tuple[bool, str]] = {}   # codec -> (can be encoded here, encoder)
        self.cuts: list[tuple[float, float]] = []
        self.angles: list[list] = [[0.0, 0]]
        self.track_info: list[tuple[int, dict]] = []
        self.history = E.EditHistory()
        self._restoring = False
        self.loop = True
        self.preview_screen: int | None = None   # None = multicam edit; k = always screen k
        self._active_video = 0
        self._resume: tuple[int, bool] | None = None   # position to restore after a reload
        self._scrub_resume = False
        self._was_playing = False
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        # Splitters: preview | export and sharing, then preview / timeline.
        self.body_split = PersistentSplitter(Qt.Horizontal, "editor_side")
        top_w, bottom_w = QWidget(), QWidget()
        left = QVBoxLayout(top_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        bottom = QVBoxLayout(bottom_w)
        bottom.setContentsMargins(0, 4, 0, 0)
        bottom.setSpacing(8)
        self.view_split = PersistentSplitter(Qt.Vertical, "editor_timeline")
        self.view_split.addWidget(top_w)
        self.view_split.addWidget(bottom_w)
        self.view_split.setStretchFactor(0, 1)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.video.setMinimumHeight(120)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left.addWidget(self.video, 1)
        self.screen_pick = None
        self.screen_order: list[int] = [0]   # video streams in display order
        self.screen_pick_box = QHBoxLayout()
        self.screen_pick_box.addStretch()     # only once: the picker stays on the left
        left.addLayout(self.screen_pick_box)
        left.addLayout(self._build_transport())
        self.timeline = EditorTimeline()
        self.timeline.seekRequested.connect(self._on_timeline_seek)
        self.timeline.scrubbing.connect(self._on_scrub)
        self.timeline.rangeChanged.connect(self._on_range)
        self.timeline.cutsChanged.connect(self._on_cuts)
        self.timeline.anglesChanged.connect(self._on_angles)
        self.timeline.mixChanged.connect(self._on_mix)
        self.timeline.editCommitted.connect(self._commit)
        self.timeline_scroll = _TimelineScroll(self.timeline)
        self.timeline.heightChanged.connect(self.timeline_scroll.updateGeometry)
        bottom.addWidget(self.timeline_scroll, 1)
        self.hint = QLabel()
        self.hint.setObjectName("faint")
        self.hint.setWordWrap(True)
        bottom.addWidget(self.hint)
        self.body_split.addWidget(self.view_split)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setMaximumWidth(680)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side = QWidget()
        side.setObjectName("root")
        self.side = QVBoxLayout(side)
        self.side.setContentsMargins(0, 0, 8, 0)
        self.side.setSpacing(12)
        self.side.addWidget(self._build_export_section())
        self.side.addWidget(self._build_share_section())
        self.side.addStretch()
        side_scroll.setWidget(side)
        side_scroll.setMinimumWidth(400)     # below this, the export settings would be cut off
        self.body_split.addWidget(side_scroll)
        self.body_split.setStretchFactor(0, 1)
        self.body_split.restore([900, 410])
        self._timeline_sized = self.view_split.restore([520, 330])
        root.addWidget(self.body_split, 1)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.8)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.preview = AudioPreview(self)
        self.preview.attach(lambda: self.player.position() / 1000)
        self.preview.ready.connect(self._on_preview_ready)

        self._tick = QTimer(self, interval=25, timeout=self._on_tick)
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save_edit)
        self._commit_timer = QTimer(self, singleShot=True, interval=700, timeout=self._commit)

        shortcuts = [
            (Qt.Key_Space, self.toggle_play), (Qt.Key_Escape, self._escape), (Qt.Key_F2, self.rename),
            (Qt.Key_Delete, self.delete), (Qt.Key_F, self.toggle_favorite), (Qt.Key_M, self.toggle_mute),
            (Qt.Key_I, self.mark_in), (Qt.Key_O, self.mark_out), (Qt.Key_C, self.cut_mark),
            (Qt.Key_L, self.toggle_loop), (Qt.Key_Left, lambda: self.step(-1.0)), (Qt.Key_Right, lambda: self.step(1.0)),
            (Qt.Key_Comma, lambda: self.step(-1 / 60)), (Qt.Key_Period, lambda: self.step(1 / 60)),
            (Qt.Key_Home, lambda: self.seek(self.timeline.t_in)), (Qt.Key_End, lambda: self.seek(self.timeline.t_out)),
            (QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo), ("Ctrl+Y", self.redo),
            (Qt.Key_F11, self.toggle_fullscreen), ("Ctrl+E", self.export),
        ] + [(getattr(Qt, f"Key_{k + 1}"), (lambda k=k: self.switch_here(k))) for k in range(6)]
        for key, fn in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        # In full screen, the video is a separate window: the page shortcuts no longer see it. It
        # handles its keys itself (and takes the focus in full screen).
        self.video.setFocusPolicy(Qt.StrongFocus)
        self.video.keyPressEvent = self._video_key
        self.video.mouseDoubleClickEvent = lambda _e: self.toggle_fullscreen()
        self.video.mousePressEvent = lambda _e: self.toggle_play() if not self.video.isFullScreen() else None

    # ------------------------------------------------------------ construction
    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        self.back_btn = icon_button("back", tr("Back to the gallery (Esc)"))
        self.back_btn.clicked.connect(self._go_back)
        header.addWidget(self.back_btn, 0, Qt.AlignTop)
        header.addSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.fav_btn = icon_button("star", tr("Favorite (F)"), "ghost")
        self.fav_btn.setFixedSize(30, 30)
        self.fav_btn.setIconSize(QSize(22, 22))
        self.fav_btn.clicked.connect(self.toggle_favorite)
        top.addWidget(self.fav_btn)
        self.title = TitleEdit()
        self.title.setFixedHeight(30)
        self.title.committed.connect(self._rename_to)
        top.addWidget(self.title)
        self.rename_btn = icon_button("edit", tr("Rename (F2)"), "ghost")
        self.rename_btn.setFixedSize(28, 28)
        self.rename_btn.clicked.connect(self.rename)
        top.addWidget(self.rename_btn)
        top.addStretch()
        col.addLayout(top)
        sub = QHBoxLayout()
        sub.setContentsMargins(40, 0, 0, 0)
        sub.setSpacing(6)
        game_icon = QLabel()
        game_icon.setPixmap(glyph_icon("game", theme.TEXT_DIM).pixmap(16, 16))
        sub.addWidget(game_icon)
        self.game_lbl = QLabel()
        self.game_lbl.setObjectName("pageSubtitle")
        sub.addWidget(self.game_lbl)
        self.game_btn = icon_button("edit", tr("Change the game"), "ghost")
        self.game_btn.setFixedSize(20, 20)
        self.game_btn.setIconSize(QSize(14, 14))
        self.game_btn.clicked.connect(self.pick_game)
        sub.addWidget(self.game_btn)
        sub.addSpacing(10)
        self.meta = QLabel()
        self.meta.setObjectName("faint")
        sub.addWidget(self.meta)
        sub.addStretch()
        col.addLayout(sub)
        header.addLayout(col, 1)
        return header

    def _build_transport(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.play_btn = icon_button("play", tr("Play / pause (Space)"))
        self.play_btn.setObjectName("primary")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setStyleSheet("border-radius: 20px; padding: 0;")
        self.play_btn.clicked.connect(self.toggle_play)
        bar.addWidget(self.play_btn)
        self.time_lbl = QLabel("0:00.0 / 0:00.0")
        self.time_lbl.setObjectName("dim")
        self.time_lbl.setMinimumWidth(110)
        bar.addWidget(self.time_lbl)

        def tool(glyph, tip, fn, checkable=False):
            b = icon_button(glyph, tip, "ghost")
            b.setFixedSize(32, 32)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b

        tool("prev", tr("Previous frame ( , )"), lambda: self.step(-1 / 60))
        tool("next", tr("Next frame ( . )"), lambda: self.step(1 / 60))
        self.loop_btn = tool("loop", tr("Loop playback (L)"), self.toggle_loop, True)
        self.loop_btn.setChecked(True)
        bar.addSpacing(8)
        for text, tip, fn in (("[ " + tr("Start"), tr("Excerpt start at the playhead (I)"), self.mark_in),
                              (tr("End") + " ]", tr("Excerpt end at the playhead (O)"), self.mark_out)):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.cut_btn = QPushButton(" " + tr("Remove"))
        self.cut_btn.setIcon(glyph_icon("cut"))
        self.cut_btn.setObjectName("ghost")
        self.cut_btn.setToolTip(tr("C: start of the passage to remove, then C at its end.\n"
                                   "Or Shift + drag on the filmstrip. Right-click a passage to put it back."))
        self.cut_btn.setFocusPolicy(Qt.NoFocus)
        self.cut_btn.clicked.connect(self.cut_mark)
        bar.addWidget(self.cut_btn)
        self.undo_btn = tool("undo", tr("Undo (Ctrl+Z)"), self.undo)
        self.redo_btn = tool("redo", tr("Redo (Ctrl+Y)"), self.redo)
        bar.addStretch()
        self.mute_btn = tool("volume", tr("Mute the playback (M)"), self.toggle_mute)
        self.listen = QSlider(Qt.Horizontal)
        self.listen.setRange(0, 100)
        self.listen.setValue(80)
        self.listen.setFixedWidth(100)
        self.listen.setFocusPolicy(Qt.NoFocus)
        self.listen.setToolTip(tr("Playback volume (does not affect the export)"))
        self.listen.valueChanged.connect(self._on_listen)
        bar.addWidget(self.listen)
        tool("fullscreen", tr("Full screen (F11)"), self.toggle_fullscreen)
        return bar

    def _build_export_section(self) -> QFrame:
        frame, lay = _section(tr("Export"))
        self.presets = Segmented([(k, label) for k, label, _d in E.PRESETS])
        self.presets.changed.connect(self._select_preset)
        lay.addWidget(self.presets)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.screen_combo = QComboBox()
        self.screen_lbl = QLabel(tr("Screen"))
        grid.addWidget(self.screen_lbl, 0, 0)
        grid.addWidget(self.screen_combo, 0, 1)
        self.res_combo = QComboBox()
        for h in E.HEIGHTS:
            self.res_combo.addItem(tr("Original") if h == 0 else f"{h}p", h)
        grid.addWidget(QLabel(tr("Resolution")), 1, 0)
        grid.addWidget(self.res_combo, 1, 1)
        self.fps_combo = QComboBox()
        for f in E.FPS_CHOICES:
            self.fps_combo.addItem(tr("Original") if f == 0 else tr("{n} fps", n=f), f)
        grid.addWidget(QLabel(tr("Frame rate")), 2, 0)
        grid.addWidget(self.fps_combo, 2, 1)
        self.codec_combo = QComboBox()
        for key, label in CODECS.items():
            self.codec_combo.addItem(label, key)
        grid.addWidget(QLabel("Codec"), 3, 0)
        grid.addWidget(self.codec_combo, 3, 1)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(0, 4000)
        self.size_spin.setSuffix(" " + tr("MB"))
        self.size_spin.setSpecialValueText(tr("Free"))
        grid.addWidget(QLabel(tr("Max size")), 4, 0)
        grid.addWidget(self.size_spin, 4, 1)
        self.quality_combo = QComboBox()
        for qp, label in QUALITY_STEPS:
            self.quality_combo.addItem(label, qp)
        grid.addWidget(QLabel(tr("Quality")), 5, 0)
        grid.addWidget(self.quality_combo, 5, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.norm_cb = QCheckBox(tr("Normalized volume"))
        self.norm_cb.setToolTip(tr("Standard level of the platforms (-14 LUFS), without clipping"))
        self.norm_cb.toggled.connect(self._on_normalize)
        lay.addWidget(self.norm_cb)
        for w in (self.screen_combo, self.res_combo, self.fps_combo, self.codec_combo, self.quality_combo):
            w.currentIndexChanged.connect(self._on_custom)
        self.size_spin.valueChanged.connect(self._on_custom)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        self.summary.setObjectName("dim")
        lay.addWidget(self.summary)
        self.export_btn = QPushButton("  " + tr("Export"))
        self.export_btn.setObjectName("primary")
        self.export_btn.setIcon(glyph_icon("export", "white"))
        self.export_btn.setIconSize(QSize(16, 16))
        self.export_btn.clicked.connect(self.export)
        lay.addWidget(self.export_btn)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.export_state = QLabel()
        self.export_state.setObjectName("faint")
        lay.addWidget(self.export_state)
        return frame

    def _build_share_section(self) -> QFrame:
        frame, lay = _section(tr("Share"))
        hint = QLabel(tr("Drag a file into Discord, a folder, a browser… or copy it."))
        hint.setObjectName("settingHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.tile_clip = ShareTile(tr("Original clip"), "video")
        self.tile_edit = ShareTile(tr("Exported version"), "export")
        self.tile_audio = ShareTile(tr("Clip sound"), "music")
        self.tile_aedit = ShareTile(tr("Exported sound"), "music")
        for heading, glyph, tiles in ((tr("Video"), "video", (self.tile_clip, self.tile_edit)),
                                      (tr("Audio"), "music", (self.tile_audio, self.tile_aedit))):
            lay.addSpacing(4)
            lay.addWidget(_subheading(heading, glyph))
            for t in tiles:
                t.copied.connect(lambda name: self.message.emit(tr('"{name}" copied: paste it with Ctrl+V', name=name)))
                t.revealRequested.connect(self.revealPath)
                lay.addWidget(t)
        return frame

    # ------------------------------------------------------------- loading
    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings

    def show_clip(self, clip: Clip, path: Path, codecs: dict[str, tuple[bool, str]]) -> None:
        """Shows a clip (called by the controller). codecs: {codec: (can be encoded, encoder)}."""
        if self.clip is not None:
            self._save_edit()
        self._resume = None
        self.clip, self.path, self._codecs = clip, Path(path), codecs
        self._refresh_header()
        self.track_info = clip.separate_tracks()
        edit = clip.edit or {}
        kinds = [t.get("kind", "other") for _s, t in self.track_info]
        lanes = []
        app_i = 0
        for k, ((_stream, t), m) in enumerate(zip(self.track_info, E.track_mix(edit, kinds, self.settings.levels))):
            kind = kinds[k]
            if kind == "app":
                color = APP_COLORS[app_i % len(APP_COLORS)]
                app_i += 1
            else:
                color = KIND_COLORS.get(kind, KIND_COLORS["other"])
            lanes.append({"label": t.get("label", tr("Track {n}", n=k + 1)), "color": color, "solo": False, **m})
        screens = [s.get("label") or tr("Screen {n}", n=i + 1) for i, s in enumerate(clip.screens)]
        self.screen_order = self.settings.display_order(clip.screens) if len(screens) > 1 else [0]
        self.timeline.set_clip(clip.duration or 30, screens, lanes, self.screen_order)
        if not self._timeline_sized:
            QTimer.singleShot(0, self._fit_timeline)
        self.timeline.peak_source = self.preview.peaks
        self._build_screen_pick(screens)
        self.cuts = [tuple(c) for c in edit.get("cuts", [])]
        self.angles = [list(a) for a in (clip.angles or [[0.0, 0]]) if a[1] < max(1, len(screens))] or [[0.0, 0]]
        t_in, t_out = edit.get("range", [0.0, clip.duration or 30])
        self.timeline.set_edit(t_in, min(t_out, clip.duration or t_out), self.cuts, self.angles)
        self.norm_cb.blockSignals(True)
        self.norm_cb.setChecked(self.settings.normalize_audio)
        self.norm_cb.blockSignals(False)
        self._fill_screen_combo(screens)
        self._select_preset(edit.get("preset", "discord"))
        self.history.reset(self._snapshot())
        self._update_undo()
        self.preview_screen = None
        self._active_video = 0
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        self.audio_out.setMuted(False)
        gains = [lane["gain"] for lane in lanes]
        self.preview.load(str(self.path), [s for s, _t in self.track_info], gains)
        self._apply_mix()
        self.player.setPosition(int(t_in * 1000))
        self.player.play()
        self._tick.start()
        self.timeline.set_frames([])
        self._update_hint()
        self.setFocus()

    def _fit_timeline(self) -> None:
        """First opening (splitter never moved): the timeline takes its height, without going over 55 %
        of the page; afterwards the chosen position wins."""
        total = sum(self.view_split.sizes())
        if total <= 0 or self._timeline_sized:
            return
        want = self.timeline.minimumHeight() + self.hint.sizeHint().height() + 16
        bottom = min(want, int(total * 0.55))
        self.view_split.setSizes([total - bottom, bottom])
        self._timeline_sized = True

    def _build_screen_pick(self, screens: list[str]) -> None:
        if self.screen_pick:
            self.screen_pick.setParent(None)
            self.screen_pick.deleteLater()
            self.screen_pick = None
        if len(screens) < 2:
            return
        items = [("m", tr("Multicam"))] + [(str(k), self._screen_name(k)) for k in self.screen_order]
        self.screen_pick = Segmented(items)
        self.screen_pick.setToolTip(tr("Preview: the multicam edit (timeline switches) or always the same screen.\n"
                                       "Keys 1, 2…: switch to that screen at the playhead."))
        self.screen_pick.set_current("m")
        self.screen_pick.changed.connect(self._on_screen_pick)
        self.screen_pick_box.insertWidget(0, self.screen_pick)

    def _screen_name(self, k: int) -> str:
        return E.screen_name(self.clip.screens if self.clip else [], k)

    def _fill_screen_combo(self, screens: list[str]) -> None:
        self.screen_combo.blockSignals(True)
        self.screen_combo.clear()
        if len(screens) > 1:
            self.screen_combo.addItem(tr("Multicam (timeline switches)"), None)
            for k in self.screen_order:
                self.screen_combo.addItem(tr("{screen} alone — {label}", screen=self._screen_name(k), label=screens[k]), k)
        self.screen_combo.blockSignals(False)
        self.screen_combo.setVisible(len(screens) > 1)
        self.screen_lbl.setVisible(len(screens) > 1)
        # Codecs this machine can encode (AV1 needs a recent card).
        self.codec_combo.blockSignals(True)
        for i in range(self.codec_combo.count()):
            codec = self.codec_combo.itemData(i)
            self.codec_combo.model().item(i).setEnabled(self._codecs.get(codec, (True, ""))[0])
        self.codec_combo.blockSignals(False)

    def close_clip(self) -> None:
        if self.video.isFullScreen():
            self.video.setFullScreen(False)
        self._save_edit()
        self._tick.stop()
        self.preview.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        self._resume = None
        self.clip = None
        self.path = None

    def _refresh_header(self) -> None:
        c = self.clip
        if not c:
            return
        if not self.title.hasFocus() or self.title.isReadOnly():
            self.title.set_title(c.title)
        self.game_lbl.setText(c.group or tr("Unknown"))
        self.game_lbl.setStyleSheet("" if c.group else f"color:{theme.TEXT_FAINT}; font-style:italic;")
        parts = [tr("{date:%Y-%m-%d at %H:%M:%S}", date=c.created_dt), fmt_time_precise(c.duration), fmt_size(c.size)]
        if c.height:
            parts.append(f"{c.height}p{c.fps}")
        if c.n_screens > 1:
            parts.append(tr("{n} screens", n=c.n_screens))
        self.meta.setText("  ·  ".join(parts))
        self.fav_btn.setIcon(glyph_icon("star_filled" if c.favorite else "star",
                                        theme.FAVORITE if c.favorite else theme.TEXT_FAINT))
        self.fav_btn.setToolTip(tr("Remove from favorites (F)") if c.favorite else tr("Add to favorites (F)"))

    def set_frames(self, path: str, frames: list[list[bytes]]) -> None:
        """Timeline frames (JPEG, per screen), provided by the controller."""
        if not self.clip or str(self.path) != path:
            return
        per_screen = []
        for screen in frames:
            pixmaps = []
            for data in screen:
                pm = QPixmap()
                pm.loadFromData(data)
                pixmaps.append(pm)
            per_screen.append(pixmaps)
        self.timeline.set_frames(per_screen)

    def _on_preview_ready(self, ok: bool) -> None:
        if ok:
            # The mix of the separate tracks replaces the player's sound (which plays one track only).
            self.audio_out.setMuted(True)
            self._apply_mix()
            self.timeline.refresh_peaks()
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.preview.play(self.player.position() / 1000)

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia and self.clip and not self._resume and not self._scrub_resume:
            # The file can end before the excerpt end seen by the tick (audio a few ms longer than the
            # video, 25 ms tick): the player stops by itself, so the loop is handled here too.
            if self.loop:
                self.seek(self.timeline.t_in)
                self.player.play()
                if self.preview.loaded:
                    self.preview.play(self.timeline.t_in)
            else:
                self.preview.pause()
            return
        if status == QMediaPlayer.LoadedMedia and self.clip and self.clip.n_screens > 1:
            self._active_video = -1   # forced at the next tick
        if status == QMediaPlayer.LoadedMedia and self._resume:
            pos, playing = self._resume
            self._resume = None
            self.player.setPosition(pos)
            if playing:
                self.player.play()
                if self.preview.loaded:
                    self.preview.play(pos / 1000)
            else:
                self.player.pause()   # "paused" (not "stopped"): the frame at this position shows

    # ------------------------------------------------------------- playback
    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.preview.pause()
        else:
            pos = self.player.position() / 1000
            if pos >= self.timeline.t_out - 0.05 or pos < self.timeline.t_in:
                self.seek(self.timeline.t_in)
            self.player.play()
            if self.preview.loaded:
                self.preview.play(self.player.position() / 1000)

    def seek(self, t: float) -> None:
        self.player.setPosition(int(max(0.0, t) * 1000))
        self.timeline.set_position(t)
        self.preview.seek(t)
        self._update_time(t)

    def step(self, seconds: float) -> None:
        self.player.pause()
        self.preview.pause()
        self.seek(min(max(0.0, self.player.position() / 1000 + seconds), self.timeline.duration))

    def toggle_loop(self) -> None:
        self.loop = not self.loop
        self.loop_btn.setChecked(self.loop)

    def toggle_mute(self) -> None:
        self._listen_muted = not getattr(self, "_listen_muted", False)
        self._on_listen(self.listen.value())

    def _on_listen(self, v: int) -> None:
        muted = getattr(self, "_listen_muted", False) or v == 0
        self.preview.monitor = 0.0 if muted else v / 100
        self.audio_out.setVolume(0.0 if muted else v / 100)
        self.mute_btn.setIcon(glyph_icon("mute" if muted else "volume"))

    def toggle_fullscreen(self) -> None:
        self.video.setFullScreen(not self.video.isFullScreen())
        if self.video.isFullScreen():
            self.video.activateWindow()
            self.video.setFocus()
        else:
            self.window().activateWindow()
            self.setFocus()

    def _video_key(self, e) -> None:
        key = e.key()
        if key in (Qt.Key_Escape, Qt.Key_F11) and self.video.isFullScreen():
            self.toggle_fullscreen()
        elif key == Qt.Key_F11:
            self.toggle_fullscreen()
        elif key == Qt.Key_Space:
            self.toggle_play()
        elif key in (Qt.Key_Left, Qt.Key_Right):
            self.step(-1.0 if key == Qt.Key_Left else 1.0)
        else:
            e.ignore()

    def _escape(self) -> None:
        if self.video.isFullScreen():
            self.toggle_fullscreen()
        elif self.timeline.pending_cut is not None:
            self.timeline.pending_cut = None
            self.timeline.update()
            self._update_hint()
        else:
            self._go_back()

    def _on_tick(self) -> None:
        if not self.clip or self._resume:     # file being reloaded (rename)
            return
        pos = self.player.position() / 1000
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        tl = self.timeline
        if playing and not self._scrub_resume:
            if pos < tl.t_in - 0.05:
                self.seek(tl.t_in)
                return
            for a, b in E.merge_ranges(self.cuts):
                if a <= pos < b - 0.02:   # removed passage: jump over it
                    if b >= tl.t_out - 0.02:
                        break
                    self.seek(b)
                    return
            if pos >= tl.t_out - 0.02 or any(a <= pos and b >= tl.t_out - 0.02 for a, b in self.cuts):
                if self.loop:
                    self.seek(tl.t_in)
                else:
                    self.player.pause()
                    self.preview.pause()
                    self.seek(tl.t_out)
                return
        if self.clip.n_screens > 1:
            k = self.preview_screen if self.preview_screen is not None else E.angle_at(self.angles, pos)
            if k != self._active_video and k < len(self.player.videoTracks()):
                self._active_video = k
                self.player.setActiveVideoTrack(k)
                # Seek again to the current position: paused, the player keeps the old screen's frame;
                # playing, a track switch right after a seek (loop, jump over a removed passage) leaves
                # the video frozen for seconds (Qt FFmpeg backend) while the clock goes on.
                self.player.setPosition(self.player.position())
        tl.set_position(pos)
        self._update_time(pos)

    def _update_time(self, pos: float) -> None:
        kept = sum(b - a for a, b in self.kept_segments())
        self.time_lbl.setText(f"{fmt_clock(pos)} / {fmt_clock(self.timeline.duration)}")
        self.time_lbl.setToolTip(tr("Kept excerpt: {length}", length=fmt_clock(kept)))

    def _on_state(self, state) -> None:
        self.play_btn.setIcon(glyph_icon("pause" if state == QMediaPlayer.PlayingState else "play", "white"))

    def _on_error(self, _err, message: str) -> None:
        if self.clip:
            self.meta.setText(tr("Cannot play: {error}", error=message))

    def _on_timeline_seek(self, t: float) -> None:
        self.player.setPosition(int(t * 1000))
        self._update_time(t)
        if not self._scrub_resume:
            self.preview.seek(t)

    def _on_scrub(self, pressed: bool) -> None:
        """While the playhead is dragged: pause, then resume on release."""
        if pressed:
            self._scrub_resume = True
            self._was_playing = self.player.playbackState() == QMediaPlayer.PlayingState
            self.player.pause()
            self.preview.pause()
        else:
            self._scrub_resume = False
            if self._was_playing:
                self.player.play()
                if self.preview.loaded:
                    self.preview.play(self.player.position() / 1000)

    def _on_screen_pick(self, key: str) -> None:
        self.preview_screen = None if key == "m" else int(key)
        self._on_tick()

    # ------------------------------------------------------------- editing
    def kept_segments(self) -> list[tuple[float, float]]:
        return E.kept_segments(self.timeline.t_in, self.timeline.t_out, self.cuts)

    def mark_in(self) -> None:
        t = self.player.position() / 1000
        if t < self.timeline.t_out - 0.5:
            self.timeline.t_in = t
            self._on_range(t, self.timeline.t_out)
            self.timeline.update()
            self._commit()

    def mark_out(self) -> None:
        t = self.player.position() / 1000
        if t > self.timeline.t_in + 0.5:
            self.timeline.t_out = t
            self._on_range(self.timeline.t_in, t)
            self.timeline.update()
            self._commit()

    def cut_mark(self) -> None:
        """1st press: start of the passage to remove; 2nd press: end (and the passage is removed)."""
        t = self.player.position() / 1000
        tl = self.timeline
        if tl.pending_cut is None:
            tl.pending_cut = t
        else:
            a, b = sorted((tl.pending_cut, t))
            tl.pending_cut = None
            if b - a >= 0.1:
                self.cuts = E.merge_ranges(self.cuts + [(a, b)])
                tl.cuts = [list(c) for c in self.cuts]
                self._after_edit()
                self._commit()
        tl.update()
        self._update_hint()

    def switch_here(self, pos: int) -> None:
        """Keys 1, 2...: the screen at that place in the display order."""
        if self.clip and pos < self.clip.n_screens:
            k = self.screen_order[pos] if pos < len(self.screen_order) else pos
            self.timeline.set_angle_at(self.player.position() / 1000, k)
            self._commit()

    def _update_hint(self) -> None:
        if self.timeline.pending_cut is not None:
            self.hint.setText(tr("Passage to remove from {time}: press C at the end of the passage (Esc to cancel).",
                                 time=fmt_clock(self.timeline.pending_cut)))
            return
        parts = [tr("Drag the orange handles or I / O for the excerpt"), tr("C … C or Shift + drag: remove a passage")]
        if self.clip and self.clip.n_screens > 1:
            parts.append(tr("click a screen row (or 1, 2…): switch screens"))
        if self.timeline.lanes:
            parts.append(tr("tracks: drag the line for the volume, click it for a point"))
        parts.append(tr("Ctrl + wheel: zoom, Shift + wheel: move in time, Alt + wheel: track height"))
        self.hint.setText("  ·  ".join(parts))

    def _on_range(self, t_in: float, t_out: float) -> None:
        self._after_edit()
        self._save_timer.start()

    def _on_cuts(self, cuts: list) -> None:
        self.cuts = [tuple(c) for c in cuts]
        self._after_edit()
        self._save_timer.start()

    def _on_angles(self, angles: list) -> None:
        self.angles = [list(a) for a in angles]
        self._active_video = -1
        self._update_summary()
        self._save_timer.start()

    def _on_mix(self) -> None:
        """Volume, curve, mute or solo changed on the timeline."""
        self._apply_mix()
        self._save_timer.start()
        self._update_export_state()

    def _apply_mix(self) -> None:
        for lane, m in zip(self.timeline.lanes, self.preview.mix):
            m.gain, m.muted, m.solo = lane["gain"], lane["muted"], lane["solo"]
            m.points = list(lane["points"])

    def _on_normalize(self, on: bool) -> None:
        self.normalizeChanged.emit(on)
        self._update_summary()

    def _mix_state(self) -> list[dict]:
        return [{"gain": lane["gain"], "muted": lane["muted"], "points": [list(p) for p in lane["points"]]}
                for lane in self.timeline.lanes]

    def _after_edit(self) -> None:
        if len(self.kept_segments()) > 1 and self.presets.current() == "copy":
            self.message.emit(tr('"Original" cannot remove passages: Discord preset chosen'))
            self._select_preset("discord")
        elif self.presets.current() == "discord":
            self._select_preset("discord")   # the bitrate depends on the final length
        else:
            self._update_summary()
        self._update_time(self.player.position() / 1000)

    # ------------------------------------------------------------- undo
    def _snapshot(self) -> str:
        return E.snapshot(self.timeline.t_in, self.timeline.t_out, self.cuts, self.angles, self._mix_state())

    def _commit(self) -> None:
        if self._restoring or not self.clip:
            return
        if self.history.commit(self._snapshot()):
            self._update_undo()
        self._save_timer.start()

    def _update_undo(self) -> None:
        self.undo_btn.setEnabled(self.history.can_undo)
        self.redo_btn.setEnabled(self.history.can_redo)

    def undo(self) -> None:
        snap = self.history.undo(self._snapshot())
        if snap is not None:
            self._restore(snap)

    def redo(self) -> None:
        snap = self.history.redo(self._snapshot())
        if snap is not None:
            self._restore(snap)

    def _restore(self, snap: str) -> None:
        import json

        self._restoring = True
        data = json.loads(snap)
        self.cuts = [tuple(c) for c in data["cuts"]]
        self.angles = data["angles"]
        self.timeline.set_edit(data["range"][0], data["range"][1], self.cuts, self.angles)
        for lane, m in zip(self.timeline.lanes, data["mix"]):
            lane["gain"], lane["muted"] = m["gain"], m["muted"]
            lane["points"] = [tuple(p) for p in m["points"]]
        self.timeline.update()
        self._restoring = False
        self._active_video = -1
        self._apply_mix()
        self._after_edit()
        self._update_undo()
        self._save_timer.start()

    def _save_edit(self) -> None:
        """Asks for the edit to be saved if it changed (the controller writes it)."""
        clip = self.clip
        if not clip:
            return
        edit = {"range": [self.timeline.t_in, self.timeline.t_out], "cuts": [list(c) for c in self.cuts],
                "mix": self._mix_state(),
                "preset": self.presets.current() or "custom"}
        angles = self.angles if self.angles != [[0.0, 0]] else []
        if edit != clip.edit or angles != clip.angles:
            self.saveEditRequested.emit(edit, angles)

    # ------------------------------------------------------------- export
    def _set_combo(self, combo: QComboBox, value) -> None:
        i = combo.findData(value)
        combo.setCurrentIndex(i if i >= 0 else 0)

    def _select_preset(self, key: str) -> None:
        if not self.clip:
            return
        self._updating = True
        c = self.clip
        dur = sum(b - a for a, b in self.kept_segments())
        if key == "discord":
            limit = self.settings.discord_limit_mb
            h, fps = E.discord_settings(dur, limit, c.height or 1080, c.fps or 60)
            self._set_combo(self.res_combo, h)
            self._set_combo(self.fps_combo, fps)
            self._set_combo(self.codec_combo, "h264")
            self.size_spin.setValue(limit)
        elif key == "hq":
            self._set_combo(self.res_combo, 0)
            self._set_combo(self.fps_combo, 0)
            self._set_combo(self.codec_combo, "h264")
            self.size_spin.setValue(0)
            self._set_combo(self.quality_combo, 20)
        elif key == "light":
            self._set_combo(self.res_combo, 720 if 720 < (c.height or 1080) else 0)
            self._set_combo(self.fps_combo, 30 if 30 < (c.fps or 60) else 0)
            self._set_combo(self.codec_combo, "h264")
            self.size_spin.setValue(0)
            self._set_combo(self.quality_combo, 27)
        elif key == "copy" and len(self.kept_segments()) > 1:
            key = "hq"
            self._updating = False
            return self._select_preset("hq")
        self.presets.set_current(key if key in dict((k, 1) for k, *_ in E.PRESETS) else None)
        self._updating = False
        self._update_sensitivity()
        self._update_summary()
        self._save_timer.start()

    def _on_custom(self, *_):
        if self._updating:
            return
        self.presets.set_current(None)
        self._update_sensitivity()
        self._update_summary()

    def _update_sensitivity(self) -> None:
        copy_ok = len(self.kept_segments()) <= 1
        self.presets.buttons["copy"].setEnabled(copy_ok)
        copy = self.presets.current() == "copy"
        for w in (self.res_combo, self.fps_combo, self.codec_combo, self.size_spin, self.screen_combo):
            w.setEnabled(not copy)
        self.quality_combo.setEnabled(not copy and self.size_spin.value() == 0)
        self.norm_cb.setEnabled(not copy)

    def _export_screen(self):
        """None = multicam edit, k = screen k alone."""
        if not self.clip or self.clip.n_screens < 2:
            return 0
        choice = self.screen_combo.currentData()
        if choice is None and len(self.angles) == 1:
            return self.angles[0][1]
        return choice

    def _settings_now(self) -> dict:
        return {"copy": self.presets.current() == "copy", "height": self.res_combo.currentData(),
                "fps": self.fps_combo.currentData(), "codec": self.codec_combo.currentData(),
                "target": self.size_spin.value(), "qp": self.quality_combo.currentData(),
                "screen": self._export_screen()}

    def _update_summary(self) -> None:
        if not self.clip:
            return
        s = self._settings_now()
        c = self.clip
        dur = sum(b - a for a, b in self.kept_segments())
        key = self.presets.current()
        intro = dict((k, d) for k, _l, d in E.PRESETS).get(key, tr("Custom settings"))
        if key == "discord":
            intro = tr("To send it on Discord (under {size} MB)", size=self.settings.discord_limit_mb)
        if c.n_screens > 1 and not s["copy"]:
            sc = s["screen"]
            switches = len(self.angles) - 1
            if sc is not None:
                intro += " · " + tr("screen {n} alone", n=sc + 1)
            else:
                intro += " · " + (tr("multicam, {n} switch", n=switches) if switches == 1 else
                                  tr("multicam, {n} switches", n=switches))
        if s["copy"]:
            est = c.size * dur / max(c.duration, 0.1)
            text = (f"<b>{intro}</b><br>{c.height}p{c.fps} · ≈ {fmt_size(int(est))} · {fmt_clock(dur)}<br>"
                    "<small>" + tr("Mixed tracks + separate tracks kept · cut aligned on keyframes") + "</small>")
        else:
            h = s["height"] or c.height
            fps = s["fps"] or c.fps
            size = tr("≤ {size} MB", size=s["target"]) if s["target"] else tr("size depends on the content")
            enc = self._codecs.get(s["codec"], (True, ""))[1] or tr("detecting…")
            norm = " · " + tr("normalized volume") if self.norm_cb.isChecked() and self.track_info else ""
            text = (f"<b>{intro}</b><br>{h}p{fps} · {s['codec'].upper()} · {size} · {fmt_clock(dur)}<br>"
                    "<small>" + tr("Encoding: {encoder}", encoder=enc) + f"{norm}</small>")
        self.summary.setText(text)
        self._update_export_state()

    def _edit_sig(self) -> str:
        """Signature of the edit: compared with the last export's."""
        return E.signature(self.timeline.t_in, self.timeline.t_out, self.cuts, self._mix_state(),
                           self.angles if self.clip and self.clip.n_screens > 1 else None)

    def _update_export_state(self) -> None:
        if not self.clip or self.exporting:
            return
        latest = self._latest_export
        if not latest:
            text, color = tr("Not exported yet"), theme.TEXT_FAINT
        elif not self.clip.export_sig:
            text, color = tr("Last export: {name}", name=latest.name), theme.TEXT_FAINT
        elif self.clip.export_sig == self._edit_sig():
            text, color = "✓ " + tr("The last export is up to date"), theme.SUCCESS
        else:
            text, color = "⚠ " + tr("Edit changed since the last export"), "#e66100"
        self.export_state.setText(text)
        self.export_state.setStyleSheet(f"color:{color}; font-size:12px;")
        stale = bool(latest and self.clip.export_sig and self.clip.export_sig != self._edit_sig())
        for tile in (self.tile_edit, self.tile_aedit):
            if tile.path:
                suffix = " · " + tr("not up to date")
                tile.set_file(tile.path, tile.subtitle.text().replace(suffix, "") + (suffix if stale else ""),
                              tile._pixmap, stale=stale)

    def export(self) -> None:
        """Export = the video (with the sound), then the sound alone as MP3 (same edit, same volume).
        During an export, the button cancels it."""
        if self.exporting:
            self.exportCancelRequested.emit()
            return
        if not self.clip:
            return
        s = self._settings_now()
        self.exportRequested.emit({
            "settings": s,
            "segments": self.kept_segments(),
            "angles": self.angles,
            "tracks": [{"stream": stream, "gain": m["gain"], "muted": m["muted"], "points": m["points"]}
                       for (stream, _t), m in zip(self.track_info, self._mix_state())],
            "normalize": self.norm_cb.isChecked() and not s["copy"],
            "signature": self._edit_sig(),
        })

    def export_started(self) -> None:
        self.exporting = True
        self.export_btn.setText("  " + tr("Cancel · preparing"))
        self.export_btn.setIcon(glyph_icon("stop", "white"))
        self.progress.setValue(0)
        self.progress.show()

    def export_progress(self, f: float, step: str) -> None:
        self.progress.setMaximum(1000)
        self.progress.setValue(int(f * 1000))
        self.export_btn.setText("  " + tr("Cancel · {step} {percent} %", step=step, percent=int(f * 100)))

    def export_finished(self, message: str) -> None:
        self.exporting = False
        self.progress.hide()
        self.export_btn.setText("  " + tr("Export"))
        self.export_btn.setIcon(glyph_icon("export", "white"))
        if message:
            self.message.emit(message)
        self._update_summary()

    # ------------------------------------------------------------- sharing
    def show_share(self, info: dict) -> None:
        """Share tiles (provided by the controller): thumb, clip, latest, latest_mp3, audio_resolve
        (sound extracted on demand)."""
        c = self.clip
        if not c:
            return
        thumb = QPixmap(str(info["thumb"]))
        n = len(c.tracks)
        sub = f"{fmt_size(c.size)} · {c.height}p{c.fps}" + (" · " + tr("{n} mixed tracks", n=n) if n > 1 else "")
        self.tile_clip.set_file(info["clip"], sub, thumb)
        latest = self._latest_export = info.get("latest")
        limit = self.settings.discord_limit_mb * 1e6
        if latest:
            size = latest.stat().st_size
            self.tile_edit.set_file(latest, fmt_size(size) + (" · " + tr("ready for Discord") if size <= limit else ""),
                                    thumb)
        else:
            self.tile_edit.set_file(None, tr("Not exported yet"))
        if c.tracks:
            self.tile_audio.set_file(None, "MP3 · " + (tr("{n} mixed tracks", n=n) if n > 1 else tr("1 track")),
                                     resolve=info["audio_resolve"])
        else:
            self.tile_audio.set_file(None, tr("This clip has no sound"))
        mp3 = info.get("latest_mp3")
        if mp3:
            self.tile_aedit.set_file(mp3, f"MP3 · {fmt_size(mp3.stat().st_size)}")
        else:
            self.tile_aedit.set_file(None, tr("Exported with the video"))
        self._update_export_state()

    # ------------------------------------------------------------- actions
    def _go_back(self) -> None:
        self.close_clip()
        self.backRequested.emit()

    def toggle_favorite(self) -> None:
        if self.clip:
            self.favoriteToggled.emit()

    def refresh_clip(self) -> None:
        """The clip changed (favorite, game, name): header updated."""
        self._refresh_header()

    def pick_game(self) -> None:
        if self.clip:
            self.gamePickRequested.emit()

    def ask_game(self, known: dict[str, str], current: str) -> tuple[str, str] | None:
        """Game picker dialog; (name, appid) or None if cancelled."""
        dlg = GamePickerDialog(known, current, 1, self)
        if dlg.exec() and dlg.result_value is not None:
            return dlg.result_value
        return None

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def rename(self) -> None:
        if self.clip:
            self.title.start_edit()

    def _rename_to(self, text: str) -> None:
        if self.clip and text != self.clip.title:
            self.renameRequested.emit(text)

    def release_media(self) -> tuple[int, bool]:
        """Releases the file (Windows refuses to rename an open file); (position, playing)."""
        pos = self.player.position()
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self._save_edit()
        self.player.stop()
        self.preview.stop()
        self.player.setSource(QUrl())
        return pos, playing

    def reload_media(self, path: Path, pos: int, playing: bool) -> None:
        """Reloads the (renamed) file at the same position; see `_on_media_status`."""
        self.path = Path(path)
        self._resume = (pos, playing)
        self._active_video = -1
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        self._refresh_header()

    def delete(self) -> None:
        if not self.clip:
            return
        clip = self.clip
        self.close_clip()  # releases the file (otherwise Windows refuses to move it)
        QTimer.singleShot(150, lambda: self.trashRequested.emit(clip))
        self.backRequested.emit()
