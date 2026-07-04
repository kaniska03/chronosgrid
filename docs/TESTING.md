# Testing

## Layout

| Suite | Location | Runner |
|---|---|---|
| Backend unit + integration (53 tests) | `backend/tests` | `pytest` |
| Frontend component tests | `frontend/src/__tests__` | `vitest` |
| End-to-end | `frontend/e2e` | Playwright against `docker compose` stack |

## Running

```bash
# backend — SQLite fast path (no services needed)
cd backend && python -m pytest -q

# backend — against PostgreSQL (exercises FOR UPDATE SKIP LOCKED + advisory locks)
TEST_DATABASE_URL=postgresql+asyncpg://chronos:chronos@localhost:5432/chronosgrid_test \
  python -m pytest -q

# frontend components
cd frontend && npm test

# e2e (stack must be running: docker compose up --build)
cd frontend && npx playwright install chromium && npx playwright test
```

CI (`.github/workflows/ci.yml`) runs migrations against clean PostgreSQL,
the backend suite on both engines, the frontend build + component tests, and
Playwright against the composed stack.

## Coverage of the 20 mandated scenarios

| # | Scenario | Test |
|---|---|---|
| 1 | Two workers can't claim the same job | `test_claiming.py::test_two_workers_cannot_claim_same_job` |
| — | Concurrency stress (40 jobs × 4 workers) | `test_claiming.py::test_concurrency_stress_many_workers` |
| 2 | Crash → expired lease recovered | `test_leases_recovery.py::test_worker_crash_recovers_expired_lease` |
| 3 | Stale worker can't complete late | `test_leases_recovery.py::test_stale_worker_cannot_complete_after_recovery` |
| 4 | Fixed/linear/exponential retry math (+jitter bounds) | `test_state_machine_and_retries.py` |
| 5 | Exhausted → DLQ (+ replay) | `test_leases_recovery.py::test_exhausted_attempts_enter_dlq` |
| 6 | Idempotency keys dedupe | `test_auth_rbac_isolation.py::test_idempotency_key_prevents_duplicates` |
| 7 | Queue pause blocks claims | `test_claiming.py::test_paused_queue_releases_nothing` |
| 8 | Queue concurrency respected | `test_claiming.py::test_queue_concurrency_limit_respected` |
| 9 | Project isolation | `test_auth_rbac_isolation.py::test_project_isolation_between_orgs` |
| 10 | RBAC enforced | `test_auth_rbac_isolation.py::test_rbac_viewer_cannot_mutate` |
| 11 | Cyclic workflow rejected | `test_workflows…::test_cyclic_workflow_rejected` |
| 12 | Dependencies release correctly (diamond fan-out/fan-in) | `test_workflows…::test_dependencies_release_in_order` |
| 13 | Timeout → correct state | `test_leases_recovery.py::test_timeout_moves_job_to_correct_state` |
| 14 | Cancellation (queued + cooperative running) | `test_leases_recovery.py::test_cancellation_of_queued_and_running` |
| 15 | Webhooks signed & retried | `test_workflows…::test_webhooks_signed_and_retried` |
| 16 | WS events emitted | `test_workflows…::test_websocket_events_emitted` (+ token-rejection test) |
| 17 | Recurring dedupe | `test_workflows…::test_recurring_jobs_no_duplicate_occurrences` |
| 18 | Priority aging beats starvation | `test_claiming.py::test_priority_aging_prevents_starvation` |
| 19 | Pagination & filters | `test_workflows…::test_pagination_and_filters` + cursor-log test |
| 20 | Graceful shutdown loses nothing | `test_leases_recovery.py::test_graceful_shutdown_does_not_lose_jobs` |

Plus: state-machine edge validation, capability/tag/routing-key claim
routing, scheduled-job availability, non-retryable short-circuit, dead-worker
sweep, dependency-failure cancel/skip policies, invalid cron/timezone
rejection, payload/batch/daily-quota limits (429 + Retry-After), API-key
lifecycle & scoping, token refresh, demo-account flow, payload masking, AI
local fallback and audit trail.
