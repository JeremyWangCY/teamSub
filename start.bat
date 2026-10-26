@echo off
rem TeamsSub - Teams live caption reader
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv not found. See debug.bat for details.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "teamsub.py"
