"""Linux: screen and audio capture by gpu-screen-recorder.

gpu-screen-recorder records the screen on the graphics card (KMS or the desktop portal on
Wayland, X11 otherwise), encodes with NVENC / VA-API and keeps the replay in memory itself. Sound
goes through PipeWire: each -a option is one track, per application ("app:vesktop"), everything
except some applications ("app-inverse:..."), the default output or input, or a device. Nothing is
rerouted: the audio outputs stay untouched.

Without graphics acceleration (virtual machine...), gpu-screen-recorder does not start: on X11,
Snappy then falls back to its FFmpeg engine (x11grab for the screen, PulseAudio / PipeWire for the
sound), without splitting the sound per application.
"""

from __future__ import annotations

import functools
import logging
import glob
import os
import shlex
import shutil
import subprocess

from ...i18n import tr
from .. import generic

log = logging.getLogger(__name__)

CAN_CAPTURE = True
# Manual recording (start / stop): gpu-screen-recorder's command socket, or the FFmpeg ring's segments.
MANUAL_RECORDING = True
DEFAULT_METHOD = "kms"
# FFmpeg graphics card encoders (exports); the buffer itself encodes with gpu-screen-recorder.
ENCODER_VENDORS = ("nvenc", "vaapi")
ENCODER_CHOICES = [(tr("Graphics card"), "gpu")]
FLATPAK_ID = "com.dec05eba.gpu_screen_recorder"

# FFmpeg engine ("x11" method, --demo mode): FFmpeg reads the sound itself (one input per track).
DIRECT_AUDIO = True
fallback_capture_method = generic.fallback_capture_method
video_prefilter = generic.video_prefilter
audio_pipe_name = generic.audio_pipe_name
open_audio_pipe = generic.open_audio_pipe
make_capture_source = generic.make_capture_source
capture_plan = generic.capture_plan
prepare_audio_process = generic.prepare_audio_process


def is_x11() -> bool:
    return bool(os.environ.get("DISPLAY")) and (os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11"
                                                or not os.environ.get("WAYLAND_DISPLAY"))


def screen_capture_methods() -> list[tuple[str, str]]:
    methods = [("kms", tr("Direct screen capture (recommended)")),
               ("portal", tr("Desktop portal (asks for permission, one screen)"))]
    if is_x11():
        methods.append(("x11", tr("FFmpeg X11 (no graphics card: virtual machine…; sound not split per app)")))
    return methods


def video_input_args(method: str, monitor: int, fps: int, show_cursor: bool,
                     geometry: tuple[int, int, int, int] | None) -> list[str]:
    """FFmpeg engine: X11 capture of a screen (geometry: x, y, w, h in pixels)."""
    from ...services.ffmpeg import thread_queue_args

    args = ["-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1" if show_cursor else "0"]
    args += thread_queue_args(512)
    display = os.environ.get("DISPLAY", ":0")
    if geometry:
        x, y, w, h = geometry
        args += ["-video_size", f"{w}x{h}", "-i", f"{display}+{x},{y}"]
    else:
        args += ["-i", display]
    return args


@functools.lru_cache(maxsize=1)
def _ffmpeg_has_pulse() -> bool:
    from ...services import ffmpeg as ff

    try:
        out = ff.run([ff.ffmpeg(), "-hide_banner", "-devices"], timeout=10, check=False).stdout.decode("utf-8", "replace")
    except Exception:
        return False
    return any(line.split()[1:2] == ["pulse"] and "D" in line.split()[0] for line in out.splitlines() if line.strip())


def audio_input_args(track: dict) -> list[str]:
    """FFmpeg engine: input of a track. All the sound (or "all except...") = monitor of the default
    output, microphone = PulseAudio source; a single application cannot be split out: silence."""
    from ...services.ffmpeg import thread_queue_args

    source = track["source"]
    if source.startswith("mic:"):
        device = source[4:].strip() or "default"
    elif source == "all" or source.startswith("exclude:"):
        device = "@DEFAULT_MONITOR@"
    else:
        device = ""
    if device and _ffmpeg_has_pulse():
        return ["-f", "pulse", "-sample_rate", "48000", "-channels", "2"] + thread_queue_args(1024) + ["-i", device]
    return ["-re", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]


def render_node() -> str | None:
    """Render node of the graphics card, for VA-API."""
    nodes = sorted(glob.glob("/dev/dri/renderD*"))
    return nodes[0] if nodes else None


@functools.lru_cache(maxsize=1)
def gsr_command() -> tuple[str, ...] | None:
    """gpu-screen-recorder command: SNAPPY_GSR, the PATH, or the Flatpak."""
    env = os.environ.get("SNAPPY_GSR")
    if env:
        return tuple(shlex.split(env))
    path = shutil.which("gpu-screen-recorder")
    if path:
        return (path,)
    if shutil.which("flatpak"):
        try:
            if subprocess.run(["flatpak", "info", FLATPAK_ID], capture_output=True, timeout=10).returncode == 0:
                return ("flatpak", "run", "--command=gpu-screen-recorder", FLATPAK_ID)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def install_hint() -> str:
    return tr("gpu-screen-recorder was not found: install it (Arch: pacman -S gpu-screen-recorder; "
              "other distributions: Flatpak {flatpak}), then restart Snappy.", flatpak=FLATPAK_ID)


def _gsr_lines(*args: str) -> list[str]:
    cmd = gsr_command()
    if not cmd:
        return []
    try:
        out = subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def list_monitors() -> list[tuple[str, str]]:
    """[(name, "2560x1440")] of the screens gpu-screen-recorder can record."""
    lines = _gsr_lines("--list-capture-options") or _gsr_lines("--list-monitors")
    out = []
    for line in lines:
        name, sep, size = line.partition("|")
        # gpu-screen-recorder 6 also lists the cameras ("/dev/video0|1920x1080@30hz|mjpeg").
        if sep and name not in ("window", "region", "focused", "portal", "screen") and not name.startswith("/dev/"):
            out.append((name, size))
    return out


def audio_devices() -> list[tuple[str, str]]:
    """[(device, description)] that gpu-screen-recorder accepts after "device:" (or as they are
    for default_output / default_input, only listed when PipeWire declares one)."""
    out = []
    for line in _gsr_lines("--list-audio-devices"):
        name, _, desc = line.partition("|")
        if name:
            out.append((name, desc or name))
    return out


def _is_input(name: str) -> bool:
    return not (name in ("default_output", "default_input") or name.endswith(".monitor")
                or "output" in name.split(".")[0])


def list_microphones() -> list[tuple[str, str]]:
    """[(displayed name, PipeWire device)] of the audio inputs."""
    return [(desc, name) for name, desc in audio_devices() if _is_input(name)]


_IGNORED_STREAMS = {"python3", "python", "snappy", "gpu-screen-recorder", "pw-play", "paplay"}


def audio_apps() -> list[tuple[str, str]]:
    """[(PipeWire name, displayed name)] of the applications playing sound right now."""
    seen, out = set(), []
    for name in _gsr_lines("--list-application-audio"):
        if name.lower() in _IGNORED_STREAMS or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append((name, name))
    return out


def gsr_audio_arg(source: str) -> str:
    """Source of a Snappy track (see models.settings.audio_tracks) -> -a argument."""
    if source == "all":
        return "default_output"
    if source.startswith("exclude:"):
        apps = [a.strip() for a in source[len("exclude:"):].split(",") if a.strip()]
        return "|".join(f"app-inverse:{a}" for a in apps) if apps else "default_output"
    if source.startswith("app:"):
        return source
    if source.startswith("mic:"):
        dev = source[len("mic:"):].strip()
        return f"device:{dev}" if dev else "default_input"
    return source


def resolve_audio_arg(arg: str, devices: list[str]) -> str:
    """-a argument checked against the devices gpu-screen-recorder sees: it refuses to start with
    an unknown source. Without a declared default input / output, the first real input (or the
    first output monitor) is taken; an unplugged microphone falls back to the default one. Empty
    devices (list unavailable): nothing is changed."""
    if not devices:
        return arg
    inputs = [d for d in devices if _is_input(d)]
    # "mono-fallback" inputs (USB headsets) last: the stereo profile is better when it exists.
    inputs.sort(key=lambda d: "mono-fallback" in d)
    monitors = [d for d in devices if d.endswith(".monitor")]

    def one(part: str) -> str:
        if part.startswith("device:") and part[len("device:"):] not in devices:
            log.warning("Microphone %s missing: default microphone", part[len("device:"):])
            part = "default_input"
        if part == "default_input" and part not in devices:
            return f"device:{inputs[0]}" if inputs else part
        if part == "default_output" and part not in devices:
            return f"device:{monitors[0]}" if monitors else part
        return part

    return "|".join(one(p) for p in arg.split("|"))


def replay_buffer_class():
    from .replay import LinuxReplayBuffer
    return LinuxReplayBuffer
