"""Registration, login, refresh, current user."""
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal
from ..errors import ApiError, conflict
from ..models import Organization, OrganizationMember, Project, User, utcnow
from ..schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from ..security import (
    create_access_token, create_refresh_token, decode_token, hash_password,
    verify_password,
)
from ..serializers import user_out

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, request: Request,
                   db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(User).where(
        User.email == body.email.lower()))).scalar_one_or_none()
    if existing:
        raise conflict("EMAIL_TAKEN", "an account with this email already exists")
    user = User(email=body.email.lower(), name=body.name,
                password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    org_name = body.organization_name or f"{body.name}'s Organization"
    slug = _slugify(org_name)
    n = 0
    while (await db.execute(select(Organization).where(
            Organization.slug == (slug if n == 0 else f"{slug}-{n}")))).scalar_one_or_none():
        n += 1
    org = Organization(name=org_name, slug=slug if n == 0 else f"{slug}-{n}")
    db.add(org)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="org_admin"))
    db.add(Project(organization_id=org.id, name="Default", slug="default"))
    await db.commit()
    await audit(db, request, Principal(user=user), "user.register", "user", user.id,
                org_id=org.id)
    return TokenResponse(access_token=create_access_token(user.id),
                         refresh_token=create_refresh_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(
        User.email == body.email.lower()))).scalar_one_or_none()
    # Constant-shape flow: verify against a dummy hash when the user is
    # missing to avoid timing-based account enumeration.
    ok = verify_password(body.password, user.password_hash if user else
                         "pbkdf2_sha256$600000$00$00")
    if user is None or not ok or not user.is_active:
        raise ApiError(401, "INVALID_CREDENTIALS", "email or password is incorrect")
    await audit(db, request, Principal(user=user), "user.login", "user", user.id)
    return TokenResponse(access_token=create_access_token(user.id),
                         refresh_token=create_refresh_token(user.id))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    import jwt as pyjwt
    import uuid as _uuid
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except pyjwt.PyJWTError:
        raise ApiError(401, "INVALID_TOKEN", "refresh token is invalid or expired")
    user = (await db.execute(select(User).where(
        User.id == _uuid.UUID(payload["sub"])))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise ApiError(401, "INVALID_TOKEN", "user not found or inactive")
    return TokenResponse(access_token=create_access_token(user.id),
                         refresh_token=create_refresh_token(user.id))


@router.get("/me")
async def me(principal: Principal = Depends(get_principal)):
    if principal.user is None:
        return {"type": "api_key", "prefix": principal.api_key.prefix,
                "project_id": str(principal.api_key.project_id)}
    return {"type": "user", **user_out(principal.user)}
