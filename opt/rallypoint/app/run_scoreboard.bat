@echo off
setlocal
cd /d "%~dp0"
set "SCOREBOARD_PYTHON=%~dp0.venv-dev\Scripts\python.exe"
if not exist "%SCOREBOARD_PYTHON%" set "SCOREBOARD_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%SCOREBOARD_PYTHON%" (
    echo No project Python environment found. See README.md for installation.
    pause
    exit /b 1
)
"%SCOREBOARD_PYTHON%" -m pickleball_scoreboard.main --keyboard --windowed %*
if errorlevel 1 pause
