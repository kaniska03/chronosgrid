"""Response serialization helpers (dict-based; masking applied centrally)."""
from .masking import mask_sensitive


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def user_out(u):
    return {"id": str(u.id), "email": u.email, "name": u.name,
            "is_active": u.is_active, "created_at": _iso(u.created_at)}


def org_out(o, role=None):
    return {"id": str(o.id), "name": o.name, "slug": o.slug,
            "created_at": _iso(o.created_at), "role": role}


def project_out(p, role=None):
    return {"id": str(p.id), "organization_id": str(p.organization_id),
            "name": p.name, "slug": p.slug, "description": p.description,
            "max_concurrent_jobs": p.max_concurrent_jobs,
            "daily_job_quota": p.daily_job_quota,
            "max_payload_bytes": p.max_payload_bytes,
            "max_batch_size": p.max_batch_size,
            "created_at": _iso(p.created_at), "role": role}


def queue_out(q, stats=None):
    return {"id": str(q.id), "project_id": str(q.project_id), "name": q.name,
            "description": q.description, "priority": q.priority,
            "max_concurrent_jobs": q.max_concurrent_jobs,
            "per_worker_concurrency": q.per_worker_concurrency,
            "paused": q.paused, "rate_limit_per_minute": q.rate_limit_per_minute,
            "default_max_attempts": q.default_max_attempts,
            "default_retry_policy": q.default_retry_policy,
            "default_timeout_seconds": q.default_timeout_seconds,
            "retention_days": q.retention_days, "dlq_enabled": q.dlq_enabled,
            "allowed_worker_tags": q.allowed_worker_tags,
            "routing_key": q.routing_key, "created_at": _iso(q.created_at),
            "stats": stats}


def job_out(j, *, detail=False):
    base = {"id": str(j.id), "project_id": str(j.project_id),
            "queue_id": str(j.queue_id), "job_type": j.job_type,
            "state": j.state, "priority": j.priority, "progress": j.progress,
            "attempt_count": j.attempt_count, "max_attempts": j.max_attempts,
            "tags": j.tags, "correlation_id": j.correlation_id,
            "workflow_id": str(j.workflow_id) if j.workflow_id else None,
            "batch_id": str(j.batch_id) if j.batch_id else None,
            "parent_job_id": str(j.parent_job_id) if j.parent_job_id else None,
            "recurring_job_id": str(j.recurring_job_id) if j.recurring_job_id else None,
            "cancel_requested": j.cancel_requested,
            "scheduled_at": _iso(j.scheduled_at), "available_at": _iso(j.available_at),
            "claimed_at": _iso(j.claimed_at), "started_at": _iso(j.started_at),
            "finished_at": _iso(j.finished_at), "created_at": _iso(j.created_at),
            "worker_id": str(j.claimed_by_worker_id) if j.claimed_by_worker_id else None,
            "next_retry_at": _iso(j.next_retry_at)}
    if detail:
        base.update({
            "payload": mask_sensitive(j.payload),          # never raw
            "result": mask_sensitive(j.result) if j.result else None,
            "error": j.error, "retry_policy": j.retry_policy,
            "timeout_seconds": j.timeout_seconds,
            "idempotency_key": j.idempotency_key,
            "routing_key": j.routing_key,
            "required_capabilities": j.required_capabilities,
            "on_dependency_failure": j.on_dependency_failure,
            "cancel_reason": j.cancel_reason,
            "lease_expires_at": _iso(j.lease_expires_at),
            "timezone": j.timezone})
    return base


def execution_out(e):
    return {"id": str(e.id), "attempt_number": e.attempt_number,
            "worker_id": str(e.worker_id) if e.worker_id else None,
            "state": e.state, "claimed_at": _iso(e.claimed_at),
            "started_at": _iso(e.started_at), "finished_at": _iso(e.finished_at),
            "error": e.error, "error_category": e.error_category,
            "retry_delay_seconds": e.retry_delay_seconds,
            "next_retry_at": _iso(e.next_retry_at),
            "result": mask_sensitive(e.result) if e.result else None}


def transition_out(t):
    return {"id": t.id, "from_state": t.from_state, "to_state": t.to_state,
            "at": _iso(t.at), "worker_id": str(t.worker_id) if t.worker_id else None,
            "attempt_number": t.attempt_number, "reason": t.reason}


def worker_out(w):
    return {"id": str(w.id), "name": w.name, "host": w.host, "pid": w.pid,
            "version": w.version, "capacity": w.capacity, "tags": w.tags,
            "capabilities": w.capabilities, "status": w.status,
            "started_at": _iso(w.started_at),
            "last_heartbeat_at": _iso(w.last_heartbeat_at),
            "active_jobs": w.active_jobs, "completed_jobs": w.completed_jobs,
            "failed_jobs": w.failed_jobs}


def dlq_out(d):
    return {"id": str(d.id), "job_id": str(d.job_id), "project_id": str(d.project_id),
            "queue_id": str(d.queue_id), "reason": d.reason, "error": d.error,
            "attempts": d.attempts, "note": d.note,
            "resolved_at": _iso(d.resolved_at), "created_at": _iso(d.created_at)}


def webhook_out(w):
    # NB: never includes the signing secret.
    return {"id": str(w.id), "project_id": str(w.project_id), "url": w.url,
            "events": w.events, "active": w.active,
            "failure_count": w.failure_count, "disabled_at": _iso(w.disabled_at),
            "created_at": _iso(w.created_at)}


def delivery_out(d):
    return {"id": str(d.id), "endpoint_id": str(d.endpoint_id),
            "event_type": d.event_type, "status": d.status,
            "attempt_count": d.attempt_count, "response_status": d.response_status,
            "next_attempt_at": _iso(d.next_attempt_at),
            "delivered_at": _iso(d.delivered_at), "last_error": d.last_error,
            "created_at": _iso(d.created_at)}


def recurring_out(r):
    return {"id": str(r.id), "project_id": str(r.project_id),
            "queue_id": str(r.queue_id), "name": r.name, "job_type": r.job_type,
            "cron_expression": r.cron_expression, "timezone": r.timezone,
            "enabled": r.enabled, "priority": r.priority,
            "next_run_at": _iso(r.next_run_at), "last_run_at": _iso(r.last_run_at),
            "created_at": _iso(r.created_at)}


def workflow_out(w):
    return {"id": str(w.id), "project_id": str(w.project_id), "name": w.name,
            "state": w.state, "progress": w.progress, "result": w.result,
            "correlation_id": w.correlation_id, "created_at": _iso(w.created_at)}


def apikey_out(k, full_key=None):
    out = {"id": str(k.id), "project_id": str(k.project_id), "name": k.name,
           "prefix": k.prefix, "last_used_at": _iso(k.last_used_at),
           "expires_at": _iso(k.expires_at), "revoked_at": _iso(k.revoked_at),
           "created_at": _iso(k.created_at)}
    if full_key:               # only on creation, never again
        out["key"] = full_key
    return out


def audit_out(a):
    return {"id": a.id, "organization_id": str(a.organization_id) if a.organization_id else None,
            "project_id": str(a.project_id) if a.project_id else None,
            "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
            "actor_api_key_id": str(a.actor_api_key_id) if a.actor_api_key_id else None,
            "action": a.action, "resource_type": a.resource_type,
            "resource_id": a.resource_id, "ip_address": a.ip_address,
            "at": _iso(a.at), "changes": a.changes}


def log_out(entry):
    return {"id": entry.id, "job_id": str(entry.job_id), "at": _iso(entry.at),
            "level": entry.level, "message": entry.message,
            "worker_id": str(entry.worker_id) if entry.worker_id else None}


def page_meta(total: int, page: int, page_size: int):
    return {"total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size))}
