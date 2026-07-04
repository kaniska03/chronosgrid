"""Authentication and authorization dependencies (JWT + API key + RBAC)."""
import uuid

import jwt as pyjwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .errors import ApiError, forbidden, not_found
from .models import (
    ApiKey, AuditLog, Organization, OrganizationMember, Project, ProjectMember,
    User, utcnow,
)
from .security import decode_token, hash_api_key

ROLE_RANK = {"viewer": 0, "developer": 1, "project_admin": 2, "org_admin": 3}


class Principal:
    """Either a logged-in user or a project API key."""

    def __init__(self, user: User | None = None, api_key: ApiKey | None = None):
        self.user = user
        self.api_key = api_key

    @property
    def id(self):
        return self.user.id if self.user else self.api_key.id

    @property
    def label(self) -> str:
        return self.user.email if self.user else f"api-key:{self.api_key.prefix}"


async def get_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    auth = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key")
    if api_key_header:
        key = (await db.execute(select(ApiKey).where(
            ApiKey.key_hash == hash_api_key(api_key_header)))).scalar_one_or_none()
        if key is None or key.revoked_at is not None or (
                key.expires_at and key.expires_at < utcnow()):
            raise ApiError(401, "INVALID_API_KEY", "API key is invalid, expired or revoked")
        key.last_used_at = utcnow()
        await db.commit()
        return Principal(api_key=key)
    if auth.startswith("Bearer "):
        try:
            payload = decode_token(auth.removeprefix("Bearer "))
        except pyjwt.ExpiredSignatureError:
            raise ApiError(401, "TOKEN_EXPIRED", "access token has expired")
        except pyjwt.InvalidTokenError:
            raise ApiError(401, "INVALID_TOKEN", "access token is invalid")
        user = (await db.execute(select(User).where(
            User.id == uuid.UUID(payload["sub"])))).scalar_one_or_none()
        if user is None or not user.is_active:
            raise ApiError(401, "INVALID_TOKEN", "user not found or inactive")
        return Principal(user=user)
    raise ApiError(401, "UNAUTHENTICATED", "provide a Bearer token or X-API-Key header")


async def org_role(db: AsyncSession, user: User, org_id) -> str | None:
    m = (await db.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user.id))).scalar_one_or_none()
    return m.role if m else None


async def project_role(db: AsyncSession, principal: Principal, project: Project) -> str | None:
    """Effective role on a project. API keys act as 'developer' on their own
    project only (strict project isolation)."""
    if principal.api_key:
        return "developer" if principal.api_key.project_id == project.id else None
    role = await org_role(db, principal.user, project.organization_id)
    pm = (await db.execute(select(ProjectMember).where(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == principal.user.id))).scalar_one_or_none()
    ranks = [ROLE_RANK[r] for r in (role, pm.role if pm else None) if r]
    if not ranks:
        return None
    return {v: k for k, v in ROLE_RANK.items()}[max(ranks)]


async def require_project(db: AsyncSession, principal: Principal, project_id,
                          min_role: str = "viewer") -> tuple[Project, str]:
    project = (await db.execute(select(Project).where(
        Project.id == project_id, Project.deleted_at.is_(None)))).scalar_one_or_none()
    if project is None:
        raise not_found("project")
    role = await project_role(db, principal, project)
    if role is None:
        raise not_found("project")     # hide existence across tenants
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise forbidden(f"requires {min_role} role (you are {role})")
    return project, role


async def require_org(db: AsyncSession, principal: Principal, org_id,
                      min_role: str = "viewer") -> tuple[Organization, str]:
    if principal.api_key:
        raise forbidden("organization endpoints require user authentication")
    org = (await db.execute(select(Organization).where(
        Organization.id == org_id))).scalar_one_or_none()
    if org is None:
        raise not_found("organization")
    role = await org_role(db, principal.user, org_id)
    if role is None:
        raise not_found("organization")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise forbidden(f"requires {min_role} role (you are {role})")
    return org, role


async def audit(db: AsyncSession, request: Request | None, principal: Principal | None,
                action: str, resource_type: str, resource_id=None,
                org_id=None, project_id=None, changes: dict | None = None) -> None:
    session_add = AuditLog(
        organization_id=org_id, project_id=project_id,
        actor_user_id=principal.user.id if principal and principal.user else None,
        actor_api_key_id=principal.api_key.id if principal and principal.api_key else None,
        action=action, resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        ip_address=request.client.host if request and request.client else None,
        changes=changes)
    db.add(session_add)
    await db.commit()
