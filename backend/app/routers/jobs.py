"""Job explorer + job lifecycle endpoints."""
import uuid
import uuid as _uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import bad_request, conflict, not_found
from ..models import (
    Job, JobDependency, JobExecution, JobLog, JobStateTransition, Queue, utcnow,
)
from ..schemas import BatchCreate, CancelRequest, JobCreate
from ..serializers import (
    execution_out, job_out, log_out, page_meta, transition_out,
)
from ..services import jobs as job_service
from ..services import lifecycle
from ..state_machine import transition

router = APIRouter(prefix="/projects/{project_id}/jobs", tags=["jobs"])

SORTABLE = {"created_at": Job.created_at, "priority": Job.priority,
            "available_at": Job.available_at, "finished_at": Job.finished_at,
            "state": Job.state}


async def _get_job(db, project, job_id) -> Job:
    job = (await db.execute(select(Job).where(
        Job.id == job_id, Job.project_id == project.id))).scalar_one_or_none()
    if job is None:
        raise not_found("job")
    return job


@router.get("")
async def list_jobs(
    project_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    state: str | None = None, queue_id: uuid.UUID | None = None,
    job_type: str | None = None, worker_id: uuid.UUID | None = None,
    tag: str | None = None, search: str | None = None,
    has_retries: bool | None = None, batch_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
    created_after: datetime | None = None, created_before: datetime | None = None,
    sort: str = Query("created_at"), order: str = Query("desc", pattern="^(asc|desc)$"),
):
    project, _ = await require_project(db, principal, project_id, "viewer")
    q = select(Job).where(Job.project_id == project.id)
    if state:
        q = q.where(Job.state.in_(state.split(",")))
    if queue_id:
        q = q.where(Job.queue_id == queue_id)
    if job_type:
        q = q.where(Job.job_type == job_type)
    if worker_id:
        q = q.where(Job.claimed_by_worker_id == worker_id)
    if batch_id:
        q = q.where(Job.batch_id == batch_id)
    if workflow_id:
        q = q.where(Job.workflow_id == workflow_id)
    if has_retries is not None:
        q = q.where(Job.attempt_count > 1) if has_retries else q.where(Job.attempt_count <= 1)
    if created_after:
        q = q.where(Job.created_at >= created_after.replace(tzinfo=None))
    if created_before:
        q = q.where(Job.created_at <= created_before.replace(tzinfo=None))
    if search:
        like = f"%{search}%"
        cond = Job.correlation_id.ilike(like) | Job.job_type.ilike(like)
        try:
            cond = cond | (Job.id == _uuid.UUID(search))
        except ValueError:
            pass
        q = q.where(cond)
    if tag:
        # portable JSON containment: LIKE over serialized tags (indexed GIN on
        # PG would be the production upgrade; documented in DATABASE.md)
        q = q.where(func.cast(Job.tags, __import__("sqlalchemy").String).like(f'%"{tag}"%'))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    col = SORTABLE.get(sort, Job.created_at)
    q = q.order_by(col.desc() if order == "desc" else col.asc(), Job.id)
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [job_out(j) for j in rows], "meta": page_meta(total, page, page_size)}


@router.post("", status_code=201)
async def create_job(project_id: uuid.UUID, body: JobCreate, request: Request,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = (await db.execute(select(Queue).where(
        Queue.id == body.queue_id, Queue.project_id == project.id,
        Queue.deleted_at.is_(None)))).scalar_one_or_none()
    if queue is None:
        raise not_found("queue")
    scheduled_at = body.scheduled_at.replace(tzinfo=None) if body.scheduled_at else None
    if body.delay_seconds:
        scheduled_at = utcnow() + timedelta(seconds=body.delay_seconds)
    job = await job_service.create_job(
        db, project, queue, job_type=body.job_type, payload=body.payload,
        priority=body.priority, scheduled_at=scheduled_at,
        max_attempts=body.max_attempts, retry_policy=body.retry_policy,
        timeout_seconds=body.timeout_seconds, idempotency_key=body.idempotency_key,
        correlation_id=body.correlation_id, tags=body.tags,
        routing_key=body.routing_key, required_capabilities=body.required_capabilities,
        created_by=principal.user.id if principal.user else None)
    return job_out(job, detail=True)


@router.post("/batch", status_code=201)
async def create_batch(project_id: uuid.UUID, body: BatchCreate, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = (await db.execute(select(Queue).where(
        Queue.id == body.queue_id, Queue.project_id == project.id,
        Queue.deleted_at.is_(None)))).scalar_one_or_none()
    if queue is None:
        raise not_found("queue")
    allowed = {"job_type", "payload", "priority", "max_attempts", "idempotency_key",
               "tags", "timeout_seconds", "correlation_id"}
    items = [{k: v for k, v in item.items() if k in allowed} for item in body.jobs]
    for item in items:
        if "job_type" not in item:
            raise bad_request("MISSING_JOB_TYPE", "every batch item needs job_type")
    batch_id, jobs = await job_service.create_batch(
        db, project, queue, items,
        created_by=principal.user.id if principal.user else None)
    return {"batch_id": str(batch_id), "count": len(jobs),
            "items": [job_out(j) for j in jobs]}


@router.get("/{job_id}")
async def get_job(project_id: uuid.UUID, job_id: uuid.UUID,
                  principal: Principal = Depends(get_principal),
                  db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    job = await _get_job(db, project, job_id)
    executions = (await db.execute(select(JobExecution).where(
        JobExecution.job_id == job.id).order_by(JobExecution.attempt_number))).scalars().all()
    transitions = (await db.execute(select(JobStateTransition).where(
        JobStateTransition.job_id == job.id)
        .order_by(JobStateTransition.id))).scalars().all()
    deps = (await db.execute(select(JobDependency).where(
        JobDependency.job_id == job.id))).scalars().all()
    dependents = (await db.execute(select(JobDependency).where(
        JobDependency.depends_on_job_id == job.id))).scalars().all()
    return {**job_out(job, detail=True),
            "executions": [execution_out(e) for e in executions],
            "timeline": [transition_out(t) for t in transitions],
            "depends_on": [str(d.depends_on_job_id) for d in deps],
            "dependents": [str(d.job_id) for d in dependents]}


@router.get("/{job_id}/logs")
async def job_logs(project_id: uuid.UUID, job_id: uuid.UUID,
                   principal: Principal = Depends(get_principal),
                   db: AsyncSession = Depends(get_db),
                   after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    """Cursor-paginated logs (keyset on autoincrement id)."""
    project, _ = await require_project(db, principal, project_id, "viewer")
    job = await _get_job(db, project, job_id)
    rows = (await db.execute(select(JobLog).where(
        JobLog.job_id == job.id, JobLog.id > after_id)
        .order_by(JobLog.id).limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [log_out(r) for r in rows],
            "next_cursor": rows[-1].id if rows and has_more else None}


@router.post("/{job_id}/cancel")
async def cancel_job(project_id: uuid.UUID, job_id: uuid.UUID, body: CancelRequest, request: Request,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    job = await _get_job(db, project, job_id)
    if job.state in ("COMPLETED", "CANCELLED", "DEAD_LETTERED", "SKIPPED", "FAILED"):
        raise conflict("ALREADY_TERMINAL", f"job is already {job.state}")
    job = await lifecycle.request_cancel(
        db, job, requested_by=principal.user.id if principal.user else None,
        reason=body.reason)
    await audit(db, request, principal, "job.cancel", "job", job.id,
                org_id=project.organization_id, project_id=project.id,
                changes={"reason": body.reason})
    return job_out(job)


@router.post("/{job_id}/retry")
async def retry_job(project_id: uuid.UUID, job_id: uuid.UUID, request: Request,
                    principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db)):
    """Manual retry of FAILED / DEAD_LETTERED / TIMED_OUT / CANCELLED jobs."""
    project, _ = await require_project(db, principal, project_id, "developer")
    job = await _get_job(db, project, job_id)
    if job.state not in ("FAILED", "DEAD_LETTERED", "TIMED_OUT", "CANCELLED"):
        raise conflict("NOT_RETRYABLE", f"cannot manually retry a {job.state} job")
    await transition(db, job, "QUEUED", reason=f"manual retry by {principal.label}")
    job.available_at = utcnow()
    job.cancel_requested = False
    job.cancel_reason = None
    job.finished_at = None
    job.error = None
    job.next_retry_at = None
    job.max_attempts = max(job.max_attempts, job.attempt_count + 1)
    await db.commit()
    await audit(db, request, principal, "job.retry", "job", job.id,
                org_id=project.organization_id, project_id=project.id)
    return job_out(job)


@router.post("/{job_id}/clone", status_code=201)
async def clone_job(project_id: uuid.UUID, job_id: uuid.UUID, request: Request,
                    principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    src = await _get_job(db, project, job_id)
    queue = (await db.execute(select(Queue).where(Queue.id == src.queue_id))).scalar_one()
    job = await job_service.create_job(
        db, project, queue, job_type=src.job_type, payload=src.payload,
        priority=src.priority, max_attempts=src.max_attempts,
        retry_policy=src.retry_policy, timeout_seconds=src.timeout_seconds,
        tags=src.tags, routing_key=src.routing_key,
        required_capabilities=src.required_capabilities,
        created_by=principal.user.id if principal.user else None,
        correlation_id=f"clone-of:{src.id}")
    await audit(db, request, principal, "job.clone", "job", src.id,
                org_id=project.organization_id, project_id=project.id,
                changes={"new_job_id": str(job.id)})
    return job_out(job)
