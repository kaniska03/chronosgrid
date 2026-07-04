"""Recurring (cron) job endpoints."""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import not_found
from ..models import Queue, RecurringJob
from ..schemas import RecurringCreate
from ..serializers import recurring_out
from ..services import jobs as job_service

router = APIRouter(prefix="/projects/{project_id}/recurring", tags=["recurring"])


@router.get("")
async def list_recurring(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                         db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    rows = (await db.execute(select(RecurringJob).where(
        RecurringJob.project_id == project.id).order_by(RecurringJob.name))).scalars().all()
    return {"items": [recurring_out(r) for r in rows]}


@router.post("", status_code=201)
async def create_recurring(project_id: uuid.UUID, body: RecurringCreate, request: Request,
                           principal: Principal = Depends(get_principal),
                           db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = (await db.execute(select(Queue).where(
        Queue.id == body.queue_id, Queue.project_id == project.id,
        Queue.deleted_at.is_(None)))).scalar_one_or_none()
    if queue is None:
        raise not_found("queue")
    rec = await job_service.create_recurring(
        db, project, queue, name=body.name, job_type=body.job_type,
        cron_expression=body.cron_expression, timezone_name=body.timezone,
        payload=body.payload, priority=body.priority,
        max_attempts=body.max_attempts, timeout_seconds=body.timeout_seconds)
    await audit(db, request, principal, "recurring.create", "recurring_job", rec.id,
                org_id=project.organization_id, project_id=project.id)
    return recurring_out(rec)


@router.post("/{recurring_id}/toggle")
async def toggle_recurring(project_id: uuid.UUID, recurring_id: uuid.UUID, request: Request,
                           principal: Principal = Depends(get_principal),
                           db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    rec = (await db.execute(select(RecurringJob).where(
        RecurringJob.id == recurring_id,
        RecurringJob.project_id == project.id))).scalar_one_or_none()
    if rec is None:
        raise not_found("recurring job")
    rec.enabled = not rec.enabled
    if rec.enabled and rec.next_run_at is None:
        from ..models import utcnow
        rec.next_run_at = job_service.next_cron_occurrence(
            rec.cron_expression, rec.timezone, utcnow())
    await db.commit()
    await audit(db, request, principal, "recurring.toggle", "recurring_job", rec.id,
                org_id=project.organization_id, project_id=project.id,
                changes={"enabled": rec.enabled})
    return recurring_out(rec)
