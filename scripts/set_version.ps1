# Sets the version of Snappy (snappy/__init__.py and pyproject.toml): asks for it, or takes it as argument.
#   powershell -ExecutionPolicy Bypass -File scripts\set_version.ps1 [0.3.0]
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "set_version.py"
$venv = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venv) { & $venv $script @args }
elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 $script @args }
else { & python $script @args }
exit $LASTEXITCODE
