"""Linux: subprocess options, and subprocesses stopped together with Snappy."""

from __future__ import annotations

import atexit
import ctypes
import os
import signal
import subprocess
import weakref

EXE_SUFFIX = ""
NO_WINDOW = 0      # creationflags: meaningless outside Windows
BACKGROUND = 0

_children: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()
_child_pids: set[int] = set()
PR_SET_PDEATHSIG = 1


def die_with_parent() -> None:
    """preexec_fn: the kernel sends SIGTERM to the process if Snappy dies (even killed or crashed).
    Beware: the signal follows the thread that started the process, which must live as long."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except OSError:
        pass


def kill_with_parent(proc: subprocess.Popen) -> None:
    """The process (FFmpeg) is stopped when Snappy exits."""
    _children.add(proc)


def kill_with_parent_pid(pid: int) -> None:
    _child_pids.add(pid)


def boost_priority() -> None:
    pass


@atexit.register
def _kill_children() -> None:
    for proc in list(_children):
        if proc.poll() is None:
            proc.kill()
    for pid in _child_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
