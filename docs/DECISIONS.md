# Design decisions & trade-offs

## At-least-once, never exactly-once
A worker can crash after completing side effects but before committing the
completion, so the job runs again. Exactly-once is unachievable without
cooperation from the side-effect target. We therefore (a) document
at-least-once loudly, (b) provide project-scoped idempotency keys enforced by
a partial unique index, (c) rotate lease tokens so a *stale* worker can never
overwrite a newer attempt (no lost updates), and (d) surface attempt numbers
so handlers can implement their own dedupe.

## Lease-on-the-job-row instead of a `job_leases` table
The lease (token, expiry, renewal, worker) lives on `jobs`. Claim, renewal,
completion and reap all become **single-row CAS updates** — no join, no
second lock target, no lease/job consistency window. The cost: no lease
history (execution history lives in `job_executions`, which carries the
attempt's token, times and outcome — enough for forensics). For a system of
this scale the simpler invariant ("the row owns its lease") wins.

## CAS claim + SKIP LOCKED, not advisory-lock-only
The correctness anchor is `UPDATE … WHERE state='QUEUED'` — portable and
race-free by definition. `FOR UPDATE SKIP LOCKED` is a Postgres throughput
optimisation on top (claimers don't collide on candidates). A per-queue
advisory xact lock makes queue-concurrency accounting strict under true
parallelism; SQLite (test fast path) serialises writes so the same code is
correct there too. CI runs the suite against both engines.

## Retry policy embedded as JSON, not a `retry_policies` table
Policies are small validated documents (`strategy/base_delay/max_delay/jitter`)
with queue-level defaults and per-job overrides. A normalized table would add
a join to every failure-handling path for no query benefit — nothing ever
asks "which jobs use policy X". Validation happens in `retries.normalize_policy`.

## Fairness: aging + quotas rather than weighted fair queuing
True WFQ needs per-tenant virtual clocks — heavy for this scale. Priority
aging (capped boost per waiting interval) provably bounds starvation:
any job's effective priority reaches `priority + max_boost` in bounded time,
after which FIFO ordering guarantees selection. Per-project concurrency
quotas bound how much capacity a hot tenant can hold concurrently.

## Scheduler HA by idempotence, not leader election
Every scheduler loop is safe to run twice (CAS promotions, unique cron
occurrence index, token-guarded reaps). Running two scheduler replicas gives
availability without a leader-election dependency. Trade-off: some duplicate
scanning work under HA — acceptable at this scale.

## Naive-UTC timestamps
All timestamps are stored as naive UTC and serialised with a `Z` suffix.
This sidesteps SQLite/Postgres timezone-handling differences that otherwise
breed subtle bugs in a dual-engine test strategy. Cron schedules are computed
in the recurring job's IANA timezone, then converted to UTC.

## Known limitations
* Claim candidate scan is O(scan window) per idle worker poll; at very high
  queue counts you'd add LISTEN/NOTIFY wakeups instead of polling.
* API rate limiting is per-process (in-memory); multi-replica APIs would move
  it to Redis token buckets.
* Webhook secrets are stored plaintext (HMAC requires it) — production
  hardening: KMS envelope encryption.
* Tag filtering uses a JSON LIKE fallback; on Postgres a GIN index over
  `tags` is the upgrade path.
* Terminal-job retention pruning is implemented for heartbeats only; job
  pruning is left as an operator cron (`retention_days` is stored per queue).

## Future scaling path
1. Partition `jobs` by state or time (hot QUEUED partition stays small).
2. LISTEN/NOTIFY (or Redis streams) to wake idle workers → sub-ms dispatch.
3. Redis-backed distributed rate limiting and API quotas.
4. Move logs/transitions to a columnar store (ClickHouse) past ~10⁸ rows.
5. Shard by project: routing keys already flow through the claim path.
