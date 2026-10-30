@echo off
rem Run TeamSub with console output (for troubleshooting)
cd /d "%~dp0"
".venv\Scripts\python.exe" "teamsub.py"
echo.
echo [process exited]
pause
