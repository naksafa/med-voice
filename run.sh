#!/usr/bin/env sh
# MedVoice launcher (macOS / Linux). Usage: ./run.sh [--voice]
set -e
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
[ "$1" = "--voice" ] && .venv/bin/pip install -r requirements-voice.txt
echo "MedVoice running on http://localhost:8000"
exec .venv/bin/python -m uvicorn app.main:app --port 8000
