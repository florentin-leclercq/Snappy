"""Audio tracks (one per source) and delivery to FFmpeg: the system-independent part.

Each source (game, voice chat, microphone, application...) fills its own buffer; the capture
itself depends on the system (see `platforms`; on Windows: WASAPI process loopback and microphone).

All the tracks go into ONE single pipe, interleaved (track 1 = channels 0-1, track 2 = channels
2-3...): FFmpeg splits them afterwards. They stay aligned to the sample.

The stream is paced on the wall clock: when no sound is playing, nothing is delivered, so the
gap is filled with silence. The audio keeps exactly the same pace as the video.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable

import numpy as np

from ..i18n import tr

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
BLOCK_FRAMES = 480          # 10 ms
MAX_BACKLOG_S = 0.25        # beyond this, late audio is dropped to stay in sync
# Lead reserve: a source only delivers after it has gathered this much sound. WASAPI delivers in
# packets (10 ms, sometimes late): without a reserve, the last piece is missing at every read of
# the pump and the sound is choppy (small "clicks" like a dusty record).
PRIME_S = 0.06
DRIFT_MAX = 0.005           # maximum speed correction (+/-0.5 %)
DRIFT_TIME_S = 10.0         # 10 ms off -> speed +0.1 % (a typical drift)


def open_pipe(name: str, channels: int = CHANNELS):
    """Pipe that FFmpeg opens for reading (named pipe on Windows): wait_for_reader, write, close."""
    from ..platforms import system

    return system.open_audio_pipe(name, channels)


# ----------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------
class AudioSource:
    """Fills a buffer with float32 blocks (frames, 2) in a dedicated thread."""

    def __init__(self, name: str, volume: float = 1.0):
        self.name = name
        self.volume = volume
        self._buf: collections.deque[np.ndarray] = collections.deque()
        self._buffered = 0
        self._primed = False
        self.underruns = 0                # reserve ran out along the way (diagnostics)
        self.dropped = 0                  # samples dropped (huge delay)
        self._frac = 0.0                  # carried-over sample fraction (resampling)
        self._err = 0.0                   # average gap between the reserve and its target
        self._last = np.zeros((1, CHANNELS), dtype=np.float32)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    # To implement: loop that calls self._push(block) until self._stop.
    def _run(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def start(self) -> None:
        self._thread = threading.Thread(target=self._safe_run, name=f"audio-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _safe_run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # device unplugged, etc.
            self.error = str(exc)
            log.warning("Audio source %s stopped: %s", self.name, exc)

    def _push(self, block: np.ndarray) -> None:
        if block.ndim == 1:
            block = np.repeat(block[:, None], CHANNELS, axis=1)
        elif block.shape[1] == 1:
            block = np.repeat(block, CHANNELS, axis=1)
        elif block.shape[1] > CHANNELS:
            block = block[:, :CHANNELS]
        with self._lock:
            self._buf.append(block.astype(np.float32, copy=False))
            self._buffered += len(block)

    def pull(self, frames: int) -> np.ndarray:
        """Returns exactly `frames` samples (padded with silence).

        The sound is played PRIME_S late: the source waits for this reserve before delivering,
        and builds it up again if it empties (the app went quiet, nothing is lost).

        Clock drift: an audio output (USB headset, Sonar...) never runs exactly at the speed of
        the PC clock, so the reserve slowly grows or shrinks. Rather than dropping 10 ms at once
        (a "click"), the track is read a tiny bit faster or slower (at most +/-0.5 %, inaudible)
        to bring it back to its target size."""
        out = np.zeros((frames, CHANNELS), dtype=np.float32)
        with self._lock:
            # Huge delay (resuming after a pause...): only then is audio dropped.
            limit = int((MAX_BACKLOG_S + PRIME_S) * SAMPLE_RATE) + frames
            while self._buffered > limit and self._buf:
                dropped = self._buf.popleft()
                self._buffered -= len(dropped)
                self.dropped += len(dropped)
            if not self._primed:
                if self._buffered < int(PRIME_S * SAMPLE_RATE) + frames:
                    return out
                self._primed = True
            target = PRIME_S * SAMPLE_RATE
            error = (self._buffered - frames) - target            # samples in excess (+) or missing (-)
            # Moving average (~1 s): WASAPI delivers 10 ms packets, the reserve oscillates.
            k = min(1.0, frames / SAMPLE_RATE)
            self._err += (error - self._err) * k
            ratio = 1.0 + max(-DRIFT_MAX, min(DRIFT_MAX, self._err / (DRIFT_TIME_S * SAMPLE_RATE)))
            self._frac += frames * ratio
            need = max(1, int(self._frac))
            self._frac -= need
            if self._buffered < need:
                self._primed = False      # reserve ran out: build it up again
                self.underruns += 1
                need = self._buffered
            chunk = self._take(need)
        if len(chunk) == 0:
            return out
        if len(chunk) == frames:
            out[:] = chunk
        else:
            # Linear resampling from `need` to `frames`, joined to the previous block.
            src = np.concatenate([self._last, chunk])
            pos = (np.arange(1, frames + 1) * (len(chunk) / frames))
            idx = np.arange(len(src))
            for c in range(CHANNELS):
                out[:, c] = np.interp(pos, idx, src[:, c])
        self._last = chunk[-1:]
        return out * self.volume

    def _take(self, n: int) -> np.ndarray:
        """Takes n samples out of the buffer (lock already held)."""
        out = np.empty((n, CHANNELS), dtype=np.float32)
        pos = 0
        while pos < n and self._buf:
            head = self._buf[0]
            take = min(n - pos, len(head))
            out[pos:pos + take] = head[:take]
            pos += take
            if take == len(head):
                self._buf.popleft()
            else:
                self._buf[0] = head[take:]
            self._buffered -= take
        return out[:pos]


def capture_plan(source: str) -> list[tuple[int, int, float]]:
    """Captures [(pid, mode, gain)] of an "app:...", "exclude:..." or "all" source."""
    from ..platforms import system

    return system.capture_plan(source)


def make_source(track: dict, test_index: int | None = None, external_plans: bool = False) -> AudioSource:
    """Source of a track (see `models.settings.audio_tracks`). test_index: test beep instead."""
    if test_index is not None:
        return ToneSource(track.get("label", "tone"), freq=330.0 * (1 + test_index * 0.5))
    from ..platforms import system

    return system.make_capture_source(track, external_plans)


class ToneSource(AudioSource):
    """Test source: a regular beep (used by the tests and the demo mode)."""

    def __init__(self, name: str = "tone", freq: float = 440.0, volume: float = 0.3):
        super().__init__(name, volume)
        self.freq = freq

    def _run(self) -> None:
        t0 = time.monotonic()
        produced = 0
        while not self._stop.is_set():
            due = int((time.monotonic() - t0) * SAMPLE_RATE)
            if due - produced >= BLOCK_FRAMES:
                n = due - produced
                t = (np.arange(produced, produced + n) / SAMPLE_RATE)
                wave = np.sin(2 * np.pi * self.freq * t).astype(np.float32)
                self._push(wave)
                produced += n
            time.sleep(0.005)


# ----------------------------------------------------------------------------
# Paced mixer
# ----------------------------------------------------------------------------
class AudioPump:
    """Writes the sources side by side (2 channels each) as PCM s16le 48 kHz, at real-time speed."""

    def __init__(self, sources: list[AudioSource], pipe,
                 on_error: Callable[[str], None] | None = None):
        self.sources = sources
        self.pipe = pipe
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        for s in self.sources:
            s.start()
        self._thread = threading.Thread(target=self._run, name="audio-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for s in self.sources:
            s.stop()
        self.pipe.close()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        if not self.pipe.wait_for_reader() or self._stop.is_set():
            return
        t0 = time.monotonic()
        written = 0
        try:
            while not self._stop.is_set():
                due = int((time.monotonic() - t0) * SAMPLE_RATE) - written
                if due < BLOCK_FRAMES:
                    time.sleep(0.004)
                    continue
                frames = np.concatenate([s.pull(due) for s in self.sources], axis=1)
                pcm = (np.clip(frames, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                self.pipe.write(pcm)
                written += due
        except (BrokenPipeError, OSError) as exc:
            if not self._stop.is_set() and self.on_error:
                self.on_error(tr("Audio stream interrupted: {error}", error=exc))
