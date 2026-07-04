"""Organizations, memberships and their projects."""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_org
from ..errors import bad_request, conflict, not_found
from ..models import Organization, OrganizationMember, Project, User
from ..schemas import MemberUpsert, ProjectCreate
from ..serializers import org_out, project_out, user_out

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.get("")
async def list_orgs(principal: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db)):
    if principal.api_key:
        return {"items": []}
    rows = (await db.execute(
        select(Organization, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == principal.user.id))).all()
    return {"items": [org_out(o, role) for o, role in rows]}


@router.get("/{org_id}/members")
async def list_members(org_id: uuid.UUID, principal: Principal = Depends(get_principal),
                       db: AsyncSession = Depends(get_db)):
    await require_org(db, principal, org_id, "viewer")
    rows = (await db.execute(
        select(User, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(OrganizationMember.organization_id == org_id))).all()
    return {"items": [{**user_out(u), "role": role} for u, role in rows]}


@router.put("/{org_id}/members")
async def upsert_member(org_id: uuid.UUID, body: MemberUpsert, request: Request,
                        principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)):
    org, _ = await require_org(db, principal, org_id, "org_admin")
    user = (await db.execute(select(User).where(
        User.email == body.email.lower()))).scalar_one_or_none()
    if user is None:
        raise not_found("user")
    member = (await db.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == org.id,
        OrganizationMember.user_id == user.id))).scalar_one_or_none()
    if member:
        member.role = body.role
    else:
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=body.role))
    await db.commit()
    await audit(db, request, principal, "member.upsert", "organization_member",
                user.id, org_id=org.id, changes={"role": body.role})
    return {"email": body.email, "role": body.role}


@router.get("/{org_id}/projects")
async def list_projects(org_id: uuid.UUID, principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)):
    org, role = await require_org(db, principal, org_id, "viewer")
    projects = (await db.execute(select(Project).where(
        Project.organization_id == org.id, Project.deleted_at.is_(None)))).scalars().all()
    return {"items": [project_out(p, role) for p in projects]}


@router.post("/{org_id}/projects", status_code=201)
async def create_project(org_id: uuid.UUID, body: ProjectCreate, request: Request,
                         principal: Principal = Depends(get_principal),
                         db: AsyncSession = Depends(get_db)):
    org, _ = await require_org(db, principal, org_id, "org_admin")
    dup = (await db.execute(select(Project).where(
        Project.organization_id == org.id, Project.slug == body.slug))).scalar_one_or_none()
    if dup:
        raise conflict("DUPLICATE_SLUG", f"project slug {body.slug!r} already exists")
    project = Project(organization_id=org.id, name=body.name, slug=body.slug,
                      description=body.description)
    db.add(project)
    await db.commit()
    await audit(db, request, principal, "project.create", "project", project.id,
                org_id=org.id, project_id=project.id)
    return project_out(project, "org_admin")
