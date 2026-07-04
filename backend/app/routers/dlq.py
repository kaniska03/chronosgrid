"""Dead Letter Queue inspection and replay."""
import uuid
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import not_found
from ..models import DeadLetterEntry, Job, Queue, utcnow
from ..schemas import DlqNote
from ..serializers import dlq_out, job_out, page_meta
from ..state_machine import transition

router = APIRouter(prefix="/projects/{project_id}/dlq", tags=["dlq"])


class BulkRetry(BaseModel):
    entry_ids: list[str]
    target_queue_id: uuid.UUID | None = None


async def _get_entry(db, project, entry_id) -> DeadLetterEntry:
    e = (await db.execute(select(DeadLetterEntry).where(
        DeadLetterEntry.id == entry_id,
        DeadLetterEntry.project_id == project.id))).scalar_one_or_none()
    if e is None:
        raise not_found("DLQ entry")
    return e


async def _replay(db, project, entry: DeadLetterEntry, target_queue_id=None) -> Job:
    job = (await db.execute(select(Job).where(Job.id == entry.job_id))).scalar_one()
    if target_queue_id:
        queue = (await db.execute(select(Queue).where(
            Queue.id == target_queue_id, Queue.project_id == project.id,
            Queue.deleted_at.is_(None)))).scalar_one_or_none()
        if queue is None:
            raise not_found("target queue")
        job.queue_id = queue.id
    if job.state == "DEAD_LETTERED":
        await transition(db, job, "QUEUED", reason="replayed from DLQ")
        job.available_at = utcnow()
        job.error = None
        job.finished_at = None
        job.max_attempts = max(job.max_attempts, job.attempt_count + 1)
    entry.resolved_at = utcnow()
    await db.commit()
    return job


@router.get("")
async def list_dlq(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                   db: AsyncSession = Depends(get_db),
                   page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                   include_resolved: bool = False):
    project, _ = await require_project(db, principal, project_id, "viewer")
    q = select(DeadLetterEntry).where(DeadLetterEntry.project_id == project.id)
    if not include_resolved:
        q = q.where(DeadLetterEntry.resolved_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(DeadLetterEntry.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [dlq_out(e) for e in rows], "meta": page_meta(total, page, page_size)}


@router.post("/{entry_id}/retry")
async def retry_entry(project_id: uuid.UUID, entry_id: uuid.UUID, request: Request,
                      principal: Principal = Depends(get_principal),
                      db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    entry = await _get_entry(db, project, entry_id)
    entry.resolved_by = principal.user.id if principal.user else None
    job = await _replay(db, project, entry)
    await audit(db, request, principal, "dlq.retry", "dlq_entry", entry.id,
                org_id=project.organization_id, project_id=project.id)
    return job_out(job)


@router.post("/bulk-retry")
async def bulk_retry(project_id: uuid.UUID, body: BulkRetry, request: Request,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    replayed = []
    for entry_id in body.entry_ids[:200]:
        try:
            entry = await _get_entry(db, project, entry_id)
        except Exception:
            continue
        entry.resolved_by = principal.user.id if principal.user else None
        job = await _replay(db, project, entry, body.target_queue_id)
        replayed.append(str(job.id))
    await audit(db, request, principal, "dlq.bulk_retry", "dlq_entry", None,
                org_id=project.organization_id, project_id=project.id,
                changes={"count": len(replayed)})
    return {"replayed_job_ids": replayed}


@router.post("/{entry_id}/note")
async def add_note(project_id: uuid.UUID, entry_id: uuid.UUID, body: DlqNote, request: Request,
                   principal: Principal = Depends(get_principal),
                   db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    entry = await _get_entry(db, project, entry_id)
    entry.note = body.note
    await db.commit()
    await audit(db, request, principal, "dlq.note", "dlq_entry", entry.id,
                org_id=project.organization_id, project_id=project.id)
    return dlq_out(entry)


@router.delete("/{entry_id}")
async def delete_entry(project_id: uuid.UUID, entry_id: uuid.UUID, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    entry = await _get_entry(db, project, entry_id)
    await db.delete(entry)
    await db.commit()
    await audit(db, request, principal, "dlq.delete", "dlq_entry", entry_id,
                org_id=project.organization_id, project_id=project.id)
    return {"deleted": True}
