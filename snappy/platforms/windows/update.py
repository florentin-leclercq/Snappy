"""Windows: installing an update of Snappy.exe (PyInstaller) downloaded by `snappy.update`.

Snappy.exe cannot replace its own folder while it runs: the new Snappy.exe, unpacked in the
staging folder, is started with --install-update. It shows the update window, waits for the old
Snappy to quit, swaps the folders (`update.install_folder`, the previous one is put back if the
copy fails) and starts the installed Snappy. PyInstaller folders are deep (numpy licenses...):
the paths go beyond 260 characters, hence `long_path`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .process import NO_WINDOW

# Snappy.exe updates itself from the release's Windows zip (made by build.ps1).
SELF_UPDATE = True
UPDATE_ASSET = r"windows.*\.zip$"
# The sources (run.bat) are updated from a git clone only.
SOURCE_ASSET = ""


def long_path(path: Path) -> str:
    """Path usable beyond 260 characters."""
    text = str(path.resolve())
    return text if text.startswith("\\\\") else "\\\\?\\" + text


def install_update(new_exe: Path, target: Path, version: str, wait_pid: int, args: list[str]) -> bool:
    """Starts the new Snappy.exe as installer of `target` once process `wait_pid` has quit; it then
    starts Snappy again with `args`. The caller quits right after."""
    cmd = [str(new_exe), "--install-update", str(target), "--update-version", version,
           "--wait-pid", str(wait_pid), "--relaunch", json.dumps(args)]
    # It must outlive Snappy: own process group, out of any job Snappy was started in.
    flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(cmd, cwd=str(new_exe.parent), creationflags=flags, close_fds=True)
    except OSError:      # the job Snappy runs in refuses the breakaway
        subprocess.Popen(cmd, cwd=str(new_exe.parent), creationflags=flags & ~subprocess.CREATE_BREAKAWAY_FROM_JOB,
                         close_fds=True)
    return True
