# Downloads an "essentials" build of FFmpeg (gyan.dev) into .\ffmpeg\bin
# Usage: powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "ffmpeg"
$bin = Join-Path $dest "bin"

if (Test-Path (Join-Path $bin "ffmpeg.exe")) {
    Write-Host "FFmpeg is already in $bin"
    exit 0
}

$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$zip = Join-Path $env:TEMP "ffmpeg-snappy.zip"
$tmp = Join-Path $env:TEMP "ffmpeg-snappy"

Write-Host "Downloading FFmpeg from $url ..."
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $tmp
$extracted = Get-ChildItem $tmp -Directory | Select-Object -First 1

New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item (Join-Path $extracted.FullName "bin\ffmpeg.exe") $bin
Copy-Item (Join-Path $extracted.FullName "bin\ffprobe.exe") $bin
Copy-Item (Join-Path $extracted.FullName "LICENSE") $dest -ErrorAction SilentlyContinue

Remove-Item $zip -Force
Remove-Item $tmp -Recurse -Force
& (Join-Path $bin "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
Write-Host "FFmpeg installed in $bin"
