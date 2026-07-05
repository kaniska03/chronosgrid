#!/bin/sh
# Single-container start for Render free tier:
# migrations -> background worker -> API with embedded scheduler.
set -e
echo "Running database migrations…"
python -m alembic upgrade head
echo "Starting worker in background…"
python worker_main.py &
echo "Starting API (embedded scheduler)…"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
