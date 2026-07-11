@echo off
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYVER=
for %%V in (3.13 3.12 3.11) do (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 if not defined PYVER set PYVER=%%V
)
if not defined PYVER (
    echo [musicdl-webui] Python 3.11-3.13 not found.
    exit /b 1
)
if not exist venv\Scripts\python.exe (
    echo [musicdl-webui] creating venv...
    py -%PYVER% -m venv venv || exit /b 1
)
venv\Scripts\python -m pip install -r requirements.txt || exit /b 1
REM 想每次启动自动把 musicdl 升到最新，设 MUSICDL_AUTO_UPDATE=1 后再运行本脚本
if "%MUSICDL_AUTO_UPDATE%"=="1" (
    echo [musicdl-webui] updating musicdl to latest...
    venv\Scripts\python -m pip install -U musicdl || exit /b 1
)
echo [musicdl-webui] starting at http://127.0.0.1:8765
:run
venv\Scripts\python app.py
REM 退出码 88 = 网页里点了“重启服务”（如升级 musicdl 后），自动拉起
if %errorlevel%==88 (
    echo [musicdl-webui] restarting...
    goto run
)
