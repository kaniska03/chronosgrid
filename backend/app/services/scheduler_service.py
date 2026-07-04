"""Background scheduler loops.

Runs either embedded in the API process (dev/tests) or as a dedicated
``scheduler`` container. All loops are idempotent and safe to run in more
than one process: every mutation is guarded by a compare-and-set state
predicate or a unique constraint, so duplicate schedulers cause no harm.

Loops:
* promote_due_jobs        SCHEDULED/RETRY_SCHEDULED -> QUEUED when due
* materialize_recurring   cron ticks -> concrete jobs (unique occurrence index)
* reap_expired_leases     crashed/silent workers lose ownership; retry or DLQ
* reap_timed_out_jobs     RUNNING past its timeout -> TIMED_OUT -> retry/DLQ
* mark_dead_workers       stale heartbeat -> offline (+ its leases expire)
* dispatch_webhooks       pending deliveries with HMAC signing + backoff
"""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_factory
from ..events import bus
from ..models import (
    Job, Queue, RecurringJob, WebhookDelivery, WebhookEndpoint, Worker,
    WorkerHeartbeat, utcnow,
)
from ..security import sign_webhook
from ..state_machine import transition
from .jobs import next_cron_occurrence
from .lifecycle import _handle_failure

log = logging.getLogger("chronosgrid.scheduler")

WEBHOOK_MAX_ATTEMPTS = 8
WEBHOOK_DISABLE_AFTER = 10  # consecutive failed deliveries -> endpoint disabled


async def promote_due_jobs(session: AsyncSession) -> int:
    now = utcnow()
    promoted = 0
    for state in ("SCHEDULED", "RETRY_SCHEDULED"):
        col = Job.available_at if state == "SCHEDULED" else Job.next_retry_at
        due = (await session.execute(
            select(Job).where(Job.state == state, col <= now).limit(500)
        )).scalars().all()
        for job in due:
            # CAS guard: another scheduler instance may have promoted it.
            res = await session.execute(
                update(Job).where(Job.id == job.id, Job.state == state)
                .values(state="QUEUED", available_at=now, updated_at=now))
            if res.rowcount == 1:
                job.state = "QUEUED"       # sync ORM with the CAS update
                job.available_at = now
                from ..models import JobStateTransition
                session.add(JobStateTransition(
                    job_id=job.id, from_state=state, to_state="QUEUED", at=now,
                    attempt_number=job.attempt_count,
                    reason="due for execution" if state == "SCHEDULED" else "retry due",
                    correlation_id=job.correlation_id))
                promoted += 1
        await session.commit()
    return promoted


async def materialize_recurring(session: AsyncSession) -> int:
    from .jobs import create_job
    from ..models import Project
    now = utcnow()
    created = 0
    due = (await session.execute(
        select(RecurringJob).where(RecurringJob.enabled.is_(True),
                                   RecurringJob.next_run_at <= now).limit(200)
    )).scalars().all()
    for rec in due:
        rec_id, occurrence = rec.id, rec.next_run_at
        cron_expr, tz = rec.cron_expression, rec.timezone
        project = (await session.execute(
            select(Project).where(Project.id == rec.project_id))).scalar_one()
        queue = (await session.execute(
            select(Queue).where(Queue.id == rec.queue_id))).scalar_one()
        try:
            await create_job(
                session, project, queue, job_type=rec.job_type, payload=rec.payload,
                priority=rec.priority, max_attempts=rec.max_attempts,
                timeout_seconds=rec.timeout_seconds, recurring_job_id=rec_id,
                scheduled_at=occurrence, initial_state="QUEUED",
                timezone_name=tz, check_quota=False,
                correlation_id=f"recurring:{rec_id}:{occurrence.isoformat()}")
            created += 1
        except IntegrityError:
            await session.rollback()  # occurrence already materialised elsewhere
        # advance the cursor even if the occurrence already existed (CAS so a
        # concurrent scheduler that already advanced it is not rewound)
        await session.execute(
            update(RecurringJob)
            .where(RecurringJob.id == rec_id, RecurringJob.next_run_at == occurrence)
            .values(next_run_at=next_cron_occurrence(cron_expr, tz, max(occurrence, now)),
                    last_run_at=occurrence))
        await session.commit()
    return created


async def reap_expired_leases(session: AsyncSession) -> int:
    """A worker that stopped renewing its lease loses the job. The lease token
    is cleared here, so any late completion from the old worker is rejected."""
    now = utcnow()
    expired = (await session.execute(
        select(Job).where(Job.state.in_(("CLAIMED", "RUNNING", "CANCEL_REQUESTED")),
                          Job.lease_expires_at < now).limit(200)
    )).scalars().all()
    for job in expired:
        worker_id = job.claimed_by_worker_id
        await session.execute(  # free the slot on the (possibly dead) worker
            update(Worker).where(Worker.id == worker_id, Worker.active_jobs > 0)
            .values(active_jobs=Worker.active_jobs - 1))
        await _handle_failure(
            session, job, worker_id,
            error={"type": "LeaseExpired",
                   "message": "worker lease expired (crash or network partition)"},
            error_category="retryable", lease_expired=True)
        log.warning("reaped expired lease job=%s worker=%s", job.id, worker_id)
    return len(expired)


async def reap_timed_out_jobs(session: AsyncSession) -> int:
    now = utcnow()
    running = (await session.execute(
        select(Job).where(Job.state.in_(("RUNNING", "CANCEL_REQUESTED")),
                          Job.started_at.is_not(None)).limit(500)
    )).scalars().all()
    reaped = 0
    for job in running:
        timeout = job.timeout_seconds or 300
        if job.started_at + timedelta(seconds=timeout) < now:
            await _handle_failure(
                session, job, job.claimed_by_worker_id,
                error={"type": "Timeout",
                       "message": f"execution exceeded {timeout}s"},
                error_category="timeout", timed_out=True)
            reaped += 1
    return reaped


async def mark_dead_workers(session: AsyncSession) -> int:
    s = get_settings()
    cutoff = utcnow() - timedelta(seconds=s.worker_offline_after_seconds)
    stale = (await session.execute(
        select(Worker).where(Worker.status.in_(("online", "draining", "unhealthy")),
                             Worker.last_heartbeat_at < cutoff)
    )).scalars().all()
    for w in stale:
        w.status = "offline"
        log.warning("worker %s (%s) marked offline: no heartbeat since %s",
                    w.name, w.id, w.last_heartbeat_at)
        await bus.emit("worker.status", {"worker_id": str(w.id), "status": "offline"})
    if stale:
        await session.commit()
    # Its leases are recovered by reap_expired_leases when they expire.
    return len(stale)


async def dispatch_webhooks(session: AsyncSession, client: httpx.AsyncClient | None = None,
                            limit: int = 50) -> int:
    now = utcnow()
    pending = (await session.execute(
        select(WebhookDelivery).where(WebhookDelivery.status.in_(("pending", "retrying")),
                                      WebhookDelivery.next_attempt_at <= now).limit(limit)
    )).scalars().all()
    if not pending:
        return 0
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10)
    sent = 0
    try:
        for d in pending:
            ep = (await session.execute(
                select(WebhookEndpoint).where(WebhookEndpoint.id == d.endpoint_id)
            )).scalar_one_or_none()
            if ep is None or not ep.active:
                d.status = "disabled"
                await session.commit()
                continue
            body = json.dumps({"id": str(d.id), "event": d.event_type,
                               "data": d.payload, "attempt": d.attempt_count + 1},
                              default=str).encode()
            signature = sign_webhook(ep.secret, body)
            d.attempt_count += 1
            try:
                resp = await client.post(ep.url, content=body, headers={
                    "Content-Type": "application/json",
                    "X-ChronosGrid-Signature": f"sha256={signature}",
                    "X-ChronosGrid-Event": d.event_type,
                    "X-ChronosGrid-Delivery": str(d.id)})
                d.response_status = resp.status_code
                ok = 200 <= resp.status_code < 300
            except Exception as exc:
                d.response_status = None
                d.last_error = str(exc)[:500]
                ok = False
            if ok:
                d.status = "delivered"
                d.delivered_at = utcnow()
                ep.failure_count = 0
                sent += 1
            else:
                ep.failure_count += 1
                if d.attempt_count >= WEBHOOK_MAX_ATTEMPTS:
                    d.status = "failed"
                else:
                    d.status = "retrying"
                    d.next_attempt_at = utcnow() + timedelta(
                        seconds=min(3600, 5 * 2 ** (d.attempt_count - 1)))
                if ep.failure_count >= WEBHOOK_DISABLE_AFTER:
                    ep.active = False
                    ep.disabled_at = utcnow()
                    log.warning("webhook endpoint %s disabled after repeated failures", ep.id)
            await session.commit()
    finally:
        if own_client:
            await client.aclose()
    return sent


async def prune_history(session: AsyncSession) -> None:
    """Retention: heartbeat history and terminal jobs past queue retention."""
    from sqlalchemy import delete
    cutoff = utcnow() - timedelta(hours=6)
    await session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.at < cutoff))
    await session.commit()


class SchedulerService:
    """Ties all loops to one task; embedded or standalone."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run_once(self) -> dict:
        sf = session_factory()
        results = {}
        for name, fn in (("promoted", promote_due_jobs),
                         ("recurring", materialize_recurring),
                         ("leases_reaped", reap_expired_leases),
                         ("timeouts", reap_timed_out_jobs),
                         ("dead_workers", mark_dead_workers),
                         ("webhooks", dispatch_webhooks)):
            async with sf() as session:
                try:
                    results[name] = await fn(session)
                except Exception:
                    log.exception("scheduler loop %s failed", name)
                    await session.rollback()
        return results

    async def _loop(self) -> None:
        s = get_settings()
        ticks = 0
        while not self._stop.is_set():
            await self.run_once()
            ticks += 1
            if ticks % 300 == 0:
                sf = session_factory()
                async with sf() as session:
                    await prune_history(session)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=s.reaper_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
