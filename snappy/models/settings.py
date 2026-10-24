"""Snappy's persistent settings (JSON in %APPDATA%\\Snappy or ~/.config/Snappy)."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .. import APP_NAME
from ..i18n import tr
from ..platforms import system

# Accepted bounds for the buffer length (seconds).
MIN_CLIP_SECONDS = 5
MAX_CLIP_SECONDS = 600


def app_data_dir() -> Path:
    """Configuration folder of the application."""
    path = system.data_dir(APP_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Rebuildable caches (share copies, encoder benchmarks, editor audio)."""
    path = system.cache_dir(APP_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_clips_dir() -> Path:
    """Default clips folder: Videos\\Snappy."""
    return system.videos_dir() / APP_NAME


def default_buffer_dir() -> Path:
    """Temporary folder where the replay buffer segments rotate."""
    return system.buffer_dir(APP_NAME)


# Audio sources, each on its own track.
SOURCE_LABELS = {"game": tr("Game"), "chat": tr("Voice chat"), "mic": tr("Microphone"), "app": tr("Application"),
                 "other": tr("Track")}
GAME_MODES = {
    "except_chat": tr("All the PC sound except the voice chat"),
    "all": tr("All the PC sound"),
    "app": tr("A single application"),
}
# Suggested voice chat applications (Windows: executable name; Linux: PipeWire stream name).
CHAT_APPS = list(system.CHAT_APPS)
DEFAULT_LEVELS = {"game": 1.0, "chat": 0.8, "mic": 1.0, "app": 1.0, "other": 1.0}

# Accent color keys saved by older versions -> current keys.
LEGACY_ACCENTS = {"rouge": "red", "rose": "pink", "bleu": "blue", "vert": "green", "ambre": "amber"}

CODECS = {"h264": tr("H.264 (plays everywhere)"), "hevc": tr("HEVC (smaller files)"), "av1": tr("AV1 (smallest)")}


@dataclass
class Settings:
    # --- Capture ---
    clip_seconds: int = 30              # maximum length of a clip
    fps: int = 60
    monitor_index: int = 0              # primary recorded screen (with the sound)
    extra_monitors: list = field(default_factory=list)   # other screens recorded alongside
    screen_order: list = field(default_factory=list)     # display order of the screens (editor, settings)
    output_height: int = 1080           # 0 = native resolution
    capture_method: str = "ddagrab"     # Windows: "ddagrab" (Desktop Duplication, fast) or "gdigrab"
    show_cursor: bool = True

    # --- Encoding ---
    encoder: str = "auto"               # auto | cpu | card (system.ENCODER_CHOICES); the codec comes from "codec"
    codec: str = "h264"                 # h264 | hevc | av1
    quality: str = "high"               # low | medium | high | ultra
    audio_bitrate_kbps: int = 160

    # --- Audio sources (one track each) ---
    capture_system_audio: bool = True   # "Game" track
    game_mode: str = "except_chat"      # except_chat | all | app
    game_app: str = ""                  # application captured in "app" mode
    capture_chat: bool = True
    chat_app: str = field(default_factory=lambda: system.CHAT_APPS[0])
    capture_microphone: bool = False
    microphone_name: str = ""           # empty = default microphone
    split_apps: bool = False            # advanced: one track per listed application
    split_app_list: list = field(default_factory=list)
    levels: dict = field(default_factory=lambda: dict(DEFAULT_LEVELS))   # default mix levels (1.0 = 100 %)
    # Old settings (v1), turned into mix levels when loading.
    microphone_volume: float = 1.0
    system_volume: float = 1.0

    # --- Shortcuts ---
    hotkey_save: str = "Alt+F10"
    hotkey_record: str = "Alt+F9"     # start / stop a manual recording

    # --- Library ---
    clips_dir: str = field(default_factory=lambda: str(default_clips_dir()))
    max_storage_gb: float = 20.0        # 0 = unlimited; favorites are never deleted
    detect_game: bool = True            # names the clips after the running Steam game
    thumb_size: int = 300               # width of the gallery cards

    # --- Export & sharing ---
    discord_limit_mb: int = 10
    normalize_audio: bool = True        # export: volume at the platforms' level, without clipping
    export_encoder: str = "auto"        # auto (fastest measured) | gpu | cpu
    advanced_audio: bool = False        # obsolete: volume curves are always available

    # --- Application ---
    start_minimized: bool = False
    accent: str = "orange"              # accent color: key of theme.ACCENTS, "system" or "#rrggbb"
    start_with_windows: bool = False    # start at login (the name dates from the Windows version)
    play_sound_on_save: bool = True
    sound: str = "shutter"
    sound_volume: int = 70              # %
    show_toast_on_save: bool = True

    # ------------------------------------------------------------------
    @staticmethod
    def path() -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        known = {f.name: f for f in fields(cls)}
        for key, value in data.items():
            if key in known:
                default = getattr(settings, key)
                # Values whose type does not match are ignored.
                if isinstance(default, bool) != isinstance(value, bool):
                    continue
                if isinstance(default, (int, float)) and isinstance(value, (int, float)):
                    value = type(default)(value)
                elif not isinstance(value, type(default)):
                    continue
                setattr(settings, key, value)
        if "levels" not in data:   # v1: volumes applied while recording -> mix levels
            settings.levels["game"] = settings.system_volume
            settings.levels["mic"] = settings.microphone_volume
        if data.get("encoder", "").startswith("h264_"):   # v1: "h264_amf" -> "amf"
            settings.encoder = data["encoder"][5:]
        elif data.get("encoder") == "libx264":
            settings.encoder = "cpu"
        settings.normalize()
        return settings

    def save(self) -> None:
        self.normalize()
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path())

    def normalize(self) -> None:
        self.clip_seconds = max(MIN_CLIP_SECONDS, min(MAX_CLIP_SECONDS, int(self.clip_seconds)))
        self.fps = max(10, min(240, int(self.fps)))
        self.monitor_index = max(0, int(self.monitor_index))
        extras = []
        for m in self.extra_monitors:
            if isinstance(m, int) and m >= 0 and m != self.monitor_index and m not in extras:
                extras.append(m)
        self.extra_monitors = extras
        self.screen_order = [m for m in dict.fromkeys(self.screen_order) if isinstance(m, int) and m >= 0]
        if self.capture_method not in dict(system.screen_capture_methods()):
            self.capture_method = system.DEFAULT_METHOD
        if self.encoder not in {"auto", "cpu"} | {k for _label, k in system.ENCODER_CHOICES}:
            self.encoder = "auto"
        if self.codec not in CODECS:
            self.codec = "h264"
        if self.quality not in QUALITY_PRESETS:
            self.quality = "high"
        if self.game_mode not in GAME_MODES:
            self.game_mode = "except_chat"
        self.split_app_list = [a for a in dict.fromkeys(str(a).strip() for a in self.split_app_list) if a]
        levels = dict(DEFAULT_LEVELS)
        for k, v in self.levels.items():
            if isinstance(v, (int, float)):
                levels[k] = max(0.0, min(2.0, float(v)))
        self.levels = levels
        self.microphone_volume = max(0.0, min(2.0, float(self.microphone_volume)))
        self.system_volume = max(0.0, min(2.0, float(self.system_volume)))
        self.max_storage_gb = max(0.0, float(self.max_storage_gb))
        self.discord_limit_mb = max(1, min(1000, int(self.discord_limit_mb)))
        self.sound_volume = max(0, min(100, int(self.sound_volume)))
        self.thumb_size = max(180, min(560, int(self.thumb_size)))
        if self.export_encoder not in ("auto", "gpu", "cpu"):
            self.export_encoder = "auto"
        self.accent = LEGACY_ACCENTS.get(self.accent, self.accent)

    def copy(self) -> "Settings":
        return Settings(**copy.deepcopy(asdict(self)))

    def monitors(self) -> list[int]:
        """Recorded screens: the primary one (with the sound) first."""
        return [self.monitor_index] + list(self.extra_monitors)

    def display_order(self, screens: list) -> list[int]:
        """Video streams of a clip ([{monitor, ...}]) in the order chosen in the settings; a screen
        missing from that order stays after the others, in file order."""
        from .edit import display_order

        return display_order(screens, self.screen_order)

    def capture_changed(self, other: "Settings") -> bool:
        """True if a setting that requires restarting the buffer has changed."""
        keys = ("clip_seconds", "fps", "monitor_index", "extra_monitors", "output_height", "capture_method",
                "show_cursor", "encoder", "codec", "quality", "audio_bitrate_kbps")
        return any(getattr(self, k) != getattr(other, k) for k in keys) or audio_tracks(self) != audio_tracks(other)


# Quality -> (CRF/CQ, max bitrate in kb/s for 1080p60). The bitrate is scaled with the
# resolution and the frame rate.
QUALITY_PRESETS: dict[str, tuple[int, int]] = {
    "low": (30, 6000),
    "medium": (26, 10000),
    "high": (22, 16000),
    "ultra": (18, 30000),
}

QUALITY_LABELS = {
    "low": tr("Low (small files)"),
    "medium": tr("Medium"),
    "high": tr("High"),
    "ultra": tr("Ultra"),
}


def app_label(exe: str) -> str:
    """"Discord.exe" -> "Discord"."""
    exe = exe.strip()
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def split_apps(s: Settings) -> list[str]:
    """Apps recorded each on its own track (advanced mode), without the chat app."""
    if not s.split_apps:
        return []
    chat = s.chat_app.strip().lower() if s.capture_chat else ""
    seen, out = set(), []
    for a in s.split_app_list:
        a = a.strip()
        if a and a.lower() != chat and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def audio_tracks(s: Settings) -> list[dict]:
    """Tracks to record, in file order: [{kind, label, source}].

    source: "all" (all the PC sound), "exclude:<exe>,<exe>..." (everything except these apps),
    "app:<exe>" (this app only), "mic:<name>" (microphone, empty = default).
    """
    chat_app = s.chat_app.strip() if s.capture_chat else ""
    apps = split_apps(s)
    tracks = []
    if s.capture_system_audio:
        same_as_chat = chat_app and s.game_app.strip().lower() == chat_app.lower()
        if s.game_mode == "app" and s.game_app.strip() and not same_as_chat:
            source, label = f"app:{s.game_app.strip()}", SOURCE_LABELS["game"]
        else:
            # "A single application" set to the chat app: the voice would also go into "Game";
            # fall back to "everything except the chat".
            excluded = ([chat_app] if chat_app and (s.game_mode != "all" or apps) else []) + apps
            excluded = list(dict.fromkeys(excluded))
            source = "exclude:" + ",".join(excluded) if excluded else "all"
            label = tr("Other sounds") if apps else SOURCE_LABELS["game"]
        tracks.append({"kind": "game", "label": label, "source": source})
    for a in apps:
        tracks.append({"kind": "app", "label": app_label(a), "source": f"app:{a}"})
    if chat_app:
        tracks.append({"kind": "chat", "label": SOURCE_LABELS["chat"], "source": f"app:{chat_app}"})
    if s.capture_microphone:
        tracks.append({"kind": "mic", "label": SOURCE_LABELS["mic"], "source": f"mic:{s.microphone_name}"})
    return tracks


def track_level(s: Settings, track: dict) -> float:
    return s.levels.get(track.get("kind", "other"), 1.0)


def hotkey_problem(text: str) -> str | None:
    """Why this shortcut is invalid ("Unknown key: ..."), or None if it is valid."""
    try:
        system.parse_hotkey(text)
    except ValueError as exc:
        return str(exc)
    return None
