"""Job lifecycle operations: lease renewal, completion, failure/retry, DLQ,
timeout, cancellation and dependency release.

Stale-owner protection: every ownership-sensitive mutation requires the
caller's ``lease_token`` to match the row. When a lease expires and the job is
requeued the token is rotated, so a late worker's COMPLETE/FAIL is rejected
with ``StaleLeaseError`` — this yields at-least-once semantics with no
lost-update anomaly.
"""
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..events import bus
from ..models import (
    DeadLetterEntry, Job, JobDependency, JobExecution, JobLog, Queue,
    WebhookDelivery, WebhookEndpoint, Worker, Workflow, utcnow,
)
from ..state_machine import transition
from .retries import compute_delay, is_retryable, normalize_policy

log = logging.getLogger("chronosgrid.lifecycle")


class StaleLeaseError(Exception):
    """Raised when a worker acts on a job whose lease it no longer owns."""


async def _get_owned(session: AsyncSession, job_id, lease_token) -> Job:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None or job.lease_token is None or str(job.lease_token) != str(lease_token):
        raise StaleLeaseError(f"lease token mismatch for job {job_id}")
    return job


async def _current_execution(session: AsyncSession, job: Job) -> JobExecution | None:
    return (await session.execute(
        select(JobExecution).where(
            JobExecution.job_id == job.id,
            JobExecution.attempt_number == job.attempt_count)
    )).scalar_one_or_none()


async def add_log(session: AsyncSession, job: Job, message: str, *,
                  level: str = "info", worker_id=None, execution_id=None) -> None:
    session.add(JobLog(job_id=job.id, message=message[:8000], level=level,
                       worker_id=worker_id, execution_id=execution_id))
    await bus.emit("job.log", {"job_id": str(job.id), "level": level,
                               "message": message[:500]}, str(job.project_id))


# --------------------------------------------------------------------------- #
# Leases & heartbeats
# --------------------------------------------------------------------------- #
async def renew_lease(session: AsyncSession, job_id, lease_token, worker_id) -> dict:
    """Extend the lease. Returns cancellation status so running handlers can
    cooperatively stop. Raises StaleLeaseError if ownership was lost."""
    now = utcnow()
    exp = now + timedelta(seconds=get_settings().lease_seconds)
    result = await session.execute(
        update(Job).where(
            Job.id == job_id,
            Job.lease_token == lease_token,
            Job.claimed_by_worker_id == worker_id,
            Job.state.in_(("CLAIMED", "RUNNING", "CANCEL_REQUESTED")),
        ).values(lease_expires_at=exp, lease_renewed_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise StaleLeaseError(f"cannot renew lease for job {job_id}")
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.commit()
    return {"cancel_requested": job.cancel_requested, "lease_expires_at": exp}


async def start_job(session: AsyncSession, job_id, lease_token, worker_id) -> Job:
    """CLAIMED -> RUNNING (or immediate CANCELLED if cancel was requested)."""
    job = await _get_owned(session, job_id, lease_token)
    now = utcnow()
    if job.cancel_requested:
        await transition(session, job, "CANCELLED", reason="cancelled before start",
                         worker_id=worker_id)
        job.finished_at = now
        job.lease_token = None
        execution = await _current_execution(session, job)
        if execution:
            execution.state = "CANCELLED"
            execution.finished_at = now
        await _release_worker_slot(session, worker_id)
        await session.commit()
        await _post_terminal(session, job, "job.cancelled")
        return job
    await transition(session, job, "RUNNING", reason="execution started", worker_id=worker_id)
    job.started_at = now
    execution = await _current_execution(session, job)
    if execution:
        execution.state = "RUNNING"
        execution.started_at = now
    await session.commit()
    await bus.emit("job.state", {"job_id": str(job.id), "state": "RUNNING"},
                   str(job.project_id))
    return job


async def update_progress(session: AsyncSession, job_id, lease_token, progress: float) -> None:
    result = await session.execute(
        update(Job).where(Job.id == job_id, Job.lease_token == lease_token)
        .values(progress=max(0.0, min(100.0, progress)), updated_at=utcnow())
    )
    if result.rowcount != 1:
        await session.rollback()
        raise StaleLeaseError(f"cannot update progress for job {job_id}")
    await session.commit()


async def _release_worker_slot(session: AsyncSession, worker_id, *,
                               completed=False, failed=False) -> None:
    if worker_id is None:
        return
    values = {"active_jobs": Worker.active_jobs - 1}
    if completed:
        values["completed_jobs"] = Worker.completed_jobs + 1
    if failed:
        values["failed_jobs"] = Worker.failed_jobs + 1
    await session.execute(
        update(Worker).where(Worker.id == worker_id, Worker.active_jobs > 0).values(**values)
    )


# --------------------------------------------------------------------------- #
# Completion / failure / timeout
# --------------------------------------------------------------------------- #
async def complete_job(session: AsyncSession, job_id, lease_token, worker_id,
                       result: dict | None = None) -> Job:
    job = await _get_owned(session, job_id, lease_token)
    now = utcnow()
    await transition(session, job, "COMPLETED", reason="handler succeeded",
                     worker_id=worker_id)
    job.result = result
    job.progress = 100.0
    job.finished_at = now
    job.lease_token = None
    job.lease_expires_at = None
    execution = await _current_execution(session, job)
    if execution:
        execution.state = "COMPLETED"
        execution.finished_at = now
        execution.result = result
    await _release_worker_slot(session, worker_id, completed=True)
    await session.commit()
    await _post_terminal(session, job, "job.completed")
    await resolve_dependents(session, job)
    return job


async def fail_job(session: AsyncSession, job_id, lease_token, worker_id,
                   error: dict, *, error_category: str = "retryable",
                   timed_out: bool = False) -> Job:
    """Handler failure -> RETRY_SCHEDULED, FAILED or DEAD_LETTERED."""
    job = await _get_owned(session, job_id, lease_token)
    return await _handle_failure(session, job, worker_id, error,
                                 error_category=error_category, timed_out=timed_out)


async def _handle_failure(session: AsyncSession, job: Job, worker_id,
                          error: dict, *, error_category: str,
                          timed_out: bool = False, lease_expired: bool = False) -> Job:
    now = utcnow()
    queue = (await session.execute(select(Queue).where(Queue.id == job.queue_id))).scalar_one()
    policy = normalize_policy(job.retry_policy, queue.default_retry_policy)
    retryable = is_retryable(error_category) and not job.cancel_requested
    attempts_left = job.attempt_count < job.max_attempts

    execution = await _current_execution(session, job)
    intermediate = None
    if timed_out:
        intermediate = "TIMED_OUT"
        await transition(session, job, "TIMED_OUT",
                         reason=f"execution exceeded {job.timeout_seconds}s timeout",
                         worker_id=worker_id)

    if job.cancel_requested:
        await transition(session, job, "CANCELLED", reason="cancel requested during execution",
                         worker_id=worker_id)
        final = "CANCELLED"
    elif retryable and attempts_left:
        delay = compute_delay(policy, job.attempt_count)
        job.next_retry_at = now + timedelta(seconds=delay)
        await transition(
            session, job, "RETRY_SCHEDULED", worker_id=worker_id,
            reason=(f"attempt {job.attempt_count}/{job.max_attempts} "
                    f"{'timed out' if timed_out else 'failed'}; retry in {delay}s "
                    f"({policy['strategy']})"))
        if execution:
            execution.retry_delay_seconds = delay
            execution.next_retry_at = job.next_retry_at
        final = "RETRY_SCHEDULED"
    else:
        reason = ("non-retryable error" if not retryable
                  else f"attempts exhausted ({job.attempt_count}/{job.max_attempts})")
        if queue.dlq_enabled:
            if not timed_out:
                await transition(session, job, "FAILED", reason=reason, worker_id=worker_id)
            await transition(session, job, "DEAD_LETTERED", reason=reason, worker_id=worker_id)
            session.add(DeadLetterEntry(
                job_id=job.id, project_id=job.project_id, queue_id=job.queue_id,
                reason="timeout" if timed_out else
                       ("non_retryable" if not retryable else "attempts_exhausted"),
                error=error, attempts=job.attempt_count))
            final = "DEAD_LETTERED"
        else:
            await transition(session, job, "FAILED", reason=reason, worker_id=worker_id)
            final = "FAILED"
        job.finished_at = now

    job.error = error
    job.lease_token = None
    job.lease_expires_at = None
    if execution:
        execution.state = intermediate or final
        execution.finished_at = now
        execution.error = error
        execution.error_category = "timeout" if timed_out else error_category
    if not lease_expired:
        await _release_worker_slot(session, worker_id, failed=True)
    await session.commit()

    if final in ("FAILED", "DEAD_LETTERED", "CANCELLED"):
        event = {"FAILED": "job.failed", "DEAD_LETTERED": "job.dead_lettered",
                 "CANCELLED": "job.cancelled"}[final]
        if timed_out and final != "DEAD_LETTERED":
            event = "job.timed_out"
        await _post_terminal(session, job, event)
        await resolve_dependents(session, job)
    else:
        await bus.emit("job.state", {"job_id": str(job.id), "state": final},
                       str(job.project_id))
    return job


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
CANCELLABLE_DIRECT = {"CREATED", "QUEUED", "SCHEDULED", "BLOCKED", "RETRY_SCHEDULED"}


async def request_cancel(session: AsyncSession, job: Job, *, requested_by=None,
                         reason: str | None = None) -> Job:
    now = utcnow()
    job.cancel_requested = True
    job.cancel_reason = reason
    job.cancelled_by = requested_by
    job.cancel_requested_at = now
    if job.state in CANCELLABLE_DIRECT:
        await transition(session, job, "CANCELLED",
                         reason=reason or "cancelled by user")
        job.finished_at = now
        job.lease_token = None
        await session.commit()
        await _post_terminal(session, job, "job.cancelled")
        await resolve_dependents(session, job)
    elif job.state == "RUNNING":
        await transition(session, job, "CANCEL_REQUESTED",
                         reason=reason or "cancellation requested")
        await session.commit()
        await bus.emit("job.state", {"job_id": str(job.id), "state": "CANCEL_REQUESTED"},
                       str(job.project_id))
    else:
        # CLAIMED: flag only — worker checks the flag before starting.
        await session.commit()
    return job


# --------------------------------------------------------------------------- #
# Dependency resolution & workflow progress
# --------------------------------------------------------------------------- #
async def resolve_dependents(session: AsyncSession, finished: Job) -> None:
    """Release, skip or cancel jobs that depend on ``finished``."""
    dependent_ids = (await session.execute(
        select(JobDependency.job_id).where(JobDependency.depends_on_job_id == finished.id)
    )).scalars().all()
    for dep_id in dependent_ids:
        dependent = (await session.execute(
            select(Job).where(Job.id == dep_id))).scalar_one_or_none()
        if dependent is None or dependent.state != "BLOCKED":
            continue
        upstream = (await session.execute(
            select(Job.state, Job.id).join(
                JobDependency, JobDependency.depends_on_job_id == Job.id)
            .where(JobDependency.job_id == dep_id)
        )).all()
        states = [s for s, _ in upstream]
        failed_states = {"FAILED", "DEAD_LETTERED", "CANCELLED", "TIMED_OUT", "SKIPPED"}
        if any(st in failed_states for st in states):
            policy = dependent.on_dependency_failure
            if policy == "continue":
                if all(st in {"COMPLETED"} | failed_states for st in states):
                    await transition(session, dependent, "QUEUED",
                                     reason="dependencies finished (continue-on-failure)")
                    dependent.available_at = utcnow()
                    await session.commit()
                    await bus.emit("job.state", {"job_id": str(dependent.id),
                                                 "state": "QUEUED"}, str(dependent.project_id))
            elif policy == "skip":
                await transition(session, dependent, "SKIPPED",
                                 reason="upstream dependency failed")
                dependent.finished_at = utcnow()
                await session.commit()
                await resolve_dependents(session, dependent)
            else:  # fail -> cancel dependants
                await transition(session, dependent, "CANCELLED",
                                 reason="upstream dependency failed")
                dependent.finished_at = utcnow()
                await session.commit()
                await _post_terminal(session, dependent, "job.cancelled")
                await resolve_dependents(session, dependent)
        elif all(st == "COMPLETED" for st in states):
            await transition(session, dependent, "QUEUED", reason="all dependencies completed")
            dependent.available_at = utcnow()
            await session.commit()
            await bus.emit("job.state", {"job_id": str(dependent.id), "state": "QUEUED"},
                           str(dependent.project_id))
    if finished.workflow_id:
        await _update_workflow(session, finished.workflow_id)


async def _update_workflow(session: AsyncSession, workflow_id) -> None:
    wf = (await session.execute(
        select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
    if wf is None:
        return
    states = (await session.execute(
        select(Job.state).where(Job.workflow_id == workflow_id))).scalars().all()
    total = len(states) or 1
    terminal = {"COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTERED", "SKIPPED", "TIMED_OUT"}
    done = sum(1 for st in states if st in terminal)
    wf.progress = round(100.0 * done / total, 2)
    if done == total:
        wf.state = "COMPLETED" if all(st in ("COMPLETED", "SKIPPED") for st in states) else "FAILED"
        wf.result = {"states": {st: states.count(st) for st in set(states)}}
        await session.commit()
        await enqueue_webhooks(session, wf.project_id, "workflow.completed", {
            "workflow_id": str(wf.id), "state": wf.state, "progress": wf.progress})
        await bus.emit("workflow.completed", {"workflow_id": str(wf.id), "state": wf.state},
                       str(wf.project_id))
    else:
        wf.state = "RUNNING"
        await session.commit()


# --------------------------------------------------------------------------- #
# Terminal-event side effects (webhooks + websocket)
# --------------------------------------------------------------------------- #
async def _post_terminal(session: AsyncSession, job: Job, event_type: str) -> None:
    await bus.emit("job.state", {
        "job_id": str(job.id), "state": job.state, "queue_id": str(job.queue_id),
        "job_type": job.job_type}, str(job.project_id))
    await enqueue_webhooks(session, job.project_id, event_type, {
        "job_id": str(job.id), "job_type": job.job_type, "state": job.state,
        "queue_id": str(job.queue_id), "correlation_id": job.correlation_id,
        "attempt_count": job.attempt_count,
        "error": job.error, "finished_at":
            job.finished_at.isoformat() + "Z" if job.finished_at else None,
    })


async def enqueue_webhooks(session: AsyncSession, project_id, event_type: str,
                           payload: dict) -> None:
    endpoints = (await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.project_id == project_id,
            WebhookEndpoint.active.is_(True))
    )).scalars().all()
    created = False
    for ep in endpoints:
        if event_type in (ep.events or []):
            session.add(WebhookDelivery(endpoint_id=ep.id, event_type=event_type,
                                        payload=payload))
            created = True
    if created:
        await session.commit()
