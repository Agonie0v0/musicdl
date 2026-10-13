#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if ! .venv/bin/python -c 'import requests_toolbelt' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm ci && npm run build)
fi
exec .venv/bin/python app.py
