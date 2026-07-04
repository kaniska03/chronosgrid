# Architecture

## System overview

```mermaid
flowchart LR
    subgraph Clients
        UI[React dashboard]
        SDK[API clients / CI]
    end
    subgraph Control plane
        API[API service - FastAPI]
        SCH[Scheduler service - promoter, cron, reaper, webhooks]
    end
    subgraph Data plane
        W1[Worker replica 1]
        W2[Worker replica 2]
        WN[Worker replica N]
    end
    PG[(PostgreSQL - source of truth)]
    RD[(Redis - optional event fan-out)]
    HK[Webhook receivers]

    UI -- REST + WebSocket --> API
    SDK -- REST + API keys --> API
    API --> PG
    SCH --> PG
    W1 & W2 & WN -- atomic claim / lease renew / complete --> PG
    API <-. pub/sub .-> RD
    SCH <-. pub/sub .-> RD
    W1 <-. pub/sub .-> RD
    SCH -- HMAC-signed POST --> HK
```

**PostgreSQL is the single source of truth.** Workers and the scheduler
coordinate exclusively through transactional state in Postgres; Redis is
optional and only fans out observability events to WebSocket clients. If
Redis is absent everything still works (the UI falls back to polling).

Every loop in the scheduler service is idempotent and CAS-guarded, so you
can run N scheduler replicas for HA without duplicate effects (cron
occurrences are deduplicated by a unique index, promotions and reaps by
compare-and-set updates).

## Job state machine

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> QUEUED: immediate
    CREATED --> SCHEDULED: delayed / scheduled
    CREATED --> BLOCKED: has dependencies
    SCHEDULED --> QUEUED: due
    BLOCKED --> QUEUED: deps completed
    BLOCKED --> SKIPPED: dep failed (skip policy)
    BLOCKED --> CANCELLED: dep failed (fail policy)
    QUEUED --> CLAIMED: atomic claim (CAS)
    CLAIMED --> RUNNING: worker starts
    CLAIMED --> QUEUED: lease expired
    RUNNING --> COMPLETED
    RUNNING --> RETRY_SCHEDULED: retryable failure
    RUNNING --> FAILED: non-retryable / exhausted
    RUNNING --> TIMED_OUT: exceeded timeout
    RUNNING --> CANCEL_REQUESTED: cancel while running
    CANCEL_REQUESTED --> CANCELLED: cooperative stop
    RETRY_SCHEDULED --> QUEUED: retry due
    TIMED_OUT --> RETRY_SCHEDULED: attempts left
    TIMED_OUT --> DEAD_LETTERED: exhausted
    FAILED --> DEAD_LETTERED: DLQ enabled
    FAILED --> QUEUED: manual retry
    DEAD_LETTERED --> QUEUED: DLQ replay
    COMPLETED --> [*]
    CANCELLED --> [*]
    SKIPPED --> [*]
```

All transitions go through `app/state_machine.py::transition()`, which
validates the edge and records previous state, new state, timestamp, worker,
attempt, reason and correlation id in `job_state_transitions`.

## Atomic claiming

```mermaid
sequenceDiagram
    participant W as Worker
    participant DB as PostgreSQL

    W->>DB: BEGIN
    W->>DB: SELECT candidates (state=QUEUED, due, queue not paused)<br/>ORDER BY priority DESC, available_at<br/>FOR UPDATE SKIP LOCKED
    Note over W,DB: Candidates scored with priority aging,<br/>filtered by tags/capabilities/routing,<br/>queue+project concurrency, rate limits
    W->>DB: pg_advisory_xact_lock(queue) — strict concurrency accounting
    W->>DB: UPDATE jobs SET state='CLAIMED', lease_token=:new,<br/>lease_expires_at=now()+30s, attempt_count=attempt+1<br/>WHERE id=:id AND state='QUEUED'
    alt rowcount = 1 (won the race)
        W->>DB: INSERT job_execution(attempt), state transition row
        W->>DB: COMMIT
        W->>W: execute handler
    else rowcount = 0 (lost)
        W->>DB: try next candidate / COMMIT empty
    end
```

The claim itself is the CAS `UPDATE … WHERE state='QUEUED'` — even without
row locks two workers cannot both observe `rowcount=1` for the same row, so
uniqueness per (job, attempt) holds on any SQL engine; `SKIP LOCKED` merely
removes contention on PostgreSQL.

## Worker crash recovery

```mermaid
sequenceDiagram
    participant W1 as Worker A (crashes)
    participant R as Reaper (scheduler service)
    participant DB as PostgreSQL
    participant W2 as Worker B

    W1->>DB: claim job (lease_token=T1, expires t+30s)
    W1--xW1: 💥 crash — no more heartbeats or renewals
    loop every 2s
        R->>DB: find CLAIMED/RUNNING with lease_expires_at < now()
    end
    R->>DB: transition → RETRY_SCHEDULED (or DLQ if exhausted),<br/>clear lease_token, record reason "lease expired"
    Note over DB: token T1 is now invalid
    R->>DB: promote when retry due → QUEUED
    W2->>DB: claims job, new token T2, attempt n+1
    W1->>DB: (zombie returns) COMPLETE with T1
    DB-->>W1: rejected — StaleLeaseError (token mismatch)
```

The lease token rotates on every claim, so a partitioned or paused worker
that comes back after losing ownership can never overwrite the newer
attempt's result. This yields **at-least-once** semantics: the same payload
may run twice, which is why idempotency keys and idempotent handlers matter.

## Fairness

Selection order = `effective_priority DESC, available_at ASC` where
`effective_priority = priority + min(max_boost, wait_time / aging_interval)`.

* FIFO within the same effective priority (tie-break on `available_at`).
* **Priority aging** (default +1 per waiting minute, capped at +5) guarantees
  a starving low-priority job eventually outranks fresh high-priority work.
* **Per-project concurrency quotas** cap how much of the worker fleet one
  tenant can hold; **per-queue caps and rate limits** bound each queue.
* Candidate scans interleave across projects, so a tenant flooding one queue
  cannot monopolise a scan window.

## Observability

Structured JSON logs everywhere; every request/job carries a correlation id.
`/api/v1/health` (liveness), `/api/v1/ready` (DB + Redis checks) and
`/metrics` (Prometheus text format: jobs by state, queue depth, DLQ count,
worker utilization, p50/p95/p99 execution latency). The dashboard's numbers
come from the same live queries — nothing is hard-coded.
