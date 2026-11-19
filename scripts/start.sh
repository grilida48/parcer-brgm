#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
cd backend
../.venv/bin/python -m alembic upgrade head
exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
