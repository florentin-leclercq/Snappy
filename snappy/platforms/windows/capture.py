"""Windows: screen capture by FFmpeg (Desktop Duplication or GDI)."""

from __future__ import annotations

from ...i18n import tr

CAN_CAPTURE = True
DEFAULT_METHOD = "ddagrab"
# FFmpeg graphics card encoders (exports) and the choices offered for the buffer.
ENCODER_VENDORS = ("nvenc", "amf", "qsv")
ENCODER_CHOICES = [("NVIDIA NVENC", "nvenc"), ("AMD AMF", "amf"), ("Intel Quick Sync", "qsv")]


def render_node() -> str | None:
    """Render node of the graphics card (VA-API, Linux only)."""
    return None


def replay_buffer_class():
    """Replay buffer engine: FFmpeg (Desktop Duplication + WASAPI)."""
    from ...services.recorder import ReplayBuffer
    return ReplayBuffer


def screen_capture_methods() -> list[tuple[str, str]]:
    """[(key, label)] of the methods offered in the settings, fastest first."""
    return [("ddagrab", tr("Desktop Duplication (fast)")), ("gdigrab", tr("GDI (compatibility)"))]


def fallback_capture_method(method: str) -> str | None:
    """Fallback method when this one fails (old drivers, remote desktop...)."""
    return "gdigrab" if method == "ddagrab" else None


def video_input_args(method: str, monitor: int, fps: int, show_cursor: bool,
                     geometry: tuple[int, int, int, int] | None) -> list[str]:
    """FFmpeg input arguments to record a screen (geometry: x, y, w, h in physical pixels)."""
    if method == "ddagrab":
        return ["-f", "lavfi", "-i",
                f"ddagrab=output_idx={monitor}:framerate={fps}:draw_mouse={1 if show_cursor else 0}"]
    from ...services.ffmpeg import thread_queue_args

    args = ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1" if show_cursor else "0"] + thread_queue_args(512)
    if geometry:
        x, y, w, h = geometry
        args += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
    return args + ["-i", "desktop"]


def video_prefilter(method: str) -> list[str]:
    """Filters to put first: Desktop Duplication delivers frames on the graphics card."""
    return ["hwdownload", "format=bgra"] if method == "ddagrab" else []
