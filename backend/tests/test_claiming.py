"""Atomic claiming, pause, concurrency limits, capability routing, fairness
(spec tests 1, 7, 8, 18 + stress test)."""
import asyncio

import pytest

from tests.conftest import U, create_job_api, make_ctx, register_test_worker


async def _claim_all(worker, max_claims=100):
    """Claim repeatedly in a dedicated session until empty."""
    from app.db import session_factory
    from app.services.claiming import claim_next_job
    claimed = []
    for _ in range(max_claims):
        async with session_factory()() as s:
            job = await claim_next_job(s, worker)
        if job is None:
            break
        claimed.append(job)
    return claimed


async def test_two_workers_cannot_claim_same_job(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx)
    w1 = await register_test_worker("w1")
    w2 = await register_test_worker("w2")

    from app.db import session_factory
    from app.services.claiming import claim_next_job

    async def try_claim(w):
        async with session_factory()() as s:
            return await claim_next_job(s, w)

    results = await asyncio.gather(try_claim(w1), try_claim(w2))
    got = [r for r in results if r is not None]
    assert len(got) == 1, "exactly one worker must win the claim"
    assert str(got[0].id) == job["id"]


async def test_concurrency_stress_many_workers(app_ctx):
    """Stress: 40 jobs, 4 workers claiming concurrently; every job claimed
    exactly once per attempt (spec's mandatory concurrency stress test)."""
    _, client = app_ctx
    ctx = await make_ctx(client, queue_config={"max_concurrent_jobs": 100,
                                           "per_worker_concurrency": 100})
    n_jobs = 40
    for i in range(n_jobs):
        await create_job_api(ctx, idempotency_key=f"stress-{i}")
    workers = [await register_test_worker(f"stress-w{i}", capacity=100) for i in range(4)]

    results = await asyncio.gather(*[_claim_all(w) for w in workers])
    all_claims = [str(j.id) for claims in results for j in claims]
    assert len(all_claims) == n_jobs
    assert len(set(all_claims)) == n_jobs, "a job was claimed twice!"

    # Attempt-level uniqueness in the execution history
    from sqlalchemy import func, select
    from app.db import session_factory
    from app.models import JobExecution
    async with session_factory()() as s:
        dupes = (await s.execute(
            select(JobExecution.job_id, JobExecution.attempt_number, func.count())
            .group_by(JobExecution.job_id, JobExecution.attempt_number)
            .having(func.count() > 1))).all()
    assert dupes == []


async def test_paused_queue_releases_nothing(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    await create_job_api(ctx)
    r = await client.post(
        f"/api/v1/projects/{ctx.project['id']}/queues/{ctx.queue['id']}/pause",
        headers=ctx.headers)
    assert r.status_code == 200 and r.json()["paused"] is True
    w = await register_test_worker()
    assert await _claim_all(w) == []
    # resume -> claimable again
    await client.post(
        f"/api/v1/projects/{ctx.project['id']}/queues/{ctx.queue['id']}/resume",
        headers=ctx.headers)
    assert len(await _claim_all(w)) == 1


async def test_queue_concurrency_limit_respected(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client, queue_config={"max_concurrent_jobs": 2,
                                               "per_worker_concurrency": 10})
    for i in range(5):
        await create_job_api(ctx)
    w = await register_test_worker(capacity=50)
    claimed = await _claim_all(w)
    assert len(claimed) == 2, "queue max_concurrent_jobs must cap active claims"


async def test_worker_capability_and_tag_routing(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    await create_job_api(ctx, job_type="report", payload={"rows": 5})
    wrong = await register_test_worker("no-report", capabilities=["math"])
    assert await _claim_all(wrong) == []
    right = await register_test_worker("has-report", capabilities=["report"])
    assert len(await _claim_all(right)) == 1


async def test_routing_key_requires_matching_tag(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    await create_job_api(ctx, routing_key="gpu")
    plain = await register_test_worker("plain", tags=["general"])
    assert await _claim_all(plain) == []
    gpu = await register_test_worker("gpu-1", tags=["general", "gpu"])
    assert len(await _claim_all(gpu)) == 1


async def test_priority_and_fifo_order(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    low = await create_job_api(ctx, priority=0, idempotency_key="low")
    high = await create_job_api(ctx, priority=8, idempotency_key="high")
    w = await register_test_worker()
    claimed = await _claim_all(w)
    assert [str(j.id) for j in claimed] == [high["id"], low["id"]]


async def test_priority_aging_prevents_starvation(app_ctx, monkeypatch):
    """An old low-priority job must eventually beat a new higher-priority one."""
    monkeypatch.setenv("PRIORITY_AGING_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PRIORITY_AGING_MAX_BOOST", "5")
    from app.config import get_settings
    get_settings.cache_clear()

    _, client = app_ctx
    ctx = await make_ctx(client)
    from datetime import timedelta
    from sqlalchemy import update
    from app.db import session_factory
    from app.models import Job, utcnow

    old_low = await create_job_api(ctx, priority=0, idempotency_key="old-low")
    async with session_factory()() as s:  # it has waited 10s -> boost capped at 5
        await s.execute(update(Job).where(Job.id == U(old_low["id"])).values(
            available_at=utcnow() - timedelta(seconds=10)))
        await s.commit()
    await create_job_api(ctx, priority=3, idempotency_key="new-mid")

    w = await register_test_worker()
    claimed = await _claim_all(w)
    assert str(claimed[0].id) == old_low["id"], \
        "aged job (0 + boost 5) must outrank fresh priority-3 job"
    get_settings.cache_clear()


async def test_scheduled_job_not_claimable_until_due(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, delay_seconds=3600)
    assert job["state"] == "SCHEDULED"
    w = await register_test_worker()
    assert await _claim_all(w) == []
