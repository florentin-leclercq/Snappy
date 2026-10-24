"""Windows: installing an update of Snappy.exe (PyInstaller) downloaded by `snappy.update`.

Snappy.exe cannot replace its own folder while it runs: a small PowerShell script waits for it
to quit, swaps the folders (the previous one is put back if the copy fails) and starts the new
Snappy. PyInstaller folders are deep (numpy licenses...): the paths go beyond 260 characters, hence
`\\\\?\\` paths, robocopy and `rd` rather than Copy-Item / Remove-Item.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .process import NO_WINDOW

# Snappy.exe updates itself from the release's Windows zip (made by build.ps1).
SELF_UPDATE = True
UPDATE_ASSET = r"windows.*\.zip$"

# Waits for Snappy to quit, swaps the folders (old one back if the copy fails), restarts Snappy.
APPLY_SCRIPT = r"""param([int]$WaitPid, [string]$Source, [string]$Target, [string]$Staging, [string]$Log,
      [string]$Version, [string]$Relaunch)
$ErrorActionPreference = "Stop"
function Write-Log([string]$m) { Add-Content -LiteralPath $Log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) }
function Remove-Tree([string]$p) { if (Test-Path -LiteralPath $p) { cmd /c rd /s /q "\\?\$p" 2>$null | Out-Null } }
try { Wait-Process -Id $WaitPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
$old = "$Target.old"
$moved = $false
for ($i = 0; $i -lt 20 -and -not $moved; $i++) {
    try {
        Remove-Tree $old
        Rename-Item -LiteralPath $Target -NewName (Split-Path $old -Leaf)
        $moved = $true
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $moved) {
    Write-Log "install of $Version failed: $Target is in use"
} else {
    try {
        robocopy $Source $Target /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy code $LASTEXITCODE" }
        Write-Log "installed $Version in $Target"
        Remove-Tree $old
    } catch {
        Write-Log "install of $Version failed ($($_.Exception.Message)): previous version put back"
        Remove-Tree $Target
        Rename-Item -LiteralPath $old -NewName (Split-Path $Target -Leaf)
    }
}
# Done either way: a failed install is not retried at every start (it is downloaded again later).
Get-ChildItem -LiteralPath $Staging -Exclude "apply.ps1" | ForEach-Object {
    if ($_.PSIsContainer) { Remove-Tree $_.FullName } else { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}
$exe = Join-Path $Target "Snappy.exe"
if ($Relaunch) { Start-Process -FilePath $exe -ArgumentList $Relaunch } else { Start-Process -FilePath $exe }
"""


def long_path(path: Path) -> str:
    """Path usable beyond 260 characters."""
    text = str(path.resolve())
    return text if text.startswith("\\\\") else "\\\\?\\" + text


def install_update(source: Path, target: Path, staging: Path, log_path: Path, version: str,
                   wait_pid: int, args: list[str]) -> bool:
    """Starts the script that replaces `target` with `source` once process `wait_pid` has quit,
    then starts Snappy again with `args`. The caller quits right after."""
    script = staging / "apply.ps1"
    script.write_text(APPLY_SCRIPT, encoding="utf-8-sig")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-File", str(script), "-WaitPid", str(wait_pid), "-Source", str(source),
           "-Target", str(target), "-Staging", str(staging), "-Log", str(log_path), "-Version", version]
    if args:
        cmd += ["-Relaunch", subprocess.list2cmdline(args)]
    # It must outlive Snappy: own process group, out of any job Snappy was started in. Not
    # DETACHED_PROCESS: PowerShell without a console quietly does nothing.
    flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    except OSError:      # the job Snappy runs in refuses the breakaway
        subprocess.Popen(cmd, creationflags=flags & ~subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    return True
