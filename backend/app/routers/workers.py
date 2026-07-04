"""Worker monitor endpoints (read) + drain control."""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal
from ..errors import forbidden, not_found
from ..models import JobExecution, Worker
from ..serializers import worker_out
from ..services.worker_service import set_status

router = APIRouter(prefix="/workers", tags=["workers"])


@router.get("")
async def list_workers(principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db), status: str | None = None):
    q = select(Worker).order_by(Worker.last_heartbeat_at.desc())
    if status:
        q = q.where(Worker.status == status)
    workers = (await db.execute(q.limit(500))).scalars().all()
    # avg execution latency per worker (finished executions)
    items = []
    for w in workers:
        avg = (await db.execute(
            select(func.avg(
                func.julianday(JobExecution.finished_at) - func.julianday(JobExecution.started_at))
                if db.bind.dialect.name == "sqlite" else
                func.avg(func.extract("epoch", JobExecution.finished_at - JobExecution.started_at)))
            .where(JobExecution.worker_id == w.id,
                   JobExecution.finished_at.is_not(None),
                   JobExecution.started_at.is_not(None)))).scalar()
        if avg is not None and db.bind.dialect.name == "sqlite":
            avg = avg * 86400
        items.append({**worker_out(w),
                      "avg_execution_seconds": round(avg, 3) if avg is not None else None})
    return {"items": items}


@router.get("/{worker_id}")
async def get_worker(worker_id: uuid.UUID, principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    w = (await db.execute(select(Worker).where(Worker.id == worker_id))).scalar_one_or_none()
    if w is None:
        raise not_found("worker")
    return worker_out(w)


@router.post("/{worker_id}/drain")
async def drain_worker(worker_id: uuid.UUID, request: Request,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    if principal.user is None:
        raise forbidden("worker control requires user authentication")
    w = await set_status(db, worker_id, "draining")
    if w is None:
        raise not_found("worker")
    await audit(db, request, principal, "worker.drain", "worker", worker_id)
    return worker_out(w)
