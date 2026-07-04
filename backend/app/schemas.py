"""Pydantic request/response schemas (subset shown in OpenAPI with examples)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    organization_name: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
            raise ValueError("password must contain letters and digits")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    model_config = {"json_schema_extra": {"examples": [
        {"email": "demo@chronosgrid.dev", "password": "Demo@1234"}]}}


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class QueueCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_\-\.]+$")
    description: str | None = None
    priority: int = 0
    max_concurrent_jobs: int = Field(default=10, ge=1, le=10_000)
    per_worker_concurrency: int = Field(default=5, ge=1, le=1000)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    default_max_attempts: int = Field(default=3, ge=1, le=50)
    default_retry_policy: dict | None = None
    default_timeout_seconds: int = Field(default=300, ge=1, le=86400)
    retention_days: int = Field(default=30, ge=1, le=365)
    dlq_enabled: bool = True
    allowed_worker_tags: list[str] | None = None
    routing_key: str | None = None


class QueueUpdate(BaseModel):
    description: str | None = None
    priority: int | None = None
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=10_000)
    per_worker_concurrency: int | None = Field(default=None, ge=1, le=1000)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    default_max_attempts: int | None = Field(default=None, ge=1, le=50)
    default_retry_policy: dict | None = None
    default_timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    retention_days: int | None = Field(default=None, ge=1, le=365)
    dlq_enabled: bool | None = None
    allowed_worker_tags: list[str] | None = None
    routing_key: str | None = None


class JobCreate(BaseModel):
    queue_id: uuid.UUID
    job_type: str
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-10, le=10)
    scheduled_at: datetime | None = None
    delay_seconds: int | None = Field(default=None, ge=0, le=30 * 86400)
    max_attempts: int | None = Field(default=None, ge=1, le=50)
    retry_policy: dict | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    idempotency_key: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    routing_key: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    model_config = {"json_schema_extra": {"examples": [{
        "queue_id": "00000000-0000-0000-0000-000000000000",
        "job_type": "math",
        "payload": {"operation": "sum", "numbers": [1, 2, 3]},
        "priority": 5, "idempotency_key": "order-1234-total"}]}}


class BatchCreate(BaseModel):
    queue_id: uuid.UUID
    jobs: list[dict] = Field(min_length=1)


class WorkflowNode(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    queue_id: uuid.UUID
    job_type: str
    payload: dict = Field(default_factory=dict)
    priority: int = 0
    max_attempts: int | None = None
    depends_on: list[str] = Field(default_factory=list)
    on_dependency_failure: str = "fail"


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=100)


class RecurringCreate(BaseModel):
    queue_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    job_type: str
    cron_expression: str = Field(examples=["*/5 * * * *"])
    timezone: str = "UTC"
    payload: dict = Field(default_factory=dict)
    priority: int = 0
    max_attempts: int = Field(default=3, ge=1, le=50)
    timeout_seconds: int | None = None


class WebhookCreate(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=500)
    events: list[str] = Field(min_length=1)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9\-]+$")
    description: str | None = None


class MemberUpsert(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(org_admin|project_admin|developer|viewer)$")


class DlqNote(BaseModel):
    note: str = Field(max_length=2000)
