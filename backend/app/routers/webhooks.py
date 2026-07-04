"""Webhook endpoint management + delivery history + replay."""
import uuid
import secrets

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import Principal, audit, get_principal, require_project
from ..errors import bad_request, not_found
from ..models import WEBHOOK_EVENTS, WebhookDelivery, WebhookEndpoint, utcnow
from ..schemas import WebhookCreate
from ..serializers import delivery_out, page_meta, webhook_out

router = APIRouter(prefix="/projects/{project_id}/webhooks", tags=["webhooks"])


@router.get("")
async def list_webhooks(project_id: uuid.UUID, principal: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    rows = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.project_id == project.id))).scalars().all()
    return {"items": [webhook_out(w) for w in rows], "available_events": list(WEBHOOK_EVENTS)}


@router.post("", status_code=201)
async def create_webhook(project_id: uuid.UUID, body: WebhookCreate, request: Request,
                         principal: Principal = Depends(get_principal),
                         db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    invalid = set(body.events) - set(WEBHOOK_EVENTS)
    if invalid:
        raise bad_request("INVALID_EVENTS", f"unknown events: {sorted(invalid)}")
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    ep = WebhookEndpoint(project_id=project.id, url=body.url, secret=secret,
                         events=body.events)
    db.add(ep)
    await db.commit()
    await audit(db, request, principal, "webhook.create", "webhook", ep.id,
                org_id=project.organization_id, project_id=project.id)
    # The signing secret is returned exactly once, at creation.
    return {**webhook_out(ep), "secret": secret}


@router.delete("/{webhook_id}")
async def delete_webhook(project_id: uuid.UUID, webhook_id: uuid.UUID, request: Request,
                         principal: Principal = Depends(get_principal),
                         db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "project_admin")
    ep = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.project_id == project.id))).scalar_one_or_none()
    if ep is None:
        raise not_found("webhook")
    await db.delete(ep)
    await db.commit()
    await audit(db, request, principal, "webhook.delete", "webhook", webhook_id,
                org_id=project.organization_id, project_id=project.id)
    return {"deleted": True}


@router.get("/{webhook_id}/deliveries")
async def deliveries(project_id: uuid.UUID, webhook_id: uuid.UUID,
                     principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db),
                     page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    project, _ = await require_project(db, principal, project_id, "viewer")
    ep = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.project_id == project.id))).scalar_one_or_none()
    if ep is None:
        raise not_found("webhook")
    q = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(WebhookDelivery.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [delivery_out(d) for d in rows], "meta": page_meta(total, page, page_size)}


@router.post("/{webhook_id}/deliveries/{delivery_id}/replay")
async def replay_delivery(project_id: uuid.UUID, webhook_id: uuid.UUID, delivery_id: uuid.UUID,
                          request: Request,
                          principal: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)):
    project, _ = await require_project(db, principal, project_id, "developer")
    d = (await db.execute(
        select(WebhookDelivery).join(WebhookEndpoint,
                                     WebhookEndpoint.id == WebhookDelivery.endpoint_id)
        .where(WebhookDelivery.id == delivery_id,
               WebhookEndpoint.id == webhook_id,
               WebhookEndpoint.project_id == project.id))).scalar_one_or_none()
    if d is None:
        raise not_found("delivery")
    clone = WebhookDelivery(endpoint_id=d.endpoint_id, event_type=d.event_type,
                            payload=d.payload, status="pending", next_attempt_at=utcnow())
    db.add(clone)
    await db.commit()
    await audit(db, request, principal, "webhook.replay", "webhook_delivery", clone.id,
                org_id=project.organization_id, project_id=project.id)
    return delivery_out(clone)
