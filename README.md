# ⏱ ChronosGrid — Distributed Job Scheduler

A production-inspired, multi-tenant distributed job scheduling platform.
PostgreSQL-backed atomic claiming, leases with crash recovery, retries with
backoff, DAG workflows, webhooks, live dashboard — all scheduling logic
implemented from scratch (no Celery/Temporal/BullMQ).

## Quick start

```bash
docker compose up --build
```

Then open **http://localhost:3000** and click **“Login as Demo User”**
(`demo@chronosgrid.dev` / `Demo@1234`). OpenAPI docs: http://localhost:8000/api/docs.

The compose stack starts PostgreSQL, Redis, the API, a dedicated scheduler
service, **two worker replicas** and the frontend. Scale workers with:

```bash
docker compose up --build --scale worker=4
```

## What's inside

| Piece | Where | Notes |
|---|---|---|
| API service | `backend/app` | FastAPI, async SQLAlchemy 2, JWT + API keys, RBAC |
| Scheduler service | `backend/scheduler_main.py` | promotion, cron materialization, lease reaper, timeout sweep, webhook dispatch |
| Worker service | `backend/worker` | claim loop, heartbeats, lease renewal, cooperative cancel, graceful drain |
| Frontend | `frontend/` | React + TS + Vite + Tailwind + TanStack Query + Recharts + WebSocket live updates |
| Migrations | `backend/alembic` | `alembic upgrade head` (run automatically by the api container) |
| Tests | `backend/tests`, `frontend/src/__tests__`, `frontend/e2e` | 53 backend tests incl. concurrency stress; vitest; Playwright |
| CI | `.github/workflows/ci.yml` | SQLite fast path + PostgreSQL integration + build + e2e |

## Key guarantees

* **At-least-once execution** — never "exactly once". Use idempotency keys
  (`idempotency_key` per project) and idempotent handlers.
* **Single claimer per attempt** — claims are compare-and-set state
  transitions in PostgreSQL (`FOR UPDATE SKIP LOCKED` candidate scan + CAS
  `UPDATE … WHERE state='QUEUED'`).
* **Crash recovery** — leases expire, the reaper requeues or dead-letters,
  and rotated lease tokens reject stale completions from zombie workers.
* **Validated state machine** — every transition is checked and recorded
  with worker, attempt, reason and correlation id.

## Local development (without Docker)

```bash
# backend (needs Python 3.12+; SQLite works out of the box for dev/tests)
cd backend
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload            # embedded scheduler + auto-seed
python worker_main.py                    # in a second terminal

# frontend
cd frontend
npm install
npm run dev                              # http://localhost:5173 (proxies /api)

# tests
cd backend && python -m pytest -q                       # SQLite fast path
TEST_DATABASE_URL=postgresql+asyncpg://… python -m pytest -q   # against Postgres
cd frontend && npm test                                 # component tests
cd frontend && npx playwright test                      # e2e (stack must be up)
```

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — services, diagrams, claiming & recovery sequences
* [docs/DATABASE.md](docs/DATABASE.md) — ER diagram, index rationale, retention
* [docs/API.md](docs/API.md) — endpoint map + sample requests
* [docs/SECURITY.md](docs/SECURITY.md) — authn/z, hashing, masking, webhook signing
* [docs/TESTING.md](docs/TESTING.md) — test inventory and how to run
* [docs/DECISIONS.md](docs/DECISIONS.md) — design decisions and trade-offs
* [docs/DEMO.md](docs/DEMO.md) — guided walkthrough with the demo account
* [.env.example](.env.example) — all configuration knobs
