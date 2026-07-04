"""AuthN/AuthZ: RBAC, project isolation, API keys, idempotency, quotas
(spec tests 6, 9, 10 + rate limiting)."""
import pytest

from tests.conftest import create_job_api, make_ctx


async def test_project_isolation_between_orgs(app_ctx):
    _, client = app_ctx
    a = await make_ctx(client)
    b = await make_ctx(client)
    job = await create_job_api(a)
    # user B cannot see or touch project A resources (404, not 403 — hides existence)
    r = await client.get(f"/api/v1/projects/{a.project['id']}/jobs", headers=b.headers)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/projects/{a.project['id']}/jobs/{job['id']}",
                         headers=b.headers)
    assert r.status_code == 404
    r = await client.post(f"/api/v1/projects/{a.project['id']}/jobs/{job['id']}/cancel",
                          json={}, headers=b.headers)
    assert r.status_code == 404


async def test_rbac_viewer_cannot_mutate(app_ctx):
    _, client = app_ctx
    admin = await make_ctx(client)
    viewer = await make_ctx(client)
    # add viewer to admin's org with viewer role
    r = await client.put(f"/api/v1/orgs/{admin.org['id']}/members",
                         json={"email": "ignored@test.dev", "role": "viewer"},
                         headers=admin.headers)
    assert r.status_code == 404  # unknown user rejected cleanly
    # register the real viewer email
    me = (await client.get("/api/v1/auth/me", headers=viewer.headers)).json()
    r = await client.put(f"/api/v1/orgs/{admin.org['id']}/members",
                         json={"email": me["email"], "role": "viewer"},
                         headers=admin.headers)
    assert r.status_code == 200

    # viewer can read
    r = await client.get(f"/api/v1/projects/{admin.project['id']}/queues",
                         headers=viewer.headers)
    assert r.status_code == 200
    # ...but cannot create jobs (developer+), queues, or API keys (project_admin+)
    r = await client.post(f"/api/v1/projects/{admin.project['id']}/jobs",
                          json={"queue_id": admin.queue["id"], "job_type": "math",
                                "payload": {"operation": "sum", "numbers": [1]}},
                          headers=viewer.headers)
    assert r.status_code == 403
    r = await client.post(f"/api/v1/projects/{admin.project['id']}/queues",
                          json={"name": "nope"}, headers=viewer.headers)
    assert r.status_code == 403
    r = await client.post(f"/api/v1/projects/{admin.project['id']}/api-keys",
                          json={"name": "nope"}, headers=viewer.headers)
    assert r.status_code == 403


async def test_api_key_lifecycle_and_scoping(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    other = await make_ctx(client)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/api-keys",
                          json={"name": "ci-key"}, headers=ctx.headers)
    assert r.status_code == 201
    created = r.json()
    full_key = created["key"]
    assert full_key.startswith("cg_")
    # key is never returned again
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/api-keys",
                         headers=ctx.headers)
    assert all("key" not in item for item in r.json()["items"])

    kh = {"X-API-Key": full_key}
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs",
                          json={"queue_id": ctx.queue["id"], "job_type": "math",
                                "payload": {"operation": "sum", "numbers": [1]}},
                          headers=kh)
    assert r.status_code == 201, "API key can create jobs in its own project"
    r = await client.get(f"/api/v1/projects/{other.project['id']}/jobs", headers=kh)
    assert r.status_code == 404, "API key must not cross projects"

    # last_used_at is tracked
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/api-keys",
                         headers=ctx.headers)
    assert r.json()["items"][0]["last_used_at"] is not None

    # revocation
    r = await client.delete(
        f"/api/v1/projects/{ctx.project['id']}/api-keys/{created['id']}",
        headers=ctx.headers)
    assert r.status_code == 200
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/jobs", headers=kh)
    assert r.status_code == 401


async def test_idempotency_key_prevents_duplicates(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    j1 = await create_job_api(ctx, idempotency_key="pay-42")
    j2 = await create_job_api(ctx, idempotency_key="pay-42")
    assert j1["id"] == j2["id"], "same idempotency key must return the same job"
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/jobs",
                         headers=ctx.headers)
    assert r.json()["meta"]["total"] == 1


async def test_payload_size_and_batch_limits(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.patch(f"/api/v1/projects/{ctx.project['id']}",
                           json={"max_payload_bytes": 2048, "max_batch_size": 3},
                           headers=ctx.headers)
    assert r.status_code == 200
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs",
                          json={"queue_id": ctx.queue["id"], "job_type": "text_transform",
                                "payload": {"text": "x" * 5000}},
                          headers=ctx.headers)
    assert r.status_code == 413
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs/batch",
                          json={"queue_id": ctx.queue["id"],
                                "jobs": [{"job_type": "math",
                                          "payload": {"operation": "sum", "numbers": [1]}}] * 5},
                          headers=ctx.headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BATCH_TOO_LARGE"


async def test_daily_quota_returns_429(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.patch(f"/api/v1/projects/{ctx.project['id']}",
                           json={"daily_job_quota": 2}, headers=ctx.headers)
    await create_job_api(ctx, idempotency_key="q1")
    await create_job_api(ctx, idempotency_key="q2")
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs",
                          json={"queue_id": ctx.queue["id"], "job_type": "math",
                                "payload": {"operation": "sum", "numbers": [1]}},
                          headers=ctx.headers)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert r.json()["error"]["code"] == "RATE_LIMITED"


async def test_token_refresh_flow(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": ctx.refresh})
    assert r.status_code == 200 and "access_token" in r.json()
    # access token cannot be used as refresh token
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": ctx.token})
    assert r.status_code == 401


async def test_demo_account_seed_and_login(app_ctx):
    """Demo account uses the normal login flow, org_admin role, seeded data."""
    app, client = app_ctx
    from app.db import session_factory
    from app.services.seed import seed_demo_data
    async with session_factory()() as s:
        await seed_demo_data(s)
    r = await client.post("/api/v1/auth/login", json={
        "email": "demo@chronosgrid.dev", "password": "Demo@1234"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    orgs = (await client.get("/api/v1/orgs", headers=h)).json()["items"]
    demo_org = [o for o in orgs if o["slug"] == "demo-org"][0]
    assert demo_org["role"] == "org_admin"
    projects = (await client.get(f"/api/v1/orgs/{demo_org['id']}/projects",
                                 headers=h)).json()["items"]
    assert {p["slug"] for p in projects} == {"payments", "analytics"}
    # wrong password rejected (no bypass)
    r = await client.post("/api/v1/auth/login", json={
        "email": "demo@chronosgrid.dev", "password": "wrong"})
    assert r.status_code == 401


async def test_sensitive_payload_masked_in_api(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, job_type="text_transform",
                               payload={"text": "hi", "api_key": "sk-super-secret",
                                        "nested": {"password": "hunter2"}})
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/jobs/{job['id']}",
                         headers=ctx.headers)
    payload = r.json()["payload"]
    assert payload["api_key"] == "***REDACTED***"
    assert payload["nested"]["password"] == "***REDACTED***"
    assert payload["text"] == "hi"
