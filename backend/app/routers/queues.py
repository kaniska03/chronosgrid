"""Queue CRUD, pause/resume, statistics and health."""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import conflict, not_found
from ..events import bus
from ..models import Job, Queue, utcnow
from ..schemas import QueueCreate, QueueUpdate
from ..serializers import queue_out
from ..services.retries import normalize_policy

router = APIRouter(prefix="/projects/{project_id}/queues", tags=["queues"])


async def _queue_stats(db: AsyncSession, queue: Queue) -> dict:
    counts = dict((await db.execute(
        select(Job.state, func.count()).where(Job.queue_id == queue.id)
        .group_by(Job.state))).all())
    oldest = (await db.execute(
        select(func.min(Job.available_at)).where(
            Job.queue_id == queue.id, Job.state == "QUEUED"))).scalar()
    depth = counts.get("QUEUED", 0) + counts.get("SCHEDULED", 0)
    active = sum(counts.get(s, 0) for s in ("CLAIMED", "RUNNING", "CANCEL_REQUESTED"))
    completed = counts.get("COMPLETED", 0)
    failed = counts.get("FAILED", 0) + counts.get("DEAD_LETTERED", 0)
    finished = completed + failed
    return {
        "depth": depth, "active": active, "by_state": counts,
        "completed": completed, "failed": failed,
        "success_rate": round(100.0 * completed / finished, 2) if finished else None,
        "oldest_waiting_at": oldest.isoformat() + "Z" if oldest else None,
        "health": ("paused" if queue.paused else
                   "backed_up" if depth > 5 * queue.max_concurrent_jobs else "healthy"),
    }


async def _get_queue(db, project, queue_id) -> Queue:
    queue = (await db.execute(select(Queue).where(
        Queue.id == queue_id, Queue.project_id == project.id,
        Queue.deleted_at.is_(None)))).scalar_one_or_none()
    if queue is None:
        raise not_found("queue")
    return queue


@router.get("")
async def list_queues(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                      db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    queues = (await db.execute(select(Queue).where(
        Queue.project_id == project.id, Queue.deleted_at.is_(None))
        .order_by(Queue.name))).scalars().all()
    return {"items": [queue_out(q, await _queue_stats(db, q)) for q in queues]}


@router.post("", status_code=201)
async def create_queue(project_id: uuid.UUID, body: QueueCreate, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    if body.default_retry_policy is not None:
        normalize_policy(body.default_retry_policy)
    queue = Queue(project_id=project.id, **body.model_dump(exclude_none=True))
    db.add(queue)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise conflict("DUPLICATE_QUEUE", f"queue {body.name!r} already exists")
    await audit(db, request, principal, "queue.create", "queue", queue.id,
                org_id=project.organization_id, project_id=project.id)
    return queue_out(queue, await _queue_stats(db, queue))


@router.get("/{queue_id}")
async def get_queue(project_id: uuid.UUID, queue_id: uuid.UUID,
                    principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    queue = await _get_queue(db, project, queue_id)
    return queue_out(queue, await _queue_stats(db, queue))


@router.patch("/{queue_id}")
async def update_queue(project_id: uuid.UUID, queue_id: uuid.UUID, body: QueueUpdate,
                       request: Request, principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = await _get_queue(db, project, queue_id)
    changes = body.model_dump(exclude_unset=True)
    if "default_retry_policy" in changes and changes["default_retry_policy"]:
        normalize_policy(changes["default_retry_policy"])
    for k, v in changes.items():
        setattr(queue, k, v)
    await db.commit()
    await audit(db, request, principal, "queue.update", "queue", queue.id,
                org_id=project.organization_id, project_id=project.id, changes=changes)
    return queue_out(queue, await _queue_stats(db, queue))


@router.post("/{queue_id}/pause")
async def pause_queue(project_id: uuid.UUID, queue_id: uuid.UUID, request: Request,
                      principal: Principal = Depends(get_principal),
                      db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = await _get_queue(db, project, queue_id)
    queue.paused = True
    await db.commit()
    await audit(db, request, principal, "queue.pause", "queue", queue.id,
                org_id=project.organization_id, project_id=project.id)
    await bus.emit("queue.paused", {"queue_id": str(queue.id)}, str(project.id))
    return queue_out(queue, await _queue_stats(db, queue))


@router.post("/{queue_id}/resume")
async def resume_queue(project_id: uuid.UUID, queue_id: uuid.UUID, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    queue = await _get_queue(db, project, queue_id)
    queue.paused = False
    await db.commit()
    await audit(db, request, principal, "queue.resume", "queue", queue.id,
                org_id=project.organization_id, project_id=project.id)
    await bus.emit("queue.resumed", {"queue_id": str(queue.id)}, str(project.id))
    return queue_out(queue, await _queue_stats(db, queue))


@router.delete("/{queue_id}")
async def delete_queue(project_id: uuid.UUID, queue_id: uuid.UUID, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    queue = await _get_queue(db, project, queue_id)
    queue.deleted_at = utcnow()   # soft delete: jobs/history remain queryable
    queue.paused = True
    await db.commit()
    await audit(db, request, principal, "queue.delete", "queue", queue.id,
                org_id=project.organization_id, project_id=project.id)
    return {"deleted": True}
