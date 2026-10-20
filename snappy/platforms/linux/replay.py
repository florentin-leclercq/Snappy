"""Linux replay buffer: gpu-screen-recorder in replay mode.

* One gpu-screen-recorder per recorded screen, each keeping the last `clip_seconds` seconds in
  memory (`-r`, `-replay-storage ram`); the first screen also carries the audio tracks (one -a
  option per source: game, voice chat, microphone...).
* Saving a clip = SIGUSR1 to every recorder: they write their replay and run the `-sc` script,
  which writes down the file path. All saved at the same moment, the files end together: their
  ends are aligned.
* The files are then merged without re-encoding into the clip MP4, in the same format as on
  Windows: screens, "Mix" track, named separate tracks; length capped at the limit.
* Manual recording (start / stop, on top of the replay): each recorder has a command socket
  (-ipc); "start-replay-recording" makes it write everything from now on, without a second
  encode, until "stop-replay-recording". With several screens, gpu-screen-recorder writes the
  timestamp of each file's first frame (same clock for all): the screens are aligned exactly.

Same interface as `services.recorder.ReplayBuffer`. `LinuxReplayBuffer` picks the engine:
gpu-screen-recorder, or the FFmpeg buffer (x11grab) for the "x11" method and as a fallback when
gpu-screen-recorder cannot run (no graphics acceleration: virtual machine...).
"""

from __future__ import annotations

import collections
import json
import logging
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from ...i18n import tr
from ...models.settings import Settings, audio_tracks, default_buffer_dir
from ...services import ffmpeg as ff
from ...services.recorder import RecorderError, ReplayBuffer, SavedClip, mux_args
from . import capture
from .process import die_with_parent

log = logging.getLogger(__name__)

QUALITY = {"low": "medium", "medium": "high", "high": "very_high", "ultra": "ultra"}
# gpu-screen-recorder errors that no retry will fix.
FATAL_ERRORS = ("failed to load opengl", "llvmpipe")
SAVED_LIST = "saved.list"
KEYINT = 1.0          # one keyframe per second: precision of the clip length


def no_gpu_message() -> str:
    return tr("gpu-screen-recorder needs the graphics acceleration of the card (OpenGL), which is "
              "missing here (virtual machine, missing driver…).")


class GsrReplayBuffer:
    """Manages the replay gpu-screen-recorders and the assembly of the clips."""

    # Time without a crash after which the replay is considered active.
    STARTUP_GRACE = 2.0

    def __init__(self, settings: Settings,
                 monitors: dict[int, dict] | None = None,
                 test_mode: bool = False,
                 buffer_dir: Path | None = None,
                 on_state: Callable[[str, str], None] | None = None):
        """monitors: {screen number: {"geometry": (x, y, w, h), "label": str, "name": "DP-1"}}."""
        self.settings = settings.copy()
        self.monitors = monitors or {}
        self.buffer_dir = Path(buffer_dir or default_buffer_dir())
        self.on_state = on_state or (lambda state, msg: None)
        self._procs: list[subprocess.Popen] = []
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._lock = threading.RLock()
        self._proc_lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._running = False
        self._announced = False
        self._force_cpu = False
        self._watchdog: threading.Thread | None = None
        self.encoder = "gpu-screen-recorder"
        self.state = "stopped"
        self.started_at = 0.0
        self.tracks: list[dict] = []
        self.screens: list[dict] = []
        self._targets: list[str] = []      # -w of each recorded screen
        self._errors: list[str] = []       # gpu-screen-recorder error lines (lowercase)
        # Called (from the watchdog thread) when gpu-screen-recorder cannot run here.
        self.on_fatal: Callable[[str], None] | None = None
        self.recording_since: float | None = None   # manual recording in progress (time.time())

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
    def _screen_list(self) -> tuple[list[dict], list[str]]:
        """Recorded screens ([{monitor, label}]) and the gpu-screen-recorder -w target of each one."""
        if self.settings.capture_method == "portal":
            m = self.settings.monitor_index
            return [{"monitor": m, "label": tr("Chosen screen")}], ["portal"]
        screens, targets = [], []
        for m in self.settings.monitors():
            info = self.monitors.get(m, {})
            name = info.get("name") or ("screen" if not screens else "")
            if not name or name in targets:
                continue
            screens.append({"monitor": m, "label": info.get("label") or tr("Screen {n}", n=m + 1)})
            targets.append(name)
        return screens, targets

    def _output_size(self, monitor: int) -> list[str]:
        """-s: lowers the resolution (never upscales), keeping the aspect ratio."""
        h_out = self.settings.output_height
        geo = self.monitors.get(monitor, {}).get("geometry")
        if not h_out or not geo or geo[3] <= h_out:
            return []
        w = int(round(h_out * geo[2] / geo[3] / 2)) * 2
        return ["-s", f"{w}x{h_out}"]

    def screen_dir(self, k: int) -> Path:
        return self.buffer_dir / f"screen_{k}"

    def ipc_path(self, k: int) -> Path:
        return self.buffer_dir / f"screen_{k}.sock"

    def build_command(self, k: int) -> list[str]:
        s = self.settings
        cmd = list(capture.gsr_command() or ("gpu-screen-recorder",))
        cmd += ["-w", self._targets[k], "-c", "mp4", "-f", str(s.fps),
                "-r", str(max(2, int(s.clip_seconds))), "-replay-storage", "ram",
                "-k", s.codec, "-q", QUALITY.get(s.quality, "very_high"),
                "-keyint", f"{KEYINT:g}", "-cursor", "yes" if s.show_cursor else "no",
                "-encoder", "cpu" if (s.encoder == "cpu" or self._force_cpu) else "gpu",
                "-fallback-cpu-encoding", "yes", "-v", "no",
                "-o", str(self.screen_dir(k)), "-sc", str(self.buffer_dir / "on_save.sh"),
                # Manual recording: command socket, files written next to the replays.
                "-ro", str(self.screen_dir(k)), "-ipc", str(self.ipc_path(k))]
        if len(self._targets) > 1:
            cmd += ["-write-first-frame-ts", "yes"]   # aligns the screens of a manual recording
        if self._targets[k] == "portal":
            cmd += ["-restore-portal-session", "yes"]
        else:
            cmd += self._output_size(self.screens[k]["monitor"])
        if k == 0 and self.tracks:
            devices = [name for name, _desc in capture.audio_devices()]
            for t in self.tracks:
                cmd += ["-a", capture.resolve_audio_arg(capture.gsr_audio_arg(t["source"]), devices)]
            cmd += ["-ac", "aac", "-ab", str(s.audio_bitrate_kbps)]
        return cmd

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._set_state("starting", tr("Starting the replay…"))
        self.screens, self._targets = self._screen_list()
        self.tracks = audio_tracks(self.settings)
        if not capture.gsr_command():
            self._running = False
            self._set_state("error", capture.install_hint())
            raise RecorderError(capture.install_hint())
        try:
            ff.ffmpeg()
        except ff.FFmpegNotFound as exc:
            self._running = False
            self._set_state("error", str(exc))
            raise RecorderError(str(exc)) from exc
        self._watchdog = threading.Thread(target=self._watch, name="gsr-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._kill_processes()
        if self._watchdog and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=8)
        self._set_state("stopped", tr("Buffer stopped"))

    def restart(self, settings: Settings) -> None:
        self.stop()
        self.settings = settings.copy()
        self._force_cpu = False
        self.start()

    def _prepare_dir(self) -> None:
        if self.buffer_dir.exists():
            shutil.rmtree(self.buffer_dir, ignore_errors=True)
        for k in range(len(self.screens)):
            self.screen_dir(k).mkdir(parents=True, exist_ok=True)
        script = self.buffer_dir / "on_save.sh"
        # Called by gpu-screen-recorder once the file is written: <path> <type>.
        script.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> '{self.buffer_dir / SAVED_LIST}'\n", encoding="utf-8")
        script.chmod(0o755)

    def _spawn(self) -> None:
        with self._proc_lock:
            if not self._running:
                return
            self._kill_processes()
            self._prepare_dir()
            self._stderr_tail.clear()
            for k in range(len(self.screens)):
                cmd = self.build_command(k)
                log.info("gpu-screen-recorder : %s", subprocess.list2cmdline(cmd))
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, preexec_fn=die_with_parent)
                threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
                self._procs.append(proc)
            self.started_at = time.monotonic()
            self._announced = False
            self.recording_since = None     # new recorders: a manual recording does not survive

    def _drain(self, proc: subprocess.Popen) -> None:
        assert proc.stdout
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line.startswith("usage:"):      # option summary printed after an error: useless
                continue
            if line:
                self._stderr_tail.append(line)
                if "error" in line.lower():
                    self._errors.append(line.lower())
                log.debug("gsr: %s", line)

    def _kill_processes(self) -> None:
        with self._proc_lock:
            procs, self._procs = self._procs, []
            for p in procs:
                if p.poll() is None:
                    p.send_signal(signal.SIGINT)      # clean stop
            for p in procs:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
            self._announced = False

    def _alive(self) -> bool:
        return bool(self._procs) and all(p.poll() is None for p in self._procs)

    def _watch(self) -> None:
        """Starts the recorders and restarts them if one stops. The thread lives as long as the
        replay runs: the recorders, tied to it (PR_SET_PDEATHSIG), die with Snappy."""
        try:
            self._watch_loop()
        finally:
            if not self._running:
                self._kill_processes()

    def _watch_loop(self) -> None:
        failures = 0
        while self._running:
            try:
                self._spawn()
            except Exception as exc:
                self._running = False
                self._set_state("error", tr("Could not start gpu-screen-recorder: {error}", error=exc))
                return
            while self._running and self._alive():
                if not self._announced and time.monotonic() - self.started_at > self.STARTUP_GRACE:
                    self._announced = True
                    failures = 0
                    mode = tr("processor") if self._force_cpu or self.settings.encoder == "cpu" else tr("graphics card")
                    self._set_state("recording", tr("Recording (gpu-screen-recorder, {mode})", mode=mode))
                time.sleep(0.25)
            if not self._running:
                return
            failures += 1
            tail = "\n".join(list(self._stderr_tail)[-6:])
            log.warning("gpu-screen-recorder stopped (attempt %d):\n%s", failures, tail)
            self._kill_processes()
            if any(err in line for line in self._errors for err in FATAL_ERRORS):
                self._running = False
                if self.on_fatal:
                    self.on_fatal(no_gpu_message())
                else:
                    self._set_state("error", no_gpu_message())
                return
            if failures >= 3:
                if not self._force_cpu and self.settings.encoder != "cpu":
                    self._force_cpu = True
                    failures = 0
                    self._set_state("starting", tr("Falling back to the software encoder…"))
                    continue
                self._running = False
                self._set_state("error", tr("gpu-screen-recorder keeps stopping:") + "\n" + tail)
                return
            self._set_state("starting", tr("Restarting the replay…"))
            time.sleep(1.5)

    def buffered_seconds(self) -> float:
        if not self._announced or not self._alive():
            return 0.0
        return min(float(self.settings.clip_seconds), time.monotonic() - self.started_at)

    # ------------------------------------------------------------ saving
    def _saved(self) -> list[str]:
        try:
            return (self.buffer_dir / SAVED_LIST).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def _request_files(self) -> list[Path | None]:
        """SIGUSR1 to every recorder, then the written files ([None] for a late screen)."""
        before = len(self._saved())
        procs = list(self._procs)
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGUSR1)
        return self._wait_files(before, len(procs), 15 + self.settings.clip_seconds / 20)

    def _wait_files(self, before: int, count: int, timeout: float) -> list[Path | None]:
        """Files the recorders announced (on_save.sh) after the first `before` lines of the list,
        one per screen ([None] for a late screen)."""
        dirs = [self.screen_dir(k).resolve() for k in range(count)]
        found: list[Path | None] = [None] * count
        deadline = time.monotonic() + timeout
        main_at = None
        while time.monotonic() < deadline:
            for line in self._saved()[before:]:
                path = Path(line.strip())
                for k, d in enumerate(dirs):
                    if found[k] is None and path.parent.resolve() == d and path.exists():
                        found[k] = path
            if all(found):
                break
            if found[0] and main_at is None:
                main_at = time.monotonic()
            if main_at and time.monotonic() - main_at > 5:      # another screen does not answer
                break
            time.sleep(0.05)
        return found

    @staticmethod
    def _video_duration(path: Path) -> float:
        info = ff.probe(path)
        for st in info.get("streams", []):
            if st.get("codec_type") == "video":
                try:
                    return float(st["duration"])
                except (KeyError, ValueError):
                    break
        try:
            return float(info["format"]["duration"])
        except (KeyError, ValueError):
            return 0.0

    def save_clip(self, dest_dir: Path, name: str | None = None, wait_for_current: bool = True) -> SavedClip:
        """Writes the last `clip_seconds` seconds into an MP4. Blocking (1 to 3 s)."""
        with self._save_lock:
            if not self._running or not self._alive():
                raise RecorderError(tr("The buffer is not running."))
            if not self._announced:
                raise RecorderError(tr("The buffer is still empty, try again in a moment."))
            files = self._request_files()
            if not files or files[0] is None:
                raise RecorderError(tr("gpu-screen-recorder did not write the replay:") + "\n" +
                                    "\n".join(list(self._stderr_tail)[-4:]))
            return self._make_clip(files, dest_dir, name, float(self.settings.clip_seconds))

    def _make_clip(self, files: list[Path | None], dest_dir: Path, name: str | None, limit: float | None,
                   aligned_starts: bool = False) -> SavedClip:
        """Screen files -> clip MP4 in dest_dir; the screen files are deleted afterwards."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        created = datetime.now()
        out = ReplayBuffer.unique_path(dest_dir, name or f"Snappy {created:%Y-%m-%d %H-%M-%S}")
        inputs = [(sc, f) for sc, f in zip(self.screens, files) if f is not None]
        offsets = None
        if aligned_starts and len(inputs) > 1:
            stamps = [self._first_frame_ts(f) for _sc, f in inputs]
            if all(s is not None for s in stamps):
                offsets = [(s - min(stamps)) / 1e6 for s in stamps]
        try:
            tracks = self.assemble([f for _sc, f in inputs], out, limit, [sc for sc, _f in inputs], offsets)
        finally:
            for f in files:
                if f is not None:
                    for p in (f, f.with_name(f.name + ".ts")):
                        try:
                            p.unlink()
                        except OSError:
                            pass
        return SavedClip(out, ff.probe_duration(out), created, tracks, [sc for sc, _f in inputs], len(tracks) > 1)

    @staticmethod
    def _first_frame_ts(path: Path) -> int | None:
        """Timestamp of a file's first frame (monotonic microseconds), written by gpu-screen-recorder
        (-write-first-frame-ts) in "<file>.ts"."""
        try:
            return int(path.with_name(path.name + ".ts").read_text().split()[2])
        except (OSError, ValueError, IndexError):
            return None

    # ------------------------------------------------------------ manual recording
    def _ipc(self, k: int, request: str, timeout: float = 5.0) -> tuple[bool | None, str]:
        """Request to a recorder's command socket: (ok, data or error); ok is None when the
        recorder does not answer."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(str(self.ipc_path(k)))
                sock.sendall(json.dumps({"id": 1, "name": request}).encode() + b"\n")
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            return None, str(exc)
        try:
            reply = json.loads(buf.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False, tr("Unreadable reply from gpu-screen-recorder")
        return reply.get("result") == "ok", str(reply.get("data") or "")

    @property
    def is_recording(self) -> bool:
        return self.recording_since is not None

    def start_recording(self) -> None:
        """Starts a manual recording on every screen (everything from now on, until stop)."""
        with self._save_lock:
            if not self._running or not self._alive() or not self._announced:
                raise RecorderError(tr("The buffer is not running."))
            if self.recording_since is not None:
                return
            ok, data = self._ipc(0, "start-replay-recording")
            if not ok:
                raise RecorderError(tr("Could not start the recording: {error}", error=data))
            for k in range(1, len(self._procs)):
                ok_k, data_k = self._ipc(k, "start-replay-recording")
                if not ok_k:
                    log.warning("Screen %d: recording not started (%s)", k + 1, data_k)
            self.recording_since = time.time()

    def stop_recording(self, dest_dir: Path, name: str | None = None) -> SavedClip:
        """Stops the manual recording and writes it as a clip (the whole recording). Blocking."""
        with self._save_lock:
            if self.recording_since is None:
                raise RecorderError(tr("No recording in progress."))
            self.recording_since = None
            before = len(self._saved())
            # The reply comes once the file is written: long recordings take a while. The other
            # screens first, so their files are complete when the main one is.
            for k in range(1, len(self._procs)):
                self._ipc(k, "stop-replay-recording", timeout=120)
            ok, data = self._ipc(0, "stop-replay-recording", timeout=120)
            if not ok:
                raise RecorderError(tr("Could not stop the recording: {error}", error=data))
            files = self._wait_files(before, len(self._procs), 30)
            if files[0] is None:
                raise RecorderError(tr("gpu-screen-recorder did not write the recording."))
            return self._make_clip(files, dest_dir, name, None, aligned_starts=True)

    def assemble(self, files: list[Path], out: Path, limit: float | None, screens: list[dict],
                 offsets: list[float] | None = None) -> list[dict]:
        """Screen files (the first one with the sound) -> clip MP4. Replay: the ends are aligned
        (all saved at the same moment) and the start is cut at the first keyframe that fits
        within the limit. Manual recording (limit None): the starts are placed at `offsets`
        (seconds, from the first frame timestamps), nothing is cut.
        Returns the separate tracks of the clip (with several, a "Mix" comes before them)."""
        durations = [self._video_duration(f) for f in files]
        if offsets is None:
            total = max(durations)
            offsets = [total - d for d in durations]
        total = max(o + d for o, d in zip(offsets, durations))
        info = ff.media_info(files[0])
        tracks = self.tracks[:info["audio_streams"]]
        if len(tracks) != len(self.tracks):
            log.warning("Replay with %d audio tracks instead of %d", info["audio_streams"], len(self.tracks))
        cmd = [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        for f, o in zip(files, offsets):
            if o > 0.02:
                cmd += ["-itsoffset", f"{o:.3f}"]
            cmd += ["-i", str(f)]
        if limit is None:
            limit = total
        start = total - limit
        if start > 0.01:
            # Output option: copied streams resume at the first keyframe after this point (never longer).
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-t", f"{limit:.3f}"]
        cmd += mux_args(self.settings, tracks, screens, [f"{k}:v:0" for k in range(len(files))],
                        [f"0:a:{i}" for i in range(len(tracks))])
        cmd += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]
        tmp_out = out.with_suffix(".part.mp4")
        cmd.append(str(tmp_out))
        ff.run(cmd, timeout=max(120, limit * 2))
        tmp_out.replace(out)
        return list(tracks)


class LinuxReplayBuffer:
    """Linux replay buffer: gpu-screen-recorder, or the FFmpeg engine (X11 capture) for the "x11"
    method and as an automatic fallback when gpu-screen-recorder cannot run."""

    def __init__(self, settings: Settings, monitors: dict[int, dict] | None = None, test_mode: bool = False,
                 buffer_dir: Path | None = None, on_state: Callable[[str, str], None] | None = None):
        self._buffer_dir = buffer_dir
        self._on_state = on_state or (lambda state, msg: None)
        self._fallback = False          # gpu-screen-recorder impossible here: FFmpeg for the session
        self.engine = self._make(settings, monitors or {})

    def _use_ffmpeg(self, settings: Settings) -> bool:
        return settings.capture_method == "x11" or self._fallback

    def _engine_settings(self, settings: Settings) -> Settings:
        s = settings.copy()
        if self._use_ffmpeg(settings):
            s.capture_method = "x11"
        return s

    def _make(self, settings: Settings, monitors: dict):
        if self._use_ffmpeg(settings):
            return ReplayBuffer(self._engine_settings(settings), monitors, buffer_dir=self._buffer_dir,
                                on_state=self._on_state)
        engine = GsrReplayBuffer(settings, monitors, buffer_dir=self._buffer_dir, on_state=self._on_state)
        engine.on_fatal = self._on_gsr_fatal
        return engine

    def _on_gsr_fatal(self, reason: str) -> None:
        old = self.engine
        if not capture.is_x11():
            self._on_state("error", reason + " " + tr("No fallback is possible on Wayland: open an X11 session "
                                                      "or enable the 3D acceleration of the virtual machine."))
            return
        log.warning("gpu-screen-recorder cannot run (no OpenGL): falling back to the FFmpeg X11 capture")
        self._fallback = True
        self.engine = self._make(old.settings, old.monitors)
        self._on_state("starting", tr("No graphics acceleration: FFmpeg capture (X11), sound not split per application…"))
        try:
            self.engine.start()
        except RecorderError:
            pass

    # The settings go to the current engine (the "x11" method is forced on the FFmpeg engine).
    @property
    def settings(self) -> Settings:
        return self.engine.settings

    @settings.setter
    def settings(self, value: Settings) -> None:
        self.engine.settings = self._engine_settings(value)

    @property
    def monitors(self) -> dict:
        return self.engine.monitors

    @monitors.setter
    def monitors(self, value: dict) -> None:
        self.engine.monitors = value

    def __getattr__(self, name: str):
        if name == "engine":            # not created yet
            raise AttributeError(name)
        return getattr(self.engine, name)

    def start(self) -> None:
        self.engine.start()

    def stop(self) -> None:
        self.engine.stop()

    def restart(self, settings: Settings) -> None:
        self.engine.stop()
        self.engine = self._make(settings, self.engine.monitors)
        self.engine.start()
