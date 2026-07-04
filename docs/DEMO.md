# Demo walkthrough

## 1. Start & log in

```bash
docker compose up --build
```

Open **http://localhost:3000** → click **“Login as Demo User”**. The button
autofills `demo@chronosgrid.dev` / `Demo@1234` and submits through the
normal `/auth/login` flow (the account is a regular org-admin user created by
the seed on first startup — no auth bypass).

Seeded for you: the **Demo Organization** with **Payments** and **Analytics**
projects, three queues (`default`, `critical`, `reports`), completed/failed/
retrying/scheduled jobs, a recurring hourly report, a 4-node `nightly-etl`
workflow and two DLQ entries.

## 2. Tour

* **Overview** — live throughput, success/failure/retry rates, p50/p95/p99
  latency, queue depth, worker utilization (all computed from live rows).
* **Queues** — pause `default`, create a job in it, watch it stay QUEUED,
  resume and watch a worker pick it up. Edit concurrency/rate limits/retry
  policy inline.
* **Jobs** — filter by state/queue/tag/date, open a job: full state
  timeline, execution attempts with retry delays, masked payload, logs.
  Try **Cancel**, **Retry**, **Clone**.
* **Watch a retry storm**: create a job with handler `flaky` and payload
  `{"succeed_on_attempt": 3}` — it fails twice with exponential backoff, then
  succeeds. The detail page shows the whole retry history.
* **Failure + AI assistant**: create a job with handler `always_fail`
  (payload `{"message": "kaboom"}`), let it dead-letter, open it and click
  **Analyze failure** — with no `ANTHROPIC_API_KEY` you get the deterministic
  local analysis; with a key, an AI summary. Either way it's advisory only.
* **Workflows** — open `nightly-etl` for the live DAG visualizer
  (fan-out/fan-in with per-node status and progress).
* **Workers** — see the compose workers online with heartbeats and load;
  `docker compose stop worker` and watch the reaper recover their leases,
  then `docker compose start worker`.
* **Dead Letters** — retry a single entry, bulk-retry with “move to queue”,
  add an operator note.
* **Settings** — mint a project API key (shown once), register a webhook
  (secret shown once), adjust quotas.
* **Audit** — every sensitive action above is now in the trail with actor+IP.

## 3. Crash-recovery demo (the fun one)

```bash
# create a slow job from the UI: handler sleep, payload {"seconds": 60}
docker compose kill worker      # simulate a crash mid-execution
# within ~30s the lease expires; the reaper schedules a retry
docker compose start worker     # a fresh worker claims attempt #2
```

The job's timeline shows: RUNNING → RETRY_SCHEDULED ("worker lease expired")
→ QUEUED → CLAIMED → RUNNING → COMPLETED, with a different worker id on the
second attempt.
