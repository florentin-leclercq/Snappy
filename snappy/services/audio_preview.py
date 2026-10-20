"""Playback of the separate tracks in the editor, mixed live (volume, mute, solo, curves).

The video player (QMediaPlayer) only plays one audio track at a time: the editor mutes it and
this module plays the mix of the separate tracks, decoded in memory, following the position of
the video (resynchronized when the gap exceeds 80 ms).
"""

from __future__ import annotations

import threading

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from . import media

RATE = 48_000
BYTES_PER_FRAME = 4          # stereo int16
RESYNC_S = 0.08


class TrackMix:
    """Setting of a track: overall gain, mute, solo, curve [(t, level)]."""

    def __init__(self, gain: float = 1.0):
        self.gain = gain
        self.muted = False
        self.solo = False
        self.points: list[tuple[float, float]] = []


class AudioPreview(QObject):
    ready = Signal(bool)          # tracks decoded (False: failure -> the player keeps its own sound)
    _decoded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks: list[np.ndarray] = []
        self.mix: list[TrackMix] = []
        self.monitor = 1.0           # listening volume (never part of the export)
        self.playing = False
        self._write_pos = 0          # in samples
        self._sink: QAudioSink | None = None
        self._dev = None
        self._timer = QTimer(self, interval=10, timeout=self._pump)
        self._position_source = None
        self._token = 0
        self._decoded.connect(self._on_decoded)

    # ------------------------------------------------------------ loading
    def load(self, path: str, streams: list[int], gains: list[float]) -> None:
        self.stop()
        self.tracks = []
        self.mix = [TrackMix(g) for g in gains]
        self._token += 1
        token = self._token

        def work():
            try:
                arrays = media.decode_tracks(path, streams, RATE)
            except Exception:
                arrays = None
            self._decoded.emit((token, arrays))

        threading.Thread(target=work, name="decode-audio", daemon=True).start()

    def _on_decoded(self, payload) -> None:
        token, arrays = payload
        if token != self._token:
            return
        self.tracks = arrays or []
        self.ready.emit(bool(arrays))

    @property
    def loaded(self) -> bool:
        return bool(self.tracks)

    # ------------------------------------------------------------ playback
    def attach(self, position_source) -> None:
        """position_source() -> position of the video in seconds (the master clock)."""
        self._position_source = position_source

    def play(self, t: float) -> None:
        if not self.loaded:
            return
        if self._sink is None:
            fmt = QAudioFormat()
            fmt.setSampleRate(RATE)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.Int16)
            self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
            self._sink.setBufferSize(int(RATE * BYTES_PER_FRAME * 0.12))
        self._sink.reset()
        self._dev = self._sink.start()
        self._write_pos = int(t * RATE)
        self.playing = True
        self._timer.start()
        self._pump()

    def pause(self) -> None:
        self.playing = False
        self._timer.stop()
        if self._sink:
            self._sink.reset()
            self._sink.stop()
        self._dev = None

    def stop(self) -> None:
        self.pause()

    def seek(self, t: float) -> None:
        if self.playing:
            self.play(t)

    def _played_pos(self) -> float:
        if not self._sink:
            return self._write_pos / RATE
        buffered = (self._sink.bufferSize() - self._sink.bytesFree()) / BYTES_PER_FRAME
        return (self._write_pos - buffered) / RATE

    def _pump(self) -> None:
        if not self.playing or not self._dev or not self._sink:
            return
        if self._position_source:
            target = self._position_source()
            if abs(self._played_pos() - target) > RESYNC_S:
                self._sink.reset()
                self._dev = self._sink.start()
                self._write_pos = int(target * RATE)
        free = self._sink.bytesFree() // BYTES_PER_FRAME
        if free <= 0:
            return
        chunk = self.render(self._write_pos, free)
        self._dev.write(chunk.tobytes())
        self._write_pos += free

    # ------------------------------------------------------------ mixing
    def render(self, start: int, n: int) -> np.ndarray:
        """n samples of the mix from `start` (stereo int16)."""
        out = np.zeros((n, 2), dtype=np.float32)
        solo = any(m.solo for m in self.mix)
        for arr, m in zip(self.tracks, self.mix):
            if m.muted or (solo and not m.solo):
                continue
            seg = arr[start:start + n]
            if not len(seg):
                continue
            seg = seg.astype(np.float32) / 32768.0
            if m.points:
                ts = (start + np.arange(len(seg))) / RATE
                pts = sorted(m.points)
                env = np.interp(ts, [p[0] for p in pts], [p[1] for p in pts])
                seg = seg * (m.gain * env)[:, None]
            else:
                seg = seg * m.gain
            out[:len(seg)] += seg
        out *= self.monitor
        return (np.clip(out, -1.0, 1.0) * 32767).astype("<i2")

    def peaks(self, index: int, buckets: int) -> np.ndarray:
        if index >= len(self.tracks):
            return np.zeros(buckets, dtype=np.float32)
        return media.peaks(self.tracks[index], buckets)
