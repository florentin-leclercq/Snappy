"""FFmpeg editing and export (no Qt): removed passages, multi-screen edit, per-track volume
(+ curves), loudness normalization, GPU encoding with processor fallback.

Everything that builds a command is made of pure (tested) functions; `Exporter` runs the command
in a thread and reports progress. The edit itself (excerpt, cuts, screen switches) is described
by `models.edit`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from ..i18n import tr
from ..models.settings import cache_dir

log = logging.getLogger(__name__)

def envelope_expr(points: list, gain: float, offset: float) -> str:
    """FFmpeg expression (function of t) for a piecewise linear volume curve.
    t is the output time after "-ss offset"; the points are in original clip time."""
    T = f"(t+{offset:.4f})"
    if not points:
        return f"{gain:.4f}"
    pts = sorted(points)
    expr = f"{pts[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        span = max(t1 - t0, 1e-3)
        seg = f"{v0:.4f}+({v1 - v0:.4f})*({T}-{t0:.4f})/{span:.4f}"
        expr = f"if(lt({T},{t1:.4f}),{seg},{expr})"
    expr = f"if(lt({T},{pts[0][0]:.4f}),{pts[0][1]:.4f},{expr})"
    return f"{gain:.4f}*({expr})"


@dataclass
class ExportSpec:
    src: str
    out: str
    segments: list                   # [(start, end)] kept, in original time, sorted
    tracks: list                     # [{"stream", "gain", "muted", "points"}]: separate tracks
    height: int = 0                  # 0 = original
    fps: int = 0                     # 0 = original
    codec: str = "h264"
    target_mb: float = 0             # 0 = constant quality (qp)
    qp: int = 22
    copy: bool = False               # "Original": video copied, only the mix is encoded
    normalize: bool = False
    audio_only: bool = False         # MP3 of the mix
    plan: list | None = None         # multi-screen: [(start, end, screen)]
    screen: int = 0                  # a single screen (without a plan)
    n_screens: int = 1
    src_size: tuple = (1920, 1080)   # original frame size
    src_fps: int = 60

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.segments)

    def active(self) -> list[tuple[int, dict]]:
        return [(i, t) for i, t in enumerate(self.tracks) if not t["muted"] and (t["gain"] > 0 or t["points"])]


def audio_graph(spec: ExportSpec, norm_gain: float | None = None, limit: bool = False) -> tuple[list[str], str | None]:
    """Volume (or curve) per track -> mix -> cutting the passages -> normalization gain."""
    active = spec.active()
    if not active:
        return [], None
    A = spec.segments[0][0]
    graph = [f"[0:a:{t['stream']}]volume='{envelope_expr(t['points'], t['gain'], A)}':eval=frame[t{i}]"
             for i, t in active]
    if len(active) > 1:
        graph.append("".join(f"[t{i}]" for i, _ in active) +
                     f"amix=inputs={len(active)}:normalize=0:dropout_transition=0[amix]")
        label = "[amix]"
    else:
        label = f"[t{active[0][0]}]"
    n = len(spec.segments)
    if n > 1:
        graph.append(f"{label}asplit={n}" + "".join(f"[as{k}]" for k in range(n)))
        for k, (s, e) in enumerate(spec.segments):
            graph.append(f"[as{k}]atrim=start={s - A:.4f}:end={e - A:.4f},asetpts=PTS-STARTPTS[a{k}]")
        graph.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[acat]")
        label = "[acat]"
    if norm_gain:
        lim = ",alimiter=limit=0.891:level=0:attack=5:release=50" if limit else ""
        graph.append(f"{label}volume={norm_gain:.2f}dB{lim}[anorm]")
        label = "[anorm]"
    return graph, label


def _rate_args(spec: ExportSpec, encoder: str, bitrate: int) -> list[str]:
    vendor = ff.vendor_of(encoder)
    q = str(spec.qp)
    vbr = ["-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.3)), "-bufsize", str(bitrate * 2)]
    if vendor == "nvenc":
        return ["-preset", "p5"] + (["-rc", "vbr"] + vbr if bitrate else ["-rc", "vbr", "-cq", q, "-b:v", "0"])
    if vendor == "amf":
        base = ["-quality", "quality"]
        return base + (["-rc", "vbr_peak"] + vbr if bitrate else ["-rc", "cqp", "-qp_i", q, "-qp_p", q])
    if vendor == "qsv":
        return ["-preset", "medium"] + (vbr if bitrate else ["-global_quality", q])
    if vendor == "vaapi":
        return ["-rc_mode", "VBR"] + vbr if bitrate else ["-rc_mode", "CQP", "-qp", q]
    preset = ["-preset", "veryfast"] if encoder == "libx264" else ["-preset", "fast"]
    return preset + (vbr if bitrate else ["-crf", q])


def video_bitrate(spec: ExportSpec, scale: float = 1.0) -> int:
    if not spec.target_mb:
        return 0
    audio = 160_000 if spec.active() else 0
    total = spec.target_mb * 1e6 * 8 * 0.94 / max(spec.duration, 0.1)
    return max(int((total - audio) * scale), 250_000)


def build_command(spec: ExportSpec, encoder: str | None, norm_gain: float | None = None, limit: bool = False,
                  bitrate_scale: float = 1.0) -> list[str]:
    """Export command. encoder=None: "Original" (copy) or MP3 depending on spec."""
    A, B = spec.segments[0][0], spec.segments[-1][1]
    a = [ff.ffmpeg(), "-v", "error", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
         "-ss", f"{A:.3f}", "-t", f"{B - A:.3f}", "-i", spec.src]
    if spec.audio_only:
        graph, label = audio_graph(spec, norm_gain, limit)
        if not label:
            raise ValueError(tr("No active audio track"))
        return a + ["-filter_complex", ";".join(graph), "-map", label, "-c:a", "libmp3lame", "-b:a", "192k", spec.out]

    if spec.copy:
        active = spec.active()
        if active:
            graph, label = audio_graph(spec)
            a += ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", label]
            if len(spec.tracks) > 1:   # mix as the 1st track, untouched separate tracks after it
                for t in spec.tracks:
                    a += ["-map", f"0:a:{t['stream']}"]
            a += ["-c:v", "copy", "-c:a", "copy", "-c:a:0", "aac", "-b:a:0", "192k",
                  "-metadata:s:a:0", "handler_name=Mix", "-disposition:a:0", "default"]
            for k in range(1, len(spec.tracks) + 1 if len(spec.tracks) > 1 else 1):
                a += [f"-disposition:a:{k}", "0"]
        else:
            a += ["-map", "0:v", "-c", "copy"]
        return a + ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", spec.out]

    assert encoder
    a[1:1] = ff.hw_device_args(encoder)
    graph, audio = audio_graph(spec, norm_gain, limit)
    W0, H0 = spec.src_size
    H = spec.height or H0
    W = int(round(H * W0 / max(H0, 1) / 2)) * 2
    fps = spec.fps or spec.src_fps
    rel = [(s - A, e - A) for s, e in spec.segments]
    plan = spec.plan
    if plan and len({k for _s, _e, k in plan}) > 1:
        # Multicam edit: each piece comes from its screen, scaled to the clip size, then joined.
        uses: dict[int, list[int]] = {}
        for i, (_s, _e, k) in enumerate(plan):
            uses.setdefault(k, []).append(i)
        labels = {}
        for k, idx in uses.items():
            if len(idx) > 1:
                graph.append(f"[0:v:{k}]split={len(idx)}" + "".join(f"[sv{i}]" for i in idx))
                labels.update({i: f"[sv{i}]" for i in idx})
            else:
                labels[idx[0]] = f"[0:v:{k}]"
        for i, (s, e, _k) in enumerate(plan):
            graph.append(f"{labels[i]}trim=start={s - A:.4f}:end={e - A:.4f},setpts=PTS-STARTPTS,fps={fps},"
                         f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic,"
                         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[p{i}]")
        graph.append("".join(f"[p{i}]" for i in range(len(plan))) + f"concat=n={len(plan)}:v=1:a=0[vc]")
        video, chain = "[vc]", []
    else:
        k = plan[0][2] if plan else spec.screen
        video = f"[0:v:{k}]"
        if len(rel) > 1:
            graph.append(f"{video}split={len(rel)}" + "".join(f"[vs{j}]" for j in range(len(rel))))
            for j, (s, e) in enumerate(rel):
                graph.append(f"[vs{j}]trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS[v{j}]")
            graph.append("".join(f"[v{j}]" for j in range(len(rel))) + f"concat=n={len(rel)}:v=1:a=0[vc]")
            video = "[vc]"
        chain = []
        if spec.fps:
            chain.append(f"fps={spec.fps}")
        if spec.height:
            chain.append(f"scale=-2:{spec.height}:flags=bicubic")
    chain.append(ff.upload_filter(encoder))
    graph.append(f"{video}{','.join(chain)}[vout]")
    a += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    if audio:
        a += ["-map", audio, "-c:a", "aac", "-b:a", "160k"]
    a += ["-c:v", encoder] + _rate_args(spec, encoder, video_bitrate(spec, bitrate_scale))
    return a + ["-movflags", "+faststart", spec.out]


def loudness_command(spec: ExportSpec, target: float, true_peak: float) -> list[str] | None:
    graph, label = audio_graph(spec)
    if not label:
        return None
    A, B = spec.segments[0][0], spec.segments[-1][1]
    return [ff.ffmpeg(), "-v", "info", "-nostdin", "-hide_banner", "-ss", f"{A:.3f}", "-t", f"{B - A:.3f}",
            "-i", spec.src, "-filter_complex",
            ";".join(graph) + f";{label}loudnorm=I={target}:TP={true_peak}:print_format=json[an]",
            "-map", "[an]", "-f", "null", "-"]


# ---------------------------------------------------------------- encoders (benchmark)
class EncoderDetector:
    """Tests once which graphics card encoder works for each codec, then times it against the
    processor on a short 1440p60 sample: the fastest one is used for exports ("Automatic"
    setting). The buffer already keeps the card's encoder busy: depending on the machine, the
    processor can win. Results are cached."""

    CODECS = ("h264", "hevc", "av1")

    def __init__(self):
        self.path = cache_dir() / "encoders.json"
        self.best: dict[str, str] = {}      # GPU encoder that works (or processor)
        self.fastest: dict[str, str] = {}
        self.timings: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.signature = ""
        try:
            exe = ff.ffmpeg()
            self.signature = f"{exe}|{os.path.getmtime(exe)}"
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("signature") == self.signature:
                self.best, self.fastest, self.timings = data["best"], data["fastest"], data.get("timings", {})
        except (OSError, ValueError, KeyError, ff.FFmpegNotFound):
            pass

    @property
    def ready(self) -> bool:
        return all(c in self.fastest for c in self.CODECS)

    def get(self, codec: str, preference: str = "auto") -> str:
        cpu = ff.CPU_ENCODERS.get(codec, "libx264")
        if not ff.encoder_works(cpu):
            cpu = "libx264"
        if preference == "cpu":
            return cpu
        if preference == "gpu":
            return self.best.get(codec) or cpu
        return self.fastest.get(codec) or self.best.get(codec) or cpu

    def detect(self) -> None:
        """Blocking (a few seconds the first time): to be run in a thread."""
        with self._lock:
            if self.ready:
                return
            sample = cache_dir() / "bench-1440p60.mp4"
            try:
                for codec in self.CODECS:
                    if codec in self.fastest:
                        continue
                    gpu = next((ff.encoder_name(codec, v) for v in ff.VENDORS
                                if ff.encoder_works(ff.encoder_name(codec, v))), None)
                    cpu = ff.CPU_ENCODERS[codec] if ff.encoder_works(ff.CPU_ENCODERS[codec]) else None
                    self.best[codec] = gpu or cpu or "libx264"
                    if not gpu or not cpu:
                        self.fastest[codec] = self.best[codec]
                        continue
                    if not sample.exists():
                        ff.run([ff.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                                "testsrc2=s=2560x1440:r=60,noise=alls=10:allf=t", "-t", "2", "-c:v", "libx264",
                                "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", str(sample)],
                               timeout=120, background=True)
                    times = {}
                    for which, enc in (("gpu", gpu), ("cpu", cpu)):
                        t0 = time.monotonic()
                        r = ff.run([ff.ffmpeg(), "-v", "error"] + ff.hw_device_args(enc) +
                                   ["-i", str(sample), "-vf", ff.upload_filter(enc), "-c:v", enc, "-f", "null", "-"],
                                   timeout=300, check=False, background=True)
                        times[which] = time.monotonic() - t0 if r.returncode == 0 else float("inf")
                    self.timings[codec] = {k: round(v, 2) for k, v in times.items() if v != float("inf")}
                    # the processor only when clearly faster (it heats more and takes over the machine)
                    self.fastest[codec] = cpu if times["cpu"] < times["gpu"] * 0.8 else gpu
            except Exception:
                log.exception("Encoder benchmark")
                return
            finally:
                try:
                    sample.unlink()
                except OSError:
                    pass
            try:
                self.path.write_text(json.dumps({"signature": self.signature, "best": self.best,
                                                 "fastest": self.fastest, "timings": self.timings}), encoding="utf-8")
            except OSError:
                pass


ENCODERS = EncoderDetector()


# ---------------------------------------------------------------- running
class Exporter:
    """Runs an export in a thread. on_progress(fraction 0-1, step); on_done(ok, error)."""

    TARGET_LUFS = -14.0   # usual YouTube / social media level
    TRUE_PEAK = -1.0      # anti-clipping margin (dBTP)
    LIMITER_DB = 6.0      # maximum gain handed to the limiter
    force_cpu = False     # a GPU encoder failed during this session

    def __init__(self, spec: ExportSpec, encoder_pref: str = "auto",
                 on_progress: Callable[[float, str], None] | None = None,
                 on_done: Callable[[bool, str], None] | None = None):
        self.spec = spec
        self.encoder_pref = encoder_pref
        self.on_progress = on_progress or (lambda f, s: None)
        self.on_done = on_done or (lambda ok, err: None)
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.norm_gain: float | None = None
        self.limit = False
        self.encoder = ""

    def start(self) -> None:
        threading.Thread(target=self._run_safe, name="export", daemon=True).start()

    def cancel(self) -> None:
        self.cancelled = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.kill()

    def _run_safe(self) -> None:
        try:
            ok, err = self.run()
        except Exception as exc:
            log.exception("Export")
            ok, err = False, str(exc)
        if not ok:
            try:
                os.remove(self.spec.out)
            except OSError:
                pass
        self.on_done(ok, "" if self.cancelled else err)

    def run(self) -> tuple[bool, str]:
        spec = self.spec
        if spec.normalize and not spec.copy and spec.active() and self.norm_gain is None:
            self.on_progress(0.0, tr("analyzing the sound"))
            self.analyze()
        if self.cancelled:
            return False, ""
        if spec.audio_only or spec.copy:
            return self._encode(None, 1.0)
        ENCODERS.detect()
        encoder = ff.CPU_ENCODERS.get(spec.codec, "libx264") if Exporter.force_cpu else \
            ENCODERS.get(spec.codec, self.encoder_pref)
        if not ff.encoder_works(encoder):
            encoder = "libx264"
        scale = 1.0
        for attempt in range(3):
            self.encoder = encoder
            ok, err = self._encode(encoder, scale)
            if self.cancelled:
                return False, ""
            if not ok:
                if ff.vendor_of(encoder) != "cpu":   # processor fallback for the session
                    log.warning("GPU export failed (%s), falling back to the processor", err)
                    Exporter.force_cpu = True
                    encoder = ff.CPU_ENCODERS.get(spec.codec, "libx264")
                    if not ff.encoder_works(encoder):
                        encoder = "libx264"
                    continue
                return False, err
            size = os.path.getsize(spec.out)
            if spec.target_mb and size > spec.target_mb * 1e6 and attempt < 2:
                # The bitrate of GPU encoders is not exact: try again a bit lower.
                scale *= (spec.target_mb * 1e6) / size * 0.92
                continue
            return True, ""
        return True, ""

    def analyze(self) -> None:
        """Pass 1: measures the mix (loudnorm) -> fixed gain = target - level, capped by the peak.
        A fixed gain does not "pump", unlike a dynamic normalization."""
        cmd = loudness_command(self.spec, self.TARGET_LUFS, self.TRUE_PEAK)
        self.norm_gain = 0.0
        if not cmd:
            return
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     creationflags=ff.BACKGROUND)
        _, err = self.proc.communicate()
        text = err.decode("utf-8", "replace")
        try:
            js = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
            i, tp = float(js["input_i"]), float(js["input_tp"])
            if i > -70:
                # Up to 6 dB beyond the peak: the limiter then only clips isolated peaks.
                self.norm_gain = max(min(self.TARGET_LUFS - i, self.TRUE_PEAK - tp + self.LIMITER_DB, 30.0), -20.0)
                self.limit = self.norm_gain > self.TRUE_PEAK - tp
        except (ValueError, KeyError):
            pass

    def _encode(self, encoder: str | None, scale: float) -> tuple[bool, str]:
        cmd = build_command(self.spec, encoder, self.norm_gain, self.limit, scale)
        log.info("Export: %s", subprocess.list2cmdline(cmd))
        Path(self.spec.out).parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     creationflags=ff.BACKGROUND)
        errors: list[str] = []
        threading.Thread(target=lambda: errors.extend(
            l.decode("utf-8", "replace").strip() for l in self.proc.stderr), daemon=True).start()
        dur = max(self.spec.duration, 0.1)
        step = tr("sound") if self.spec.audio_only else tr("encoding")
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us="):
                try:
                    self.on_progress(min(max(int(line.split("=", 1)[1]) / 1e6 / dur, 0.0), 1.0), step)
                except ValueError:
                    pass
        code = self.proc.wait()
        time.sleep(0.05)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        if code != 0:
            return False, (errors[-1] if errors else tr("FFmpeg failed"))
        return True, ""


# ---------------------------------------------------------------- editor audio
def decode_tracks(path: str, streams: list[int], rate: int = 48000):
    """Audio tracks -> [numpy int16 (n, 2)], decoded together (aligned to the sample)."""
    import numpy as np

    if not streams:
        return []
    n = len(streams)
    # Each track in stereo first: a mono track would shift the channels of all the following ones.
    # Mono is duplicated (same level; FFmpeg's conversion would lower it by 3 dB).
    try:
        channels = [int(st.get("channels") or 2) for st in ff.probe(path).get("streams", [])
                    if st.get("codec_type") == "audio"]
    except Exception:
        channels = []
    def to_stereo(s: int) -> str:
        return "pan=stereo|c0=c0|c1=c0" if s < len(channels) and channels[s] == 1 else "aformat=channel_layouts=stereo"
    graph = ";".join(f"[0:a:{s}]{to_stereo(s)}[s{i}]" for i, s in enumerate(streams))
    graph += ";" + "".join(f"[s{i}]" for i in range(n)) + (f"amerge=inputs={n}[m]" if n > 1 else "anull[m]")
    cmd = [ff.ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-filter_complex", graph, "-map", "[m]",
           "-ac", str(2 * n), "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                          creationflags=ff.BACKGROUND, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or "decoding failed")
    data = np.frombuffer(proc.stdout, dtype="<i2")
    data = data[: len(data) - len(data) % (2 * n)].reshape(-1, 2 * n)
    return [np.ascontiguousarray(data[:, 2 * i:2 * i + 2]) for i in range(n)]


def peaks(track, buckets: int):
    """Peak (0-1) per slice, to draw the waveform."""
    import numpy as np

    if track is None or not len(track) or buckets <= 0:
        return np.zeros(max(buckets, 0), dtype=np.float32)
    mono = np.abs(track.astype(np.int32)).max(axis=1)
    edges = np.linspace(0, len(mono), buckets + 1).astype(int)
    out = np.maximum.reduceat(mono, np.minimum(edges[:-1], len(mono) - 1)) / 32768.0
    return out.astype(np.float32)


# ---------------------------------------------------------------- sharing
def share_dir() -> Path:
    d = cache_dir() / "share"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_key(path: str | Path, extra: str = "") -> str:
    st = os.stat(path)
    return hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{extra}".encode()).hexdigest()[:16]


def audio_mp3(path: str | Path, stream: int = 0) -> Path:
    """Sound of the clip as MP3 (the Mix track, or the only track): ready to drop into Discord. Cached."""
    out = share_dir() / file_key(path, f"mp3{stream}") / (Path(path).stem + ".mp3")
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part.mp3")
    ff.run([ff.ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(path), "-map", f"0:a:{stream}",
            "-c:a", "libmp3lame", "-b:a", "192k", str(part)], timeout=600, background=True)
    part.replace(out)
    return out


def prune_share(max_age_days: float = 3) -> None:
    """Share copies are only useful when sending: deleted after a few days."""
    import shutil

    limit = time.time() - max_age_days * 86400
    root = cache_dir() / "share"
    if not root.is_dir():
        return
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

