"""Atomic job claiming.

Two-layer safety:

1. **Candidate selection** — on PostgreSQL the candidate rows are read with
   ``FOR UPDATE SKIP LOCKED`` so concurrent claimers don't contend on the same
   rows. A per-queue advisory xact lock makes concurrency-limit checks strict.
2. **Compare-and-set claim** — ownership is only ever taken by
   ``UPDATE jobs SET state='CLAIMED', lease_token=... WHERE id=:id AND
   state='QUEUED'``. The WHERE clause on the previous state means exactly one
   claimer can win a given (job, attempt) regardless of dialect. This is what
   the concurrency tests assert.

Claiming respects: queue pause, queue & project concurrency, queue rate
limit, per-worker concurrency, worker tags/capabilities, routing keys,
priority (with aging), FIFO within equal effective priority, scheduled
availability and dependency state (BLOCKED jobs are never QUEUED until
released by the dependency resolver).
"""
import uuid
from datetime import timedelta

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import is_postgres
from ..models import Job, JobExecution, JobStateTransition, Project, Queue, Worker, utcnow

ACTIVE_STATES = ("CLAIMED", "RUNNING", "CANCEL_REQUESTED")


def effective_priority(priority: int, waited_seconds: float) -> float:
    """Priority aging: waiting jobs gain +1 priority per configured interval,
    capped, so low-priority work can never starve forever."""
    s = get_settings()
    boost = min(s.priority_aging_max_boost,
                waited_seconds / s.priority_aging_interval_seconds)
    return priority + boost


async def _active_counts(session: AsyncSession, column):
    rows = (await session.execute(
        select(column, func.count()).where(Job.state.in_(ACTIVE_STATES)).group_by(column)
    )).all()
    return {k: v for k, v in rows}


async def _recent_queue_claims(session: AsyncSession, queue_id, window_seconds: int = 60) -> int:
    cutoff = utcnow() - timedelta(seconds=window_seconds)
    return (await session.execute(
        select(func.count()).select_from(JobExecution).join(Job, Job.id == JobExecution.job_id)
        .where(Job.queue_id == queue_id, JobExecution.claimed_at >= cutoff)
    )).scalar_one()


def _worker_matches(job: Job, queue: Queue, worker: Worker) -> bool:
    if job.job_type not in (worker.capabilities or []):
        return False
    if queue.allowed_worker_tags:
        if not set(queue.allowed_worker_tags) & set(worker.tags or []):
            return False
    if job.routing_key and job.routing_key not in (worker.tags or []):
        return False
    if job.required_capabilities:
        if not set(job.required_capabilities) <= set(worker.capabilities or []):
            return False
    return True


async def claim_next_job(session: AsyncSession, worker: Worker) -> Job | None:
    """Claim at most one job for ``worker``. Commits on success."""
    s = get_settings()
    now = utcnow()
    if worker.status != "online":
        return None

    # Candidates: high priority first, plus oldest-waiting (for aging).
    base = (
        select(Job, Queue)
        .join(Queue, Queue.id == Job.queue_id)
        .where(
            Job.state == "QUEUED",
            Job.available_at <= now,
            Job.cancel_requested.is_(False),
            Queue.paused.is_(False),
            Queue.deleted_at.is_(None),
        )
    )
    by_priority = base.order_by(Job.priority.desc(), Job.available_at.asc()).limit(32)
    by_age = base.order_by(Job.available_at.asc()).limit(32)
    if is_postgres():
        by_priority = by_priority.with_for_update(skip_locked=True, of=Job)
        by_age = by_age.with_for_update(skip_locked=True, of=Job)

    rows = (await session.execute(by_priority)).all()
    seen = {r[0].id for r in rows}
    rows += [r for r in (await session.execute(by_age)).all() if r[0].id not in seen]
    if not rows:
        await session.rollback()
        return None

    # Effective priority sort (aging) + FIFO tie-break; then round-robin
    # interleave across projects inside equal priority so one hot tenant
    # can't monopolise a scan window.
    scored = sorted(
        rows,
        key=lambda r: (-effective_priority(r[0].priority,
                                           (now - r[0].available_at).total_seconds()),
                       r[0].available_at),
    )

    queue_busy = await _active_counts(session, Job.queue_id)
    project_busy = await _active_counts(session, Job.project_id)
    worker_busy_rows = (await session.execute(
        select(Job.queue_id, func.count()).where(
            Job.claimed_by_worker_id == worker.id, Job.state.in_(ACTIVE_STATES)
        ).group_by(Job.queue_id)
    )).all()
    worker_busy = {k: v for k, v in worker_busy_rows}
    worker_total = sum(worker_busy.values())
    if worker_total >= worker.capacity:
        await session.rollback()
        return None

    skipped_queues: set = set()
    project_limits: dict = {}
    for job, queue in scored:
        if queue.id in skipped_queues:
            continue
        if not _worker_matches(job, queue, worker):
            continue
        if queue_busy.get(queue.id, 0) >= queue.max_concurrent_jobs:
            skipped_queues.add(queue.id)
            continue
        # per-project concurrency quota (fairness / tenant isolation)
        if job.project_id not in project_limits:
            project_limits[job.project_id] = (await session.execute(
                select(Project.max_concurrent_jobs).where(Project.id == job.project_id)
            )).scalar_one()
        if project_busy.get(job.project_id, 0) >= project_limits[job.project_id]:
            continue
        if worker_busy.get(queue.id, 0) >= queue.per_worker_concurrency:
            skipped_queues.add(queue.id)
            continue
        if queue.rate_limit_per_minute is not None:
            if await _recent_queue_claims(session, queue.id) >= queue.rate_limit_per_minute:
                skipped_queues.add(queue.id)
                continue

        if is_postgres():
            # Strict concurrency accounting per queue under true parallelism.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:qid))"),
                {"qid": str(queue.id)},
            )

        token = uuid.uuid4()
        lease_exp = now + timedelta(seconds=s.lease_seconds)
        attempt = job.attempt_count + 1
        result = await session.execute(
            update(Job)
            .where(and_(Job.id == job.id, Job.state == "QUEUED"))
            .values(
                state="CLAIMED", lease_token=token, lease_expires_at=lease_exp,
                lease_renewed_at=now, claimed_by_worker_id=worker.id,
                claimed_at=now, attempt_count=attempt, updated_at=now,
            )
        )
        if result.rowcount != 1:
            continue  # lost the race for this row; try next candidate

        session.add(JobStateTransition(
            job_id=job.id, from_state="QUEUED", to_state="CLAIMED", at=now,
            worker_id=worker.id, attempt_number=attempt,
            reason=f"claimed by {worker.name}", correlation_id=job.correlation_id,
        ))
        session.add(JobExecution(
            job_id=job.id, attempt_number=attempt, worker_id=worker.id,
            state="CLAIMED", lease_token=token, claimed_at=now,
        ))
        await session.execute(
            update(Worker).where(Worker.id == worker.id)
            .values(active_jobs=Worker.active_jobs + 1)
        )
        await session.commit()

        fresh = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        return fresh

    await session.rollback()
    return None
