"""Windows implementation of the system layer (see `snappy.platforms`).

Heavy modules (audio capture, WASAPI) are only loaded when needed."""

from .capture import (CAN_CAPTURE, DEFAULT_METHOD, ENCODER_CHOICES, ENCODER_VENDORS, fallback_capture_method,
                      render_node, replay_buffer_class, screen_capture_methods, video_input_args, video_prefilter)
from .process import (BACKGROUND, EXE_SUFFIX, NO_WINDOW, boost_priority, kill_with_parent,
                      kill_with_parent_pid)
from .system import (CHAT_APPS, MANUAL_RECORDING, NAME, OVERLAY_TOAST, TEXT, accent_color, buffer_dir, cache_dir,
                     data_dir,
                     enable_dpi_awareness, foreground_app_info, open_folder, play_wav, reveal, send_to_recycle_bin,
                     set_app_id, set_autostart, steam_dirs, steam_value, system_sounds, videos_dir)
from .update import SELF_UPDATE, SOURCE_ASSET, UPDATE_ASSET, install_update, long_path


def parse_hotkey(text: str) -> tuple[int, int]:
    from .hotkeys import parse_hotkey as f
    return f(text)


def __getattr__(name: str):
    # Qt class: loaded on demand (the audio process does not need Qt).
    if name == "GlobalHotkeys":
        from .hotkeys import GlobalHotkeys
        return GlobalHotkeys
    raise AttributeError(name)


# Audio: imported on demand (comtypes, soundcard, and the services base module).
def audio_pipe_name(counter: int, buffer_dir):
    from .audio import audio_pipe_name as f
    return f(counter, buffer_dir)


def open_audio_pipe(name: str, channels: int):
    from .audio import PipeWriter
    return PipeWriter(name, channels)


def make_capture_source(track: dict, external_plans: bool = False):
    from .audio import make_capture_source as f
    return f(track, external_plans)


def capture_plan(source: str):
    from .audio import capture_plan as f
    return f(source)


def show_overlay_toast(title: str, subtitle: str, ok: bool = True, ms: int = 3500,
                       monitor: str = "", thumb: str = "") -> bool:
    return False   # OVERLAY_TOAST: Snappy places its own toast over the game


def list_microphones() -> list[str]:
    from .audio import list_microphones as f
    return f()


def audio_apps() -> list[tuple[str, str]]:
    from .audio import audio_apps as f
    return f()


def prepare_audio_process() -> None:
    from .audio import prepare_audio_process as f
    f()


__all__ = [
    "NAME", "CAN_CAPTURE", "DEFAULT_METHOD", "data_dir", "cache_dir", "buffer_dir", "videos_dir",
    "enable_dpi_awareness", "set_app_id", "foreground_app_info", "reveal", "open_folder",
    "send_to_recycle_bin", "set_autostart", "accent_color", "steam_value", "steam_dirs", "play_wav",
    "NO_WINDOW", "BACKGROUND", "EXE_SUFFIX", "kill_with_parent", "kill_with_parent_pid", "boost_priority",
    "GlobalHotkeys", "parse_hotkey", "screen_capture_methods", "fallback_capture_method", "video_input_args",
    "video_prefilter", "audio_pipe_name", "open_audio_pipe", "make_capture_source", "capture_plan",
    "list_microphones", "audio_apps", "prepare_audio_process", "TEXT", "CHAT_APPS", "OVERLAY_TOAST", "MANUAL_RECORDING", "show_overlay_toast",
    "system_sounds", "ENCODER_VENDORS", "ENCODER_CHOICES", "render_node", "replay_buffer_class",
    "SELF_UPDATE", "UPDATE_ASSET", "install_update", "long_path", "SOURCE_ASSET",
]
