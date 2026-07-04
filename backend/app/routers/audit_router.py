"""Audit log listing (org admins and project admins)."""
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, get_principal, require_org, require_project
from ..models import AuditLog
from ..serializers import audit_out, page_meta

router = APIRouter(tags=["audit"])


@router.get("/orgs/{org_id}/audit")
async def org_audit(org_id: uuid.UUID, principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db),
                    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                    action: str | None = None):
    await require_org(db, principal, org_id, "org_admin")
    q = select(AuditLog).where(AuditLog.organization_id == org_id)
    if action:
        q = q.where(AuditLog.action == action)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(AuditLog.id.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [audit_out(a) for a in rows], "meta": page_meta(total, page, page_size)}


@router.get("/projects/{project_id}/audit")
async def project_audit(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db),
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    await require_project(db, principal, project_id, "project_admin")
    q = select(AuditLog).where(AuditLog.project_id == project_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(AuditLog.id.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [audit_out(a) for a in rows], "meta": page_meta(total, page, page_size)}
