"""Native WASAPI (comtypes): sound of one given application, and the apps that play sound.

Windows 10 2004+ can capture the sound of a single process (and its children), or all the
sound EXCEPT a process: this is "process loopback"
(ActivateAudioInterfaceAsync + AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK).
Nothing is rerouted: the user's audio outputs stay untouched.

An application is named by its executable ("Discord.exe"): it can be started after Snappy or
restarted, the capture finds it again by itself.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import POINTER, Structure, byref, c_int, c_uint32, c_uint64, c_ulong, c_ushort, c_void_p
from ctypes import wintypes

log = logging.getLogger(__name__)

AVAILABLE = sys.platform == "win32" and sys.getwindowsversion().build >= 19041

if sys.platform == "win32":
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    REFERENCE_TIME = ctypes.c_longlong

    class WAVEFORMATEX(Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class BLOB(Structure):
        _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("blob", BLOB)]

    class PROCESS_LOOPBACK_PARAMS(Structure):
        _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", c_int)]

    class ACTIVATION_PARAMS(Structure):
        _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", PROCESS_LOOPBACK_PARAMS)]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer",
                      (["out"], POINTER(c_void_p), "ppData"), (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                      (["out"], POINTER(wintypes.DWORD), "pdwFlags"), (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                      (["out"], POINTER(c_uint64), "pu64QPCPosition")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], c_int, "ShareMode"), (["in"], wintypes.DWORD, "StreamFlags"),
                      (["in"], REFERENCE_TIME, "hnsBufferDuration"), (["in"], REFERENCE_TIME, "hnsPeriodicity"),
                      (["in"], POINTER(WAVEFORMATEX), "pFormat"), (["in"], POINTER(GUID), "AudioSessionGuid")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "pNumBufferFrames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(REFERENCE_TIME), "phnsLatency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], c_int, "ShareMode"),
                      (["in"], POINTER(WAVEFORMATEX), "pFormat"), (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosest")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(REFERENCE_TIME), "phnsDefault"),
                      (["out"], POINTER(REFERENCE_TIME), "phnsMinimum")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
            COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"),
                      (["out"], POINTER(POINTER(IUnknown)), "ppv")),
        ]

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "activateResult"),
                      (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
        ]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [
            COMMETHOD([], HRESULT, "ActivateCompleted",
                      (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation")),
        ]

    class IAgileObject(IUnknown):
        _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
        _methods_ = []

    class _CompletionHandler(comtypes.COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, _operation):
            self.done.set()
            return 0

    # --- audio sessions (apps that play sound) -----------------------------
    class IAudioSessionControl(IUnknown):
        _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "pRetVal")),
            COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(c_void_p), "pRetVal")),
            COMMETHOD([], HRESULT, "SetDisplayName", (["in"], wintypes.LPCWSTR, "Value"), (["in"], POINTER(GUID), "Ctx")),
            COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(c_void_p), "pRetVal")),
            COMMETHOD([], HRESULT, "SetIconPath", (["in"], wintypes.LPCWSTR, "Value"), (["in"], POINTER(GUID), "Ctx")),
            COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "pRetVal")),
            COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "Override"), (["in"], POINTER(GUID), "Ctx")),
            COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
            COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
        ]

    class IAudioSessionControl2(IAudioSessionControl):
        _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(c_void_p), "pRetVal")),
            COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(c_void_p), "pRetVal")),
            COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(wintypes.DWORD), "pRetVal")),
            COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
            COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], wintypes.BOOL, "optOut")),
        ]

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "SessionCount")),
            COMMETHOD([], HRESULT, "GetSession", (["in"], c_int, "SessionCount"),
                      (["out"], POINTER(POINTER(IAudioSessionControl)), "Session")),
        ]

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID), "AudioSessionGuid"),
                      (["in"], wintypes.DWORD, "StreamFlags"), (["out"], POINTER(c_void_p), "SessionControl")),
            COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID), "AudioSessionGuid"),
                      (["in"], wintypes.DWORD, "StreamFlags"), (["out"], POINTER(c_void_p), "AudioVolume")),
            COMMETHOD([], HRESULT, "GetSessionEnumerator",
                      (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "SessionEnum")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], wintypes.DWORD, "dwClsCtx"),
                      (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        ]

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(wintypes.UINT), "pcDevices")),
            COMMETHOD([], HRESULT, "Item", (["in"], wintypes.UINT, "nDevice"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "dataFlow"), (["in"], wintypes.DWORD, "dwStateMask"),
                      (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "dataFlow"), (["in"], c_int, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        ]

    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")

    _mmdevapi = ctypes.WinDLL("Mmdevapi")
    _ActivateAudioInterfaceAsync = _mmdevapi.ActivateAudioInterfaceAsync
    _ActivateAudioInterfaceAsync.restype = HRESULT
    _ActivateAudioInterfaceAsync.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                                             POINTER(IActivateAudioInterfaceCompletionHandler),
                                             POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

# Process loopback only delivers stereo (or mono): when an application plays into a
# multichannel output (SteelSeries Sonar, "7.1" headset...), Windows converts its 7.1 to stereo
# by dividing the front channels by ~4.7 (-13 dB), and the track sounds silent.
# Snappy compensates this (measured) factor from the channel count of the app's output.
DOWNMIX_GAIN = {8: 1 / 0.2142}

LOOPBACK_INCLUDE = 0      # the process and its children
LOOPBACK_EXCLUDE = 1      # all the sound EXCEPT the process and its children
SAMPLE_RATE = 48_000
CHANNELS = 2


# ----------------------------------------------------------------------------
# Processes
# ----------------------------------------------------------------------------
def _norm(name: str) -> str:
    name = name.strip().lower()
    return name if name.endswith(".exe") else name + ".exe"


def find_root_pids(exe_name: str) -> list[int]:
    """PIDs of the root processes with this executable name (Discord starts about ten child
    "Discord.exe": the parent is targeted, the capture includes its whole tree)."""
    import psutil

    target = _norm(exe_name)
    procs = {}
    for p in psutil.process_iter(["pid", "ppid", "name"]):
        try:
            if (p.info["name"] or "").lower() == target:
                procs[p.info["pid"]] = p.info["ppid"]
        except (psutil.Error, KeyError):
            continue
    return sorted(pid for pid, ppid in procs.items() if ppid not in procs)


# Audio mixers that replay all the PC sound (microphone included) to the headset: capturing
# them on top of the apps would double everything and put the microphone in the game track.
AUDIO_ROUTERS = {"steelseriessonar.exe", "voicemeeter.exe", "voicemeeter_x64.exe", "voicemeeterpro.exe",
                 "voicemeeterpro_x64.exe", "voicemeeter8.exe", "voicemeeter8x64.exe", "audiorelay.exe"}
# Playback done by Windows itself ("Listen to this device" replays the microphone).
SYSTEM_PLAYBACK = {"audiodg.exe"}


def _mix_channels(device) -> int:
    try:
        client = device.Activate(byref(IAudioClient._iid_), comtypes.CLSCTX_ALL, None).QueryInterface(IAudioClient)
        fmt = client.GetMixFormat()
        channels = fmt.contents.nChannels
        ctypes.windll.ole32.CoTaskMemFree(fmt)
        return channels
    except Exception:
        return CHANNELS


def session_channels() -> dict[int, set[int]]:
    """{PID: channel counts of the outputs where it has an audio session} (expired sessions left
    out), over every active output: default, headset, virtual output... Sessions that are playing
    win: a session left idle on another output does not count."""
    active: dict[int, set[int]] = {}
    idle: dict[int, set[int]] = {}
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    devices = enum.EnumAudioEndpoints(0, 1)                  # eRender, DEVICE_STATE_ACTIVE
    for d in range(devices.GetCount()):
        try:
            device = devices.Item(d)
            mgr = device.Activate(byref(IAudioSessionManager2._iid_), comtypes.CLSCTX_ALL, None)
            sessions = mgr.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            channels = None
            for i in range(sessions.GetCount()):
                ctl = sessions.GetSession(i)
                state = ctl.GetState()
                if state == 2:                              # AudioSessionStateExpired
                    continue
                pid = ctl.QueryInterface(IAudioSessionControl2).GetProcessId()
                if pid:
                    channels = channels or _mix_channels(device)
                    (active if state == 1 else idle).setdefault(pid, set()).add(channels)
        except Exception:
            continue
    return {pid: active.get(pid) or idle.get(pid, set()) for pid in set(active) | set(idle)}


def _session_pids() -> set[int]:
    return set(session_channels())


def _ancestors(pid: int, procs: dict[int, tuple[int, str]]) -> list[int]:
    chain, seen = [], {pid}
    while True:
        pid = procs.get(pid, (0, ""))[0]
        if not pid or pid in seen or pid not in procs:
            return chain
        chain.append(pid)
        seen.add(pid)


def tree_gain(root: int, channels: dict[int, set[int]], procs: dict[int, tuple[int, str]]) -> float:
    """Compensation of the multichannel -> stereo conversion for the process tree `root`.
    Only when all its sound goes to outputs of the same kind: otherwise a stereo sound would be
    wrongly amplified (and would clip)."""
    counts: set[int] = set()
    for pid, c in channels.items():
        if pid == root or root in _ancestors(pid, procs):
            counts |= c
    return DOWNMIX_GAIN.get(next(iter(counts)), 1.0) if len(counts) == 1 else 1.0


def _include_plan(pids, channels, procs, banned=frozenset()) -> list[tuple[int, int, float]]:
    """One "only" capture per process that plays sound, each with its compensation.

    A capture always includes the child processes: when a parent and a child both play (Steam
    and its game), only the child is taken, so it is not heard twice nor captured with its
    parent's compensation. A tree that contains a left-out process (audio mixer, chat app...)
    is never taken as a whole."""
    banned_lines = {b: set(_ancestors(b, procs)) for b in banned}
    included: set[int] = set()
    for pid in sorted((p for p in pids if p in procs), key=lambda p: -len(_ancestors(p, procs))):
        line = _ancestors(pid, procs)
        if pid in banned or any(a in banned for a in line):
            continue
        if any(pid in up for up in banned_lines.values()):
            continue
        if any(pid in _ancestors(i, procs) for i in included):
            continue                                        # one of its children is already captured
        included.add(pid)
    return [(pid, LOOPBACK_INCLUDE, tree_gain(pid, channels, procs)) for pid in sorted(included)]


def _process_table() -> dict[int, tuple[int, str]]:
    import psutil

    procs: dict[int, tuple[int, str]] = {}
    for p in psutil.process_iter(["pid", "ppid", "name"]):
        try:
            procs[p.info["pid"]] = (p.info["ppid"] or 0, (p.info["name"] or "").lower())
        except (psutil.Error, KeyError):
            continue
    return procs


_state_lock = threading.Lock()
_state: tuple[float, dict, dict] = (0.0, {}, {})


def system_state(max_age: float = 1.5) -> tuple[dict[int, tuple[int, str]], dict[int, set[int]]]:
    """(processes, audio sessions) shared by every track: a single snapshot every `max_age`
    seconds instead of one per track. The snapshot is Python work (hundreds of processes, COM
    objects): done by every track at once, it blocked the interpreter longer than the audio
    reserve, hence a glitch every 2 s."""
    global _state
    import time

    with _state_lock:
        stamp, procs, channels = _state
        if time.monotonic() - stamp > max_age:
            procs, channels = _process_table(), session_channels()
            _state = (time.monotonic(), procs, channels)
        return procs, channels


# "Snappy.exe" means Snappy itself, whether it runs as an .exe or through Python (run.bat:
# pythonw.exe). Capture plans are computed in the main process, the one that plays the sound of
# the editor, the gallery and the save confirmation.
SELF_NAME = "snappy.exe"
APP_PID = os.getpid()


def _roots_in(procs: dict[int, tuple[int, str]], exe_name: str) -> list[int]:
    target = _norm(exe_name)
    if target == SELF_NAME:
        return [APP_PID] if APP_PID in procs else []
    mine = {pid for pid, (_pp, n) in procs.items() if n == target}
    return sorted(pid for pid in mine if procs[pid][0] not in mine)


def app_plan(exe_name: str) -> list[tuple[int, int, float]]:
    """Captures [(pid, mode, gain)] of an application's sound: one per process of the app that
    has an audio session, each compensated for its output (Discord's voice goes out in stereo on
    one output, its system sounds on another in 7.1...). Without a session: the whole tree."""
    procs, channels = system_state()
    if _norm(exe_name) == SELF_NAME:
        # Snappy itself: the main process, never its audio process (a child, which has its own
        # session for the microphone and would get the parent left out).
        if APP_PID not in procs:
            return []
        own = channels.get(APP_PID, set())
        return [(APP_PID, LOOPBACK_INCLUDE, DOWNMIX_GAIN.get(next(iter(own)), 1.0) if len(own) == 1 else 1.0)]
    roots = _roots_in(procs, exe_name)
    if not roots:
        return []
    root = roots[0]
    mine = [pid for pid in channels if pid == root or root in _ancestors(pid, procs)]
    if not mine:
        return [(root, LOOPBACK_INCLUDE, 1.0)]
    return _include_plan(mine, channels, procs)


def exclusion_plan(excluded: list[str]) -> list[tuple[int, int, float]]:
    """Captures [(pid, mode)] that give "all the sound except these applications, Snappy and
    the audio mixers".

    Windows only excludes one process per capture. If only Snappy has to be left out and
    everything plays in stereo, a single "all except" capture is enough; otherwise one "only"
    capture per application that plays sound is added up (looked up at each call), each with its
    multichannel compensation.
    """
    own = APP_PID
    names = {_norm(e) for e in excluded if e.strip()}
    procs, channels = system_state()
    routers = {pid for pid, (_pp, n) in procs.items() if n in AUDIO_ROUTERS}
    if not routers and names <= {SELF_NAME} and not any(set(DOWNMIX_GAIN) & c for c in channels.values()):
        return [(own, LOOPBACK_EXCLUDE, 1.0)]

    banned = {pid for pid, (_pp, n) in procs.items() if n in names or n in SYSTEM_PLAYBACK} | routers | {own}
    return _include_plan(channels, channels, procs, banned)


# ----------------------------------------------------------------------------
# Capture of a process
# ----------------------------------------------------------------------------
class ProcessLoopback:
    """"Process loopback" capture client: float32 stereo 48 kHz."""

    def __init__(self, pid: int, mode: int, gain: float = 1.0):
        self.gain = gain
        params = ACTIVATION_PARAMS()
        params.ActivationType = 1                    # AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = pid
        params.ProcessLoopbackParams.ProcessLoopbackMode = mode
        prop = PROPVARIANT()
        prop.vt = 65                                 # VT_BLOB
        prop.blob.cbSize = ctypes.sizeof(params)
        prop.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()
        op = POINTER(IActivateAudioInterfaceAsyncOperation)()
        _ActivateAudioInterfaceAsync("VAD\\Process_Loopback", byref(IAudioClient._iid_), byref(prop),
                                     handler.QueryInterface(IActivateAudioInterfaceCompletionHandler), byref(op))
        if not handler.done.wait(5):
            raise OSError("Process loopback activation took too long")
        hr, unk = op.GetActivateResult()
        if hr < 0:
            raise OSError(hr, f"Process loopback activation refused (0x{hr & 0xFFFFFFFF:08X})")
        self.client = unk.QueryInterface(IAudioClient)

        fmt = WAVEFORMATEX(wFormatTag=3, nChannels=CHANNELS, nSamplesPerSec=SAMPLE_RATE,   # IEEE float
                           wBitsPerSample=32, nBlockAlign=4 * CHANNELS,
                           nAvgBytesPerSec=SAMPLE_RATE * 4 * CHANNELS, cbSize=0)
        flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000  # LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY
        self.client.Initialize(0, flags, 10_000_000, 0, byref(fmt), None)   # 1 s buffer
        self.event = _k32.CreateEventW(None, False, False, None)
        self.client.SetEventHandle(self.event)
        self.capture = self.client.GetService(byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
        self.client.Start()

    def read(self, timeout_ms: int = 50):
        """Available blocks (numpy float32 (n, 2)), after waiting at most timeout_ms."""
        import numpy as np

        _k32.WaitForSingleObject(self.event, timeout_ms)
        blocks = []
        while True:
            if not self.capture.GetNextPacketSize():
                break
            data, frames, flags, _pos, _qpc = self.capture.GetBuffer()
            if frames:
                if flags & 0x2 or not data:                 # AUDCLNT_BUFFERFLAGS_SILENT
                    blocks.append(np.zeros((frames, CHANNELS), dtype=np.float32))
                else:
                    buf = (ctypes.c_float * (frames * CHANNELS)).from_address(data)
                    block = np.frombuffer(buf, dtype=np.float32).reshape(frames, CHANNELS)
                    blocks.append(block * self.gain if self.gain != 1.0 else block.copy())
            self.capture.ReleaseBuffer(frames)
        return blocks

    def close(self) -> None:
        try:
            self.client.Stop()
        except Exception:
            pass
        if self.event:
            _k32.CloseHandle(self.event)
            self.event = None


# ----------------------------------------------------------------------------
# Applications that play sound (offered in the settings)
# ----------------------------------------------------------------------------
def audio_apps() -> list[tuple[str, str]]:
    """[(executable, readable name)] of the applications that have an audio session, except
    Snappy, audio mixers and Windows itself."""
    import psutil

    from .system import exe_description

    procs, channels = system_state(max_age=0)
    skip = AUDIO_ROUTERS | SYSTEM_PLAYBACK
    found: dict[str, tuple[str, str]] = {}
    for pid in channels:
        if pid == APP_PID or APP_PID in _ancestors(pid, procs) or procs.get(pid, (0, ""))[1] in skip:
            continue
        try:
            p = psutil.Process(pid)
            exe, path = p.name(), p.exe()
        except (psutil.Error, OSError):
            continue
        if exe and exe.lower() not in found:
            found[exe.lower()] = (exe, exe_description(path) or exe.rsplit(".", 1)[0])
    return sorted(found.values(), key=lambda t: t[1].lower())
