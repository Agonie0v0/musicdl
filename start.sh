#!/usr/bin/env bash
# Linux / macOS 启动脚本（Windows 用 start.bat）
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

if [ ! -x venv/bin/python ]; then
  echo "[musicdl-webui] creating venv..."
  python3 -m venv venv
  venv/bin/pip install -r requirements.txt
fi

# 想对外提供访问（NAS/服务器）时取消下一行注释
# export MUSICDL_HOST=0.0.0.0

# 想每次启动自动把 musicdl 升到最新：MUSICDL_AUTO_UPDATE=1 ./start.sh
if [ "${MUSICDL_AUTO_UPDATE:-}" = "1" ]; then
  echo "[musicdl-webui] updating musicdl to latest..."
  venv/bin/pip install -U musicdl
fi

echo "[musicdl-webui] starting at http://127.0.0.1:${MUSICDL_PORT:-8765}"
exec venv/bin/python app.py
