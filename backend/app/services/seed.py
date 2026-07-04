"""Demo account + optional demo data.

The demo account (demo@chronosgrid.dev / Demo@1234) is created on startup and
uses the exact same authentication flow as every other user — no bypass.
Dashboard values are always computed from live data; the seed only creates
real rows.
"""
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import (
    DeadLetterEntry, Job, JobExecution, JobStateTransition, Organization,
    OrganizationMember, Project, Queue, RecurringJob, User, Worker, utcnow,
)
from ..security import hash_password
from ..state_machine import transition
from .jobs import create_job, create_recurring, create_workflow

log = logging.getLogger("chronosgrid.seed")


async def ensure_demo_account(session: AsyncSession) -> tuple[User, Organization, Project]:
    s = get_settings()
    user = (await session.execute(select(User).where(
        User.email == s.demo_email))).scalar_one_or_none()
    if user is None:
        user = User(email=s.demo_email, name="Demo User",
                    password_hash=hash_password(s.demo_password))
        session.add(user)
        await session.flush()
    org = (await session.execute(select(Organization).where(
        Organization.slug == "demo-org"))).scalar_one_or_none()
    if org is None:
        org = Organization(name="Demo Organization", slug="demo-org")
        session.add(org)
        await session.flush()
    member = (await session.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == org.id,
        OrganizationMember.user_id == user.id))).scalar_one_or_none()
    if member is None:
        session.add(OrganizationMember(organization_id=org.id, user_id=user.id,
                                       role="org_admin"))
    project = (await session.execute(select(Project).where(
        Project.organization_id == org.id, Project.slug == "payments"))).scalar_one_or_none()
    if project is None:
        project = Project(organization_id=org.id, name="Payments", slug="payments",
                          description="Demo project: payment processing pipelines")
        session.add(project)
        session.add(Project(organization_id=org.id, name="Analytics", slug="analytics",
                            description="Demo project: reporting and analytics"))
    await session.commit()
    return user, org, project


async def seed_demo_data(session: AsyncSession) -> None:
    """Idempotent demo dataset: queues, workers, a spread of job outcomes,
    a recurring job, a workflow and DLQ entries."""
    user, org, project = await ensure_demo_account(session)
    existing = (await session.execute(select(Queue).where(
        Queue.project_id == project.id))).scalars().first()
    if existing:
        return  # already seeded

    q_default = Queue(project_id=project.id, name="default",
                      description="General purpose queue", priority=0)
    q_critical = Queue(project_id=project.id, name="critical",
                       description="High priority, low latency", priority=10,
                       max_concurrent_jobs=20,
                       default_retry_policy={"strategy": "fixed", "base_delay": 2,
                                             "max_delay": 60, "jitter": False})
    q_reports = Queue(project_id=project.id, name="reports",
                      description="Slow report generation",
                      rate_limit_per_minute=120, default_timeout_seconds=900)
    session.add_all([q_default, q_critical, q_reports])
    analytics = (await session.execute(select(Project).where(
        Project.organization_id == org.id, Project.slug == "analytics"))).scalar_one()
    q_analytics = Queue(project_id=analytics.id, name="default",
                        description="Analytics default queue")
    session.add(q_analytics)
    await session.flush()

    # A retired demo worker so the worker monitor has history even before
    # real workers connect.
    session.add(Worker(name="seed-worker-1", host="seed", pid=0, version="1.0.0",
                       capacity=5, tags=["general"],
                       capabilities=["sleep", "math", "report"], status="offline",
                       last_heartbeat_at=utcnow() - timedelta(hours=1),
                       completed_jobs=42, failed_jobs=3))
    await session.commit()

    now = utcnow()

    async def finished_job(queue, job_type, payload, state, error=None, minutes_ago=30):
        job = await create_job(session, project, queue, job_type=job_type,
                               payload=payload, check_quota=False)
        job.attempt_count = 1
        started = now - timedelta(minutes=minutes_ago)
        session.add(JobExecution(job_id=job.id, attempt_number=1, state=state,
                                 claimed_at=started, started_at=started,
                                 finished_at=started + timedelta(seconds=3),
                                 error=error))
        for to in (("CLAIMED", "RUNNING", state)):
            pass
        await transition(session, job, "CLAIMED", reason="seed")
        await transition(session, job, "RUNNING", reason="seed")
        if state == "DEAD_LETTERED":
            await transition(session, job, "FAILED", reason="seed: attempts exhausted")
            await transition(session, job, "DEAD_LETTERED", reason="seed")
            session.add(DeadLetterEntry(job_id=job.id, project_id=project.id,
                                        queue_id=queue.id, reason="attempts_exhausted",
                                        error=error, attempts=3))
        else:
            await transition(session, job, state, reason="seed")
        job.started_at = started
        job.finished_at = started + timedelta(seconds=3)
        job.error = error
        if state == "COMPLETED":
            job.result = {"ok": True}
            job.progress = 100.0
        await session.commit()
        return job

    for i in range(8):
        await finished_job(q_default, "math",
                           {"operation": "sum", "numbers": [i, i + 1]},
                           "COMPLETED", minutes_ago=60 - i * 5)
    for i in range(2):
        await finished_job(q_critical, "always_fail", {"message": "demo failure"},
                           "DEAD_LETTERED",
                           error={"type": "RuntimeError", "message": "demo failure"},
                           minutes_ago=45 - i * 10)

    # Retrying, scheduled and queued jobs
    retry_job = await create_job(session, project, q_default, job_type="flaky",
                                 payload={"succeed_on_attempt": 3}, check_quota=False)
    await create_job(session, project, q_reports, job_type="report",
                     payload={"rows": 5000}, scheduled_at=now + timedelta(hours=2),
                     check_quota=False)
    await create_job(session, project, q_default, job_type="sleep",
                     payload={"seconds": 2}, check_quota=False)
    await create_job(session, analytics, q_analytics, job_type="text_transform",
                     payload={"text": "hello chronosgrid", "transform": "title"},
                     check_quota=False)

    await create_recurring(session, project, q_reports, name="hourly-usage-report",
                           job_type="report", cron_expression="0 * * * *",
                           timezone_name="UTC", payload={"rows": 1000})

    await create_workflow(session, project, name="nightly-etl", nodes=[
        {"key": "extract", "queue_id": q_default.id, "job_type": "sleep",
         "payload": {"seconds": 1}},
        {"key": "transform-a", "queue_id": q_default.id, "job_type": "math",
         "payload": {"operation": "sum", "numbers": [1, 2, 3]},
         "depends_on": ["extract"]},
        {"key": "transform-b", "queue_id": q_default.id, "job_type": "text_transform",
         "payload": {"text": "etl", "transform": "upper"}, "depends_on": ["extract"]},
        {"key": "load", "queue_id": q_default.id, "job_type": "report",
         "payload": {"rows": 10}, "depends_on": ["transform-a", "transform-b"]},
    ])
    log.info("demo data seeded for project %s", project.id)
