"""Metrics aggregation for the dashboard and Prometheus exposition."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    DeadLetterEntry, Job, JobExecution, Queue, Worker, utcnow,
)


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, round(pct / 100 * (len(sorted_vals) - 1))))
    return round(sorted_vals[k], 4)


async def latency_percentiles(db: AsyncSession, project_id=None,
                              window_minutes: int = 60) -> dict:
    """P50/P95/P99 execution latency over recent finished executions.
    Computed client-side over a bounded window (portable across dialects;
    percentile_cont would be the PG-only equivalent)."""
    cutoff = utcnow() - timedelta(minutes=window_minutes)
    q = (select(JobExecution.started_at, JobExecution.finished_at)
         .where(JobExecution.finished_at.is_not(None),
                JobExecution.started_at.is_not(None),
                JobExecution.finished_at >= cutoff)
         .limit(10_000))
    if project_id:
        q = q.join(Job, Job.id == JobExecution.job_id).where(Job.project_id == project_id)
    rows = (await db.execute(q)).all()
    durations = sorted((f - s).total_seconds() for s, f in rows)
    return {"p50": _percentile(durations, 50), "p95": _percentile(durations, 95),
            "p99": _percentile(durations, 99),
            "avg": round(sum(durations) / len(durations), 4) if durations else None,
            "count": len(durations)}


async def overview(db: AsyncSession, project_id=None) -> dict:
    now = utcnow()
    jobs_q = select(Job.state, func.count()).group_by(Job.state)
    if project_id:
        jobs_q = jobs_q.where(Job.project_id == project_id)
    by_state = dict((await db.execute(jobs_q)).all())

    minute_ago = now - timedelta(minutes=1)
    recent_q = select(func.count()).select_from(JobExecution).where(
        JobExecution.finished_at >= minute_ago)
    if project_id:
        recent_q = recent_q.join(Job, Job.id == JobExecution.job_id).where(
            Job.project_id == project_id)
    jobs_per_minute = (await db.execute(recent_q)).scalar_one()

    completed = by_state.get("COMPLETED", 0)
    failed = by_state.get("FAILED", 0) + by_state.get("DEAD_LETTERED", 0)
    finished = completed + failed
    retries_q = select(func.count()).select_from(Job).where(Job.attempt_count > 1)
    if project_id:
        retries_q = retries_q.where(Job.project_id == project_id)
    retried = (await db.execute(retries_q)).scalar_one()
    total_jobs = sum(by_state.values())

    workers_q = select(Worker.status, func.count()).group_by(Worker.status)
    workers = dict((await db.execute(workers_q)).all())
    cap_q = select(func.sum(Worker.capacity), func.sum(Worker.active_jobs)).where(
        Worker.status.in_(("online", "draining")))
    cap, active = (await db.execute(cap_q)).one()

    dlq_q = select(func.count()).select_from(DeadLetterEntry).where(
        DeadLetterEntry.resolved_at.is_(None))
    if project_id:
        dlq_q = dlq_q.where(DeadLetterEntry.project_id == project_id)
    dlq_count = (await db.execute(dlq_q)).scalar_one()

    return {
        "jobs_total": total_jobs, "by_state": by_state,
        "jobs_per_minute": jobs_per_minute,
        "queue_depth": by_state.get("QUEUED", 0),
        "scheduled_count": by_state.get("SCHEDULED", 0) + by_state.get("RETRY_SCHEDULED", 0),
        "running": by_state.get("RUNNING", 0) + by_state.get("CLAIMED", 0),
        "success_rate": round(100.0 * completed / finished, 2) if finished else None,
        "failure_rate": round(100.0 * failed / finished, 2) if finished else None,
        "retry_rate": round(100.0 * retried / total_jobs, 2) if total_jobs else None,
        "dlq_count": dlq_count,
        "workers": workers,
        "active_workers": workers.get("online", 0) + workers.get("draining", 0),
        "worker_utilization": round(100.0 * (active or 0) / cap, 2) if cap else None,
        "latency": await latency_percentiles(db, project_id),
    }


async def throughput_series(db: AsyncSession, project_id=None,
                            minutes: int = 30) -> list[dict]:
    """Per-minute completed/failed counts for charts."""
    cutoff = utcnow() - timedelta(minutes=minutes)
    q = (select(JobExecution.finished_at, JobExecution.state)
         .where(JobExecution.finished_at >= cutoff).limit(50_000))
    if project_id:
        q = q.join(Job, Job.id == JobExecution.job_id).where(Job.project_id == project_id)
    rows = (await db.execute(q)).all()
    buckets: dict[str, dict] = {}
    for finished_at, state in rows:
        key = finished_at.strftime("%H:%M")
        b = buckets.setdefault(key, {"minute": key, "completed": 0, "failed": 0, "other": 0})
        if state == "COMPLETED":
            b["completed"] += 1
        elif state in ("FAILED", "TIMED_OUT", "DEAD_LETTERED"):
            b["failed"] += 1
        else:
            b["other"] += 1
    return sorted(buckets.values(), key=lambda b: b["minute"])


async def prometheus_text(db: AsyncSession) -> str:
    o = await overview(db)
    lines = [
        "# HELP chronosgrid_jobs_total Jobs by state",
        "# TYPE chronosgrid_jobs_total gauge",
    ]
    for state, count in o["by_state"].items():
        lines.append(f'chronosgrid_jobs_total{{state="{state}"}} {count}')
    lines += [
        "# TYPE chronosgrid_queue_depth gauge",
        f"chronosgrid_queue_depth {o['queue_depth']}",
        "# TYPE chronosgrid_dlq_count gauge",
        f"chronosgrid_dlq_count {o['dlq_count']}",
        "# TYPE chronosgrid_active_workers gauge",
        f"chronosgrid_active_workers {o['active_workers']}",
        "# TYPE chronosgrid_jobs_per_minute gauge",
        f"chronosgrid_jobs_per_minute {o['jobs_per_minute']}",
    ]
    for k in ("p50", "p95", "p99"):
        v = o["latency"].get(k)
        if v is not None:
            lines.append(f'chronosgrid_execution_latency_seconds{{quantile="{k[1:]}"}} {v}')
    if o["worker_utilization"] is not None:
        lines.append(f"chronosgrid_worker_utilization_percent {o['worker_utilization']}")
    return "\n".join(lines) + "\n"
