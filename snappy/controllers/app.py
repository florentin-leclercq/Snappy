"""Main controller: connects the replay buffer, the library, the global shortcut, the notification
area and the window; creates the gallery, editor and settings controllers."""

from __future__ import annotations

import logging
import os
import threading
import time

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, __version__, update
from ..i18n import tr
from ..models import games
from ..models.library import ClipLibrary, safe_filename
from ..models.settings import Settings
from ..platforms import system
from ..services import ffmpeg as ff
from ..services import media, sounds
from ..services.jobs import SaveClipJob, StopRecordingJob
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
    updateReady = Signal(str)       # version downloaded (update thread)


class AppController(QObject):
    def __init__(self, qapp: QApplication, start_minimized: bool, demo: bool = False):
        super().__init__()
        self.qapp = qapp
        self.settings_ctl = SettingsController(Settings.load(), self)
        updated_from = self._remember_version()
        self.library = ClipLibrary(self.settings.clips_dir)
        self.pool = QThreadPool.globalInstance()
        self._saving = False
        self._tray_hint_shown = False
        self._paused = False
        self._last_clip = None
        self._record_ctx: dict = {}
        self._stopping_record = False

        self.bridge = _StateBridge()
        self.bridge.state.connect(self._on_state)
        self.bridge.updateReady.connect(self._on_update_ready)
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
        self.tray.recordRequested.connect(self.toggle_recording)
        self.tray.openRequested.connect(self.window.bring_to_front)
        self.tray.pauseToggled.connect(self.toggle_pause)
        self.tray.openFolderRequested.connect(lambda: system.open_folder(self.library.folder))
        self.tray.updateRequested.connect(self.restart_to_update)
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
        if updated_from:
            self.tray.notify(tr("Snappy was updated to version {version}.", version=__version__))
        elif start_minimized:
            self.tray.notify(tr("Snappy is recording in the background. {key} to save a clip.",
                                key=self.settings.hotkey_save))
        if not start_minimized:
            self.window.show()
        QTimer.singleShot(0, self._start_recorder)

        # Snappy.exe: looks for a new release a little after starting, then every few hours.
        self._update_version = ""
        self._update_cleaned = False
        if update.self_updating():
            QTimer.singleShot(20_000, self._check_for_update)
            self.update_timer = QTimer(self, interval=6 * 3600 * 1000, timeout=self._check_for_update)
            self.update_timer.start()

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
        names = [("save", "hotkey_save")] + ([("record", "hotkey_record")] if self.can_record else [])
        for name, key in names:
            try:
                self.hotkeys.register(name, getattr(self.settings, key))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            self.window.settings_page.show_hotkey_error("\n".join(errors))
            self.tray.notify(tr("Shortcut unavailable: {errors}", errors="; ".join(errors)), "warning", 5000)
        self.tray.set_hotkey(self.settings.hotkey_save)
        self.tray.set_record(self.can_record, False, self.settings.hotkey_record)

    @property
    def can_record(self) -> bool:
        """Manual recording (start / stop) offered by the platform and the current replay engine."""
        return system.MANUAL_RECORDING and self.recorder.can_record

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    def _on_hotkey(self, name: str) -> None:
        if name == "save":
            self.save_clip()
        elif name == "record":
            self.toggle_recording()

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
        since = self.recorder.recording_since
        self.window.set_buffer_fill(seconds, time.time() - since if since else None)
        if self.can_record:
            self.tray.set_record(True, since is not None, self.settings.hotkey_record)

    def toggle_pause(self) -> None:
        self._warn_recording_lost()
        self._paused = not self._paused
        self.tray.set_paused(self._paused)
        if self._paused:
            self.recorder.stop()
        else:
            self.recorder.start()

    # ------------------------------------------------------------ manual recording
    def toggle_recording(self) -> None:
        if not self.can_record or self._stopping_record:
            return
        if self.is_recording:
            self._stopping_record = True
            if self.settings.play_sound_on_save:   # every press of the shortcut is heard, like a save
                sounds.play(self.settings.sound, self.settings.sound_volume)
            job = StopRecordingJob(self.recorder, self.library, self._record_ctx)
            job.signals.done.connect(self._on_recording_saved)
            self.pool.start(job)
            return
        try:
            self.recorder.start_recording(self.library.folder)
        except RecorderError as exc:
            self._show_toast(tr("Recording not started"), str(exc)[:120], ok=False)
            return
        self._record_ctx = self._capture_context()   # names the clip after the game being recorded
        if self.settings.play_sound_on_save:
            sounds.play(self.settings.sound, self.settings.sound_volume)
        self._show_toast(tr("Recording started"), tr("{key} to stop it", key=self.settings.hotkey_record), ok=False)
        self._update_fill()

    def _on_recording_saved(self, clip, error: str) -> None:
        self._stopping_record = False
        self._on_saved(clip, error)

    def _warn_recording_lost(self) -> None:
        """Restarting the buffer ends the recorders: a manual recording in progress is lost."""
        if self.is_recording:
            self.tray.notify(tr("The manual recording was interrupted (buffer restarted)."), "warning", 5000)

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
            if system.OVERLAY_TOAST:
                subtitle = " · ".join(parts) + " — " + tr("click to edit it")
            else:   # the Wayland overlay lets the clicks through to the game
                subtitle = " · ".join(parts)
            self._show_toast(tr("Clip saved"), subtitle, thumb, thumb_path=str(self.library.thumb_path(clip)))

    def _show_toast(self, title: str, subtitle: str, thumb=None, ok: bool = True, ms: int = 3500,
                    thumb_path: str = "") -> None:
        """Notification on the recorded screen (the game's), without taking the focus. On Wayland,
        where a regular window cannot choose its place, a layer-shell overlay when the desktop
        supports it, otherwise a desktop notification."""
        screens = QGuiApplication.screens()
        screen = screens[min(self.settings.monitor_index, len(screens) - 1)] if screens else None
        if not system.OVERLAY_TOAST:
            if system.show_overlay_toast(title, subtitle, ok=ok, ms=ms, monitor=screen.name() if screen else "",
                                         thumb=thumb_path):
                return
            self.tray.notify(f"{title} — {subtitle}" if subtitle else title, "info" if ok else "warning", ms)
            return
        self.toast.show_message(title, subtitle, thumb, ok=ok, ms=ms, screen=screen)

    def _open_last_clip(self) -> None:
        if self._last_clip and self.library.get(self._last_clip.file):
            self.window.bring_to_front()
            self.window.open_clip(self._last_clip)

    # ------------------------------------------------------------ updates (update.py)
    def _remember_version(self) -> str:
        """Previous version when this start follows an update ("" otherwise); remembers this one."""
        last = self.settings.last_version
        if last == __version__:
            return ""
        self.settings.last_version = __version__
        try:
            self.settings.save()
        except OSError:
            pass
        return last if last and update.is_newer(__version__, last) else ""

    def _check_for_update(self) -> None:
        """Snappy.exe: downloads the latest release in the background; installed at the restart."""
        if self._update_version:
            return

        def run():
            try:
                if not self._update_cleaned:     # leftovers of the last install, before any download
                    self._update_cleaned = True
                    update.cleanup_after_install()
                release = update.latest_release()
                if release is None or not update.is_newer(release.tag):
                    return
                staged = update.staged_update()
                if not (staged and staged[0] == release.version):
                    if not update.can_replace(update.install_dir()):
                        update.log(f"{release.tag} available, but {update.install_dir()} cannot be replaced "
                                   "without administrator rights")
                        return
                    update.download_release(release)
                self.bridge.updateReady.emit(release.version)
            except OSError as exc:
                update.log(f"update check failed: {exc}")

        threading.Thread(target=run, name="update-check", daemon=True).start()

    def _on_update_ready(self, version: str) -> None:
        self._update_version = version
        self.tray.set_update(version)
        self.tray.notify(tr("Snappy {version} is ready: it is installed when Snappy restarts.", version=version),
                         ms=6000)

    def restart_to_update(self) -> None:
        self._warn_recording_lost()
        if update.apply_staged(os.getpid()):
            self.quit()

    # ------------------------------------------------------------ settings
    def _on_accent(self, value: str) -> None:
        self.settings_ctl.set_accent(value)
        theme.reapply(self.qapp, value)

    def apply_settings(self, new: Settings, old: Settings) -> None:
        if new.clips_dir != old.clips_dir:
            self.library.move_to(new.clips_dir)
            self.window.thumbs.clear()
        if (new.hotkey_save, new.hotkey_record) != (old.hotkey_save, old.hotkey_record):
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
            self._warn_recording_lost()
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
