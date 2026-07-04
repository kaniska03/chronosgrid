"""AI failure assistant endpoints (advisory only — never mutates job state)."""
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_assistant import analyze_job
from ..db import get_db
from ..deps import Principal, get_principal, require_project
from ..errors import conflict, not_found
from ..models import FailureAnalysis, Job
from ..serializers import _iso

router = APIRouter(prefix="/projects/{project_id}/jobs/{job_id}/analysis", tags=["ai"])


def _analysis_out(a: FailureAnalysis) -> dict:
    return {"id": str(a.id), "job_id": str(a.job_id), "source": a.source,
            "summary": a.summary, "likely_causes": a.likely_causes,
            "suggestions": a.suggestions, "log_line_ids": a.log_line_ids,
            "created_at": _iso(a.created_at)}


@router.get("")
async def get_analysis(project_id: uuid.UUID, job_id: uuid.UUID,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    a = (await db.execute(select(FailureAnalysis).where(
        FailureAnalysis.job_id == job_id)
        .order_by(FailureAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
    return {"analysis": _analysis_out(a) if a else None}


@router.post("", status_code=201)
async def create_analysis(project_id: uuid.UUID, job_id: uuid.UUID,
                          principal: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    job = (await db.execute(select(Job).where(
        Job.id == job_id, Job.project_id == project.id))).scalar_one_or_none()
    if job is None:
        raise not_found("job")
    if job.state not in ("FAILED", "DEAD_LETTERED", "TIMED_OUT", "RETRY_SCHEDULED"):
        raise conflict("NOT_FAILED", "analysis is only available for failed jobs")
    return _analysis_out(await analyze_job(db, job))
