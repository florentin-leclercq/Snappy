"""Settings controller: gives the page the system lists, answers its requests (play a sound, find
the apps playing sound, encoder summary) and applies the validated settings (saving, shortcut,
start at login, buffer, library)."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from ..models.settings import Settings
from ..platforms import system
from ..services import ffmpeg as ff
from ..services import media, sounds
from ..services.recorder import SEGMENT_SECONDS
from ..views.settings_page import SettingsPage

log = logging.getLogger(__name__)


class _AppsBridge(QObject):
    found = Signal(object, list)


class SettingsController(QObject):
    applied = Signal(object, object)      # (new settings, old ones)
    accentChanged = Signal(str)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._apps = _AppsBridge()
        self._apps.found.connect(lambda page, apps: page.on_apps_found(apps))

    @staticmethod
    def options() -> dict:
        """System lists shown by the settings page."""
        return {"capture_methods": system.screen_capture_methods(), "microphones": system.list_microphones(),
                "sounds": sounds.available(), "segment_seconds": SEGMENT_SECONDS,
                "manual_recording": getattr(system, "MANUAL_RECORDING", False)}

    def attach(self, page: SettingsPage) -> None:
        """Connects a settings page (created at startup, then at every "Cancel")."""
        page.settingsApplied.connect(self._on_applied)
        page.accentChanged.connect(self.accentChanged)
        page.soundPreviewRequested.connect(lambda key, volume: sounds.play(key, volume))
        page.detectAppsRequested.connect(lambda: self._detect_apps(page))
        page.encoderSummaryRequested.connect(lambda choice: page.set_encoder_summary(self.encoder_summary(choice)))
        page.refresh_encoder_summary()

    def _detect_apps(self, page: SettingsPage) -> None:
        def work():
            try:
                apps = system.audio_apps()
            except Exception:
                log.exception("Lookup of the applications playing sound")
                apps = []
            self._apps.found.emit(page, apps)

        threading.Thread(target=work, name="audio-apps", daemon=True).start()

    @staticmethod
    def encoder_summary(choice: str) -> str:
        """Encoder picked for each codec, with the measured times."""
        enc = media.ENCODERS
        if not enc.ready:
            return tr("Speed measured at the first export.")
        parts = []
        for c in ("h264", "hevc", "av1"):
            e = enc.get(c, choice)
            if ff.codec_of(e) != c:
                parts.append(tr("{codec}: unavailable on this machine", codec=c.upper()))
                continue
            t = enc.timings.get(c, {})
            bench = " " + tr("({gpu} s card / {cpu} s processor)", gpu=f"{t['gpu']:.1f}", cpu=f"{t['cpu']:.1f}") \
                if "gpu" in t and "cpu" in t else ""
            parts.append(tr("{codec}: {encoder}", codec=c.upper(), encoder=ff.VENDOR_LABELS[ff.vendor_of(e)]) + bench)
        return "\n".join(parts)

    def _on_applied(self, new: Settings) -> None:
        old = self.settings
        self.settings = new
        new.save()
        self.applied.emit(new, old)

    def set_accent(self, value: str) -> None:
        """Accent color: saved right away (without "Apply")."""
        self.settings.accent = value
        try:
            self.settings.save()
        except OSError:
            pass
