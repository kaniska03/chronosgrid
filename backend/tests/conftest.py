"""Test fixtures.

Each test gets a fresh database. Locally the suite runs on SQLite
(aiosqlite); in CI/compose set TEST_DATABASE_URL to a PostgreSQL DSN and the
same suite runs against Postgres, exercising the SKIP LOCKED claim path.
"""
import asyncio
import os
import sys
import uuid

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _database_url(tmp_path) -> str:
    base = os.environ.get("TEST_DATABASE_URL")
    if base:  # e.g. postgresql+asyncpg://cg:cg@localhost:5432/cg_test
        return base
    return f"sqlite+aiosqlite:///{tmp_path}/test.db"


@pytest_asyncio.fixture
async def app_ctx(tmp_path, monkeypatch):
    """Fresh app + database + demo-less environment."""
    monkeypatch.setenv("DATABASE_URL", _database_url(tmp_path))
    monkeypatch.setenv("SEED_DEMO_DATA", "0")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("PRIORITY_AGING_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "100000")

    from app.config import get_settings
    get_settings.cache_clear()
    from app import db as db_module
    await db_module.reset_engine()

    import app.models  # noqa: F401 — populate Base.metadata before create_all
    from app.db import Base, get_engine
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from app.main import create_app
    app = create_app(embedded_scheduler=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client
    await db_module.reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def client(app_ctx):
    return app_ctx[1]


class Ctx:
    """Convenience bundle: registered user + org + project + queue."""
    def __init__(self, client, token, refresh, org, project, queue, headers):
        self.client = client
        self.token = token
        self.refresh = refresh
        self.org = org
        self.project = project
        self.queue = queue
        self.headers = headers


async def make_ctx(client, email=None, queue_config=None) -> Ctx:
    email = email or f"user-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/v1/auth/register", json={
        "email": email, "password": "Passw0rd123", "name": "Test User"})
    assert r.status_code == 201, r.text
    tokens = r.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    org = (await client.get("/api/v1/orgs", headers=headers)).json()["items"][0]
    projects = (await client.get(f"/api/v1/orgs/{org['id']}/projects",
                                 headers=headers)).json()["items"]
    project = projects[0]
    qbody = {"name": "default"}
    qbody.update(queue_config or {})
    r = await client.post(f"/api/v1/projects/{project['id']}/queues",
                          json=qbody, headers=headers)
    assert r.status_code == 201, r.text
    return Ctx(client, tokens["access_token"], tokens["refresh_token"],
               org, project, r.json(), headers)


async def register_test_worker(name="w1", capacity=10, tags=None, capabilities=None):
    from app.db import session_factory
    from app.services.worker_service import register_worker
    async with session_factory()() as s:
        return await register_worker(
            s, name=name, host="test", pid=1, capacity=capacity,
            tags=tags or ["general"],
            capabilities=capabilities or ["sleep", "math", "text_transform",
                                          "http_check", "report", "flaky", "always_fail"])


async def create_job_api(ctx: Ctx, **kwargs) -> dict:
    body = {"queue_id": ctx.queue["id"], "job_type": "math",
            "payload": {"operation": "sum", "numbers": [1, 2]}}
    body.update(kwargs)
    r = await ctx.client.post(f"/api/v1/projects/{ctx.project['id']}/jobs",
                              json=body, headers=ctx.headers)
    assert r.status_code == 201, r.text
    return r.json()


def U(value):
    """Coerce an API-returned id (str) to uuid.UUID for direct model queries."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
