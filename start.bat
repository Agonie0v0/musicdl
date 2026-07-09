@echo off
cd /d %~dp0
if not exist venv\Scripts\python.exe (
    echo [musicdl-webui] creating venv...
    py -3.13 -m venv venv
    venv\Scripts\pip install -r requirements.txt
)
echo [musicdl-webui] starting at http://127.0.0.1:8765
venv\Scripts\python app.py
