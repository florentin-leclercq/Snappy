"""Dedicated audio process: captures the tracks and feeds FFmpeg, sheltered from the rest of Snappy.

Windows gives a "process loopback" capture only a 10 ms buffer: if the thread reading it is not
served in time, the packet is lost without any notice (a "click", like a dusty record). In the
main process, the Qt interface, the thumbnails or the application lookup regularly keep the
Python interpreter busy for longer than that. Here, the process only reads the captures and feeds
FFmpeg.

Snappy (`AudioWorker`) looks up the applications playing sound and sends the audio process the
list of captures of each track every 2 s.
"""

from __future__ import annotations

import logging
import multiprocessing
import sys
import threading

from ..i18n import tr

log = logging.getLogger(__name__)

PLAN_EVERY_S = 2.0
READY_TIMEOUT_S = 20.0


# ----------------------------------------------------------------------------
# Audio process side
# ----------------------------------------------------------------------------
def worker_main(conn, pipe_name: str, tracks: list[dict], log_path: str = "") -> None:
    """Entry point of the audio process."""
    if log_path:
        logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(name)s [audio] %(message)s")
    sys.setswitchinterval(0.0005)     # capture threads get the interpreter back as soon as possible
    from ..platforms import system

    system.boost_priority()
    system.prepare_audio_process()    # before any other audio import (COM initialization)
    from .audio import CHANNELS, AudioPump, make_source, open_pipe

    send_lock = threading.Lock()

    def send(msg) -> None:
        with send_lock:
            try:
                conn.send(msg)
            except (OSError, EOFError):
                pass

    try:
        sources = [make_source(t, external_plans=True) for t in tracks]
        pipe = open_pipe(pipe_name, channels=CHANNELS * len(sources))
    except Exception as exc:
        send(("failed", str(exc)))
        return
    pump = AudioPump(sources, pipe, on_error=lambda m: send(("error", m)))
    pump.start()
    send(("ready",))
    try:
        while True:
            msg = conn.recv()
            if msg[0] == "plan":
                _, index, wanted = msg
                if hasattr(sources[index], "set_wanted"):
                    sources[index].set_wanted(wanted)
            elif msg[0] == "stats":
                send(("stats", [(s.name, s.underruns, getattr(s, "dropped", 0)) for s in sources]))
            elif msg[0] == "stop":
                break
    except (EOFError, OSError):
        pass                          # Snappy is gone: stop with it
    finally:
        pump.stop()


# ----------------------------------------------------------------------------
# Snappy side
# ----------------------------------------------------------------------------
class AudioWorker:
    """Starts the audio process, sends it the captures to make, and stops it.
    Used like `AudioPump` by the recorder: start() then stop()."""

    def __init__(self, tracks: list[dict], pipe_name: str, on_error=None, log_path: str = ""):
        self.tracks = tracks
        self.pipe_name = pipe_name
        self.on_error = on_error
        self.log_path = log_path
        self.proc = None
        self._conn = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=worker_main, args=(child, self.pipe_name, self.tracks, self.log_path),
                           name="snappy-audio", daemon=True)
        try:
            proc.start()
        finally:
            child.close()
        self.proc = proc
        self._conn = parent
        try:
            from ..platforms import system

            system.kill_with_parent_pid(self.proc.pid)       # dies with Snappy, even after a crash
        except Exception:
            pass
        if not parent.poll(READY_TIMEOUT_S):
            self.stop()
            raise RuntimeError("The audio process does not answer")
        msg = parent.recv()
        if msg[0] != "ready":
            self.stop()
            raise RuntimeError(f"Audio capture failed: {msg[1] if len(msg) > 1 else msg}")
        self._send_plans()
        threading.Thread(target=self._loop, name="audio-plans", daemon=True).start()

    def _send(self, msg) -> bool:
        with self._lock:
            if self._conn is None:
                return False
            try:
                self._conn.send(msg)
                return True
            except (OSError, EOFError, ValueError):
                return False

    def _send_plans(self) -> None:
        from .audio import capture_plan

        for i, t in enumerate(self.tracks):
            source = t["source"]
            if source.startswith("mic:"):
                continue
            try:
                wanted = {(pid, mode): gain for pid, mode, gain in capture_plan(source)}
            except Exception:
                log.exception("Track %s: application lookup", t.get("label"))
                continue
            self._send(("plan", i, wanted))

    def _loop(self) -> None:
        while not self._stop.wait(PLAN_EVERY_S):
            self._drain()
            if self.proc is None or not self.proc.is_alive():
                if not self._stop.is_set() and self.on_error:
                    self.on_error(tr("The audio process stopped"))
                return
            self._send_plans()

    def _drain(self) -> None:
        """Messages from the audio process (stream errors)."""
        conn = self._conn
        try:
            while conn is not None and conn.poll():
                msg = conn.recv()
                if msg[0] == "error" and self.on_error:
                    self.on_error(msg[1])
        except (OSError, EOFError):
            pass

    def stop(self) -> None:
        self._stop.set()
        self._send(("stop",))
        if self.proc is not None:
            try:
                self.proc.join(timeout=3)
                if self.proc.is_alive():
                    self.proc.terminate()
                    self.proc.join(timeout=2)
            except (AssertionError, OSError, ValueError):
                pass
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
