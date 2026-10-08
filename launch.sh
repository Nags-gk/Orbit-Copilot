#!/usr/bin/env bash
# Start Orbit Copilot on :8001 (Orbit's backend uses :8000).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/uvicorn orbit_copilot.main:app --host 0.0.0.0 --port 8001
