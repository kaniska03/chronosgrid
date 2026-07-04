"""Workflows/DAG, recurring jobs, webhooks, WS events, pagination
(spec tests 11, 12, 15, 16, 17, 19)."""
import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from tests.conftest import U, create_job_api, make_ctx, register_test_worker


def _wf_nodes(ctx, spec):
    """spec: {'a': [], 'b': ['a'], ...}"""
    return [{"key": k, "queue_id": ctx.queue["id"], "job_type": "math",
             "payload": {"operation": "sum", "numbers": [1]}, "depends_on": deps}
            for k, deps in spec.items()]


async def _finish(job_id, worker, *, succeed=True):
    from app.db import session_factory
    from app.services import lifecycle
    from app.services.claiming import claim_next_job
    async with session_factory()() as s:
        job = await claim_next_job(s, worker)
    assert job is not None and str(job.id) == str(job_id)
    async with session_factory()() as s:
        await lifecycle.start_job(s, job.id, job.lease_token, worker.id)
        if succeed:
            await lifecycle.complete_job(s, job.id, job.lease_token, worker.id, {"ok": 1})
        else:
            await lifecycle.fail_job(s, job.id, job.lease_token, worker.id,
                                     {"type": "E", "message": "boom"},
                                     error_category="non_retryable")


async def test_cyclic_workflow_rejected(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/workflows",
                          json={"name": "cycle",
                                "nodes": _wf_nodes(ctx, {"a": ["c"], "b": ["a"], "c": ["b"]})},
                          headers=ctx.headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "WORKFLOW_CYCLE"


async def test_dependencies_release_in_order(app_ctx):
    """Diamond DAG: a -> (b, c) -> d. Fan-out and fan-in."""
    _, client = app_ctx
    ctx = await make_ctx(client, queue_config={"max_concurrent_jobs": 100})
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/workflows",
                          json={"name": "diamond",
                                "nodes": _wf_nodes(ctx, {"a": [], "b": ["a"],
                                                         "c": ["a"], "d": ["b", "c"]})},
                          headers=ctx.headers)
    assert r.status_code == 201
    wf = r.json()
    detail = (await client.get(
        f"/api/v1/projects/{ctx.project['id']}/workflows/{wf['id']}",
        headers=ctx.headers)).json()
    by_state = {}
    for n in detail["nodes"]:
        by_state.setdefault(n["state"], []).append(n["id"])
    assert len(by_state.get("QUEUED", [])) == 1     # only root a
    assert len(by_state.get("BLOCKED", [])) == 3

    w = await register_test_worker(capacity=100)
    a = by_state["QUEUED"][0]
    await _finish(a, w)

    from app.db import session_factory
    from app.models import Job
    async with session_factory()() as s:
        states = dict((await s.execute(select(Job.id, Job.state).where(
            Job.workflow_id == U(wf["id"])))).all())
    queued = [jid for jid, st in states.items() if st == "QUEUED"]
    assert len(queued) == 2, "b and c must both be released (fan-out)"
    blocked = [jid for jid, st in states.items() if st == "BLOCKED"]
    assert len(blocked) == 1, "d still blocked until fan-in complete"

    for jid in queued:
        await _finish(jid, w)
    async with session_factory()() as s:
        d_state = dict((await s.execute(select(Job.id, Job.state).where(
            Job.workflow_id == U(wf["id"])))).all())
    assert list(d_state.values()).count("QUEUED") == 1, "d released after fan-in"
    await _finish(blocked[0], w)

    detail = (await client.get(
        f"/api/v1/projects/{ctx.project['id']}/workflows/{wf['id']}",
        headers=ctx.headers)).json()
    assert detail["state"] == "COMPLETED" and detail["progress"] == 100.0


async def test_dependency_failure_cancels_dependants(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client, queue_config={"max_concurrent_jobs": 100})
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/workflows",
                          json={"name": "failing",
                                "nodes": _wf_nodes(ctx, {"a": [], "b": ["a"], "c": ["b"]})},
                          headers=ctx.headers)
    wf = r.json()
    w = await register_test_worker(capacity=100)
    detail = (await client.get(
        f"/api/v1/projects/{ctx.project['id']}/workflows/{wf['id']}",
        headers=ctx.headers)).json()
    root = [n["id"] for n in detail["nodes"] if n["state"] == "QUEUED"][0]
    await _finish(root, w, succeed=False)

    from app.db import session_factory
    from app.models import Job
    async with session_factory()() as s:
        states = [st for (st,) in (await s.execute(select(Job.state).where(
            Job.workflow_id == U(wf["id"]))))]
    assert states.count("CANCELLED") == 2, "default policy cancels dependants transitively"


async def test_skip_dependants_policy(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client, queue_config={"max_concurrent_jobs": 100})
    nodes = _wf_nodes(ctx, {"a": [], "b": ["a"]})
    nodes[1]["on_dependency_failure"] = "skip"
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/workflows",
                          json={"name": "skipper", "nodes": nodes}, headers=ctx.headers)
    wf = r.json()
    w = await register_test_worker(capacity=100)
    detail = (await client.get(
        f"/api/v1/projects/{ctx.project['id']}/workflows/{wf['id']}",
        headers=ctx.headers)).json()
    root = [n["id"] for n in detail["nodes"] if n["state"] == "QUEUED"][0]
    await _finish(root, w, succeed=False)
    from app.db import session_factory
    from app.models import Job
    async with session_factory()() as s:
        states = [st for (st,) in (await s.execute(select(Job.state).where(
            Job.workflow_id == U(wf["id"]))))]
    assert "SKIPPED" in states


async def test_recurring_jobs_no_duplicate_occurrences(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/recurring",
                          json={"queue_id": ctx.queue["id"], "name": "tick",
                                "job_type": "math", "cron_expression": "* * * * *",
                                "timezone": "UTC",
                                "payload": {"operation": "sum", "numbers": [1]}},
                          headers=ctx.headers)
    assert r.status_code == 201
    rec_id = r.json()["id"]

    from app.db import session_factory
    from app.models import Job, RecurringJob, utcnow
    from app.services.scheduler_service import materialize_recurring
    # force the occurrence due, then materialize twice with the same cursor
    async with session_factory()() as s:
        due = utcnow() - timedelta(seconds=1)
        await s.execute(update(RecurringJob).where(RecurringJob.id == U(rec_id))
                        .values(next_run_at=due))
        await s.commit()
        assert await materialize_recurring(s) == 1
        await s.execute(update(RecurringJob).where(RecurringJob.id == U(rec_id))
                        .values(next_run_at=due))   # simulate a second scheduler replaying
        await s.commit()
        assert await materialize_recurring(s) == 0, "unique occurrence index must dedupe"
    async with session_factory()() as s:
        count = len((await s.execute(select(Job).where(
            Job.recurring_job_id == U(rec_id)))).scalars().all())
    assert count == 1


async def test_invalid_cron_and_timezone_rejected(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/recurring",
                          json={"queue_id": ctx.queue["id"], "name": "bad",
                                "job_type": "math", "cron_expression": "not a cron"},
                          headers=ctx.headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CRON"
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/recurring",
                          json={"queue_id": ctx.queue["id"], "name": "bad2",
                                "job_type": "math", "cron_expression": "* * * * *",
                                "timezone": "Mars/Olympus"},
                          headers=ctx.headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_TIMEZONE"


async def test_webhooks_signed_and_retried(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/webhooks",
                          json={"url": "http://receiver.test/hook",
                                "events": ["job.completed", "job.failed"]},
                          headers=ctx.headers)
    assert r.status_code == 201
    secret = r.json()["secret"]
    assert secret.startswith("whsec_")
    # secret never shown again
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/webhooks",
                         headers=ctx.headers)
    assert all("secret" not in item for item in r.json()["items"])

    # complete a job -> delivery row enqueued
    job = await create_job_api(ctx)
    w = await register_test_worker()
    await _finish(job["id"], w)

    received = []

    async def hook_handler(request: httpx.Request):
        received.append(request)
        if len(received) == 1:
            return httpx.Response(500, text="try again")   # first delivery fails
        return httpx.Response(200)

    from app.db import session_factory
    from app.models import WebhookDelivery, utcnow
    from app.services.scheduler_service import dispatch_webhooks
    mock = httpx.AsyncClient(transport=httpx.MockTransport(hook_handler))
    async with session_factory()() as s:
        await dispatch_webhooks(s, client=mock)
    async with session_factory()() as s:
        d = (await s.execute(select(WebhookDelivery))).scalars().first()
        assert d.status == "retrying" and d.attempt_count == 1
        assert d.next_attempt_at > utcnow(), "exponential backoff scheduled"
        # make it due now and redeliver
        await s.execute(update(WebhookDelivery).values(next_attempt_at=utcnow()))
        await s.commit()
        await dispatch_webhooks(s, client=mock)
        d = (await s.execute(select(WebhookDelivery))).scalars().first()
        assert d.status == "delivered" and d.response_status == 200
    await mock.aclose()

    # HMAC signature verifies against the creation-time secret
    req = received[-1]
    expected = hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-ChronosGrid-Signature"] == f"sha256={expected}"
    assert req.headers["X-ChronosGrid-Event"] == "job.completed"
    body = json.loads(req.content)
    assert body["data"]["job_id"] == job["id"]


async def test_websocket_events_emitted(app_ctx):
    """Bus fan-out: completing a job produces job.state events."""
    _, client = app_ctx
    ctx = await make_ctx(client)
    from app.events import bus
    q = bus.subscribe()
    try:
        job = await create_job_api(ctx)
        w = await register_test_worker()
        await _finish(job["id"], w)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        types = [e["type"] for e in events]
        assert "job.created" in types
        states = [e["data"].get("state") for e in events if e["type"] == "job.state"]
        assert "RUNNING" in states and "COMPLETED" in states
    finally:
        bus.unsubscribe(q)


async def test_ws_endpoint_rejects_bad_token(app_ctx):
    app, _ = app_ctx
    from starlette.testclient import TestClient
    with TestClient(app) as tc:
        with pytest.raises(Exception):
            with tc.websocket_connect("/api/v1/ws?token=garbage") as ws:
                ws.receive_text()


async def test_pagination_and_filters(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    for i in range(7):
        await create_job_api(ctx, idempotency_key=f"p{i}",
                             tags=["even" if i % 2 == 0 else "odd"],
                             priority=i % 3)
    base = f"/api/v1/projects/{ctx.project['id']}/jobs"
    r = await client.get(f"{base}?page=1&page_size=3", headers=ctx.headers)
    body = r.json()
    assert body["meta"] == {"total": 7, "page": 1, "page_size": 3, "pages": 3}
    assert len(body["items"]) == 3
    r2 = await client.get(f"{base}?page=3&page_size=3", headers=ctx.headers)
    assert len(r2.json()["items"]) == 1
    ids_p1 = {j["id"] for j in body["items"]}
    assert ids_p1.isdisjoint({j["id"] for j in r2.json()["items"]})

    r = await client.get(f"{base}?state=QUEUED", headers=ctx.headers)
    assert r.json()["meta"]["total"] == 7
    r = await client.get(f"{base}?state=COMPLETED", headers=ctx.headers)
    assert r.json()["meta"]["total"] == 0
    r = await client.get(f"{base}?tag=even", headers=ctx.headers)
    assert r.json()["meta"]["total"] == 4
    r = await client.get(f"{base}?sort=priority&order=asc&page_size=50", headers=ctx.headers)
    priorities = [j["priority"] for j in r.json()["items"]]
    assert priorities == sorted(priorities)


async def test_job_logs_cursor_pagination(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx)
    from app.db import session_factory
    from app.models import Job
    from app.services import lifecycle
    async with session_factory()() as s:
        j = (await s.execute(select(Job).where(Job.id == U(job["id"])))).scalar_one()
        for i in range(25):
            await lifecycle.add_log(s, j, f"line {i}")
        await s.commit()
    url = f"/api/v1/projects/{ctx.project['id']}/jobs/{job['id']}/logs?limit=10"
    r = await client.get(url, headers=ctx.headers)
    body = r.json()
    assert len(body["items"]) == 10 and body["next_cursor"] is not None
    r2 = await client.get(f"{url}&after_id={body['next_cursor']}", headers=ctx.headers)
    assert len(r2.json()["items"]) == 10
    assert r2.json()["items"][0]["message"] == "line 10"


async def test_ai_failure_analysis_local_fallback(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, max_attempts=1)
    w = await register_test_worker()
    await _finish(job["id"], w, succeed=False)
    r = await client.post(
        f"/api/v1/projects/{ctx.project['id']}/jobs/{job['id']}/analysis",
        headers=ctx.headers)
    assert r.status_code == 201
    a = r.json()
    assert a["source"] == "local"           # no API key configured
    assert a["summary"] and a["suggestions"]


async def test_audit_log_records_sensitive_actions(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    await client.post(f"/api/v1/projects/{ctx.project['id']}/api-keys",
                      json={"name": "k"}, headers=ctx.headers)
    await client.post(
        f"/api/v1/projects/{ctx.project['id']}/queues/{ctx.queue['id']}/pause",
        headers=ctx.headers)
    r = await client.get(f"/api/v1/orgs/{ctx.org['id']}/audit", headers=ctx.headers)
    actions = [a["action"] for a in r.json()["items"]]
    assert "api_key.create" in actions and "queue.pause" in actions
    assert "user.register" in actions
