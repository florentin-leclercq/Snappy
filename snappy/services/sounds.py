"""Confirmation sounds (clip saved, recording started), at the chosen volume."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

from ..i18n import tr
from ..models.settings import cache_dir
from ..platforms import system

RATE = 44100

# (key, displayed name, system file or None for a synthesized sound)
SOUNDS = [
    ("shutter", tr("Shutter"), None),
    ("chime", tr("Chime"), None),
    ("pop", tr("Pop"), None),
] + system.system_sounds()


def available() -> list[tuple[str, str]]:
    return [(k, label) for k, label, f in SOUNDS if f is None or f.exists()]


def _synth(key: str) -> list[float]:
    out = []
    if key == "shutter":
        for i in range(int(RATE * 0.12)):
            t = i / RATE
            out.append(math.exp(-t * 45) * (0.6 * math.sin(2 * math.pi * 1800 * t) + 0.4 * math.sin(2 * math.pi * 900 * t)))
    elif key == "chime":   # two notes (A, E)
        n = int(RATE * 0.45)
        for i in range(n):
            t = i / RATE
            v = 0.5 * math.exp(-t * 7) * math.sin(2 * math.pi * 880 * t)
            if t > 0.09:
                v += 0.5 * math.exp(-(t - 0.09) * 7) * math.sin(2 * math.pi * 1318.5 * (t - 0.09))
            out.append(v)
    else:   # pop
        for i in range(int(RATE * 0.08)):
            t = i / RATE
            out.append(math.exp(-t * 60) * math.sin(2 * math.pi * (600 + 2400 * t) * t))
    return out


def _wav(key: str, volume: int) -> Path:
    path = cache_dir() / f"sound-{key}-{volume}.wav"
    if path.exists():
        return path
    gain = (volume / 100) ** 1.5
    src = next((f for k, _l, f in SOUNDS if k == key), None)
    buf = io.BytesIO()
    if src is None:
        samples = _synth(key)
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * gain)) * 26000)) for s in samples))
    elif src.suffix.lower() != ".wav":   # .oga of the Linux sound themes: decoded by FFmpeg
        from . import ffmpeg as ff

        ff.run([ff.ffmpeg(), "-v", "error", "-y", "-i", str(src), "-af", f"volume={gain:.3f}",
                "-c:a", "pcm_s16le", str(path)], timeout=20)
        return path
    else:
        import numpy as np

        with wave.open(str(src), "rb") as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
        if params.sampwidth != 2:
            return src
        data = (np.frombuffer(frames, dtype="<i2").astype(np.float32) * gain).clip(-32768, 32767).astype("<i2")
        with wave.open(buf, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(2)
            w.setframerate(params.framerate)
            w.writeframes(data.tobytes())
    path.write_bytes(buf.getvalue())
    return path


def play(key: str, volume: int = 70) -> None:
    if volume <= 0:
        return
    if key not in dict(available()):
        key = "shutter"
    try:
        path = _wav(key, int(volume))
    except Exception:
        return
    system.play_wav(path)
