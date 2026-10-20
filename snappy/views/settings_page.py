"""Settings view.

It shows a copy of the settings and sends it back changed (`settingsApplied`); the system lists
(capture methods, microphones, sounds) are provided by `controllers.settings`, which also answers
its requests (play a sound, find the apps playing sound, encoder summary)."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..i18n import tr
from ..models.settings import (CHAT_APPS, CODECS, GAME_MODES, MAX_CLIP_SECONDS, MIN_CLIP_SECONDS, QUALITY_LABELS,
                      QUALITY_PRESETS, Settings, app_label, audio_tracks, hotkey_problem)
from ..platforms import system
from . import theme
from .widgets import glyph_icon


def _card(title: str, subtitle: str = "") -> tuple[QFrame, QGridLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(22, 18, 22, 20)
    outer.setSpacing(12)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    outer.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("settingHint")
        s.setWordWrap(True)
        outer.addWidget(s)
    grid = QGridLayout()
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(12)
    grid.setColumnStretch(1, 1)
    outer.addLayout(grid)
    return frame, grid


class _RowLabel(QLabel):
    """Label of a settings row: a bar in the accent color, on the left, marks a setting changed but
    not applied yet."""
    BAR = 3

    def __init__(self, text: str):
        super().__init__(text)
        self.setObjectName("rowLabel")
        self.setContentsMargins(self.BAR + 9, 0, 0, 0)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.property("modified"):
            p = QPainter(self)
            p.fillRect(0, 0, self.BAR, self.height(), QColor(theme.ACCENT))
            p.end()


def _row(grid: QGridLayout, label: str, widget: QWidget, hint: str = "") -> None:
    r = grid.rowCount()
    lbl = _RowLabel(label)
    lbl.setMinimumWidth(190)
    lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    grid.addWidget(lbl, r, 0)          # full row height: the bar runs along the whole row
    widget._row_label = lbl
    box = QVBoxLayout()
    box.setSpacing(4)
    box.addWidget(widget)
    if hint:
        h = QLabel(hint)
        h.setObjectName("settingHint")
        h.setWordWrap(True)
        box.addWidget(h)
    grid.addLayout(box, r, 1)


def _combo(items: list[tuple[str, object]], current, editable: bool = False) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for label, data in items:
        c.addItem(label, data)
    idx = c.findData(current)
    if idx < 0 and editable and current:
        c.addItem(str(current), current)
        idx = c.count() - 1
    c.setCurrentIndex(max(0, idx))
    return c


def _hbox(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if x == "stretch":
            lay.addStretch()
        else:
            lay.addWidget(x)
    return w


class _Swatch(QPushButton):
    """Color swatch (choice of the accent color)."""

    def __init__(self, key: str, color: str, tooltip: str):
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setStyleSheet(
            f"QPushButton {{ background: {color}; border: 2px solid transparent; border-radius: 16px; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {theme.TEXT_FAINT}; }}"
            f"QPushButton:checked {{ border: 3px solid white; }}")


class _ScreenRow(QFrame):
    """A row of the screen list: handle, switch, name, "primary" star."""

    def __init__(self, monitor: int, title: str, detail: str, owner: "ScreenList"):
        super().__init__()
        self.monitor, self.owner = monitor, owner
        self.setObjectName("screenRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 10, 6)
        lay.setSpacing(10)
        self.handle = QLabel()
        self.handle.setPixmap(glyph_icon("drag", theme.TEXT_FAINT).pixmap(20, 20))
        self.handle.setFixedWidth(24)
        self.handle.setAlignment(Qt.AlignCenter)
        self.handle.setCursor(Qt.OpenHandCursor)
        self.handle.setToolTip(tr("Drag to change the order"))
        self.handle.installEventFilter(self)
        self.toggle = QCheckBox()
        self.toggle.setToolTip(tr("Record this screen"))
        self.toggle.toggled.connect(owner.changed)
        name = QLabel(title)
        name.setStyleSheet("font-weight: 600;")
        info = QLabel(detail)
        info.setObjectName("settingHint")
        self.star = QPushButton()
        self.star.setObjectName("ghost")
        self.star.setFixedSize(34, 34)
        self.star.setIconSize(QSize(20, 20))
        self.star.setCursor(Qt.PointingHandCursor)
        self.star.clicked.connect(lambda: owner.set_primary(self.monitor))
        for w in (self.handle, self.toggle, name):
            lay.addWidget(w)
        lay.addWidget(info, 1)
        lay.addWidget(self.star)
        self._drag_y = None

    def set_primary(self, on: bool) -> None:
        self.star.setIcon(glyph_icon("star_filled" if on else "star", theme.FAVORITE if on else theme.TEXT_FAINT))
        self.star.setToolTip(tr("Primary screen: first stream of the clip, thumbnail and default export")
                             if on else tr("Choose as the primary screen"))
        if on:
            self.toggle.setChecked(True)    # the primary screen is always recorded
        self.toggle.setEnabled(not on)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            self._drag_y = e.globalPosition().y()
            self.handle.setCursor(Qt.ClosedHandCursor)
            self.setProperty("dragging", True)
            self.style().polish(self)
            return True
        if t == QEvent.MouseMove and self._drag_y is not None:
            self.owner.drag_to(self, e.globalPosition().toPoint())
            return True
        if t == QEvent.MouseButtonRelease and self._drag_y is not None:
            self._drag_y = None
            self.handle.setCursor(Qt.OpenHandCursor)
            self.setProperty("dragging", False)
            self.style().polish(self)
            return True
        return super().eventFilter(obj, e)


class ScreenList(QWidget):
    """Recorded screens, in display order (editor timeline and picker): reordered by dragging the
    handle; the switch says whether the screen is recorded and the (exclusive) star marks the
    primary screen."""
    changed = Signal()

    def __init__(self, screens: list[tuple[int, str, str]], order: list[int], primary: int, extras: list[int]):
        super().__init__()
        self.setStyleSheet(
            f"QFrame#screenRow {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
            f"QFrame#screenRow[dragging=\"true\"] {{ border-color: {theme.ACCENT}; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        known = {m for m, _t, _d in screens}
        ranked = [m for m in order if m in known] + [m for m, _t, _d in screens if m not in order]
        by_id = {m: (t, d) for m, t, d in screens}
        self.rows: list[_ScreenRow] = []
        for m in ranked:
            row = _ScreenRow(m, *by_id[m], self)
            row.toggle.setChecked(m == primary or m in extras)
            self.rows.append(row)
            self.lay.addWidget(row)
        self.primary = primary if primary in known else (ranked[0] if ranked else 0)
        self.set_primary(self.primary, notify=False)

    def set_primary(self, monitor: int, notify: bool = True) -> None:
        self.primary = monitor
        for row in self.rows:
            row.set_primary(row.monitor == monitor)
        if notify:
            self.changed.emit()

    def drag_to(self, row: _ScreenRow, global_pos) -> None:
        """Moves the held row to the place under the cursor (live reordering)."""
        y = self.mapFromGlobal(global_pos).y()
        others = [r for r in self.rows if r is not row]
        target = sum(1 for r in others if r.geometry().center().y() < y)
        if target != self.rows.index(row):
            self.rows = others[:target] + [row] + others[target:]
            self.lay.removeWidget(row)
            self.lay.insertWidget(target, row)
            self.changed.emit()

    def order(self) -> list[int]:
        return [r.monitor for r in self.rows]

    def filmed(self) -> list[int]:
        """Recorded screens, primary screen included, in list order."""
        return [r.monitor for r in self.rows if r.toggle.isChecked() or r.monitor == self.primary]


class SettingsPage(QWidget):
    settingsApplied = Signal(object)
    accentChanged = Signal(str)       # applied right away, without "Apply"
    cancelRequested = Signal()        # back to the saved settings (the page is rebuilt)
    dirtyChanged = Signal(bool)
    soundPreviewRequested = Signal(str, int)
    detectAppsRequested = Signal()
    encoderSummaryRequested = Signal(str)

    def __init__(self, settings: Settings, options: dict, parent=None):
        """options: capture_methods [(key, label)], microphones [name or (label, device)],
        sounds [(key, label)], segment_seconds (precision of the clip length)."""
        super().__init__(parent)
        self.settings = settings.copy()
        self._detected: list[tuple[str, str]] = []
        self.options = options

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("root")
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(32, 26, 32, 32)
        lay.setSpacing(16)

        head = QHBoxLayout()
        tcol = QVBoxLayout()
        title = QLabel(tr("Settings"))
        title.setObjectName("pageTitle")
        sub = QLabel(tr("Capture changes restart the buffer (what is in memory is lost)."))
        sub.setObjectName("pageSubtitle")
        tcol.addWidget(title)
        tcol.addWidget(sub)
        head.addLayout(tcol, 1)
        # Apply bar: "Cancel" and the reminder only show up with changes.
        self.dirty_lbl = QLabel(tr("Changes not applied"))
        self.dirty_lbl.setStyleSheet(f"color: {theme.FAVORITE}; font-weight: 600;")
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setToolTip(tr("Go back to the saved settings"))
        self.cancel_btn.clicked.connect(self.cancelRequested)
        self.apply_btn = QPushButton(tr("Apply"))
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(self.dirty_lbl)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.apply_btn)
        head.addLayout(bar)
        head.setAlignment(bar, Qt.AlignTop)
        self._scroll = scroll
        self._baseline: dict | None = None
        self._dirty = False
        lay.addLayout(head)

        s = self.settings
        # --- Recording ----------------------------------------------------
        card, g = _card(tr("Recording"), tr("Snappy always keeps the last seconds of your screen. A clip is only "
                                            "written to disk when you press the shortcut."))
        self.duration = QSpinBox()
        self.duration.setRange(MIN_CLIP_SECONDS, MAX_CLIP_SECONDS)
        self.duration.setSuffix(" s")
        self.duration.setValue(s.clip_seconds)
        self.duration.setFixedWidth(110)
        presets = []
        for v in (15, 30, 60, 120, 300):
            b = QPushButton(f"{v} s" if v < 60 else f"{v // 60} min")
            b.setObjectName("ghost")
            b.clicked.connect(lambda _=False, v=v: self.duration.setValue(v))
            presets.append(b)
        _row(g, tr("Clip length"), _hbox(self.duration, *presets, "stretch"),
             tr("Each clip lasts at most this long (precision: {seconds} s). With less content in the buffer, "
                "the clip is shorter.", seconds=f"{options['segment_seconds']:g}"))
        self.hotkey_error = QLabel()
        self.hotkey_error.setStyleSheet("color: #ff4d5e; font-size: 12px;")
        self.hotkey_error.setWordWrap(True)
        self.hotkey_error.setTextInteractionFlags(Qt.TextSelectableByMouse)   # command to copy (Linux)
        self.hotkeys = {}
        hotkeys = [("hotkey_save", tr("Save a clip"), tr("The last seconds of the replay. E.g.: Alt+F10."))]
        if options.get("manual_recording"):
            hotkeys.append(("hotkey_record", tr("Start / stop a recording"),
                            tr("Records everything until you press it again, on top of the replay. E.g.: Alt+F9.")))
        for key, label, hint in hotkeys:
            edit = QKeySequenceEdit(QKeySequence(getattr(s, key).replace("Win+", "Meta+")))
            if hasattr(edit, "setMaximumSequenceLength"):  # Qt ≥ 6.5
                edit.setMaximumSequenceLength(1)
            self.hotkeys[key] = edit
            _row(g, label, edit, hint)
        g.addWidget(self.hotkey_error, g.rowCount(), 1)
        lay.addWidget(card)

        # --- Screens ------------------------------------------------------
        card, g = _card(tr("Screens"), tr("Record several screens at once: in the editor, switch from one screen to "
                                          "another along the clip, or export the one you want."))
        screens = []
        for i, sc in enumerate(QGuiApplication.screens()):
            geo = sc.geometry()
            ratio = sc.devicePixelRatio()
            detail = f"{sc.name() or sc.model() or ''} · {int(geo.width() * ratio)}×{int(geo.height() * ratio)}"
            if sc == QGuiApplication.primaryScreen():
                detail += f" · {system.TEXT['primary_screen']}"
            screens.append((i, tr("Screen {n}", n=i + 1), detail))
        self.screens = ScreenList(screens or [(0, tr("Screen {n}", n=1), "")], s.screen_order, s.monitor_index,
                                  s.extra_monitors)
        self.screens.changed.connect(self._update_estimate)
        _row(g, tr("Recorded screens"), self.screens,
             tr("The order of the list is the order of the editor timeline and preview picker. The star marks "
                "the primary screen: first stream of the clip, used for the thumbnail and the default export. "
                "Each extra screen is encoded by the graphics card, in the same file as the clip."))
        lay.addWidget(card)

        # --- Video --------------------------------------------------------
        card, g = _card(tr("Video"))
        self.resolution = _combo([(tr("Native"), 0), ("1440p", 1440), ("1080p", 1080), ("720p", 720)], s.output_height)
        _row(g, tr("Resolution"), self.resolution, tr("Reduces the file size; never upscales."))
        self.fps = _combo([(tr("{n} fps", n=v), v) for v in (30, 60, 120, 144, 165, 240)], s.fps)
        _row(g, tr("Frames per second"), self.fps)
        self.quality = _combo([(QUALITY_LABELS[k], k) for k in QUALITY_PRESETS], s.quality)
        _row(g, tr("Quality"), self.quality)
        self.codec = _combo([(label, k) for k, label in CODECS.items()], s.codec)
        _row(g, tr("Codec"), self.codec, tr("HEVC and AV1 give smaller files, but do not play everywhere (Discord "
                                            "plays H.264 everywhere). Automatic fallback if the card cannot."))
        self.encoder = _combo([(tr("Automatic (recommended)"), "auto")] + list(system.ENCODER_CHOICES)
                              + [(tr("Processor"), "cpu")], s.encoder)
        _row(g, tr("Encoder"), self.encoder,
             tr("Automatic picks the graphics card when possible: almost no impact on games."))
        self.method = _combo([(label, key) for key, label in options["capture_methods"]], s.capture_method)
        _row(g, tr("Capture method"), self.method, system.TEXT["capture_hint"])
        self.cursor = QCheckBox(tr("Show the mouse cursor"))
        self.cursor.setChecked(s.show_cursor)
        _row(g, "", self.cursor)
        self.estimate = QLabel()
        self.estimate.setObjectName("settingHint")
        _row(g, tr("Estimated clip size"), self.estimate)
        lay.addWidget(card)

        # --- Audio sources ------------------------------------------------
        card, g = _card(tr("Audio sources"),
                        tr("Each source has its own track, adjustable later in the editor. Nothing is rerouted: "
                           "your audio outputs and inputs stay exactly as they are."))
        refresh = QPushButton("  " + tr("Find applications that play sound"))
        refresh.setIcon(glyph_icon("search"))
        refresh.clicked.connect(self._detect_apps)
        _row(g, "", refresh)
        self.game_on = QCheckBox(tr("Record the game / PC sound"))
        self.game_on.setChecked(s.capture_system_audio)
        self.game_mode = _combo([(label, k) for k, label in GAME_MODES.items()], s.game_mode)
        self.game_app = _combo([], s.game_app, editable=True)
        self.game_app.setToolTip(system.TEXT["app_example"])
        self.game_level = self._level_slider(s.levels.get("game", 1.0))
        _row(g, tr("Game"), self.game_on)
        _row(g, tr("Capture"), self.game_mode)
        _row(g, tr("Game application"), self.game_app)
        _row(g, tr("Default level"), self.game_level[0])
        self.chat_on = QCheckBox(tr("Record the voice chat on its own track"))
        self.chat_on.setChecked(s.capture_chat)
        self.chat_app = _combo([(app_label(a), a) for a in CHAT_APPS], s.chat_app, editable=True)
        self.chat_level = self._level_slider(s.levels.get("chat", 0.8))
        _row(g, tr("Voice chat"), self.chat_on)
        _row(g, tr("Application"), self.chat_app, tr("Discord, TeamSpeak…: even started later, it is found."))
        _row(g, tr("Default level"), self.chat_level[0])
        self.mic = QCheckBox(tr("Record the microphone"))
        self.mic.setChecked(s.capture_microphone)
        # Windows: microphone names; Linux: (displayed name, PipeWire device).
        mics = [(tr("Default microphone"), "")] + [(m, m) if isinstance(m, str) else tuple(m) for m in options["microphones"]]
        self.mic_device = _combo(mics, s.microphone_name)
        self.mic_level = self._level_slider(s.levels.get("mic", 1.0))
        _row(g, tr("Microphone"), self.mic)
        _row(g, tr("Device"), self.mic_device)
        _row(g, tr("Default level"), self.mic_level[0])

        self.split_on = QCheckBox(tr("One track per application (advanced)"))
        self.split_on.setChecked(s.split_apps)
        _row(g, tr("Separate applications"), self.split_on,
             tr('Each checked application has its own track (Spotify, browser…). The rest goes into "Other sounds".'))
        self.split_box = QVBoxLayout()
        self.split_checks: dict[str, QCheckBox] = {}
        split_w = QWidget()
        split_w.setLayout(self.split_box)
        self.split_box.setContentsMargins(0, 0, 0, 0)
        self.split_add = QLineEdit()
        self.split_add.setPlaceholderText(system.TEXT["split_placeholder"])
        self.split_add.returnPressed.connect(self._add_split_app)
        _row(g, "", split_w)
        _row(g, "", self.split_add)
        self.tracks_lbl = QLabel()
        self.tracks_lbl.setObjectName("settingHint")
        self.tracks_lbl.setWordWrap(True)
        _row(g, tr("Recorded tracks"), self.tracks_lbl)
        lay.addWidget(card)
        self._fill_split()
        for w in (self.game_on, self.chat_on, self.mic, self.split_on):
            w.toggled.connect(self._update_sources)
        for w in (self.game_mode, self.game_app, self.chat_app, self.mic_device):
            w.currentIndexChanged.connect(self._update_sources)
        for w in (self.game_app, self.chat_app):
            w.editTextChanged.connect(self._update_sources)

        # --- Confirmation ---------------------------------------------------
        card, g = _card(tr("Save confirmation"))
        self.sound = QCheckBox(tr("Play a sound when a clip is saved"))
        self.sound.setChecked(s.play_sound_on_save)
        _row(g, tr("Sound"), self.sound)
        self.sound_choice = _combo([(label, k) for k, label in options["sounds"]], s.sound)
        listen = QPushButton()
        listen.setIcon(glyph_icon("play"))
        listen.setToolTip(tr("Listen"))
        listen.clicked.connect(self._preview_sound)
        _row(g, tr("Chosen sound"), _hbox(self.sound_choice, listen, "stretch"))
        self.sound_volume = QSlider(Qt.Horizontal)
        self.sound_volume.setRange(0, 100)
        self.sound_volume.setValue(s.sound_volume)
        self.sound_volume.setMaximumWidth(260)
        self.sound_volume.sliderReleased.connect(self._preview_sound)
        _row(g, tr("Volume"), self.sound_volume)
        self.toast = QCheckBox(tr("Show a notification (over the game, without taking its focus)"))
        self.toast.setChecked(s.show_toast_on_save)
        _row(g, tr("Notification"), self.toast)
        lay.addWidget(card)

        # --- Export & sharing ---------------------------------------------
        card, g = _card(tr("Export and sharing"))
        self.discord_limit = QSpinBox()
        self.discord_limit.setRange(1, 1000)
        self.discord_limit.setSuffix(" " + tr("MB"))
        self.discord_limit.setValue(s.discord_limit_mb)
        self.discord_limit.setFixedWidth(110)
        _row(g, tr("Discord upload limit"), self.discord_limit, tr('The "Discord" preset stays within it.'))
        self.normalize = QCheckBox(tr("Normalize the volume of exports (-14 LUFS, without clipping)"))
        self.normalize.setChecked(s.normalize_audio)
        _row(g, tr("Volume"), self.normalize)
        self.export_encoder = _combo([(tr("Automatic (fastest measured)"), "auto"), (tr("Graphics card"), "gpu"),
                                      (tr("Processor"), "cpu")], s.export_encoder)
        self.export_enc_lbl = QLabel()
        self.export_enc_lbl.setObjectName("settingHint")
        self.export_enc_lbl.setWordWrap(True)
        _row(g, tr("Export encoder"), self.export_encoder)
        _row(g, "", self.export_enc_lbl)
        lay.addWidget(card)
        self.export_encoder.currentIndexChanged.connect(lambda _i: self.refresh_encoder_summary())

        # --- Library ------------------------------------------------------
        card, g = _card(tr("Library"))
        self.folder = QLineEdit(s.clips_dir)
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._browse)
        _row(g, tr("Clips folder"), _hbox(self.folder, browse))
        self.quota = QDoubleSpinBox()
        self.quota.setRange(0, 10_000)
        self.quota.setDecimals(0)
        self.quota.setSuffix(" " + tr("GB"))
        self.quota.setSpecialValueText(tr("Unlimited"))
        self.quota.setValue(s.max_storage_gb)
        self.quota.setFixedWidth(130)
        _row(g, tr("Maximum space"), self.quota,
             tr("Beyond this, the oldest clips are deleted. Favorites are never deleted."))
        self.detect_game = QCheckBox(tr("Detect the running Steam game"))
        self.detect_game.setChecked(s.detect_game)
        _row(g, tr("Games"), self.detect_game, tr("Names each clip after the game and files it under that name, with "
                                                  "its Steam artwork. Touches no window."))
        lay.addWidget(card)

        # --- Appearance ---------------------------------------------------
        card, g = _card(tr("Appearance"))
        self.accent_group = QButtonGroup(self)
        self.accent_group.setExclusive(True)
        self.swatches: dict[str, _Swatch] = {}
        row = []
        for key, name, color in theme.ACCENTS:
            row.append(_Swatch(key, color, name))
        row.append(_Swatch("system", theme.system_accent(), system.TEXT["accent"]))
        custom = s.accent if s.accent.startswith("#") else "#808890"
        row.append(_Swatch("custom", custom, tr("Custom color")))
        for sw in row:
            self.accent_group.addButton(sw)
            self.swatches[sw.key] = sw
            sw.clicked.connect(lambda _=False, k=sw.key: self._pick_accent(k))
        self.custom_btn = QPushButton(tr("Choose…"))
        self.custom_btn.setToolTip(tr("Pick an exact color"))
        self.custom_btn.clicked.connect(self._choose_custom)
        sys_lbl = QLabel(tr("System"))
        sys_lbl.setObjectName("settingHint")
        _row(g, tr("Accent color"), _hbox(*row[:-2], self._sep(), row[-2], sys_lbl, self._sep(), row[-1],
                                          self.custom_btn, "stretch"),
             tr('Buttons, selections, timeline… The "System" color follows {source}. Applies right away.',
                source=system.TEXT["accent_follow"]))
        self._check_accent(s.accent)
        lay.addWidget(card)

        # --- Application --------------------------------------------------
        card, g = _card(tr("Application"))
        self.autostart = QCheckBox(tr("Start Snappy {when}", when=system.TEXT["session_start"]))
        self.autostart.setChecked(s.start_with_windows)
        start = system.TEXT["session_start"]
        self.minimized = QCheckBox(tr("{when}, stay minimized in the notification area", when=start[0].upper() + start[1:]))
        self.minimized.setChecked(s.start_minimized)
        self.minimized.setEnabled(s.start_with_windows)
        self.autostart.toggled.connect(self.minimized.setEnabled)
        _row(g, "", self.autostart)
        _row(g, "", self.minimized, tr("Opened by hand ({how}), Snappy always shows its window.",
                                       how=system.TEXT["manual_launch"]))
        lay.addWidget(card)
        lay.addStretch()

        for w in (self.duration, self.fps, self.quality, self.resolution, self.codec):
            sig = w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged
            sig.connect(self._update_estimate)
        self._update_estimate()
        self._update_sources()
        self._watched: set[int] = set()
        self._fields: list[QWidget] = []
        self._initial: dict[int, object] = {}
        self._watch()
        self._baseline = self._state()
        self._remember_values()
        self._check_dirty()

    # ------------------------------------------------------------ changes
    def _state(self) -> dict:
        """What "Apply" would save (the accent color is applied separately)."""
        d = asdict(self._collect())
        d.pop("accent", None)
        d["hotkeys"] = {k: self._hotkey_text(e, getattr(self.settings, k)) for k, e in self.hotkeys.items()}
        return d

    def _watch(self) -> None:
        """Hooks change tracking onto every field of the page (including those added later: separate
        applications)."""
        for w in self.findChildren(QWidget):
            if id(w) in self._watched:
                continue
            if isinstance(w, QCheckBox):
                sig = [w.toggled]
            elif isinstance(w, QComboBox):
                sig = [w.currentIndexChanged] + ([w.editTextChanged] if w.isEditable() else [])
            elif isinstance(w, (QSpinBox, QDoubleSpinBox, QSlider)):
                sig = [w.valueChanged]
            elif isinstance(w, QKeySequenceEdit):
                sig = [w.keySequenceChanged]
            elif isinstance(w, QLineEdit) and w is not self.split_add and not isinstance(w.parent(), QComboBox):
                sig = [w.textChanged]
            elif isinstance(w, ScreenList):
                sig = [w.changed]
            else:
                continue
            self._watched.add(id(w))
            self._fields.append(w)
            for s in sig:
                s.connect(self._check_dirty)

    @staticmethod
    def _value(w: QWidget):
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return (w.currentData(), w.currentText()) if w.isEditable() else w.currentData()
        if isinstance(w, (QSpinBox, QDoubleSpinBox, QSlider)):
            return w.value()
        if isinstance(w, QKeySequenceEdit):
            return w.keySequence().toString()
        if isinstance(w, QLineEdit):
            return w.text()
        if isinstance(w, ScreenList):
            return w.order(), w.primary, w.filmed()
        return None

    def _remember_values(self) -> None:
        """Saved value of each field (reference for the "changed" bars)."""
        self._initial = {id(w): self._value(w) for w in self._fields}

    def _mark_rows(self) -> None:
        """Accent bar on the left of each row with a field that differs from the saved settings."""
        changed: dict[QLabel, bool] = {}
        for w in self._fields:
            row, p = None, w
            while p is not None and row is None:
                row = getattr(p, "_row_label", None)
                p = p.parentWidget()
            if row is None:
                continue
            diff = id(w) in self._initial and self._value(w) != self._initial[id(w)]
            changed[row] = changed.get(row, False) or diff
        for lbl, on in changed.items():
            if bool(lbl.property("modified")) != on:
                lbl.setProperty("modified", on)
                lbl.setToolTip(tr("Changed, not applied yet") if on else "")
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
                lbl.update()

    def _check_dirty(self, *_) -> None:
        if self._baseline is None:
            return
        for exe, cb in self.split_checks.items():   # boxes added later: compared with the saved value
            self._initial.setdefault(id(cb), exe in self._baseline["split_app_list"])
        self._mark_rows()
        dirty = self._state() != self._baseline
        self.dirty_lbl.setVisible(dirty)
        self.cancel_btn.setVisible(dirty)
        self.apply_btn.setEnabled(dirty)
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit(dirty)

    def is_dirty(self) -> bool:
        return self._dirty

    def apply(self) -> bool:
        """Applies; False if a setting is invalid (shortcut...)."""
        self._apply()
        return not self._dirty

    def show_pending(self) -> None:
        """Brings the "Cancel / Apply" bar back on screen and makes it blink."""
        self._scroll.verticalScrollBar().setValue(0)
        self.apply_btn.setFocus()
        normal = f"color: {theme.FAVORITE}; font-weight: 600;"
        flash = f"color: white; background: {theme.DANGER}; border-radius: 6px; padding: 2px 8px; font-weight: 600;"
        for i in range(6):
            QTimer.singleShot(i * 220, lambda on=(i % 2 == 0): self.dirty_lbl.setStyleSheet(flash if on else normal))

    # ------------------------------------------------------------ appearance
    @staticmethod
    def _sep() -> QWidget:
        w = QFrame()
        w.setFixedSize(1, 22)
        w.setStyleSheet(f"background:{theme.BORDER};")
        return w

    def _check_accent(self, value: str) -> None:
        key = value if value in self.swatches and value != "custom" else "custom"
        if key == "custom":
            self.swatches["custom"].set_color(theme.resolve_accent(value))
        self.swatches[key].setChecked(True)

    def _pick_accent(self, key: str) -> None:
        value = self.swatches["custom"].color if key == "custom" else key
        if key == "system":
            self.swatches["system"].set_color(theme.system_accent())
        self._set_accent(value)

    def _choose_custom(self) -> None:
        color = QColorDialog.getColor(QColor(theme.ACCENT), self, tr("Accent color"))
        if color.isValid():
            self.swatches["custom"].set_color(color.name())
            self.swatches["custom"].setChecked(True)
            self._set_accent(color.name())

    def _set_accent(self, value: str) -> None:
        self.settings.accent = value
        self.accentChanged.emit(value)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _level_slider(value: float) -> tuple[QWidget, QSlider]:
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 200)
        sl.setValue(int(value * 100))
        sl.setMaximumWidth(260)
        pct = QLabel(f"{sl.value()} %")
        pct.setObjectName("faint")
        pct.setFixedWidth(46)
        sl.valueChanged.connect(lambda v: pct.setText(f"{v} %"))
        sl.setToolTip(tr("Level of this track in the mix (shared file, editor, quick export)"))
        return _hbox(sl, pct, "stretch"), sl

    def _detect_apps(self) -> None:
        self.detectAppsRequested.emit()

    def _preview_sound(self) -> None:
        self.soundPreviewRequested.emit(self.sound_choice.currentData(), self.sound_volume.value())

    def on_apps_found(self, apps: list) -> None:
        """Applications that play sound [(executable, name)], found by the controller."""
        self._detected = apps
        for combo in (self.game_app, self.chat_app):
            current = combo.currentData() or combo.currentText()
            combo.blockSignals(True)
            for exe, name in apps:
                if combo.findData(exe) < 0:
                    combo.addItem(f"{name} ({exe})", exe)
            i = combo.findData(current)
            if i >= 0:
                combo.setCurrentIndex(i)
            else:                     # nothing chosen: the first app found is not picked by default
                combo.setCurrentIndex(-1)
                combo.setEditText(current or "")
            combo.blockSignals(False)
        self._fill_split()

    def _fill_split(self) -> None:
        names = list(dict.fromkeys(self.settings.split_app_list + [exe for exe, _ in self._detected]))
        detected = {exe for exe, _ in self._detected}
        for exe in names:
            if exe in self.split_checks:
                continue
            label = next((n for e, n in self._detected if e == exe), app_label(exe))
            cb = QCheckBox(f"{label}  ({exe})" + ("  · " + tr("playing sound right now") if exe in detected else ""))
            cb.setChecked(exe in self.settings.split_app_list)
            cb.toggled.connect(self._update_sources)
            self.split_checks[exe] = cb
            self.split_box.addWidget(cb)
        self._update_sources()
        if self._baseline is not None:
            self._watch()
            self._check_dirty()

    def _add_split_app(self) -> None:
        name = self.split_add.text().strip()
        self.split_add.clear()
        if not name:
            return
        if system.EXE_SUFFIX and not name.lower().endswith(system.EXE_SUFFIX):
            name += system.EXE_SUFFIX
        if name not in self.settings.split_app_list:
            self.settings.split_app_list.append(name)
        self._fill_split()
        self.split_checks[name].setChecked(True)

    def _combo_value(self, combo: QComboBox) -> str:
        text = combo.currentText().strip()
        data = combo.currentData()
        if data and combo.itemText(combo.currentIndex()) == text:
            return data
        return text

    def _collect(self) -> Settings:
        s = self.settings.copy()
        s.clip_seconds = self.duration.value()
        s.monitor_index = self.screens.primary
        # Sorted: reordering the list only changes the display, without restarting the buffer.
        s.extra_monitors = sorted(m for m in self.screens.filmed() if m != s.monitor_index)
        s.screen_order = self.screens.order()
        s.output_height = self.resolution.currentData()
        s.fps = self.fps.currentData()
        s.quality = self.quality.currentData()
        s.codec = self.codec.currentData()
        s.encoder = self.encoder.currentData()
        s.capture_method = self.method.currentData()
        s.show_cursor = self.cursor.isChecked()
        s.capture_system_audio = self.game_on.isChecked()
        s.game_mode = self.game_mode.currentData()
        s.game_app = self._combo_value(self.game_app)
        s.capture_chat = self.chat_on.isChecked()
        s.chat_app = self._combo_value(self.chat_app)
        s.capture_microphone = self.mic.isChecked()
        s.microphone_name = self.mic_device.currentData()
        s.split_apps = self.split_on.isChecked()
        s.split_app_list = [exe for exe, cb in self.split_checks.items() if cb.isChecked()]
        s.levels = dict(s.levels, game=self.game_level[1].value() / 100, chat=self.chat_level[1].value() / 100,
                        mic=self.mic_level[1].value() / 100)
        s.play_sound_on_save = self.sound.isChecked()
        s.sound = self.sound_choice.currentData()
        s.sound_volume = self.sound_volume.value()
        s.show_toast_on_save = self.toast.isChecked()
        s.discord_limit_mb = self.discord_limit.value()
        s.normalize_audio = self.normalize.isChecked()
        s.export_encoder = self.export_encoder.currentData()
        s.clips_dir = self.folder.text().strip() or s.clips_dir
        s.max_storage_gb = self.quota.value()
        s.detect_game = self.detect_game.isChecked()
        s.start_with_windows = self.autostart.isChecked()
        s.start_minimized = self.minimized.isChecked()
        s.normalize()
        return s

    def _update_sources(self, *_):
        if not hasattr(self, "minimized"):   # page being built
            return
        s = self._collect()
        self.game_mode.setEnabled(s.capture_system_audio)
        self.game_app.setEnabled(s.capture_system_audio and s.game_mode == "app")
        self.chat_app.setEnabled(s.capture_chat)
        self.mic_device.setEnabled(s.capture_microphone)
        for cb in self.split_checks.values():
            cb.setEnabled(s.split_apps)
        self.split_add.setEnabled(s.split_apps)
        tracks = audio_tracks(s)
        if not tracks:
            self.tracks_lbl.setText(tr("None: the clips will have no sound."))
            return
        desc = []
        for i, t in enumerate(tracks, 1):
            src = t["source"]
            what = (tr("all the PC sound") if src == "all" else
                    tr("everything except {apps}", apps=", ".join(app_label(a) for a in src[8:].split(",")))
                    if src.startswith("exclude:") else
                    tr("only {app}", app=app_label(src[4:])) if src.startswith("app:") else
                    (src[4:] or tr("default microphone")))
            desc.append(f"{i}. {t['label']} — {what}")
        extra = ("  ·  " + tr('the file starts with a "Mix" track (played by Discord and media players)')
                 if len(tracks) > 1 else "")
        if (s.capture_system_audio and s.game_mode == "app" and s.capture_chat
                and s.game_app.strip().lower() == s.chat_app.strip().lower()):
            extra += "\n" + tr('{app} is already the "Voice chat" track: the "Game" track therefore takes all the '
                               'sound except the voice chat.', app=app_label(s.game_app))
        self.tracks_lbl.setText("\n".join(desc) + extra)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Clips folder"), self.folder.text())
        if d:
            self.folder.setText(d)

    def _update_estimate(self, *_) -> None:
        if not hasattr(self, "estimate"):
            return
        _, maxrate = QUALITY_PRESETS[self.quality.currentData()]
        height = self.resolution.currentData() or 1080
        factor = max(0.3, (height / 1080) ** 2 * (self.fps.currentData() / 60))
        if self.codec.currentData() in ("hevc", "av1"):
            factor *= 0.65
        screens = len(self.screens.filmed())
        # Average bitrate ~ 60 % of the cap for typical game content.
        mb = maxrate * factor * 0.6 * self.duration.value() / 8 / 1024 * screens
        text = tr("≈ {size} MB for {seconds} s", size=f"{mb:.0f}", seconds=self.duration.value())
        if screens > 1:
            text += " (" + tr("{n} screens", n=screens) + ")"
        self.estimate.setText(text + " (" + tr("depends on the content") + ")")

    def refresh_encoder_summary(self) -> None:
        self.encoderSummaryRequested.emit(self.export_encoder.currentData())

    def set_encoder_summary(self, text: str) -> None:
        self.export_enc_lbl.setText(text)

    def _hotkey_text(self, edit: QKeySequenceEdit, fallback: str) -> str:
        seq = edit.keySequence()
        if seq.isEmpty():
            return fallback
        return QKeySequence(seq[0]).toString(QKeySequence.PortableText).replace("Meta+", "Win+")

    def _apply(self) -> None:
        s = self._collect()
        texts = {}
        for key, edit in self.hotkeys.items():
            hk = self._hotkey_text(edit, getattr(self.settings, key))
            problem = hotkey_problem(hk)
            if problem:
                self.hotkey_error.setText(tr("Invalid shortcut ({key}): {problem}", key=hk, problem=problem))
                return
            texts[key] = hk
        if len({v.lower() for v in texts.values()}) < len(texts):
            self.hotkey_error.setText(tr("Two actions have the same shortcut."))
            return
        self.hotkey_error.clear()
        for key, hk in texts.items():
            setattr(s, key, hk)
        self.settings = s
        self.refresh_encoder_summary()
        self._baseline = self._state()
        self._remember_values()
        self._check_dirty()
        self.settingsApplied.emit(s.copy())

    def show_hotkey_error(self, message: str) -> None:
        self.hotkey_error.setText(message)
