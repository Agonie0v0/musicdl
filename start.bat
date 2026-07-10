@echo off
cd /d %~dp0
set PYTHONIOENCODING=utf-8
if not exist venv\Scripts\python.exe (
    echo [musicdl-webui] creating venv...
    py -3.13 -m venv venv
    venv\Scripts\pip install -r requirements.txt
)
REM 想每次启动自动把 musicdl 升到最新，设 MUSICDL_AUTO_UPDATE=1 后再运行本脚本
if "%MUSICDL_AUTO_UPDATE%"=="1" (
    echo [musicdl-webui] updating musicdl to latest...
    venv\Scripts\pip install -U musicdl
)
echo [musicdl-webui] starting at http://127.0.0.1:8765
:run
venv\Scripts\python app.py
REM 退出码 88 = 网页里点了“重启服务”（如升级 musicdl 后），自动拉起
if %errorlevel%==88 (
    echo [musicdl-webui] restarting...
    goto run
)
