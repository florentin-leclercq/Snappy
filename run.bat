@echo off
rem Runs Snappy from the sources (after installing: see README.md)
cd /d "%~dp0"
rem Update to the latest GitHub release before starting (snappy/update.py).
if exist .venv\Scripts\pythonw.exe (
    .venv\Scripts\python.exe -m snappy.update
    start "" .venv\Scripts\pythonw.exe -m snappy %*
) else (
    py -3 -m snappy.update
    py -3 -m snappy %*
)
