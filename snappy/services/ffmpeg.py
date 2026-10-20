"""FFmpeg / FFprobe helpers (locating, probing, thumbnails, trimming)."""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import SOURCE_ROOT
from ..i18n import tr
from ..platforms import system

# Subprocess options: no console window; background tasks (thumbnails, exports, probes) at low
# priority, the game comes first.
CREATE_NO_WINDOW = system.NO_WINDOW
BACKGROUND = system.BACKGROUND


class FFmpegNotFound(RuntimeError):
    pass


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):  # PyInstaller executable
        base = Path(sys.executable).parent
        dirs += [base / "ffmpeg" / "bin", base / "ffmpeg", base]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs += [Path(meipass) / "ffmpeg" / "bin", Path(meipass) / "ffmpeg"]
    dirs += [SOURCE_ROOT / "ffmpeg" / "bin", SOURCE_ROOT / "ffmpeg"]
    return dirs


@functools.lru_cache(maxsize=None)
def find_tool(name: str) -> str:
    """Finds ffmpeg/ffprobe: ffmpeg\\bin folder next to the app, then the PATH."""
    env = os.environ.get(f"SNAPPY_{name.upper()}")
    if env and Path(env).is_file():
        return env
    exe = name + system.EXE_SUFFIX
    for d in _candidate_dirs():
        if (d / exe).is_file():
            return str(d / exe)
    found = shutil.which(name)
    if found:
        return found
    if system.NAME == "windows":
        raise FFmpegNotFound(tr("{name} was not found. Put ffmpeg.exe and ffprobe.exe in Snappy's "
                                "\"ffmpeg\\bin\" folder or add them to the PATH.", name=name))
    raise FFmpegNotFound(tr("{name} was not found. Install FFmpeg with your package manager.", name=name))


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


def run(args: list[str], timeout: float | None = 120, check: bool = True,
        background: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=BACKGROUND if background else CREATE_NO_WINDOW,
    )
    if check and proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-8:]
        raise RuntimeError(tr("FFmpeg failed:") + "\n" + "\n".join(tail))
    return proc


def kill_with_parent(proc: subprocess.Popen) -> None:
    """FFmpeg stops with Snappy, even if Snappy crashes (see `platforms`)."""
    system.kill_with_parent(proc)


def probe(path: str | Path) -> dict:
    proc = run([ffprobe(), "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)], timeout=30)
    return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")


def media_info(path: str | Path) -> dict:
    """Summary of a clip: length, frame size, fps, number of video and audio streams."""
    info = probe(path)
    streams = info.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    v = videos[0] if videos else {}
    # Nominal frame rate: the average drops as soon as a frame is skipped while recording (58.1 instead of 60).
    num, _, den = (v.get("r_frame_rate") or v.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den)) if float(den or 0) else 0
    except ValueError:
        fps = 0
    try:
        duration = float(info.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        duration = 0.0
    def name(s):   # name given by Snappy when saving (handler_name of the MP4)
        n = (s.get("tags") or {}).get("handler_name", "")
        return "" if n in ("SoundHandler", "VideoHandler", "") else n

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    return {"duration": duration, "width": int(v.get("width", 0) or 0), "height": int(v.get("height", 0) or 0),
            "fps": fps or 60, "vcodec": v.get("codec_name", ""), "video_streams": len(videos),
            "audio_streams": len(audios), "audio_names": [name(s) for s in audios],
            "video_names": [name(s) for s in videos]}


def probe_duration(path: str | Path) -> float:
    """Real length of the file, in seconds (longest stream)."""
    info = probe(path)
    durations = []
    for stream in info.get("streams", []):
        try:
            durations.append(float(stream["duration"]))
        except (KeyError, ValueError):
            pass
    if not durations:
        try:
            durations.append(float(info["format"]["duration"]))
        except (KeyError, ValueError):
            return 0.0
    return max(durations)


def video_size(path: str | Path) -> tuple[int, int]:
    for stream in probe(path).get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream.get("width", 0)), int(stream.get("height", 0))
    return 0, 0


def make_thumbnail(video: str | Path, out: str | Path, at: float = 1.0, width: int = 480) -> bool:
    """Extracts a frame of the clip for the gallery."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for t in (at, 0.0):
        try:
            run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}",
                 "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2",
                 "-q:v", "4", str(out)], timeout=30, background=True)
            if out.is_file() and out.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def trim(src: str | Path, dst: str | Path, start: float, end: float, accurate: bool = True) -> None:
    """Exports the [start, end] part of a clip.

    accurate=True re-encodes (frame-accurate cut); False copies the streams
    (instant but aligned on keyframes).
    """
    duration = max(0.1, end - start)
    args = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    if accurate:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k"]
    else:
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    args += ["-movflags", "+faststart", str(dst)]
    run(args, timeout=600)


@functools.lru_cache(maxsize=None)
def encoder_works(encoder: str) -> bool:
    """Really tests an encoder (a missing GPU makes nvenc/amf/qsv fail)."""
    try:
        proc = run([ffmpeg(), "-hide_banner", "-loglevel", "error"] + hw_device_args(encoder) +
                   ["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.2",
                    "-vf", upload_filter(encoder), "-c:v", encoder, "-f", "null", "-"],
                   timeout=20, check=False)
        return proc.returncode == 0
    except Exception:
        return False


# FFmpeg graphics card encoders on this system (Windows: nvenc, amf, qsv; Linux: nvenc, vaapi).
VENDORS = tuple(system.ENCODER_VENDORS)
VENDOR_LABELS = {"nvenc": "GPU NVIDIA (NVENC)", "amf": "GPU AMD (AMF)", "qsv": "GPU Intel (Quick Sync)",
                 "vaapi": "GPU (VA-API)", "cpu": tr("Processor")}
CPU_ENCODERS = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"}


def encoder_name(codec: str, vendor: str) -> str:
    return CPU_ENCODERS[codec] if vendor == "cpu" else f"{codec}_{vendor}"


def vendor_of(encoder: str) -> str:
    return next((v for v in VENDORS if encoder.endswith("_" + v)), "cpu")


def codec_of(encoder: str) -> str:
    return next((c for c, e in CPU_ENCODERS.items() if e == encoder), encoder.split("_")[0])


def pick_encoder(codec: str = "h264", preference: str = "auto") -> str:
    """Best available encoder for this codec: the requested graphics card, otherwise the first one
    that works, otherwise the processor. Final fallback: libx264 (H.264)."""
    candidates = [preference] if preference in VENDORS else []
    if preference != "cpu":
        candidates += [v for v in VENDORS if v not in candidates]
    candidates.append("cpu")
    for vendor in candidates:
        enc = encoder_name(codec, vendor)
        if encoder_works(enc):
            return enc
    return "libx264"


@functools.lru_cache(maxsize=None)
def _queue_option_works() -> bool:
    try:
        return run([ffmpeg(), "-v", "error", "-thread_queue_size", "8", "-f", "lavfi", "-i", "anullsrc=d=0.05",
                    "-f", "null", "-"], timeout=20, check=False).returncode == 0
    except Exception:
        return False


def thread_queue_args(size: int) -> list[str]:
    """Queue of an input (avoids losses when a source runs ahead). The most recent FFmpeg builds
    removed this input option: it is then left out."""
    return ["-thread_queue_size", str(size)] if _queue_option_works() else []


def hw_device_args(encoder: str) -> list[str]:
    """Global options the encoder needs (VA-API: the render node of the graphics card)."""
    if vendor_of(encoder) == "vaapi":
        node = system.render_node()
        return ["-vaapi_device", node] if node else []
    return []


def upload_filter(encoder: str) -> str:
    """Last filter before the encoder: the pixel format it expects (VA-API: uploaded to the card)."""
    vendor = vendor_of(encoder)
    if vendor == "cpu":
        return "format=yuv420p"
    return "format=nv12,hwupload" if vendor == "vaapi" else "format=nv12"


def keyframe_args(encoder: str) -> list[str]:
    """Forced keyframes = real IDR frames: without it, low-latency AMF makes only one and the
    "segment" muxer can never cut (the buffer would stay stuck at startup)."""
    vendor = vendor_of(encoder)
    if vendor in ("amf", "qsv"):
        return ["-forced_idr", "1"]
    if vendor == "nvenc" or encoder in ("libx264", "libx265"):
        return ["-forced-idr", "1"]
    return []
