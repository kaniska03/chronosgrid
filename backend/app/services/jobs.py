"""Job / batch / recurring / workflow creation with validation, quotas and
idempotency."""
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, bad_request, conflict, too_many
from ..events import bus
from ..models import (
    Job, JobDependency, Project, Queue, RecurringJob, UsageCounter, Workflow, utcnow,
)
from ..state_machine import transition

SAFE_HANDLERS = ("sleep", "math", "text_transform", "http_check", "report", "flaky", "always_fail")


def validate_cron(expression: str, tz: str) -> None:
    if not croniter.is_valid(expression):
        raise bad_request("INVALID_CRON", f"invalid cron expression: {expression!r}")
    try:
        ZoneInfo(tz)
    except Exception:
        raise bad_request("INVALID_TIMEZONE", f"unknown time zone: {tz!r}")


def next_cron_occurrence(expression: str, tz: str, after: datetime) -> datetime:
    """``after`` is naive UTC; result is naive UTC."""
    zone = ZoneInfo(tz)
    local = after.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    nxt = croniter(expression, local).get_next(datetime)
    return nxt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


async def enforce_quotas(session: AsyncSession, project: Project, payload: dict,
                         n_jobs: int = 1) -> None:
    raw = json.dumps(payload or {})
    if len(raw.encode()) > project.max_payload_bytes:
        raise ApiError(413, "PAYLOAD_TOO_LARGE",
                       f"payload exceeds {project.max_payload_bytes} bytes")
    day = utcnow().strftime("%Y-%m-%d")
    counter = (await session.execute(
        select(UsageCounter).where(UsageCounter.project_id == project.id,
                                   UsageCounter.day == day)
    )).scalar_one_or_none()
    used = counter.jobs_created if counter else 0
    if used + n_jobs > project.daily_job_quota:
        raise too_many("daily job quota exceeded", retry_after=3600,
                       details={"quota": project.daily_job_quota, "used": used})
    if counter is None:
        counter = UsageCounter(project_id=project.id, day=day, jobs_created=0)
        session.add(counter)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            counter = (await session.execute(
                select(UsageCounter).where(UsageCounter.project_id == project.id,
                                           UsageCounter.day == day))).scalar_one()
    await session.execute(
        update(UsageCounter).where(UsageCounter.id == counter.id)
        .values(jobs_created=UsageCounter.jobs_created + n_jobs))


async def create_job(session: AsyncSession, project: Project, queue: Queue, *,
                     job_type: str, payload: dict | None = None, priority: int = 0,
                     scheduled_at: datetime | None = None, max_attempts: int | None = None,
                     retry_policy: dict | None = None, timeout_seconds: int | None = None,
                     idempotency_key: str | None = None, correlation_id: str | None = None,
                     tags: list | None = None, routing_key: str | None = None,
                     required_capabilities: list | None = None,
                     on_dependency_failure: str = "fail",
                     parent_job_id=None, batch_id=None, workflow_id=None,
                     recurring_job_id=None, created_by=None, timezone_name: str | None = None,
                     initial_state: str | None = None, check_quota: bool = True) -> Job:
    if job_type not in SAFE_HANDLERS:
        raise bad_request("UNKNOWN_JOB_TYPE",
                          f"job_type must be one of {', '.join(SAFE_HANDLERS)}")
    if retry_policy is not None:
        from .retries import normalize_policy
        try:
            normalize_policy(retry_policy)
        except ValueError as exc:
            raise bad_request("INVALID_RETRY_POLICY", str(exc))
    if on_dependency_failure not in ("fail", "skip", "continue"):
        raise bad_request("INVALID_DEPENDENCY_POLICY",
                          "on_dependency_failure must be fail|skip|continue")

    # Idempotent creation: return the existing job for a repeated key.
    if idempotency_key:
        existing = (await session.execute(
            select(Job).where(Job.project_id == project.id,
                              Job.idempotency_key == idempotency_key)
        )).scalar_one_or_none()
        if existing:
            return existing

    if check_quota:
        await enforce_quotas(session, project, payload or {})

    now = utcnow()
    state = initial_state or ("SCHEDULED" if scheduled_at and scheduled_at > now else "QUEUED")
    job = Job(
        project_id=project.id, queue_id=queue.id, job_type=job_type,
        payload=payload or {}, priority=priority, state="CREATED",
        scheduled_at=scheduled_at, available_at=scheduled_at or now,
        timezone=timezone_name,
        max_attempts=max_attempts or queue.default_max_attempts,
        retry_policy=retry_policy,
        timeout_seconds=timeout_seconds or queue.default_timeout_seconds,
        idempotency_key=idempotency_key, correlation_id=correlation_id or str(uuid.uuid4()),
        tags=tags or [], routing_key=routing_key or queue.routing_key,
        required_capabilities=required_capabilities or [],
        on_dependency_failure=on_dependency_failure,
        parent_job_id=parent_job_id, batch_id=batch_id, workflow_id=workflow_id,
        recurring_job_id=recurring_job_id, created_by=created_by,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:  # concurrent duplicate — return the winner
            return (await session.execute(
                select(Job).where(Job.project_id == project.id,
                                  Job.idempotency_key == idempotency_key))).scalar_one()
        raise
    await transition(session, job, state, reason="job accepted")
    await session.commit()
    await bus.emit("job.created", {"job_id": str(job.id), "state": job.state,
                                   "queue_id": str(queue.id)}, str(project.id))
    return job


async def create_batch(session: AsyncSession, project: Project, queue: Queue,
                       items: list[dict], created_by=None) -> tuple[uuid.UUID, list[Job]]:
    if len(items) > project.max_batch_size:
        raise bad_request("BATCH_TOO_LARGE",
                          f"batch exceeds max size {project.max_batch_size}")
    await enforce_quotas(session, project, {}, n_jobs=len(items))
    batch_id = uuid.uuid4()
    jobs = []
    for item in items:
        jobs.append(await create_job(
            session, project, queue, batch_id=batch_id, created_by=created_by,
            check_quota=False, **item))
    return batch_id, jobs


# --------------------------------------------------------------------------- #
# Workflows (DAG)
# --------------------------------------------------------------------------- #
def detect_cycle(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    """Kahn's algorithm; True if the graph has a cycle."""
    indeg = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for frm, to in edges:
        adj[frm].append(to)
        indeg[to] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    visited = 0
    while queue:
        n = queue.pop()
        visited += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    return visited != len(nodes)


async def create_workflow(session: AsyncSession, project: Project, *, name: str,
                          nodes: list[dict], created_by=None) -> Workflow:
    """``nodes``: [{key, queue_id, job_type, payload?, depends_on: [key], ...}]"""
    keys = [n["key"] for n in nodes]
    if len(set(keys)) != len(keys):
        raise bad_request("DUPLICATE_NODE_KEY", "workflow node keys must be unique")
    edges = []
    for n in nodes:
        for dep in n.get("depends_on", []):
            if dep not in set(keys):
                raise bad_request("UNKNOWN_DEPENDENCY", f"node {n['key']!r} depends on "
                                  f"unknown node {dep!r}")
            edges.append((dep, n["key"]))
    if detect_cycle(keys, edges):
        raise bad_request("WORKFLOW_CYCLE", "workflow graph contains a cycle")

    wf = Workflow(project_id=project.id, name=name, created_by=created_by,
                  correlation_id=str(uuid.uuid4()))
    session.add(wf)
    await session.flush()

    key_to_job: dict[str, Job] = {}
    for n in nodes:
        queue = (await session.execute(
            select(Queue).where(Queue.id == n["queue_id"],
                                Queue.project_id == project.id))).scalar_one_or_none()
        if queue is None:
            raise bad_request("UNKNOWN_QUEUE", f"queue for node {n['key']!r} not found")
        has_deps = bool(n.get("depends_on"))
        job = await create_job(
            session, project, queue, job_type=n["job_type"],
            payload=n.get("payload"), priority=n.get("priority", 0),
            max_attempts=n.get("max_attempts"),
            on_dependency_failure=n.get("on_dependency_failure", "fail"),
            workflow_id=wf.id, created_by=created_by,
            correlation_id=wf.correlation_id,
            initial_state="BLOCKED" if has_deps else "QUEUED")
        key_to_job[n["key"]] = job
    for n in nodes:
        for dep in n.get("depends_on", []):
            session.add(JobDependency(job_id=key_to_job[n["key"]].id,
                                      depends_on_job_id=key_to_job[dep].id))
    await session.commit()
    await bus.emit("workflow.created", {"workflow_id": str(wf.id), "name": name},
                   str(project.id))
    return wf


# --------------------------------------------------------------------------- #
# Recurring jobs
# --------------------------------------------------------------------------- #
async def create_recurring(session: AsyncSession, project: Project, queue: Queue, *,
                           name: str, job_type: str, cron_expression: str,
                           timezone_name: str = "UTC", payload: dict | None = None,
                           priority: int = 0, max_attempts: int = 3,
                           timeout_seconds: int | None = None) -> RecurringJob:
    validate_cron(cron_expression, timezone_name)
    if job_type not in SAFE_HANDLERS:
        raise bad_request("UNKNOWN_JOB_TYPE",
                          f"job_type must be one of {', '.join(SAFE_HANDLERS)}")
    rec = RecurringJob(
        project_id=project.id, queue_id=queue.id, name=name, job_type=job_type,
        payload=payload or {}, cron_expression=cron_expression, timezone=timezone_name,
        priority=priority, max_attempts=max_attempts, timeout_seconds=timeout_seconds,
        next_run_at=next_cron_occurrence(cron_expression, timezone_name, utcnow()))
    session.add(rec)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise conflict("DUPLICATE_RECURRING", f"recurring job {name!r} already exists")
    return rec
