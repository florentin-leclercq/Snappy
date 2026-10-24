"""Linux implementation of the system layer (see `snappy.platforms`).

Capture: gpu-screen-recorder (screen and PipeWire sound, replay kept in memory).
Desktop: XDG folders, freedesktop trash, ~/.config/autostart, KDE / GNOME / X11 shortcuts."""

from .capture import (CAN_CAPTURE, DEFAULT_METHOD, MANUAL_RECORDING, DIRECT_AUDIO, ENCODER_CHOICES, ENCODER_VENDORS, audio_apps,
                      audio_input_args, audio_pipe_name,
                      capture_plan, fallback_capture_method, list_microphones, make_capture_source, open_audio_pipe,
                      prepare_audio_process, render_node, replay_buffer_class, screen_capture_methods,
                      video_input_args, video_prefilter)
from ..generic import SELF_UPDATE, UPDATE_ASSET, install_update, long_path   # updates: git clone (update.py)
from .process import BACKGROUND, EXE_SUFFIX, NO_WINDOW, boost_priority, kill_with_parent, kill_with_parent_pid
from .system import (CHAT_APPS, NAME, OVERLAY_TOAST, TEXT, accent_color, buffer_dir, cache_dir, data_dir,
                     enable_dpi_awareness, foreground_app_info, open_folder, play_wav, reveal, send_to_recycle_bin,
                     set_app_id, set_autostart, show_overlay_toast, steam_dirs, steam_value, system_sounds,
                     videos_dir)


def parse_hotkey(text: str):
    from .hotkeys import parse_hotkey as f
    return f(text)


def __getattr__(name: str):
    # Qt class: loaded on demand.
    if name == "GlobalHotkeys":
        from .hotkeys import GlobalHotkeys
        return GlobalHotkeys
    raise AttributeError(name)


__all__ = [
    "NAME", "CAN_CAPTURE", "DEFAULT_METHOD", "data_dir", "cache_dir", "buffer_dir", "videos_dir",
    "enable_dpi_awareness", "set_app_id", "foreground_app_info", "reveal", "open_folder",
    "send_to_recycle_bin", "set_autostart", "accent_color", "steam_value", "steam_dirs", "play_wav",
    "NO_WINDOW", "BACKGROUND", "EXE_SUFFIX", "kill_with_parent", "kill_with_parent_pid", "boost_priority",
    "GlobalHotkeys", "parse_hotkey", "screen_capture_methods", "fallback_capture_method", "video_input_args",
    "video_prefilter", "audio_pipe_name", "open_audio_pipe", "make_capture_source", "capture_plan",
    "list_microphones", "audio_apps", "prepare_audio_process", "TEXT", "CHAT_APPS", "OVERLAY_TOAST",
    "system_sounds", "ENCODER_VENDORS", "ENCODER_CHOICES", "render_node", "replay_buffer_class",
    "DIRECT_AUDIO", "audio_input_args", "show_overlay_toast", "MANUAL_RECORDING",
    "SELF_UPDATE", "UPDATE_ASSET", "install_update", "long_path",
]
