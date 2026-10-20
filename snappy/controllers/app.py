"""Main controller: connects the replay buffer, the library, the global shortcut, the notification
area and the window; creates the gallery, editor and settings controllers."""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME
from ..i18n import tr
from ..models import games
from ..models.library import ClipLibrary, safe_filename
from ..models.settings import Settings
from ..platforms import system
from ..services import ffmpeg as ff
from ..services import media, sounds
from ..services.jobs import SaveClipJob
from ..services.recorder import RecorderError, ReplayBuffer
from ..views import theme
from ..views.main_window import MainWindow
from ..views.tray import TrayView
from ..views.widgets import Toast
from .editor import EditorController
from .gallery import GalleryController
from .settings import SettingsController

log = logging.getLogger("snappy")


class _StateBridge(QObject):
    """Forwards the buffer state changes (FFmpeg thread) to the UI thread."""
    state = Signal(str, str)


class AppController(QObject):
    def __init__(self, qapp: QApplication, start_minimized: bool, demo: bool = False):
        super().__init__()
        self.qapp = qapp
        self.settings_ctl = SettingsController(Settings.load(), self)
        self.library = ClipLibrary(self.settings.clips_dir)
        self.pool = QThreadPool.globalInstance()
        self._saving = False
        self._tray_hint_shown = False
        self._paused = False
        self._last_clip = None

        self.bridge = _StateBridge()
        self.bridge.state.connect(self._on_state)
        # Replay engine for the system (Windows: FFmpeg; Linux: gpu-screen-recorder).
        engine = ReplayBuffer if demo else system.replay_buffer_class()
        self.recorder = engine(self.settings, self._monitors(), test_mode=demo,
                               on_state=lambda s, m: self.bridge.state.emit(s, m))

        # Views and controllers
        self.window = MainWindow(self.settings, self.library, self.settings_ctl.options())
        self.gallery = GalleryController(self.window, self.library, self.settings)
        self.editor = EditorController(self.window.editor, self.library, self.settings)
        self.editor.clipChanged.connect(self.window.groups_changed)
        self.window.clipOpened.connect(self.editor.open)
        self.window.editorLeft.connect(self.editor.close)
        self.window.saveClipRequested.connect(self.save_clip)
        self.window.revealRequested.connect(lambda c: system.reveal(self.library.path(c)))
        self.window.revealPath.connect(lambda p: system.reveal(p))
        self.window.closeToTray.connect(self._on_close_to_tray)
        self.window.settingsPageCreated.connect(self.settings_ctl.attach)
        self.settings_ctl.attach(self.window.settings_page)
        self.settings_ctl.applied.connect(self.apply_settings)
        self.settings_ctl.accentChanged.connect(self._on_accent)
        self.window.refresh()  # starts loading the thumbnails again, now connected

        self.toast = Toast()
        self.toast.clicked.connect(self._open_last_clip)
        self.tray = TrayView(self)
        self.tray.saveRequested.connect(self.save_clip)
        self.tray.openRequested.connect(self.window.bring_to_front)
        self.tray.pauseToggled.connect(self.toggle_pause)
        self.tray.openFolderRequested.connect(lambda: system.open_folder(self.library.folder))
        self.tray.quitRequested.connect(self.quit)

        self.hotkeys = system.GlobalHotkeys(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._register_hotkeys()

        if self.settings.start_with_windows and not demo:
            try:   # rewrites the entry: fixes an old command or a moved folder
                system.set_autostart(True, self.settings.start_minimized)
            except OSError:
                log.warning("Login entry not updated", exc_info=True)

        self.fill_timer = QTimer(self, interval=500, timeout=self._update_fill)
        self.fill_timer.start()
        media.prune_share()
        # Encoder benchmark for the exports, in the background and at low priority.
        QTimer.singleShot(30_000, lambda: threading.Thread(target=media.ENCODERS.detect, daemon=True).start())

        # Minimized only when the system starts it at login ("minimized" option checked): opened
        # by hand (.exe, run.bat, run.sh), Snappy shows its window.
        if start_minimized:
            self.tray.notify(tr("Snappy is recording in the background. {key} to save a clip.",
                                key=self.settings.hotkey_save))
        else:
            self.window.show()
        QTimer.singleShot(0, self._start_recorder)

    @property
    def settings(self) -> Settings:
        return self.settings_ctl.settings

    # ------------------------------------------------------------ startup
    def _monitors(self) -> dict[int, dict]:
        out = {}
        for i, sc in enumerate(QGuiApplication.screens()):
            g, r = sc.geometry(), sc.devicePixelRatio()
            w, h = int(g.width() * r), int(g.height() * r)
            out[i] = {"geometry": (int(g.x() * r), int(g.y() * r), w, h),
                      "label": tr("Screen {n} ({width}×{height})", n=i + 1, width=w, height=h), "name": sc.name()}
        return out

    def _start_recorder(self) -> None:
        try:
            ff.ffmpeg()
        except ff.FFmpegNotFound as exc:
            self._on_state("error", str(exc))
            QMessageBox.critical(self.window, tr("FFmpeg not found"), str(exc))
            return
        try:
            self.recorder.start()
        except RecorderError as exc:
            self._on_state("error", str(exc))
        self.window.set_sources(self.recorder.screens, self.recorder.tracks)

    def _register_hotkeys(self) -> None:
        errors = []
        for name, key in (("save", "hotkey_save"),):
            try:
                self.hotkeys.register(name, getattr(self.settings, key))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            self.window.settings_page.show_hotkey_error("\n".join(errors))
            self.tray.notify(tr("Shortcut unavailable: {errors}", errors="; ".join(errors)), "warning", 5000)
        self.tray.set_hotkey(self.settings.hotkey_save)

    def _on_hotkey(self, name: str) -> None:
        if name == "save":
            self.save_clip()

    def handle_message(self, data: bytes) -> None:
        """Message from another instance: "hotkey:save" (desktop shortcut on Linux), otherwise
        "show" (Snappy started again by hand: show the window)."""
        msg = data.decode("utf-8", "replace").strip()
        if msg.startswith("hotkey:"):
            self._on_hotkey(msg[len("hotkey:"):])
        else:
            self.window.bring_to_front()

    def _on_close_to_tray(self) -> None:
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify(tr("Snappy keeps recording in the background. Right-click the icon to quit."), ms=3000)

    # ------------------------------------------------------------ buffer state
    def _on_state(self, state: str, message: str) -> None:
        if self._paused and state == "stopped":
            state = "paused"
        self.window.set_status(state, message)
        self.tray.set_status(message or state)
        if state == "error":
            self.tray.notify(message[:250], "error", 6000)

    def _update_fill(self) -> None:
        seconds = self.recorder.buffered_seconds() if self.recorder.is_running else 0.0
        self.window.set_buffer_fill(seconds)

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.tray.set_paused(self._paused)
        if self._paused:
            self.recorder.stop()
        else:
            self.recorder.start()

    # ------------------------------------------------------------ saving
    def _capture_context(self) -> dict:
        """Game / application at the time of the key press (before any focus change)."""
        app, exe = system.foreground_app_info()
        game, appid = games.running_game() if self.settings.detect_game else (None, None)
        ctx = {"app": app, "exe": exe, "game": game, "appid": appid}
        label = game or app
        if label:
            ctx["name"] = safe_filename(f"{label} {time.strftime('%Y-%m-%d %H-%M-%S')}")
        return ctx

    def save_clip(self) -> None:
        if self._saving:
            return
        if not self.recorder.is_running:
            self._show_toast(tr("Buffer inactive"), tr("The buffer is not recording right now."), ok=False)
            return
        self._saving = True
        ctx = self._capture_context()
        if self.settings.play_sound_on_save:
            sounds.play(self.settings.sound, self.settings.sound_volume)
        job = SaveClipJob(self.recorder, self.library, ctx)
        job.signals.done.connect(self._on_saved)
        self.pool.start(job)

    def _on_saved(self, clip, error: str) -> None:
        self._saving = False
        self._update_fill()
        if error or clip is None:
            if self.settings.show_toast_on_save:
                self._show_toast(tr("Save failed"), error[:120], ok=False)
            return
        self._last_clip = clip
        self.gallery.enforce_quota()
        thumb = QPixmap(str(self.library.thumb_path(clip)))
        if not thumb.isNull():
            self.window.set_thumbnail(clip.file, thumb)
        self.window.refresh()
        if self.settings.show_toast_on_save:
            parts = [f"{clip.duration:.0f} s"]
            if clip.group:
                parts.append(clip.group)
            if clip.n_screens > 1:
                parts.append(tr("{n} screens", n=clip.n_screens))
            self._show_toast(tr("Clip saved"), " · ".join(parts) + " — " + tr("click to edit it"), thumb)

    def _show_toast(self, title: str, subtitle: str, thumb=None, ok: bool = True, ms: int = 3500) -> None:
        """Notification on the recorded screen (the game's), without taking the focus. On Wayland,
        where a window cannot choose its place, it is a desktop notification."""
        if not system.OVERLAY_TOAST:
            self.tray.notify(f"{title} — {subtitle}" if subtitle else title, "info" if ok else "warning", ms)
            return
        screens = QGuiApplication.screens()
        screen = screens[min(self.settings.monitor_index, len(screens) - 1)] if screens else None
        self.toast.show_message(title, subtitle, thumb, ok=ok, ms=ms, screen=screen)

    def _open_last_clip(self) -> None:
        if self._last_clip and self.library.get(self._last_clip.file):
            self.window.bring_to_front()
            self.window.open_clip(self._last_clip)

    # ------------------------------------------------------------ settings
    def _on_accent(self, value: str) -> None:
        self.settings_ctl.set_accent(value)
        theme.reapply(self.qapp, value)

    def apply_settings(self, new: Settings, old: Settings) -> None:
        if new.clips_dir != old.clips_dir:
            self.library.move_to(new.clips_dir)
            self.window.thumbs.clear()
        if new.hotkey_save != old.hotkey_save:
            self._register_hotkeys()
        if (new.start_with_windows, new.start_minimized) != (old.start_with_windows, old.start_minimized):
            try:
                system.set_autostart(new.start_with_windows, new.start_minimized)
            except OSError as exc:
                QMessageBox.warning(self.window, APP_NAME, tr("Could not change the start at login: {error}", error=exc))
        self.gallery.apply_settings(new)
        self.editor.apply_settings(new)
        self.window.apply_settings(new)
        restart = new.capture_changed(old)
        if restart:
            self.recorder.monitors = self._monitors()
            if not self._paused:
                self.recorder.restart(new)
            else:
                self.recorder.settings = new.copy()
            self.window.set_sources(self.recorder.screens, self.recorder.tracks)
        else:
            self.recorder.settings = new.copy()   # mix levels, etc.: without restarting
        self.gallery.enforce_quota()
        self.window.toast.show_message(tr("Settings saved · buffer restarted") if restart else tr("Settings saved"))

    # ------------------------------------------------------------ exit
    def quit(self) -> None:
        self.fill_timer.stop()
        self.hotkeys.unregister_all()
        self.editor.close()
        self.recorder.stop()
        self.library.purge_trash(older_than=0)
        self.tray.hide()
        # exit() and not quit(): Qt 6 would first close the windows, and the main window refuses
        # (its close button minimizes it to the notification area): Snappy would not quit.
        self.qapp.exit(0)
