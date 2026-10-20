"""Windows: audio capture and delivery to FFmpeg.

The sound of a given application comes from WASAPI "process loopback" (see `wasapi`), the
microphone from the "soundcard" library. The tracks go to FFmpeg through a named pipe.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from ...services.audio import BLOCK_FRAMES, CHANNELS, SAMPLE_RATE, AudioSource

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Named pipe
# ----------------------------------------------------------------------------
def audio_pipe_name(counter: int, buffer_dir: Path) -> str:
    return rf"\\.\pipe\snappy_audio_{os.getpid()}_{counter}"


class PipeWriter:
    """Write end of a named pipe that FFmpeg opens for reading."""

    def __init__(self, name: str, channels: int = CHANNELS):
        import ctypes
        from ctypes import wintypes

        self.name = name
        self._closed = False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateNamedPipeW.restype = wintypes.HANDLE
        k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.LPVOID]
        k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        self._k32, self._wintypes, self._ctypes = k32, wintypes, ctypes
        PIPE_ACCESS_OUTBOUND = 0x2
        buf = SAMPLE_RATE * channels * 2  # 1 s
        h = k32.CreateNamedPipeW(name, PIPE_ACCESS_OUTBOUND, 0, 1, buf, buf, 0, None)
        if h in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateNamedPipe failed")
        self._handle = h

    def wait_for_reader(self) -> bool:
        """Blocks until FFmpeg opens the pipe."""
        ok = self._k32.ConnectNamedPipe(self._handle, None)
        ERROR_PIPE_CONNECTED = 535
        return bool(ok) or self._ctypes.get_last_error() == ERROR_PIPE_CONNECTED

    def write(self, data: bytes) -> None:
        if self._closed:
            raise BrokenPipeError
        written = self._wintypes.DWORD(0)
        if not self._k32.WriteFile(self._handle, data, len(data), self._ctypes.byref(written), None):
            raise BrokenPipeError(self._ctypes.get_last_error())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Unblocks a pending ConnectNamedPipe, if any.
        try:
            h = self._k32.CreateFileW(self.name, 0x80000000, 0, None, 3, 0, None)
            if h not in (None, self._wintypes.HANDLE(-1).value):
                self._k32.CloseHandle(h)
        except Exception:
            pass
        if self._handle:
            self._k32.CloseHandle(self._handle)


# ----------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------
class SoundcardSource(AudioSource):
    """WASAPI capture through "soundcard" (speaker loopback or microphone)."""

    def __init__(self, name: str, loopback: bool, device_name: str = "", volume: float = 1.0):
        super().__init__(name, volume)
        self.loopback = loopback
        self.device_name = device_name

    def _run(self) -> None:
        import ctypes
        import warnings

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COM is needed per thread
        import soundcard as sc

        # Data gaps reported by WASAPI: filled with silence, no need to log them.
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)

        if self.loopback:
            speaker = sc.default_speaker()
            device = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        else:
            device = None
            if self.device_name:
                for mic in sc.all_microphones():
                    if mic.name == self.device_name:
                        device = mic
                        break
            device = device or sc.default_microphone()
        with device.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_FRAMES) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=BLOCK_FRAMES)
                if data is not None and len(data):
                    self._push(np.asarray(data))


class _Buffer(AudioSource):
    """Buffer of one capture, without a thread: the composite source fills it itself."""


class ProcessLoopbackSource(AudioSource):
    """Sound of one application ("app:Discord.exe"), everything except some applications
    ("exclude:Discord.exe,Spotify.exe") or all the PC sound ("all").

    Snappy's own sound and the sound of audio mixers (SteelSeries Sonar, Voicemeeter..., which
    replay everything, microphone included) are never captured by "exclude" or "all".
    Applications are looked up every 2 s: they can be started after Snappy, or restarted,
    without stopping the recording. When several processes must be left out, the track adds
    up one capture per application that plays sound.
    """

    RESOLVE_EVERY_S = 2.0

    def __init__(self, name: str, source: str, volume: float = 1.0, external_plans: bool = False):
        super().__init__(name, volume)
        self.source = source
        self._parts: dict[tuple[int, int], _Buffer] = {}
        self._wanted: dict[tuple[int, int], float] = {}
        self._resolved = threading.Event()
        # In the audio process, the list of captures comes from Snappy (`set_wanted`): no heavy
        # lookup runs next to the capture threads.
        self.external_plans = external_plans

    def _targets(self) -> list[tuple[int, int, float]]:
        return capture_plan(self.source)

    def set_wanted(self, wanted: dict[tuple[int, int], float]) -> None:
        self._wanted = dict(wanted)
        self._resolved.set()

    def pull(self, frames: int) -> np.ndarray:
        parts = list(self._parts.values())
        if not parts:
            return np.zeros((frames, CHANNELS), dtype=np.float32)
        out = parts[0].pull(frames)
        for part in parts[1:]:
            out += part.pull(frames)
        return out * self.volume

    def _resolve_loop(self) -> None:
        """Separate thread: looks the applications up every 2 s. This lookup (processes, audio
        sessions) takes up to a few hundred ms: done in the capture thread, it blocked reading
        the packets and Windows dropped some (a glitch every 2 s)."""
        import comtypes

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass
        while not self._stop.is_set():
            try:
                self._wanted = {(pid, mode): gain for pid, mode, gain in self._targets()}
            except Exception:
                log.exception("Track %s: application lookup", self.name)
            self._resolved.set()
            self._stop.wait(self.RESOLVE_EVERY_S)

    def _run(self) -> None:
        import comtypes

        from . import wasapi

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass                    # already initialized in this thread (comtypes import)
        if not self.external_plans:
            threading.Thread(target=self._resolve_loop, name=f"audio-{self.name}-apps", daemon=True).start()
        caps: dict[tuple[int, int], wasapi.ProcessLoopback] = {}
        failed: set[tuple[int, int]] = set()
        applied = None
        try:
            while not self._stop.is_set():
                wanted = self._wanted
                if wanted is not applied:          # new list from the lookup thread
                    applied = wanted
                    for t in [t for t in caps if t not in wanted]:
                        caps.pop(t).close()
                        self._parts.pop(t, None)
                        log.info("Track %s: capture of pid %s ended", self.name, t[0])
                    for t, gain in wanted.items():
                        if t in caps:
                            caps[t].gain = gain          # same capture, compensation adjusted
                        elif t not in failed:
                            try:
                                caps[t] = wasapi.ProcessLoopback(t[0], t[1], gain)
                                self._parts[t] = _Buffer(self.name)
                                log.info("Track %s: capturing %s (pid %s, %s, gain %.2f)", self.name, self.source,
                                         t[0], "only" if t[1] == wasapi.LOOPBACK_INCLUDE else "all except", gain)
                            except OSError as exc:
                                failed.add(t)
                                log.warning("Track %s: capture failed (%s)", self.name, exc)
                if not caps:
                    self._resolved.wait(0.1)
                    self._resolved.clear()
                    continue
                single = len(caps) == 1
                for t, cap in list(caps.items()):
                    for block in cap.read(20 if single else 0):
                        self._parts[t]._push(block)
                if not single:
                    time.sleep(0.003)
        finally:
            for cap in caps.values():
                cap.close()


def capture_plan(source: str) -> list[tuple[int, int, float]]:
    """Captures [(pid, mode, gain)] of an "app:...", "exclude:..." or "all" source."""
    from . import wasapi

    kind, _, apps = source.partition(":")
    if kind == "app":
        return wasapi.app_plan(apps)
    excluded = [a for a in apps.split(",") if a.strip()] if kind == "exclude" else []
    return wasapi.exclusion_plan(excluded)


def make_capture_source(track: dict, external_plans: bool = False) -> AudioSource:
    """Source of a track (see `models.settings.audio_tracks`)."""
    source = track["source"]
    if source.startswith("mic:"):
        return SoundcardSource(track["label"], loopback=False, device_name=source[4:])
    from . import wasapi

    if wasapi.AVAILABLE:
        return ProcessLoopbackSource(track["label"], source, external_plans=external_plans)
    # Windows too old: all the PC sound, without telling applications apart.
    return SoundcardSource(track["label"], loopback=True)


def list_microphones() -> list[str]:
    try:
        import soundcard as sc
        return [m.name for m in sc.all_microphones()]
    except Exception:
        return []


def audio_apps() -> list[tuple[str, str]]:
    from . import wasapi

    return wasapi.audio_apps()


def prepare_audio_process() -> None:
    """Start of the audio process: COM in multithreaded mode everywhere. "soundcard"
    (microphone) initializes it when imported and rejects a COM already set up: it is imported
    first, then comtypes (which follows sys.coinit_flags)."""
    import importlib

    try:
        importlib.import_module("soundcard")
    except Exception:
        log.exception("soundcard import")
    sys.coinit_flags = 0              # COINIT_MULTITHREADED
    importlib.import_module("comtypes")
