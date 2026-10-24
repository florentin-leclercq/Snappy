@echo off
rem Runs Snappy from the sources (after installing: see README.md)
cd /d "%~dp0"
rem Update to the latest GitHub release, then start Snappy without a console window
rem (snappy/update.py, snappy/platforms/windows/launch.py).
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m snappy.update
    .venv\Scripts\python.exe -m snappy.platforms.windows.launch %*
) else (
    py -3 -m snappy.update
    py -3 -m snappy.platforms.windows.launch %*
)
