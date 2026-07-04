"""Workflow (DAG) endpoints."""
import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import not_found
from ..models import Job, JobDependency, Workflow
from ..schemas import WorkflowCreate
from ..serializers import job_out, page_meta, workflow_out
from ..services import jobs as job_service

router = APIRouter(prefix="/projects/{project_id}/workflows", tags=["workflows"])


@router.get("")
async def list_workflows(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                         db: AsyncSession = Depends(get_db),
                         page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    q = select(Workflow).where(Workflow.project_id == project.id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Workflow.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [workflow_out(w) for w in rows],
            "meta": page_meta(total, page, page_size)}


@router.post("", status_code=201)
async def create_workflow(project_id: uuid.UUID, body: WorkflowCreate, request: Request,
                          principal: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    wf = await job_service.create_workflow(
        db, project, name=body.name,
        nodes=[n.model_dump() for n in body.nodes],
        created_by=principal.user.id if principal.user else None)
    await audit(db, request, principal, "workflow.create", "workflow", wf.id,
                org_id=project.organization_id, project_id=project.id)
    return workflow_out(wf)


@router.get("/{workflow_id}")
async def get_workflow(project_id: uuid.UUID, workflow_id: uuid.UUID,
                       principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    """Workflow + full DAG (nodes and edges) for the visualizer."""
    project, _ = await require_project(db, principal, project_id, "viewer")
    wf = (await db.execute(select(Workflow).where(
        Workflow.id == workflow_id, Workflow.project_id == project.id))).scalar_one_or_none()
    if wf is None:
        raise not_found("workflow")
    jobs = (await db.execute(select(Job).where(
        Job.workflow_id == wf.id).order_by(Job.created_at))).scalars().all()
    job_ids = [j.id for j in jobs]
    edges = (await db.execute(select(JobDependency).where(
        JobDependency.job_id.in_(job_ids)))).scalars().all() if job_ids else []
    return {**workflow_out(wf),
            "nodes": [job_out(j) for j in jobs],
            "edges": [{"from": str(e.depends_on_job_id), "to": str(e.job_id)}
                      for e in edges]}
