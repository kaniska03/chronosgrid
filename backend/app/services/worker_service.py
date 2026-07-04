"""Worker registration, heartbeats and graceful drain."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..events import bus
from ..models import Worker, WorkerHeartbeat, utcnow


async def register_worker(session: AsyncSession, *, name: str, host: str, pid: int,
                          version: str = "dev", capacity: int = 5,
                          tags: list | None = None,
                          capabilities: list | None = None) -> Worker:
    worker = Worker(name=name, host=host, pid=pid, version=version, capacity=capacity,
                    tags=tags or [], capabilities=capabilities or [], status="online")
    session.add(worker)
    await session.commit()
    await bus.emit("worker.status", {"worker_id": str(worker.id), "status": "online",
                                     "name": name})
    return worker


async def heartbeat(session: AsyncSession, worker_id, *, active_jobs: int | None = None,
                    status: str | None = None) -> Worker | None:
    worker = (await session.execute(
        select(Worker).where(Worker.id == worker_id))).scalar_one_or_none()
    if worker is None:
        return None
    worker.last_heartbeat_at = utcnow()
    if status in ("online", "draining", "unhealthy"):
        worker.status = status
    elif worker.status == "offline":
        worker.status = "online"   # worker came back
    session.add(WorkerHeartbeat(worker_id=worker.id,
                                active_jobs=active_jobs if active_jobs is not None
                                else worker.active_jobs))
    await session.commit()
    return worker


async def set_status(session: AsyncSession, worker_id, status: str) -> Worker | None:
    worker = (await session.execute(
        select(Worker).where(Worker.id == worker_id))).scalar_one_or_none()
    if worker is None:
        return None
    worker.status = status
    await session.commit()
    await bus.emit("worker.status", {"worker_id": str(worker.id), "status": status})
    return worker
