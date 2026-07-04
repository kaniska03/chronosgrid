"""Dashboard metrics + Prometheus exposition + health/readiness."""
import uuid
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, get_principal, require_project
from ..services import metrics as metrics_service

router = APIRouter(tags=["metrics"])


@router.get("/metrics/overview")
async def system_overview(principal: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)):
    return await metrics_service.overview(db)


@router.get("/projects/{project_id}/metrics/overview")
async def project_overview(project_id: uuid.UUID,
                           principal: Principal = Depends(get_principal),
                           db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    return await metrics_service.overview(db, project.id)


@router.get("/projects/{project_id}/metrics/throughput")
async def project_throughput(project_id: uuid.UUID, minutes: int = 30,
                             principal: Principal = Depends(get_principal),
                             db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    return {"items": await metrics_service.throughput_series(
        db, project.id, min(minutes, 24 * 60))}
