@echo off
rem Build a single-file TeamSub.exe (no console window).
rem Requires: .venv with requirements.txt + requirements-dev.txt installed.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. See README.
    pause
    exit /b 1
)

rem 打包依赖单独检查：requirements.txt 只管运行期，不装 pyinstaller 也能跑应用。
".venv\Scripts\python.exe" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller missing. Run:
    echo     .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name TeamSub ^
    --icon teamsub.ico ^
    --version-file version_info.txt ^
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
echo Done: dist\TeamSub.exe
for /f %%H in ('certutil -hashfile dist\TeamSub.exe SHA256 ^| findstr /v ":"') do (
    echo SHA256: %%H
    goto :done
)
:done
echo 单文件模式首次启动需解压；配置与会议记录保存在 exe 同目录。
pause
