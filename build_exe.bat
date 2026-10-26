@echo off
rem Build a single-file TeamsSub.exe (no console window).
rem Requires: .venv with requirements.txt + pyinstaller installed.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. See README.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name TeamsSub ^
    --icon teamsub.ico ^
    --add-data "teamsub.ico;." ^
    --collect-all uiautomation ^
    --collect-submodules comtypes ^
    --hidden-import comtypes.stream ^
    --exclude-module pytest ^
    --exclude-module unittest ^
    teamsub.py

if errorlevel 1 (
    echo [ERROR] build failed
    pause
    exit /b 1
)
echo.
echo Done: dist\TeamsSub.exe
echo 单文件模式首次启动需解压（约 2-5 秒）；配置与会议记录保存在 exe 同目录。
pause
