"""Neutral fallback of the system layer, for systems without a dedicated module (macOS...).

It offers the same functions as `platforms.windows`, with standard behaviors (XDG folders,
xdg-open / open, FIFO pipe) or none at all. Screen and audio capture are not available: the
buffer then runs on test sources. It is the starting point of a port: a `platforms/macos/`
module would replace these fallbacks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..i18n import tr

NAME = sys.platform
CAN_CAPTURE = False
DEFAULT_METHOD = "ddagrab"
EXE_SUFFIX = ""
NO_WINDOW = 0
BACKGROUND = 0
ENCODER_VENDORS: tuple[str, ...] = ()
ENCODER_CHOICES: list[tuple[str, str]] = []
CHAT_APPS = ["Discord", "TeamSpeak", "Mumble"]
OVERLAY_TOAST = True
MANUAL_RECORDING = False   # manual recording (start / stop) offered next to the replay
TEXT = {
    "os": tr("the system"),
    "session_start": tr("at login"),
    "primary_screen": tr("primary screen"),
    "accent": tr("System color"),
    "accent_follow": tr("the system's"),
    "app_example": tr("Application name"),
    "split_placeholder": tr("Add an application by its name…"),
    "capture_hint": "",
    "manual_launch": tr("shortcut, run.sh"),
    "trash": tr("trash"),
    "reveal": tr("Show in folder"),
}


def render_node() -> str | None:
    return None


def replay_buffer_class():
    from ..services.recorder import ReplayBuffer
    return ReplayBuffer


def system_sounds() -> list:
    return []


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------
def data_dir(app: str) -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / app


def cache_dir(app: str) -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / app / "cache"


def buffer_dir(app: str) -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / app / "buffer"


def videos_dir() -> Path:
    return Path.home() / "Videos"


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    pass


def set_app_id() -> None:
    pass


def foreground_app_info() -> tuple[str, str]:
    return "", ""


def _opener() -> str:
    return "open" if sys.platform == "darwin" else "xdg-open"


def reveal(path: Path) -> None:
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([_opener(), str(path)])


def send_to_recycle_bin(paths: list[Path]) -> bool:
    """No known trash: permanent deletion."""
    for p in paths:
        try:
            Path(p).unlink()
        except OSError:
            return False
    return True


def set_autostart(enabled: bool, minimized: bool = True) -> None:
    pass


def accent_color() -> str:
    return "#0078d4"


def steam_value(name: str):
    return None


def steam_dirs() -> list[str]:
    return [os.path.expanduser("~/.local/share/Steam")]


def play_wav(path: Path) -> None:
    pass


def show_overlay_toast(title: str, subtitle: str, ok: bool = True, ms: int = 3500,
                       monitor: str = "", thumb: str = "") -> bool:
    """Toast over the game where OVERLAY_TOAST is False (Wayland); False: not possible here."""
    return False


# --------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------
def kill_with_parent(proc) -> None:
    pass


def kill_with_parent_pid(pid: int) -> None:
    pass


def boost_priority() -> None:
    pass


# --------------------------------------------------------------------------
# Keyboard shortcuts (validated, never registered)
# --------------------------------------------------------------------------
_MODIFIERS = {"alt", "ctrl", "control", "shift", "maj", "win", "meta"}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(tr("Empty shortcut"))
    unknown = [p for p in parts[:-1] if p.lower() not in _MODIFIERS]
    if unknown:
        raise ValueError(tr("Unknown modifier: {key}", key=unknown[0]))
    if parts[-1].lower() in _MODIFIERS:
        raise ValueError(tr("The shortcut must end with a key, not a modifier"))
    return 0, 0


class GlobalHotkeys(QObject):
    triggered = Signal(str)

    def register(self, name: str, text: str) -> None:
        parse_hotkey(text)

    def unregister(self, name: str) -> None:
        pass

    def unregister_all(self) -> None:
        pass


# --------------------------------------------------------------------------
# Capture (test sources only)
# --------------------------------------------------------------------------
def screen_capture_methods() -> list[tuple[str, str]]:
    return [("ddagrab", tr("Screen capture unavailable on this system"))]


def fallback_capture_method(method: str) -> str | None:
    return None


def video_input_args(method: str, monitor: int, fps: int, show_cursor: bool, geometry) -> list[str]:
    raise RuntimeError(tr("Screen capture unavailable on this system"))


def video_prefilter(method: str) -> list[str]:
    return []


def audio_pipe_name(counter: int, buffer_dir: Path) -> str:
    return str(Path(buffer_dir) / f"audio_{counter}.pcm")


class _FifoWriter:
    """Write end of a FIFO that FFmpeg opens for reading."""

    def __init__(self, name: str, channels: int = 2):
        self.name = name
        self._closed = False
        if os.path.exists(name):
            os.unlink(name)
        os.mkfifo(name)
        self._fd = None

    def wait_for_reader(self) -> bool:
        self._fd = os.open(self.name, os.O_WRONLY)
        return not self._closed

    def write(self, data: bytes) -> None:
        if self._closed:
            raise BrokenPipeError
        view = memoryview(data)
        while view:
            n = os.write(self._fd, view)
            view = view[n:]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._fd is None:
                # Unblocks os.open(O_WRONLY) by briefly opening for reading.
                rfd = os.open(self.name, os.O_RDONLY | os.O_NONBLOCK)
                os.close(rfd)
            else:
                os.close(self._fd)
        except OSError:
            pass
        try:
            os.unlink(self.name)
        except OSError:
            pass


def open_audio_pipe(name: str, channels: int):
    return _FifoWriter(name, channels)


def make_capture_source(track: dict, external_plans: bool = False):
    raise RuntimeError(tr("Audio capture unavailable on this system"))


def capture_plan(source: str) -> list:
    return []


def list_microphones() -> list[str]:
    return []


def audio_apps() -> list[tuple[str, str]]:
    return []


def prepare_audio_process() -> None:
    pass
