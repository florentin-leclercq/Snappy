"""Windows: subprocess options (FFmpeg, audio process) and the "job" that stops them together
with Snappy."""

from __future__ import annotations

import subprocess

EXE_SUFFIX = ".exe"
# No console window for each FFmpeg process.
NO_WINDOW = 0x08000000                       # CREATE_NO_WINDOW
# Background tasks (thumbnails, exports, probes): low priority, the game comes first.
BACKGROUND = NO_WINDOW | 0x00004000          # + BELOW_NORMAL_PRIORITY_CLASS

_job = None


def kill_with_parent(proc: subprocess.Popen) -> None:
    """Attaches the process to a Windows "job" closed with Snappy: even if Snappy crashes or is
    killed, FFmpeg stops with it instead of writing on into the buffer."""
    _assign_to_job(int(proc._handle))


def kill_with_parent_pid(pid: int) -> None:
    """Same for a process known by its PID (multiprocessing audio process)."""
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x0101, False, pid)          # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if handle:
        try:
            _assign_to_job(handle)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))


def _assign_to_job(handle: int) -> None:
    global _job
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    try:
        if _job is None:
            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _job = job
        k32.AssignProcessToJobObject(_job, handle)
    except Exception:
        pass


def boost_priority() -> None:
    """Above-normal priority (audio process: it must never wait)."""
    import ctypes

    ABOVE_NORMAL_PRIORITY_CLASS = 0x8000
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),
                                            ABOVE_NORMAL_PRIORITY_CLASS)
