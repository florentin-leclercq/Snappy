"""Diagnostics (Windows): which processes play sound, on which output, at which level.

    .\\.venv\\Scripts\\python scripts\\audio_sessions.py [seconds] [filter]

Example, during a Discord call with screen sharing:
    .\\.venv\\Scripts\\python scripts\\audio_sessions.py 10 discord
"""

import sys

sys.coinit_flags = 0   # multithreaded COM, like Snappy's capture

import time  # noqa: E402
from ctypes import POINTER, byref, c_float, wstring_at  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes  # noqa: E402
import psutil  # noqa: E402
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown  # noqa: E402

from snappy.platforms.windows import wasapi as w  # noqa: E402


class IAudioMeterInformation(IUnknown):
    _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    _methods_ = [COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "p"))]


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    flt = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    enum = comtypes.CoCreateInstance(w.CLSID_MMDeviceEnumerator, w.IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    devices = enum.EnumAudioEndpoints(0, 1)
    rows = []
    for d in range(devices.GetCount()):
        dev = devices.Item(d)
        ch = w._mix_channels(dev)
        mgr = dev.Activate(byref(w.IAudioSessionManager2._iid_), comtypes.CLSCTX_ALL, None)
        sessions = mgr.QueryInterface(w.IAudioSessionManager2).GetSessionEnumerator()
        for i in range(sessions.GetCount()):
            ctl = sessions.GetSession(i)
            c2 = ctl.QueryInterface(w.IAudioSessionControl2)
            pid = c2.GetProcessId()
            try:
                proc = psutil.Process(pid)
                name, ppid = proc.name(), proc.ppid()
                cmd = " ".join(proc.cmdline())
            except psutil.Error:
                name, ppid, cmd = "?", 0, ""
            if flt and flt not in name.lower():
                continue
            kind = next((part.split("=")[1] for part in cmd.split() if part.startswith("--type=")), "")
            sid = wstring_at(c2.GetSessionInstanceIdentifier())
            rows.append({"dev": d, "ch": ch, "pid": pid, "ppid": ppid, "name": name, "type": kind,
                         "state": ("inactive", "active", "expired")[ctl.GetState()], "sid": sid[-12:],
                         "meter": ctl.QueryInterface(IAudioMeterInformation), "peak": 0.0})
    print(f"Measuring for {seconds:.0f} s...")
    t0 = time.time()
    while time.time() - t0 < seconds:
        for r in rows:
            r["peak"] = max(r["peak"], r["meter"].GetPeakValue())
        time.sleep(0.01)
    print(f"{'output':>6} {'chans':>6} {'pid':>7} {'parent':>7}  {'process':<22} {'type':<16} {'state':<9} "
          f"{'peak':>6}  session")
    for r in sorted(rows, key=lambda r: (r["name"], r["pid"])):
        print(f"{r['dev']:>6} {r['ch']:>6} {r['pid']:>7} {r['ppid']:>7}  {r['name'][:22]:<22} {r['type'][:16]:<16} "
              f"{r['state']:<9} {r['peak']:>6.3f}  …{r['sid']}")


if __name__ == "__main__":
    main()
