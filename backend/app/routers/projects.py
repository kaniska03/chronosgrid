"""Project detail, API keys, quota settings."""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import not_found
from ..models import ApiKey, utcnow
from ..schemas import ApiKeyCreate
from ..security import generate_api_key
from ..serializers import apikey_out, project_out

router = APIRouter(prefix="/projects", tags=["projects"])


class QuotaUpdate(BaseModel):
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=100_000)
    daily_job_quota: int | None = Field(default=None, ge=1)
    max_payload_bytes: int | None = Field(default=None, ge=1024, le=10 * 1024 * 1024)
    max_batch_size: int | None = Field(default=None, ge=1, le=10_000)


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                      db: AsyncSession = Depends(get_db)):
    project, role = await require_project(db, principal, project_id, "viewer")
    return project_out(project, role)


@router.patch("/{project_id}")
async def update_quotas(project_id: uuid.UUID, body: QuotaUpdate, request: Request,
                        principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)):
    project, role = await require_project(db, principal, project_id, "project_admin")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(project, k, v)
    await db.commit()
    await audit(db, request, principal, "project.update_quotas", "project", project.id,
                org_id=project.organization_id, project_id=project.id, changes=changes)
    return project_out(project, role)


@router.get("/{project_id}/api-keys")
async def list_keys(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    keys = (await db.execute(select(ApiKey).where(
        ApiKey.project_id == project.id).order_by(ApiKey.created_at.desc()))).scalars().all()
    return {"items": [apikey_out(k) for k in keys]}


@router.post("/{project_id}/api-keys", status_code=201)
async def create_key(project_id: uuid.UUID, body: ApiKeyCreate, request: Request,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    full, prefix, digest = generate_api_key()
    key = ApiKey(project_id=project.id, name=body.name, prefix=prefix, key_hash=digest,
                 created_by=principal.user.id if principal.user else None,
                 expires_at=(utcnow() + timedelta(days=body.expires_in_days))
                 if body.expires_in_days else None)
    db.add(key)
    await db.commit()
    await audit(db, request, principal, "api_key.create", "api_key", key.id,
                org_id=project.organization_id, project_id=project.id)
    return apikey_out(key, full_key=full)  # full key shown exactly once


@router.delete("/{project_id}/api-keys/{key_id}")
async def revoke_key(project_id: uuid.UUID, key_id: uuid.UUID, request: Request,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    key = (await db.execute(select(ApiKey).where(
        ApiKey.id == key_id, ApiKey.project_id == project.id))).scalar_one_or_none()
    if key is None:
        raise not_found("api key")
    key.revoked_at = utcnow()
    await db.commit()
    await audit(db, request, principal, "api_key.revoke", "api_key", key.id,
                org_id=project.organization_id, project_id=project.id)
    return apikey_out(key)
