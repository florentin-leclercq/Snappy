"""Main view: sidebar (games and apps), gallery, editor, settings.

It reads the library to display it and emits signals for every action on the clips (favorite,
game, rename, trash, quick export): `controllers.gallery` handles them."""

from __future__ import annotations


from PySide6.QtCore import QDate, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCalendarWidget, QComboBox, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSlider, QStackedWidget, QSystemTrayIcon, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from ..i18n import tr
from ..models import games
from ..models.settings import Settings
from ..models.library import Clip, ClipLibrary
from ..platforms import system
from . import theme
from .editor import EditorPage
from .settings_page import SettingsPage
from .widgets import (GalleryView, PersistentSplitter, GamePickerDialog, InAppToast, Segmented, app_icon, copy_files, fmt_duration,
                      fmt_size, glyph_icon, group_icon, nav_button)

PAGE_GALLERY, PAGE_EDITOR, PAGE_SETTINGS = range(3)


class HeroBanner(QWidget):
    """Header of a game: Steam banner (when cached), logo, statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(170)
        self.hero = QPixmap()
        self.logo = QPixmap()
        self.title = ""
        self.stats = ""

    def set_content(self, hero: str | None, logo: str | None, title: str, stats: str) -> None:
        self.hero = QPixmap(hero) if hero else QPixmap()
        self.logo = QPixmap(logo) if logo else QPixmap()
        self.title, self.stats = title, stats
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0, 0, -1, -1), 14, 14)
        p.setClipPath(path)
        if not self.hero.isNull():
            scaled = self.hero.scaled(r.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(0, 0, scaled, (scaled.width() - r.width()) // 2, (scaled.height() - r.height()) // 3,
                         r.width(), r.height())
        else:
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor("#3a1d12"))
            g.setColorAt(1, QColor(theme.ACCENT))
            p.fillRect(r, g)
        g = QLinearGradient(0, r.height(), 0, 0)
        g.setColorAt(0, QColor(0, 0, 0, 200))
        g.setColorAt(0.8, QColor(0, 0, 0, 10))
        p.fillRect(r, g)
        x, bottom = 26, r.height() - 18
        if not self.logo.isNull():
            h = 56 if self.logo.width() / max(self.logo.height(), 1) < 6 else 40
            logo = self.logo.scaledToHeight(h, Qt.SmoothTransformation)
            if logo.width() > 420:
                logo = self.logo.scaledToWidth(420, Qt.SmoothTransformation)
            p.drawPixmap(x, bottom - 24 - logo.height(), logo)
        else:
            f = QFont(self.font())
            f.setPixelSize(26)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(x, bottom - 30, self.title)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(x, bottom, self.stats)


class MainWindow(QMainWindow):
    saveClipRequested = Signal()
    settingsPageCreated = Signal(object)     # settings page (re)created: to be connected
    revealRequested = Signal(object)
    revealPath = Signal(str)
    thumbsNeeded = Signal(list)
    spriteNeeded = Signal(object)
    thumbSizeChanged = Signal(int)
    closeToTray = Signal()
    clipOpened = Signal(object)              # the clip is shown in the editor
    editorLeft = Signal()                    # leaving the editor
    # Actions on the clips
    favoriteToggled = Signal(object)
    favoritesSet = Signal(list, bool)
    gameChosen = Signal(list, str, str)      # clips, name, appid
    renameChosen = Signal(object, str)
    trashRequested = Signal(list)
    undoTrashRequested = Signal(str)
    quickExportRequested = Signal(list, object)
    copyAudioRequested = Signal(object)

    def __init__(self, settings: Settings, library: ClipLibrary, settings_options: dict):
        """settings_options: system lists for the settings page (see `SettingsPage`)."""
        super().__init__()
        self.settings = settings
        self.library = library
        self.settings_options = settings_options
        self.thumbs: dict[str, QPixmap] = {}
        self._filter_fav = False
        self._filter_group: str | None = None    # game / app name; "" = no game
        self._day: tuple[int, int, int] | None = None
        self._side_key = None

        self.setWindowTitle("Snappy")
        self.setWindowIcon(app_icon())
        self.resize(1380, 860)
        self.setMinimumSize(1000, 640)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.splitter = PersistentSplitter(Qt.Horizontal, "sidebar")
        self.splitter.setHandleWidth(4)
        h.addWidget(self.splitter)
        self.splitter.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_gallery_page())
        self.editor = EditorPage(settings)
        self.editor.backRequested.connect(self.show_gallery)
        self.editor.copyRequested.connect(lambda c: self.copy_clips([c]))
        self.editor.revealRequested.connect(self.revealRequested)
        self.editor.revealPath.connect(self.revealPath)
        self.editor.trashRequested.connect(lambda c: self.trash([c]))
        self.editor.message.connect(lambda m: self.toast.show_message(m))
        self.stack.addWidget(self.editor)
        self.settings_page = SettingsPage(settings, settings_options)
        self.stack.addWidget(self.settings_page)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.restore([256, 1180])
        self.toast = InAppToast(root)

        self.refresh()
        # Refreshes the relative dates.
        QTimer(self, interval=60_000, timeout=self.gallery.viewport().update).start()

    # ------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setMinimumWidth(252)          # the status text (shortcut) fits on one line
        side.setMaximumWidth(420)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 22, 0, 16)
        v.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 0, 14)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(30, 30))
        brand.addWidget(logo)
        name = QLabel("snappy")
        name.setObjectName("brand")
        dot = QLabel(".")
        dot.setObjectName("brandDot")
        brand.addSpacing(6)
        brand.addWidget(name)
        brand.addWidget(dot)
        brand.addStretch()
        v.addLayout(brand)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_all = nav_button(tr("All clips"), "library")
        self.nav_fav = nav_button(tr("Favorites"), "star")
        self.nav_all.clicked.connect(lambda: self._set_filter(False, None))
        self.nav_fav.clicked.connect(lambda: self._set_filter(True, None))
        for b in (self.nav_all, self.nav_fav):
            self.nav_group.addButton(b)
            v.addWidget(b)
        self.nav_all.setChecked(True)

        apps_label = QLabel(tr("GAMES AND APPLICATIONS"))
        apps_label.setObjectName("sectionLabel")
        v.addWidget(apps_label)
        self.apps_box = QVBoxLayout()
        self.apps_box.setSpacing(2)
        apps_w = QWidget()
        apps_w.setLayout(self.apps_box)
        self.apps_box.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(apps_w)
        apps_w.setStyleSheet(f"background: {theme.SURFACE};")
        v.addWidget(scroll, 1)

        # Buffer status card
        status = QFrame()
        status.setObjectName("card")
        status.setStyleSheet(f"QFrame#card {{ background: {theme.SURFACE_2}; margin: 0 12px; }}")
        sv = QVBoxLayout(status)
        sv.setContentsMargins(16, 14, 16, 14)
        sv.setSpacing(6)
        self.status_dot = QLabel("●  " + tr("Starting…"))
        self.status_dot.setStyleSheet(f"color: {theme.TEXT_DIM}; font-weight: 600;")
        sv.addWidget(self.status_dot)
        self.buffer_bar = QProgressBar()
        self.buffer_bar.setTextVisible(False)
        self.buffer_bar.setFixedHeight(6)
        sv.addWidget(self.buffer_bar)
        self.buffer_lbl = QLabel()
        self.buffer_lbl.setObjectName("faint")
        sv.addWidget(self.buffer_lbl)
        self.sources_lbl = QLabel()
        self.sources_lbl.setObjectName("faint")
        self.sources_lbl.setWordWrap(True)
        sv.addWidget(self.sources_lbl)
        self.hotkey_lbl = QLabel()
        self.hotkey_lbl.setObjectName("faint")
        sv.addWidget(self.hotkey_lbl)
        v.addWidget(status)
        v.addSpacing(10)

        self.storage_lbl = QLabel()
        self.storage_lbl.setObjectName("faint")
        self.storage_lbl.setContentsMargins(24, 0, 0, 6)
        v.addWidget(self.storage_lbl)

        self.nav_settings = nav_button(tr("Settings"), "settings")
        self.nav_group.addButton(self.nav_settings)
        self.nav_settings.clicked.connect(self.show_settings)
        v.addWidget(self.nav_settings)
        return side

    def _rebuild_groups(self) -> None:
        groups = self.library.groups()
        ungrouped = self.library.count_ungrouped()
        key = (tuple(groups), ungrouped, len(self.library.clips(favorites_only=True)), len(self.library.clips()))
        if key == self._side_key:
            self._check_nav()
            return
        self._side_key = key
        while self.apps_box.count():
            item = self.apps_box.takeAt(0)
            w = item.widget()
            if w:
                if isinstance(w, QAbstractButton):
                    self.nav_group.removeButton(w)
                w.deleteLater()
        self.nav_all.setText(f"  {tr('All clips')}   {key[3]}")
        self.nav_fav.setText(f"  {tr('Favorites')}   {key[2]}")
        if not groups:
            hint = QLabel(tr("Games and applications\nwill show up here as you save clips."))
            hint.setObjectName("faint")
            hint.setContentsMargins(22, 4, 0, 0)
            self.apps_box.addWidget(hint)
        entries = [(name, count, appid, exe) for name, count, appid, exe in groups]
        if groups and ungrouped:
            entries.append(("", ungrouped, "", ""))
        for name, count, appid, exe in entries:
            b = nav_button(f"{name or tr('Without a game')}   {count}", "apps" if name else "video")
            icon = group_icon(appid, exe) if name else None
            if icon:
                b.setIcon(icon)
                b.setIconSize(QSize(20, 20))
            b.setToolTip(name or tr("Clips without a game or application"))
            b.setProperty("group", name)
            self.nav_group.addButton(b)
            b.clicked.connect(lambda _=False, g=name: self._set_filter(False, g))
            self.apps_box.addWidget(b)
        self.apps_box.addStretch()
        self._check_nav()

    def _check_nav(self) -> None:
        if self.stack.currentIndex() != PAGE_GALLERY:
            return
        if self._filter_fav:
            self.nav_fav.setChecked(True)
        elif self._filter_group is None:
            self.nav_all.setChecked(True)
        else:
            for b in self.nav_group.buttons():
                if b.property("group") == self._filter_group:
                    b.setChecked(True)

    # ------------------------------------------------------------ gallery
    def _build_gallery_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 22, 20, 0)
        v.setSpacing(12)

        self.hero = HeroBanner()
        v.addWidget(self.hero)
        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        self.page_title = QLabel(tr("All clips"))
        self.page_title.setObjectName("pageTitle")
        self.page_sub = QLabel()
        self.page_sub.setObjectName("pageSubtitle")
        tcol.addWidget(self.page_title)
        tcol.addWidget(self.page_sub)
        self.title_box = QWidget()
        self.title_box.setLayout(tcol)
        tcol.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.title_box, 1)
        self.save_btn = QPushButton("  " + tr("Save a clip"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setIcon(glyph_icon("save", "white"))
        self.save_btn.setIconSize(QSize(16, 16))
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.saveClipRequested)
        head.addWidget(self.save_btn)
        v.addLayout(head)

        # Filters: search · period · given day · sort · card size
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText(tr("Search a clip, a game…"))
        self.search.addAction(glyph_icon("search", theme.TEXT_FAINT), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda _: self.refresh_gallery())
        bar.addWidget(self.search)
        self.period = Segmented([("all", tr("All")), ("today", tr("Today")), ("7", tr("{n} days", n=7)),
                                 ("30", tr("{n} days", n=30))])
        self.period.set_current("all")
        self.period.changed.connect(self._on_period)
        bar.addWidget(self.period)
        self.day_btn = QToolButton()
        self.day_btn.setText("  " + tr("Date"))
        self.day_btn.setIcon(glyph_icon("calendar"))
        self.day_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.day_btn.setPopupMode(QToolButton.InstantPopup)
        self.day_btn.setObjectName("toolChip")
        cal_menu = QMenu(self.day_btn)
        self.calendar = QCalendarWidget()
        self.calendar.setMaximumDate(QDate.currentDate())
        self.calendar.clicked.connect(self._on_day)
        act = QWidgetAction(cal_menu)
        act.setDefaultWidget(self.calendar)
        cal_menu.addAction(act)
        self.day_btn.setMenu(cal_menu)
        bar.addWidget(self.day_btn)
        self.day_clear = QPushButton()
        self.day_clear.setObjectName("ghost")
        self.day_clear.setIcon(glyph_icon("close"))
        self.day_clear.setToolTip(tr("Remove the date filter"))
        self.day_clear.clicked.connect(lambda: self._set_day(None))
        self.day_clear.hide()
        bar.addWidget(self.day_clear)
        self.sort = QComboBox()
        for key, label in (("recent", tr("Newest")), ("old", tr("Oldest")), ("long", tr("Longest")),
                           ("heavy", tr("Largest"))):
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(lambda _: self.refresh_gallery())
        bar.addWidget(self.sort)
        bar.addStretch()
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("faint")
        bar.addWidget(self.count_lbl)
        size_icon = QLabel()
        size_icon.setPixmap(glyph_icon("apps", theme.TEXT_FAINT).pixmap(14, 14))
        bar.addWidget(size_icon)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(180, 560)
        self.size_slider.setValue(self.settings.thumb_size)
        self.size_slider.setFixedWidth(110)
        self.size_slider.setToolTip(tr("Preview size"))
        self.size_slider.valueChanged.connect(self._on_thumb_size)
        bar.addWidget(self.size_slider)
        v.addLayout(bar)

        self.gallery_stack = QStackedWidget()
        self.gallery = GalleryView()
        self.gallery.folder = str(self.library.folder)
        self.gallery.thumb_size = self.settings.thumb_size
        self.gallery.clipActivated.connect(self.open_clip)
        self.gallery.contextRequested.connect(self._clip_menu)
        self.gallery.favoriteToggled.connect(self.favoriteToggled)
        self.gallery.trashRequested.connect(lambda c: self.trash([c]))
        self.gallery.selectionChanged_.connect(self._on_selection)
        self.gallery.spriteNeeded.connect(self.spriteNeeded)
        self.gallery_stack.addWidget(self.gallery)

        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch()
        icon = QLabel()
        icon.setPixmap(glyph_icon("video", theme.TEXT_FAINT, 96).pixmap(72, 72))
        icon.setAlignment(Qt.AlignCenter)
        ev.addWidget(icon)
        self.empty_lbl = QLabel()
        self.empty_lbl.setObjectName("empty")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        ev.addWidget(self.empty_lbl)
        ev.addStretch(2)
        self.gallery_stack.addWidget(empty)
        v.addWidget(self.gallery_stack, 1)
        v.addWidget(self._build_selection_bar())
        return page

    def _build_selection_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("selectionBar")
        bar.setStyleSheet(f"QFrame#selectionBar {{ background:{theme.SURFACE}; border-top:1px solid {theme.BORDER}; }}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 8, 8, 8)
        cancel = QPushButton()
        cancel.setObjectName("ghost")
        cancel.setIcon(glyph_icon("close"))
        cancel.setToolTip(tr("Clear the selection (Esc)"))
        cancel.clicked.connect(lambda: self.gallery.clear_selection())
        h.addWidget(cancel)
        self.sel_lbl = QLabel()
        self.sel_lbl.setStyleSheet("font-weight:600;")
        h.addWidget(self.sel_lbl)
        all_btn = QPushButton(tr("Select all"))
        all_btn.setObjectName("ghost")
        all_btn.clicked.connect(lambda: self.gallery.select_all())
        h.addWidget(all_btn)
        h.addStretch()
        for glyph, label, tip, fn in (
                ("export", tr("Export for Discord"), tr("Export each clip with the Discord preset (Exports folder)"),
                 lambda: self.quick_export(self.gallery.selected_clips())),
                ("copy", tr("Copy"), tr("Copy the files (to paste with Ctrl+V)"),
                 lambda: self.copy_clips(self.gallery.selected_clips())),
                ("game", tr("Game"), tr("Set the game"), lambda: self.pick_game(self.gallery.selected_clips())),
                ("star", tr("Favorites"), tr("Add to / remove from favorites"), self._batch_favorite),
                ("delete", tr("Delete"), tr("Move to the trash"), self._batch_trash)):
            b = QPushButton(f"  {label}")
            b.setIcon(glyph_icon(glyph, theme.DANGER if glyph == "delete" else theme.TEXT))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
        bar.hide()
        self.sel_bar = bar
        return bar

    def _on_selection(self, n: int) -> None:
        self.sel_bar.setVisible(n > 0)
        self.sel_lbl.setText(tr("{n} clip selected", n=n) if n == 1 else tr("{n} clips selected", n=n))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.gallery.selected and self.stack.currentIndex() == PAGE_GALLERY:
            self.gallery.clear_selection()
            return
        if e.matches(QKeySequence.SelectAll) and self.stack.currentIndex() == PAGE_GALLERY:
            self.gallery.select_all()
            return
        super().keyPressEvent(e)

    def _on_period(self, key: str) -> None:
        if self._day:
            self._set_day(None, refresh=False)
        self.refresh_gallery()

    def _on_day(self, date: QDate) -> None:
        self.day_btn.menu().hide()
        self._set_day((date.year(), date.month(), date.day()))

    def _set_day(self, day, refresh: bool = True) -> None:
        self._day = day
        self.day_clear.setVisible(day is not None)
        if day:
            self.period.set_current(None)
            self.day_btn.setText("  " + tr("{year}-{month:02d}-{day:02d}", year=day[0], month=day[1], day=day[2]))
        else:
            self.day_btn.setText("  " + tr("Date"))
            if self.period.current() is None:
                self.period.set_current("all")
        if refresh:
            self.refresh_gallery()

    def _on_thumb_size(self, w: int) -> None:
        self.gallery.set_thumb_size(w)
        self.thumbSizeChanged.emit(w)

    def _set_filter(self, fav: bool, group: str | None) -> None:
        if not self.leave_settings_ok():
            return
        self._filter_fav, self._filter_group = fav, group
        self.gallery.clear_selection()
        self.show_gallery()

    def refresh(self) -> None:
        self._rebuild_groups()
        self.refresh_gallery()
        self.storage_lbl.setText(tr("{used} used · {free} free", used=fmt_size(self.library.total_size()),
                                    free=fmt_size(self.library.free_space_bytes())))

    def refresh_gallery(self) -> None:
        sort = self.sort.currentData()
        clips = self.library.clips(self._filter_fav, self._filter_group, self.search.text().strip(),
                                   self.period.current() or "all", self._day, sort)
        self._update_header()
        self.count_lbl.setText(tr("{n} clip", n=len(clips)) if len(clips) == 1 else tr("{n} clips", n=len(clips)))
        self.gallery.show_group_chip = not self._filter_group
        self.gallery.set_clips(clips, group_by_day=sort in ("recent", "old"))
        self._show_gallery_or_empty(clips)

    def _update_header(self) -> None:
        """Title and statistics of the view (number of clips, size, total length)."""
        in_view = self.library.clips(self._filter_fav, self._filter_group)
        if self._filter_fav:
            title = tr("Favorites")
        elif self._filter_group == "":
            title = tr("Without a game")
        elif self._filter_group:
            title = self._filter_group
        else:
            title = tr("All clips")
        stats = self._stats(in_view)
        appid = next((c.appid for c in in_view if c.appid), "") if self._filter_group else ""
        art = games.game_art(appid) if appid else {}
        if art.get("hero") or art.get("header"):
            self.hero.set_content(art.get("hero") or art.get("header"), art.get("logo"), title, stats)
            self.hero.show()
            self.title_box.hide()
        else:
            self.hero.hide()
            self.title_box.show()
            self.page_title.setText(title)
            self.page_sub.setText(stats)

    def _show_gallery_or_empty(self, clips: list[Clip]) -> None:
        if clips:
            self.gallery_stack.setCurrentIndex(0)
        else:
            hk = self.settings.hotkey_save
            if self.search.text().strip() or self._day or (self.period.current() or "all") != "all":
                msg = tr("No clip matches.\nChange the period or the search.")
            elif self._filter_fav:
                msg = tr("No favorites yet.\nStar your best moments.")
            else:
                msg = tr("No clips yet.\nPress {key} while playing to save the last {seconds} seconds.",
                         key=hk, seconds=self.settings.clip_seconds)
            self.empty_lbl.setText(msg)
            self.gallery_stack.setCurrentIndex(1)
        missing = [c for c in clips if c.file not in self.thumbs]
        if missing:
            self.thumbsNeeded.emit(missing)

    @staticmethod
    def _stats(clips: list[Clip]) -> str:
        n = len(clips)
        if not n:
            return ""
        total = sum(c.duration for c in clips)
        parts = [tr("{n} clip", n=n) if n == 1 else tr("{n} clips", n=n), fmt_size(sum(c.size for c in clips)),
                 tr("{duration} in total", duration=fmt_duration(total))]
        last = max(clips, key=lambda c: c.created)
        parts.append(tr("last: {date:%m-%d %H:%M}", date=last.created_dt))
        return "  ·  ".join(parts)

    def set_thumbnail(self, file: str, pixmap: QPixmap) -> None:
        self.thumbs[file] = pixmap
        self.gallery.set_thumb(file, pixmap)
        # Clips added outside Snappy get their length while their thumbnail is made: the total
        # length shown in the header is brought up to date (once for a batch of thumbnails).
        if not getattr(self, "_header_pending", False):
            self._header_pending = True
            QTimer.singleShot(300, self._update_header_later)

    def _update_header_later(self) -> None:
        self._header_pending = False
        self._update_header()

    def set_sprite(self, file: str, pixmap: QPixmap) -> None:
        self.gallery.set_sprite(file, pixmap)

    def set_preview(self, file: str, path: str) -> None:
        self.gallery.set_preview(file, path)

    # ------------------------------------------------------------ actions on the clips
    def _clip_menu(self, clip: Clip, pos) -> None:
        menu = QMenu(self)
        menu.addAction(glyph_icon("play"), tr("Open"), lambda: self.open_clip(clip))
        menu.addAction(glyph_icon("star_filled" if clip.favorite else "star", theme.FAVORITE),
                       tr("Remove from favorites") if clip.favorite else tr("Add to favorites"),
                       lambda: self.favoriteToggled.emit(clip))
        menu.addAction(glyph_icon("edit"), tr("Rename…"), lambda: self._rename(clip))
        menu.addAction(glyph_icon("game"), tr("Set the game…"), lambda: self.pick_game([clip]))
        menu.addAction(glyph_icon("select"), tr("Select"), lambda: self.gallery.toggle_selected(clip))
        menu.addSeparator()
        if clip.n_screens > 1:
            sub = menu.addMenu(glyph_icon("export"), tr("Export for Discord"))
            for k in self.settings.display_order(clip.screens):
                sc = clip.screens[k]
                m = sc.get("monitor")
                sub.addAction(tr("Screen {n} alone — {label}", n=(m if isinstance(m, int) else k) + 1,
                                 label=sc.get("label", "")),
                              lambda k=k: self.quick_export([clip], screen=k))
            if len(clip.angles) > 1:
                sub.addAction(tr("Multicam (editor switches)"), lambda: self.quick_export([clip], screen=None))
        else:
            menu.addAction(glyph_icon("export"), tr("Export for Discord"), lambda: self.quick_export([clip]))
        menu.addAction(glyph_icon("copy"), tr("Copy the file"), lambda: self.copy_clips([clip]))
        if clip.tracks:
            menu.addAction(glyph_icon("music"), tr("Copy the sound (MP3)"), lambda: self.copyAudioRequested.emit(clip))
        menu.addAction(glyph_icon("folder"), system.TEXT["reveal"], lambda: self.revealRequested.emit(clip))
        menu.addSeparator()
        menu.addAction(glyph_icon("delete", theme.DANGER), tr("Move to the trash"), lambda: self.trash([clip]))
        menu.exec(pos)

    def copy_clips(self, clips: list[Clip]) -> None:
        if not clips:
            return
        copy_files([self.library.path(c) for c in clips])
        n = len(clips)
        self.toast.show_message(tr("{n} file copied: paste it into Discord, an email or a folder (Ctrl+V)", n=n)
                                if n == 1 else
                                tr("{n} files copied: paste them into Discord, an email or a folder (Ctrl+V)", n=n))

    def _batch_favorite(self) -> None:
        clips = self.gallery.selected_clips()
        if clips:
            self.favoritesSet.emit(clips, not all(c.favorite for c in clips))

    def pick_game(self, clips: list[Clip]) -> None:
        if not clips:
            return
        known = {name: appid for name, _n, appid, _exe in self.library.groups() if appid}
        names = {c.game for c in clips}
        dlg = GamePickerDialog(known, next(iter(names)) if len(names) == 1 else "", len(clips), self)
        if dlg.exec() and dlg.result_value is not None:
            name, appid = dlg.result_value
            self.gameChosen.emit(clips, name, appid or "")

    def _rename(self, clip: Clip) -> None:
        text, ok = QInputDialog.getText(self, tr("Rename the clip"), tr("Name (also the name of the shared file):"),
                                        text=clip.title)
        if ok and text.strip():
            self.renameChosen.emit(clip, text.strip())

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def clip_renamed(self, old_file: str, clip: Clip) -> None:
        """The file of a clip was renamed: its thumbnail follows."""
        if old_file in self.thumbs:
            self.thumbs[clip.file] = self.thumbs.pop(old_file)
            self.gallery.thumbs[clip.file] = self.thumbs[clip.file]
        self.gallery.sprites.pop(old_file, None)
        self.refresh_gallery()

    def trash(self, clips: list[Clip]) -> None:
        if clips:
            self.trashRequested.emit(list(clips))

    def clips_removed(self, clips: list[Clip]) -> None:
        """Clips removed from the library (trash, quota): thumbnails dropped, view updated."""
        for c in clips:
            self.thumbs.pop(c.file, None)
        self.gallery.clear_selection()
        self.groups_changed()

    def groups_changed(self) -> None:
        """Favorites, games or clip count changed: sidebar and gallery updated."""
        self._side_key = None
        self.refresh()

    def _batch_trash(self) -> None:
        self.trash(self.gallery.selected_clips())

    def quick_export(self, clips: list[Clip], screen: int | None | str = "auto") -> None:
        """Discord preset without opening the editor (see `controllers.gallery`)."""
        if clips:
            self.quickExportRequested.emit(list(clips), screen)

    # ------------------------------------------------------------ navigation
    def show_gallery(self) -> None:
        if not self.leave_settings_ok():
            return
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editorLeft.emit()
        self.stack.setCurrentIndex(PAGE_GALLERY)
        self.refresh()

    def open_clip(self, clip: Clip) -> None:
        if not self.leave_settings_ok():
            return
        self.gallery.hover_player.stop()
        self.stack.setCurrentIndex(PAGE_EDITOR)
        self.clipOpened.emit(clip)

    def show_settings(self) -> None:
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editorLeft.emit()
        self.nav_settings.setChecked(True)
        self.stack.setCurrentIndex(PAGE_SETTINGS)

    # ------------------------------------------------------------ settings not applied
    def cancel_settings(self) -> None:
        """Cancel: the page is rebuilt from the saved settings."""
        old = self.settings_page
        scroll = old._scroll.verticalScrollBar().value()
        self.settings_page = SettingsPage(self.settings, self.settings_options)
        self.settingsPageCreated.emit(self.settings_page)
        self.stack.insertWidget(PAGE_SETTINGS, self.settings_page)
        if self.stack.currentWidget() is old:
            self.stack.setCurrentWidget(self.settings_page)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.settings_page._scroll.verticalScrollBar().setValue(scroll)

    def leave_settings_ok(self) -> bool:
        """True if the Settings page can be left. With changes not applied, asks: apply, discard
        the changes, or stay."""
        page = self.settings_page
        if self.stack.currentIndex() != PAGE_SETTINGS or not page.is_dirty():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Settings not applied"))
        box.setText(tr("You changed settings without applying them."))
        box.setInformativeText(tr("Apply them to keep them, or discard them to go back to the saved "
                                  "settings before leaving this page."))
        apply_b = box.addButton(tr("Apply"), QMessageBox.AcceptRole)
        apply_b.setObjectName("primary")
        cancel_b = box.addButton(tr("Discard the changes"), QMessageBox.DestructiveRole)
        stay_b = box.addButton(tr("Stay on the settings"), QMessageBox.RejectRole)
        box.setDefaultButton(apply_b)
        box.setEscapeButton(stay_b)
        box.exec()
        clicked = box.clickedButton()
        if clicked is apply_b and page.apply():
            return True
        if clicked is cancel_b:
            self.cancel_settings()
            return True
        # Stay (or invalid setting): back to the settings, with the apply bar highlighted.
        self.nav_settings.setChecked(True)
        self.settings_page.show_pending()
        return False

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ buffer state
    def set_status(self, state: str, message: str) -> None:
        colors = {"recording": theme.SUCCESS, "starting": theme.FAVORITE,
                  "error": theme.DANGER, "stopped": theme.TEXT_FAINT, "paused": theme.TEXT_FAINT}
        labels = {"recording": tr("Replay active"), "starting": tr("Starting…"), "error": tr("Error"),
                  "stopped": tr("Stopped"), "paused": tr("Paused")}
        self.status_dot.setText(f"●  {labels.get(state, state)}")
        self.status_dot.setStyleSheet(f"color: {colors.get(state, theme.TEXT_DIM)}; font-weight: 600;")
        self.status_dot.setToolTip(message)
        self.save_btn.setEnabled(state == "recording")

    def set_buffer_fill(self, seconds: float, recording: float | None = None) -> None:
        """recording: elapsed seconds of the manual recording in progress, None without one."""
        total = self.settings.clip_seconds
        self.buffer_bar.setMaximum(total)
        self.buffer_bar.setValue(int(seconds))
        self.buffer_lbl.setText(tr("Buffer: {seconds} / {total} s", seconds=int(seconds), total=total))
        s = self.settings
        if recording is not None:
            t = int(recording)
            elapsed = f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"
            self.hotkey_lbl.setText(tr("● Recording {time} — {key} to stop", time=elapsed, key=s.hotkey_record))
            self.hotkey_lbl.setStyleSheet(f"color: {theme.DANGER}; font-weight: 600;")
        else:
            self.hotkey_lbl.setText(tr("Save a clip: {key}", key=s.hotkey_save))
            self.hotkey_lbl.setStyleSheet("")

    def set_sources(self, screens: list, tracks: list) -> None:
        parts = []
        if len(screens) > 1:
            parts.append(tr("{n} screens", n=len(screens)))
        if tracks:
            parts.append(" · ".join(t["label"] for t in tracks))
        else:
            parts.append(tr("no sound"))
        self.sources_lbl.setText("  ·  ".join(parts))


    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.editor.apply_settings(settings)
        self.gallery.folder = str(self.library.folder)
        self._side_key = None
        self.refresh()

    # ------------------------------------------------------------ closing
    def closeEvent(self, event: QCloseEvent) -> None:
        # The close button minimizes to the notification area: the buffer keeps running.
        event.ignore()
        if self.stack.currentIndex() == PAGE_EDITOR:
            self.editor.player.pause()
            self.editor.preview.pause()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # Desktop without a notification area (GNOME without extension): a hidden window could
            # not be found again, so it is minimized to the taskbar.
            self.showMinimized()
            return
        self.hide()
        self.closeToTray.emit()

