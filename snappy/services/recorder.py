"""Replay buffer: FFmpeg records continuously into a ring of 1 s segments.

How it works:

* FFmpeg captures one or more screens and the audio tracks pushed by `audio.AudioPump`, and
  writes one-second MPEG-TS segments, each starting with a keyframe. Every segment holds ALL the
  streams: screen 1, screen 2..., then one track per audio source (game, voice chat,
  microphone...), perfectly aligned.
* The "segment" muxer reuses the file names (`segment_wrap`), so the disk never holds more than
  ~(clip length + margin) seconds of video.
* To save a clip, the current segment is awaited (<= 1 s, to include the moment the key was
  pressed), the most recent segments whose total length fits within the limit are taken,
  concatenated and remuxed into an MP4 without re-encoding the video (instant), capped with
  `-t limit`.
* With several audio tracks, the MP4 starts with a "Mix" track (the tracks mixed at their default
  levels): Discord, browsers and File Explorer only play the first track. The separate tracks
  follow, untouched, for the editor.
"""

from __future__ import annotations

import collections
import csv
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import ffmpeg as ff
from ..i18n import tr
from ..platforms import system
from .audio import CHANNELS, SAMPLE_RATE, AudioPump, AudioSource, make_source, open_pipe
from ..models.settings import QUALITY_PRESETS, Settings, app_data_dir, audio_tracks, default_buffer_dir, track_level

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 1.0
WRAP_MARGIN = 15            # segments kept on top of the clip length
LIST_NAME = "segments.csv"


@dataclass
class Segment:
    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SavedClip:
    path: Path
    duration: float
    created: datetime
    tracks: list = field(default_factory=list)    # separate tracks (without the mix)
    screens: list = field(default_factory=list)   # [{"monitor", "label"}], primary screen first
    mix_track: bool = False                       # 1st audio track = mix of the others


class RecorderError(RuntimeError):
    pass


def mux_args(settings: Settings, tracks: list, screens: list, video_refs: list[str], audio_refs: list[str]) -> list[str]:
    """FFmpeg arguments (after the inputs) of a clip: the screens, then, with several tracks, a "Mix"
    track at the default levels and the untouched separate tracks, each one named.
    video_refs / audio_refs: input streams ("0:v:1", "2:v:0"...), in clip order."""
    n = len(tracks)
    args = []
    if n > 1:
        graph = [f"[{ref}]volume={track_level(settings, t):.3f}[t{i}]" for i, (ref, t) in enumerate(zip(audio_refs, tracks))]
        graph.append("".join(f"[t{i}]" for i in range(n)) +
                     f"amix=inputs={n}:normalize=0:dropout_transition=0,alimiter=limit=0.97:level=0[mix]")
        args += ["-filter_complex", ";".join(graph)]
    for ref in video_refs:
        args += ["-map", ref]
    if n > 1:
        args += ["-map", "[mix]"]
    for ref in audio_refs:
        args += ["-map", ref]
    args += ["-c", "copy"]
    if n > 1:
        args += ["-c:a:0", "aac", "-b:a:0", "192k", "-metadata:s:a:0", "handler_name=Mix", "-disposition:a:0", "default"]
        for i, t in enumerate(tracks):
            args += [f"-metadata:s:a:{i + 1}", f"handler_name={t['label']}", f"-disposition:a:{i + 1}", "0"]
    elif n == 1:
        args += ["-metadata:s:a:0", f"handler_name={tracks[0]['label']}"]
    for k, sc in enumerate(screens):
        args += [f"-metadata:s:v:{k}", f"handler_name={sc['label']}", f"-disposition:v:{k}", "default" if k == 0 else "0"]
    return args


class ReplayBuffer:
    """Manages the FFmpeg process of the buffer and the extraction of the clips."""

    def __init__(self, settings: Settings,
                 monitors: dict[int, dict] | None = None,
                 test_mode: bool = False,
                 buffer_dir: Path | None = None,
                 on_state: Callable[[str, str], None] | None = None):
        """
        monitors   : {screen number: {"geometry": (x, y, w, h) in physical pixels, "label": str}}.
        test_mode  : synthetic video/audio sources (tests, --demo).
        on_state   : callback (state, message); states: starting, recording, stopped, error.
        """
        self.settings = settings.copy()
        self.monitors = monitors or {}
        self.test_mode = test_mode or not system.CAN_CAPTURE
        self.buffer_dir = Path(buffer_dir or default_buffer_dir())
        self.on_state = on_state or (lambda state, msg: None)

        self._proc: subprocess.Popen | None = None
        self._pump = None                         # AudioPump (tests) or AudioWorker (audio process)
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._lock = threading.RLock()
        self._proc_lock = threading.RLock()        # starting / stopping FFmpeg
        self._save_lock = threading.Lock()
        self._running = False
        self._watchdog: threading.Thread | None = None
        self._pipe_counter = 0
        self._generation = 0
        self.encoder = "libx264"
        self.state = "stopped"
        self.recording_since: float | None = None   # manual recording: not with this engine
        self.started_at = 0.0
        self.tracks: list[dict] = []
        self.screens: list[dict] = []

    # ------------------------------------------------------------------ state
    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        try:
            self.on_state(state, message)
        except Exception:
            log.exception("on_state")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------- command
    def _screen_list(self) -> list[dict]:
        out = []
        for i, m in enumerate(self.settings.monitors()):
            if self.test_mode and i > 1:
                break     # two test patterns are enough for the tests
            info = self.monitors.get(m, {})
            out.append({"monitor": m, "label": info.get("label") or tr("Screen {n}", n=m + 1)})
        return out

    def _video_input(self, monitor: int, k: int) -> list[str]:
        s = self.settings
        if self.test_mode:
            pattern = "testsrc2" if k == 0 else "smptehdbars"
            return ["-re", "-f", "lavfi", "-i", f"{pattern}=size=1280x720:rate={s.fps}"]
        return system.video_input_args(s.capture_method, monitor, s.fps, s.show_cursor,
                                       self.monitors.get(monitor, {}).get("geometry"))

    def _video_filter(self) -> str:
        s = self.settings
        chain = []
        if not self.test_mode:
            chain += system.video_prefilter(s.capture_method)
        if s.output_height > 0:
            # Never upscale: only scale down when the screen is taller.
            chain.append(f"scale=w=-2:h='min(ih\\,{s.output_height})':flags=bicubic")
        # NV12: the native format of graphics card encoders (no extra conversion).
        chain.append(ff.upload_filter(self.encoder))
        return ",".join(chain)

    def _encoder_args(self) -> list[str]:
        s = self.settings
        crf, max_kbps_1080p60 = QUALITY_PRESETS[s.quality]
        height = s.output_height or 1080
        factor = max(0.3, (height / 1080) ** 2 * (s.fps / 60))
        maxrate = int(max_kbps_1080p60 * factor)
        gop = str(s.fps)
        common = ["-g", gop, "-keyint_min", gop,
                  "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
                  "-maxrate", f"{maxrate}k", "-bufsize", f"{maxrate * 2}k"] + ff.keyframe_args(self.encoder)
        enc = self.encoder
        vendor = ff.vendor_of(enc)
        if vendor == "nvenc":
            return ["-c:v", enc, "-preset", "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0", "-bf", "0"] + common
        if vendor == "amf":
            return ["-c:v", enc, "-usage", "lowlatency", "-quality", "balanced",
                    "-rc", "vbr_peak", "-b:v", f"{int(maxrate * 0.7)}k", "-bf", "0"] + common
        if vendor == "qsv":
            return ["-c:v", enc, "-preset", "veryfast", "-global_quality", str(crf),
                    "-look_ahead", "0", "-bf", "0"] + common
        if vendor == "vaapi":
            return ["-c:v", enc, "-rc_mode", "VBR", "-b:v", f"{int(maxrate * 0.7)}k", "-bf", "0"] + common
        return ["-c:v", enc, "-preset", "veryfast", "-crf", str(crf), "-sc_threshold", "0"] + common

    def build_command(self, audio_pipe: str | None) -> list[str]:
        s = self.settings
        wrap = int(s.clip_seconds / SEGMENT_SECONDS) + WRAP_MARGIN
        args = [ff.ffmpeg(), "-hide_banner", "-loglevel", "warning", "-nostats", "-y"]
        args += ff.hw_device_args(self.encoder)
        for k, sc in enumerate(self.screens):
            args += self._video_input(sc["monitor"], k)
        direct = self._direct_audio()
        n_audio = len(self.tracks) if (audio_pipe or direct) else 0
        if direct:
            # Linux: FFmpeg reads each source itself (PulseAudio / PipeWire), one input per track.
            for t in self.tracks:
                args += system.audio_input_args(t)
        elif n_audio:
            args += ff.thread_queue_args(4096) + ["-f", "s16le", "-ar", str(SAMPLE_RATE),
                     "-ac", str(CHANNELS * n_audio)]
            if n_audio == 1:
                args += ["-ch_layout", "stereo"]
            args += ["-i", audio_pipe]
        graph = [f"[{k}:v]{self._video_filter()}[v{k}]" for k in range(len(self.screens))]
        a_in = len(self.screens)
        if n_audio > 1 and not direct:
            # A single interleaved stream -> one stereo track per source.
            graph.append(f"[{a_in}:a]asplit={n_audio}" + "".join(f"[s{i}]" for i in range(n_audio)))
            graph += [f"[s{i}]pan=stereo|c0=c{2 * i}|c1=c{2 * i + 1}[a{i}]" for i in range(n_audio)]
        args += ["-filter_complex", ";".join(graph)]
        for k in range(len(self.screens)):
            args += ["-map", f"[v{k}]"]
        if direct:
            for i in range(n_audio):
                args += ["-map", f"{a_in + i}:a:0"]
        elif n_audio == 1:
            args += ["-map", f"{a_in}:a:0"]
        else:
            for i in range(n_audio):
                args += ["-map", f"[a{i}]"]
        if n_audio:
            args += ["-c:a", "aac", "-b:a", f"{s.audio_bitrate_kbps}k"]
        args += self._encoder_args()
        args += ["-f", "segment",
                 "-segment_time", str(SEGMENT_SECONDS),
                 "-segment_format", "mpegts",
                 "-segment_wrap", str(wrap),
                 "-segment_list", str(self.buffer_dir / LIST_NAME),
                 "-segment_list_type", "csv",
                 "-segment_list_size", str(wrap),
                 "-reset_timestamps", "0",
                 str(self.buffer_dir / "seg_%04d.ts")]
        return args

    def _direct_audio(self) -> bool:
        """The system provides FFmpeg inputs for the sound (Linux): no pipe, no audio process."""
        return not self.test_mode and getattr(system, "DIRECT_AUDIO", False)

    def _make_audio_sources(self) -> list[AudioSource]:
        if self.test_mode:
            return [make_source(t, test_index=i) for i, t in enumerate(self.tracks)]
        return [make_source(t) for t in self.tracks]

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._set_state("starting", tr("Starting the buffer…"))
        self._kill_orphans()
        self.screens = self._screen_list()
        self.tracks = audio_tracks(self.settings)
        try:
            self.encoder = (ff.pick_encoder(self.settings.codec, self.settings.encoder)
                            if not self.test_mode else "libx264")
        except ff.FFmpegNotFound as exc:
            self._running = False
            self._set_state("error", str(exc))
            raise RecorderError(str(exc)) from exc
        self._watchdog = threading.Thread(target=self._watch, name="ffmpeg-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._kill_process()
        if self._watchdog:
            self._watchdog.join(timeout=5)
        self._set_state("stopped", tr("Buffer stopped"))

    def _kill_orphans(self) -> None:
        """FFmpeg processes left over by a previous buffer (app crash, failed stop) that would still
        write into the buffer folder: two FFmpeg would fight over the segments."""
        try:
            import psutil
        except ImportError:
            return
        target = str(self.buffer_dir).lower()
        own = {self._proc.pid} if self._proc else set()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if not (proc.info["name"] or "").lower().startswith("ffmpeg") or proc.info["pid"] in own:
                    continue
                if any(target in (arg or "").lower() for arg in proc.info["cmdline"] or []):
                    log.warning("Orphan FFmpeg stopped (pid %s)", proc.info["pid"])
                    proc.kill()
                    proc.wait(timeout=3)
            except (psutil.Error, OSError):
                continue

    def restart(self, settings: Settings) -> None:
        self.stop()
        self.settings = settings.copy()
        ff.encoder_works.cache_clear()
        self.start()

    def _clean_buffer_dir(self) -> None:
        if self.buffer_dir.exists():
            shutil.rmtree(self.buffer_dir, ignore_errors=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

    def _spawn(self) -> None:
        with self._proc_lock:
            if not self._running:      # stopped while starting: nothing to launch
                return
            self._kill_process()
            self._spawn_locked()

    def _spawn_locked(self) -> None:
        self._clean_buffer_dir()
        pipe_name = None
        if self.tracks and not self._direct_audio():
            self._pipe_counter += 1
            pipe_name = system.audio_pipe_name(self._pipe_counter, self.buffer_dir)
            on_error = lambda m: self._stderr_tail.append(m)   # noqa: E731
            self._pump = None
            if not self.test_mode:
                # Capture in a separate process (see audio_worker): otherwise packets get lost.
                from .audio_worker import AudioWorker

                worker = AudioWorker(self.tracks, pipe_name, on_error,
                                     log_path=str(app_data_dir() / "snappy-audio.log"))
                try:
                    worker.start()
                    self._pump = worker
                except Exception:
                    log.exception("Audio process unavailable: capturing inside Snappy")
                    worker.stop()
            if self._pump is None:
                sources = self._make_audio_sources()
                pipe = open_pipe(pipe_name, channels=CHANNELS * len(sources))
                self._pump = AudioPump(sources, pipe, on_error=on_error)
                self._pump.start()
        cmd = self.build_command(pipe_name)
        log.info("FFmpeg: %s", subprocess.list2cmdline(cmd))
        self._stderr_tail.clear()
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, creationflags=system.NO_WINDOW)
        system.kill_with_parent(self._proc)
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()
        self.started_at = time.monotonic()
        self._generation += 1

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._stderr_tail.append(line)
                log.debug("ffmpeg: %s", line)

    def _kill_process(self) -> None:
        with self._proc_lock:
            self._kill_process_locked()

    def _kill_process_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(b"q")  # clean stop
                proc.stdin.flush()
            except OSError:
                pass
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)
        if proc:
            for stream in (proc.stdin, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass
        if self._pump:
            self._pump.stop()
            self._pump = None

    # Without a segment after this delay, FFmpeg runs but produces nothing usable (an encoder that
    # ignores the forced keyframes...): it is treated as a failure.
    FIRST_SEGMENT_TIMEOUT = 15.0

    def _watch(self) -> None:
        """Starts FFmpeg and restarts it if it stops unexpectedly."""
        try:
            self._watch_loop()
        finally:
            if not self._running:     # never leave FFmpeg behind a stopped buffer
                self._kill_process()

    def _watch_loop(self) -> None:
        failures = 0
        while self._running:
            try:
                self._spawn()
            except Exception as exc:
                self._running = False
                self._set_state("error", tr("Could not start FFmpeg: {error}", error=exc))
                return
            announced = False
            stalled = False
            while self._running and self._proc and self._proc.poll() is None:
                if not announced and self.buffered_seconds() > 0:
                    announced = True
                    failures = 0
                    self._set_state("recording", tr("Recording ({encoder})", encoder=self.encoder))
                if not announced and time.monotonic() - self.started_at > self.FIRST_SEGMENT_TIMEOUT:
                    stalled = True
                    self._stderr_tail.append(tr("No segment produced: incompatible encoder?"))
                    break
                time.sleep(0.25)
            if not self._running:
                return
            failures += 1
            tail = "\n".join(list(self._stderr_tail)[-6:])
            log.warning("FFmpeg stopped (attempt %d):\n%s", failures, tail)
            self._kill_process()
            if failures >= 3 or stalled:
                fallback = system.fallback_capture_method(self.settings.capture_method)
                if fallback and not self.test_mode and not stalled:
                    # Automatic fallback (on Windows: GDI capture, for old drivers, RDP...)
                    self.settings.capture_method = fallback
                    failures = 0
                    self._set_state("starting", tr("Falling back to the GDI capture…"))
                    continue
                if self.encoder != "libx264":
                    self.encoder = "libx264"
                    failures = 0
                    self._set_state("starting", tr("Falling back to the software encoder…"))
                    continue
                self._running = False
                self._set_state("error", tr("FFmpeg keeps stopping:") + "\n" + tail)
                return
            self._set_state("starting", tr("Restarting the buffer…"))
            time.sleep(1.5)

    # ------------------------------------------------------------- segments
    def segments(self) -> list[Segment]:
        """Finished segments, oldest first."""
        list_path = self.buffer_dir / LIST_NAME
        for _ in range(5):
            try:
                text = list_path.read_text(encoding="utf-8")
                rows = list(csv.reader(text.splitlines()))
                segs = []
                for row in rows:
                    if len(row) < 3:
                        continue
                    path = self.buffer_dir / row[0]
                    segs.append(Segment(path, float(row[1]), float(row[2])))
                segs.sort(key=lambda sg: sg.start)
                # A reused name can show up twice: keep the most recent one.
                latest: dict[Path, Segment] = {}
                for sg in segs:
                    latest[sg.path] = sg
                return sorted((sg for sg in latest.values() if sg.path.exists()),
                              key=lambda sg: sg.start)
            except FileNotFoundError:
                return []
            except (OSError, ValueError):
                time.sleep(0.02)  # file being rewritten
        return []

    def buffered_seconds(self) -> float:
        segs = self.segments()
        if not segs:
            return 0.0
        return min(float(self.settings.clip_seconds), segs[-1].end - segs[0].start)

    @staticmethod
    def select_segments(segs: list[Segment], limit: float, tolerance: float = 0.05) -> list[Segment]:
        """Most recent contiguous segments whose total length <= limit."""
        chosen: list[Segment] = []
        total = 0.0
        for sg in reversed(segs):
            if chosen and abs(chosen[0].start - sg.end) > 0.5:
                break  # gap in the chain (FFmpeg restart)
            if total + sg.duration > limit + tolerance:
                break
            chosen.insert(0, sg)
            total += sg.duration
        return chosen

    def _wait_current_segment(self) -> None:
        """Waits for the current segment to be closed (to include the present moment)."""
        before = self.segments()
        if not before or not self._running:
            return
        last_end = before[-1].end
        deadline = time.monotonic() + SEGMENT_SECONDS * 2 + 1
        while time.monotonic() < deadline:
            segs = self.segments()
            if segs and segs[-1].end > last_end:
                return
            time.sleep(0.05)

    # ------------------------------------------------------------ saving
    @staticmethod
    def unique_path(dest_dir: Path, stem: str) -> Path:
        out = dest_dir / f"{stem}.mp4"
        n = 2
        while out.exists():
            out = dest_dir / f"{stem} ({n}).mp4"
            n += 1
        return out

    # Manual recording (start / stop): gpu-screen-recorder engine only (platforms/linux/replay.py).
    can_record = False

    @property
    def is_recording(self) -> bool:
        return False

    def start_recording(self) -> None:
        raise RecorderError(tr("Manual recording is not available with this capture engine."))

    def stop_recording(self, dest_dir: Path, name: str | None = None) -> SavedClip:
        raise RecorderError(tr("Manual recording is not available with this capture engine."))

    def save_clip(self, dest_dir: Path, name: str | None = None,
                  wait_for_current: bool = True) -> SavedClip:
        """Writes the last `clip_seconds` seconds into an MP4. Blocking (~1 s)."""
        with self._save_lock:
            if not self._running:
                raise RecorderError(tr("The buffer is not running."))
            if not self.segments():
                raise RecorderError(tr("The buffer is still empty, try again in a moment."))
            if wait_for_current:
                self._wait_current_segment()
            segs = self.segments()
            limit = float(self.settings.clip_seconds)
            chosen = self.select_segments(segs, limit)
            if not chosen:
                raise RecorderError(tr("No usable segment in the buffer."))

            dest_dir = Path(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            created = datetime.now()
            out = self.unique_path(dest_dir, name or f"Snappy {created:%Y-%m-%d %H-%M-%S}")

            # 1) Copy the segments right away (FFmpeg could reuse them).
            staged = dest_dir / f".{out.stem}.ts.part"
            try:
                with open(staged, "wb") as dst:
                    for sg in chosen:
                        with open(sg.path, "rb") as src:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                # 2) MP4 remux without re-encoding the video, capped at the limit.
                mix = self.finalize(staged, out, limit)
            finally:
                try:
                    staged.unlink()
                except OSError:
                    pass
            return SavedClip(out, ff.probe_duration(out), created, list(self.tracks), list(self.screens), mix)

    def finalize(self, staged: Path, out: Path, limit: float | None = None,
                 tracks: list | None = None, screens: list | None = None) -> bool:
        """Concatenated segments -> MP4. Returns True if a "Mix" track was added first."""
        tracks = self.tracks if tracks is None else tracks
        screens = self.screens if screens is None else screens
        info = ff.media_info(staged)
        if info["audio_streams"] != len(tracks) or info["video_streams"] != len(screens):
            raise RecorderError(tr("The buffer just restarted with other settings: try again in a few seconds."))
        cmd = [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts", "-i", str(staged)]
        cmd += mux_args(self.settings, tracks, screens, [f"0:v:{k}" for k in range(len(screens))],
                        [f"0:a:{i}" for i in range(len(tracks))])
        if limit:
            cmd += ["-t", f"{limit:.3f}"]
        cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]
        tmp_out = out.with_suffix(".part.mp4")
        cmd.append(str(tmp_out))
        ff.run(cmd, timeout=max(120, (limit or 600) * 2))
        tmp_out.replace(out)
        return len(tracks) > 1

    def _has_audio(self) -> bool:
        return bool(self.tracks)
