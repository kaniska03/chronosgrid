"""ChronosGrid domain model.

Conventions
-----------
* UUID primary keys (portable ``Uuid`` type: native uuid on PostgreSQL,
  CHAR(32) on SQLite for tests).
* All timestamps are naive UTC (a deliberate cross-dialect choice; the API
  layer serialises them with a ``Z`` suffix). ``utcnow()`` is the only clock.
* JSON columns use JSONB on PostgreSQL.
* Soft deletion (``deleted_at``) only on projects and queues, where history
  (jobs, audit) must outlive the container object. Everything else deletes
  hard or is retained by design (audit/log tables).

Index rationale (the important ones)
------------------------------------
* ``ix_jobs_claim``            (queue_id, state, available_at, priority):
  drives the hot claim query — equality on queue+state, range on
  available_at, sort on priority.
* ``ix_jobs_state_avail``      (state, available_at): scheduler promotion
  scans (SCHEDULED->QUEUED, RETRY_SCHEDULED->QUEUED) and timeout sweeps.
* ``uq_jobs_idempotency``      partial unique (project_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL: at-least-once safety net for clients.
* ``uq_jobs_recurring_occurrence`` partial unique (recurring_job_id,
  scheduled_at): a cron tick can never materialise twice.
* ``ix_jobs_lease``            (state, lease_expires_at): the reaper's scan
  for expired leases touches only CLAIMED/RUNNING rows.
* ``ix_workers_heartbeat``     (status, last_heartbeat_at): dead-worker sweep.
* ``ix_job_logs_job``          (job_id, id): keyset/cursor pagination of logs.
* ``ix_jobs_project_created``  (project_id, created_at): job explorer default
  listing, newest first.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from .db import Base

JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Naive UTC now — the single clock used across the codebase."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# Identity & tenancy
# --------------------------------------------------------------------------- #
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


ORG_ROLES = ("org_admin", "project_admin", "developer", "viewer")


class OrganizationMember(TimestampMixin, Base):
    __tablename__ = "organization_members"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="developer")
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
        CheckConstraint(
            "role IN ('org_admin','project_admin','developer','viewer')",
            name="ck_org_member_role",
        ),
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Usage quotas (documented in docs/DATABASE.md)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    daily_job_quota: Mapped[int] = mapped_column(Integer, default=100_000, nullable=False)
    max_payload_bytes: Mapped[int] = mapped_column(Integer, default=64 * 1024, nullable=False)
    max_batch_size: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_project_slug"),
        CheckConstraint("max_concurrent_jobs > 0", name="ck_project_concurrency_positive"),
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="developer")
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        CheckConstraint(
            "role IN ('project_admin','developer','viewer')", name="ck_project_member_role"
        ),
    )


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)  # display only
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_api_keys_project", "project_id"),)


# --------------------------------------------------------------------------- #
# Queues
# --------------------------------------------------------------------------- #
class Queue(TimestampMixin, Base):
    __tablename__ = "queues"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    per_worker_concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)  # null = unlimited
    default_max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Retry policy stored as a validated JSON document, e.g.
    # {"strategy": "exponential", "base_delay": 5, "max_delay": 300, "jitter": true}
    default_retry_policy: Mapped[dict] = mapped_column(
        JSONVariant, default=lambda: {"strategy": "exponential", "base_delay": 5,
                                      "max_delay": 300, "jitter": True}, nullable=False
    )
    default_timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    dlq_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_worker_tags: Mapped[list | None] = mapped_column(JSONVariant)  # null = any worker
    routing_key: Mapped[str | None] = mapped_column(String(120))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_queue_name"),
        CheckConstraint("max_concurrent_jobs > 0", name="ck_queue_concurrency_positive"),
        CheckConstraint("default_max_attempts >= 1", name="ck_queue_attempts_min"),
    )


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
JOB_STATES = (
    "CREATED", "QUEUED", "SCHEDULED", "BLOCKED", "CLAIMED", "RUNNING",
    "RETRY_SCHEDULED", "COMPLETED", "FAILED", "CANCEL_REQUESTED", "CANCELLED",
    "TIMED_OUT", "DEAD_LETTERED", "SKIPPED",
)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queues.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE")
    )
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recurring_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_jobs.id", ondelete="SET NULL")
    )

    job_type: Mapped[str] = mapped_column(String(120), nullable=False)  # handler name
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="CREATED", nullable=False)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))

    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_policy: Mapped[dict | None] = mapped_column(JSONVariant)  # null -> queue default
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)  # null -> queue default

    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    routing_key: Mapped[str | None] = mapped_column(String(120))
    required_capabilities: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    # Workflow dependency failure policy: fail | skip | continue
    on_dependency_failure: Mapped[str] = mapped_column(String(10), default="fail", nullable=False)

    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime)

    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONVariant)
    error: Mapped[dict | None] = mapped_column(JSONVariant)

    # Lease (embedded on the job row so claim = one atomic row update; see
    # docs/DECISIONS.md for the trade-off vs. a separate job_leases table).
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_renewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    claimed_by_worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    executions: Mapped[list["JobExecution"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="noload"
    )

    __table_args__ = (
        Index("ix_jobs_claim", "queue_id", "state", "available_at", "priority"),
        Index("ix_jobs_state_avail", "state", "available_at"),
        Index("ix_jobs_lease", "state", "lease_expires_at"),
        Index("ix_jobs_project_created", "project_id", "created_at"),
        Index("ix_jobs_workflow", "workflow_id"),
        Index("ix_jobs_correlation", "correlation_id"),
        Index("ix_jobs_next_retry", "state", "next_retry_at"),
        Index(
            "uq_jobs_idempotency", "project_id", "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_jobs_recurring_occurrence", "recurring_job_id", "scheduled_at",
            unique=True,
            postgresql_where=text("recurring_job_id IS NOT NULL"),
            sqlite_where=text("recurring_job_id IS NOT NULL"),
        ),
        CheckConstraint("max_attempts >= 1", name="ck_job_attempts_min"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_job_progress_range"),
        CheckConstraint(
            "state IN ('CREATED','QUEUED','SCHEDULED','BLOCKED','CLAIMED','RUNNING',"
            "'RETRY_SCHEDULED','COMPLETED','FAILED','CANCEL_REQUESTED','CANCELLED',"
            "'TIMED_OUT','DEAD_LETTERED','SKIPPED')",
            name="ck_job_state",
        ),
    )


class JobExecution(TimestampMixin, Base):
    """One row per execution attempt — the durable retry history."""
    __tablename__ = "job_executions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(20), default="CLAIMED", nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[dict | None] = mapped_column(JSONVariant)
    error: Mapped[dict | None] = mapped_column(JSONVariant)
    error_category: Mapped[str | None] = mapped_column(String(30))  # retryable/non_retryable/timeout
    retry_delay_seconds: Mapped[float | None] = mapped_column(Float)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)

    job: Mapped[Job] = relationship(back_populates="executions")
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_execution_attempt"),
        Index("ix_executions_worker", "worker_id", "state"),
    )


class JobStateTransition(Base):
    __tablename__ = "job_state_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = (Index("ix_transitions_job", "job_id", "id"),)


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dependency"),
        Index("ix_dependency_upstream", "depends_on_job_id"),
        CheckConstraint("job_id != depends_on_job_id", name="ck_no_self_dependency"),
    )


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="RUNNING", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONVariant)
    correlation_id: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    __table_args__ = (Index("ix_workflows_project", "project_id", "created_at"),)


class RecurringJob(TimestampMixin, Base):
    __tablename__ = "recurring_jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queues.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    job_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_recurring_name"),
        Index("ix_recurring_next_run", "enabled", "next_run_at"),
    )


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #
WORKER_STATUSES = ("online", "draining", "offline", "unhealthy")


class Worker(TimestampMixin, Base):
    __tablename__ = "workers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(40), default="dev", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    tags: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="online", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (
        Index("ix_workers_heartbeat", "status", "last_heartbeat_at"),
        CheckConstraint(
            "status IN ('online','draining','offline','unhealthy')", name="ck_worker_status"
        ),
    )


class WorkerHeartbeat(Base):
    """Recent heartbeat history (pruned by the reaper; see retention docs)."""
    __tablename__ = "worker_heartbeats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (Index("ix_heartbeats_worker", "worker_id", "at"),)


# --------------------------------------------------------------------------- #
# Logs, DLQ, webhooks, audit, AI
# --------------------------------------------------------------------------- #
class JobLog(Base):
    __tablename__ = "job_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (Index("ix_job_logs_job", "job_id", "id"),)


class DeadLetterEntry(TimestampMixin, Base):
    __tablename__ = "dead_letter_entries"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queues.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(60), nullable=False)  # attempts_exhausted/...
    error: Mapped[dict | None] = mapped_column(JSONVariant)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    __table_args__ = (Index("ix_dlq_project", "project_id", "created_at"),)


WEBHOOK_EVENTS = (
    "job.completed", "job.failed", "job.timed_out", "job.cancelled",
    "job.dead_lettered", "workflow.completed",
)


class WebhookEndpoint(TimestampMixin, Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Signing secret is needed for HMAC so it cannot be hashed. It is never
    # returned by the API after creation and never logged.
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_webhooks_project", "project_id"),)


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_deliveries_pending", "status", "next_attempt_at"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_api_key_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    changes: Mapped[dict | None] = mapped_column(JSONVariant)
    __table_args__ = (Index("ix_audit_org", "organization_id", "at"),)


class FailureAnalysis(TimestampMixin, Base):
    """AI (or deterministic local) failure summary. Read-only advice: nothing
    in the scheduler consumes this table."""
    __tablename__ = "failure_analyses"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # 'ai' | 'local'
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    likely_causes: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    suggestions: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    log_line_ids: Mapped[list] = mapped_column(JSONVariant, default=list, nullable=False)
    __table_args__ = (Index("ix_analyses_job", "job_id"),)


class UsageCounter(Base):
    """Daily job-creation counter per project (daily quota enforcement)."""
    __tablename__ = "usage_counters"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (UTC)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "day", name="uq_usage_day"),)
