# Builds dist\Snappy\Snappy.exe (self-contained folder, FFmpeg included).
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# External command: stop right away if it fails (otherwise the script went on and printed "OK").
function Invoke-Step([string]$what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: $what (code $LASTEXITCODE)" }
}

# A Snappy.exe started from dist (or its FFmpeg) locks the folder: PyInstaller then cannot
# replace it. Close it before rebuilding.
$dist = Join-Path $PSScriptRoot "dist\Snappy"
$running = Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($dist, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Host "Closing Snappy started from dist (pid $($running.Id -join ', '))..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

if (-not (Test-Path ".venv")) { Invoke-Step "creating the venv" { py -3 -m venv .venv } }
Invoke-Step "updating pip" { & .\.venv\Scripts\python.exe -m pip install --upgrade pip }
Invoke-Step "installing dependencies" { & .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller }

Invoke-Step "downloading FFmpeg" { & powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1 }

# .ico icon generated from the icon the application draws
Invoke-Step "creating the icon" { & .\.venv\Scripts\python.exe scripts\make_icon.py assets\snappy.ico }

Invoke-Step "PyInstaller" { & .\.venv\Scripts\pyinstaller.exe --noconfirm snappy.spec }
if (-not (Test-Path (Join-Path $dist "Snappy.exe"))) { throw "Failed: dist\Snappy\Snappy.exe was not created" }
Write-Host ""
Write-Host "OK: dist\Snappy\Snappy.exe"
