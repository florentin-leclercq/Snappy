@echo off
rem Runs Snappy from the sources (after installing: see README.md)
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe -m snappy %*
) else (
    py -3 -m snappy %*
)
