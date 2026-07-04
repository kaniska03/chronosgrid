"""Lease expiry, crash recovery, stale completions, timeouts, DLQ, graceful
shutdown (spec tests 2, 3, 5, 13, 20)."""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from tests.conftest import U, create_job_api, make_ctx, register_test_worker


async def _claim_one(worker):
    from app.db import session_factory
    from app.services.claiming import claim_next_job
    async with session_factory()() as s:
        return await claim_next_job(s, worker)


async def _expire_lease(job_id):
    from app.db import session_factory
    from app.models import Job, utcnow
    async with session_factory()() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(
            lease_expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()


async def _run_reaper():
    from app.db import session_factory
    from app.services.scheduler_service import reap_expired_leases
    async with session_factory()() as s:
        return await reap_expired_leases(s)


async def _get_job(job_id):
    from app.db import session_factory
    from app.models import Job
    async with session_factory()() as s:
        return (await s.execute(select(Job).where(Job.id == U(job_id)))).scalar_one()


async def test_worker_crash_recovers_expired_lease(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, max_attempts=3)
    w = await register_test_worker()
    claimed = await _claim_one(w)
    assert claimed is not None
    # simulate crash: no heartbeats, lease expires
    await _expire_lease(claimed.id)
    assert await _run_reaper() == 1
    recovered = await _get_job(claimed.id)
    assert recovered.state == "RETRY_SCHEDULED"
    assert recovered.lease_token is None, "lease token must rotate on recovery"
    # after the retry delay elapses the promoter requeues it
    from app.db import session_factory
    from app.models import Job, utcnow
    from app.services.scheduler_service import promote_due_jobs
    async with session_factory()() as s:
        await s.execute(update(Job).where(Job.id == claimed.id).values(
            next_retry_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
        await promote_due_jobs(s)
    assert (await _get_job(claimed.id)).state == "QUEUED"


async def test_stale_worker_cannot_complete_after_recovery(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    await create_job_api(ctx)
    w = await register_test_worker()
    claimed = await _claim_one(w)
    old_token = claimed.lease_token
    await _expire_lease(claimed.id)
    await _run_reaper()

    from app.db import session_factory
    from app.services import lifecycle
    with pytest.raises(lifecycle.StaleLeaseError):
        async with session_factory()() as s:
            await lifecycle.complete_job(s, claimed.id, old_token, w.id, {"late": True})
    with pytest.raises(lifecycle.StaleLeaseError):
        async with session_factory()() as s:
            await lifecycle.renew_lease(s, claimed.id, old_token, w.id)
    job = await _get_job(claimed.id)
    assert job.state != "COMPLETED", "stale completion must not win"


async def test_exhausted_attempts_enter_dlq(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, max_attempts=2,
                               retry_policy={"strategy": "fixed", "base_delay": 0,
                                             "max_delay": 1, "jitter": False})
    w = await register_test_worker()
    from app.db import session_factory
    from app.services import lifecycle
    from app.services.scheduler_service import promote_due_jobs
    for attempt in (1, 2):
        claimed = await _claim_one(w)
        assert claimed is not None, f"attempt {attempt} should be claimable"
        async with session_factory()() as s:
            await lifecycle.start_job(s, claimed.id, claimed.lease_token, w.id)
            await lifecycle.fail_job(s, claimed.id, claimed.lease_token, w.id,
                                     {"type": "RuntimeError", "message": "boom"},
                                     error_category="retryable")
        async with session_factory()() as s:
            await promote_due_jobs(s)
    final = await _get_job(job["id"])
    assert final.state == "DEAD_LETTERED"
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/dlq", headers=ctx.headers)
    assert r.json()["meta"]["total"] == 1
    entry = r.json()["items"][0]
    assert entry["reason"] == "attempts_exhausted" and entry["attempts"] == 2

    # DLQ replay works
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/dlq/{entry['id']}/retry",
                          headers=ctx.headers)
    assert r.status_code == 200 and r.json()["state"] == "QUEUED"


async def test_non_retryable_error_skips_retries(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, max_attempts=5)
    w = await register_test_worker()
    claimed = await _claim_one(w)
    from app.db import session_factory
    from app.services import lifecycle
    async with session_factory()() as s:
        await lifecycle.start_job(s, claimed.id, claimed.lease_token, w.id)
        await lifecycle.fail_job(s, claimed.id, claimed.lease_token, w.id,
                                 {"type": "ValidationError", "message": "bad payload"},
                                 error_category="non_retryable")
    assert (await _get_job(job["id"])).state == "DEAD_LETTERED"


async def test_timeout_moves_job_to_correct_state(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, timeout_seconds=1, max_attempts=1)
    w = await register_test_worker()
    claimed = await _claim_one(w)
    from app.db import session_factory
    from app.models import Job, utcnow
    from app.services import lifecycle
    from app.services.scheduler_service import reap_timed_out_jobs
    async with session_factory()() as s:
        await lifecycle.start_job(s, claimed.id, claimed.lease_token, w.id)
    async with session_factory()() as s:  # backdate start beyond timeout
        await s.execute(update(Job).where(Job.id == claimed.id).values(
            started_at=utcnow() - timedelta(seconds=5)))
        await s.commit()
        assert await reap_timed_out_jobs(s) == 1
    final = await _get_job(job["id"])
    assert final.state == "DEAD_LETTERED"  # max_attempts=1 -> straight to DLQ
    # the TIMED_OUT transition is recorded in the timeline
    r = await client.get(f"/api/v1/projects/{ctx.project['id']}/jobs/{job['id']}",
                         headers=ctx.headers)
    states = [t["to_state"] for t in r.json()["timeline"]]
    assert "TIMED_OUT" in states


async def test_cancellation_of_queued_and_running(app_ctx):
    _, client = app_ctx
    ctx = await make_ctx(client)
    # queued -> cancelled immediately
    j1 = await create_job_api(ctx, idempotency_key="c1")
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs/{j1['id']}/cancel",
                          json={"reason": "operator request"}, headers=ctx.headers)
    assert r.status_code == 200 and r.json()["state"] == "CANCELLED"

    # running -> CANCEL_REQUESTED, then cooperative cancel finishes it
    j2 = await create_job_api(ctx, idempotency_key="c2")
    w = await register_test_worker()
    claimed = await _claim_one(w)
    from app.db import session_factory
    from app.services import lifecycle
    async with session_factory()() as s:
        await lifecycle.start_job(s, claimed.id, claimed.lease_token, w.id)
    r = await client.post(f"/api/v1/projects/{ctx.project['id']}/jobs/{j2['id']}/cancel",
                          json={"reason": "changed my mind"}, headers=ctx.headers)
    assert r.json()["state"] == "CANCEL_REQUESTED"
    # worker learns via lease renewal
    async with session_factory()() as s:
        info = await lifecycle.renew_lease(s, claimed.id, claimed.lease_token, w.id)
    assert info["cancel_requested"] is True
    async with session_factory()() as s:
        await lifecycle.fail_job(s, claimed.id, claimed.lease_token, w.id,
                                 {"type": "Cancelled", "message": "coop cancel"},
                                 error_category="cancelled")
    final = await _get_job(j2["id"])
    assert final.state == "CANCELLED"
    assert final.cancel_reason == "changed my mind"


async def test_graceful_shutdown_does_not_lose_jobs(app_ctx, monkeypatch):
    """WorkerRunner drains: finishes the running job, goes offline, job COMPLETED."""
    monkeypatch.setenv("WORKER_POLL_SECONDS", "0.05")
    _, client = app_ctx
    ctx = await make_ctx(client)
    job = await create_job_api(ctx, job_type="sleep", payload={"seconds": 0.4})

    from worker.main import WorkerRunner
    runner = WorkerRunner(name="drain-test", capacity=2, tags=["general"],
                          capabilities=["sleep"])
    task = asyncio.create_task(runner.run())
    # wait until the job is picked up
    for _ in range(100):
        await asyncio.sleep(0.05)
        state = (await _get_job(job["id"])).state
        if state in ("CLAIMED", "RUNNING"):
            break
    runner.draining.set()      # SIGTERM equivalent
    await asyncio.wait_for(task, timeout=15)
    final = await _get_job(job["id"])
    assert final.state == "COMPLETED", "in-flight job must finish during drain"
    from app.db import session_factory
    from app.models import Worker
    async with session_factory()() as s:
        w = (await s.execute(select(Worker).where(
            Worker.id == runner.worker_id))).scalar_one()
    assert w.status == "offline"


async def test_dead_worker_marked_offline(app_ctx):
    _, client = app_ctx
    await make_ctx(client)
    w = await register_test_worker("silent")
    from app.db import session_factory
    from app.models import Worker, utcnow
    from app.services.scheduler_service import mark_dead_workers
    async with session_factory()() as s:
        await s.execute(update(Worker).where(Worker.id == w.id).values(
            last_heartbeat_at=utcnow() - timedelta(minutes=10)))
        await s.commit()
        assert await mark_dead_workers(s) == 1
    async with session_factory()() as s:
        assert (await s.execute(select(Worker).where(
            Worker.id == w.id))).scalar_one().status == "offline"
