"""ChronosGrid worker process.

Lifecycle: register -> heartbeat task + N claim/execute slots -> on SIGTERM
drain (stop claiming, finish running jobs, mark offline).

The worker talks directly to PostgreSQL through the same service layer as the
API: claiming is atomic in the database, so any number of worker replicas can
run concurrently (``docker compose up --scale worker=4``).
"""
import asyncio
import contextlib
import logging
import os
import signal
import socket
import traceback

from app.config import get_settings
from app.db import session_factory
from app.services import lifecycle
from app.services.claiming import claim_next_job
from app.services.lifecycle import StaleLeaseError
from app.services.worker_service import heartbeat, register_worker, set_status

from .handlers import HANDLERS, NonRetryableError

log = logging.getLogger("chronosgrid.worker")

IDLE_SLEEP = float(os.environ.get("WORKER_POLL_SECONDS", "0.5"))


class ExecutionContext:
    def __init__(self, runner: "WorkerRunner", job) -> None:
        self._runner = runner
        self._job = job
        self._cancelled = False
        self.attempt = job.attempt_count

    def cancelled(self) -> bool:
        return self._cancelled

    def mark_cancelled(self) -> None:
        self._cancelled = True

    async def report_progress(self, pct: float) -> None:
        with contextlib.suppress(StaleLeaseError):
            async with session_factory()() as session:
                await lifecycle.update_progress(session, self._job.id,
                                                self._job.lease_token, pct)

    async def log(self, message: str, level: str = "info") -> None:
        async with session_factory()() as session:
            from sqlalchemy import select
            from app.models import Job
            job = (await session.execute(
                select(Job).where(Job.id == self._job.id))).scalar_one()
            await lifecycle.add_log(session, job, message, level=level,
                                    worker_id=self._runner.worker_id)
            await session.commit()


class WorkerRunner:
    def __init__(self, *, name: str, capacity: int, tags: list[str],
                 capabilities: list[str], version: str = "1.0.0") -> None:
        self.name = name
        self.capacity = capacity
        self.tags = tags
        self.capabilities = capabilities
        self.version = version
        self.worker_id = None
        self.draining = asyncio.Event()
        self._active: set[asyncio.Task] = set()
        self._contexts: dict = {}

    async def register(self) -> None:
        async with session_factory()() as session:
            worker = await register_worker(
                session, name=self.name, host=socket.gethostname(), pid=os.getpid(),
                version=self.version, capacity=self.capacity, tags=self.tags,
                capabilities=self.capabilities)
            self.worker_id = worker.id
        log.info("registered worker %s id=%s capacity=%d", self.name, self.worker_id,
                 self.capacity)

    async def _heartbeat_loop(self) -> None:
        s = get_settings()
        while not self.draining.is_set() or self._active:
            async with session_factory()() as session:
                await heartbeat(session, self.worker_id,
                                active_jobs=len(self._active),
                                status="draining" if self.draining.is_set() else "online")
            # Renew leases of running jobs and pick up cancellation requests.
            for job_id, (job, ctx) in list(self._contexts.items()):
                try:
                    async with session_factory()() as session:
                        info = await lifecycle.renew_lease(
                            session, job.id, job.lease_token, self.worker_id)
                    if info["cancel_requested"]:
                        ctx.mark_cancelled()
                except StaleLeaseError:
                    ctx.mark_cancelled()  # ownership lost; stop wasting cycles
            try:
                await asyncio.wait_for(self.draining.wait(), timeout=s.heartbeat_seconds)
            except asyncio.TimeoutError:
                pass
            if self.draining.is_set() and not self._active:
                break

    async def _execute(self, job) -> None:
        ctx = ExecutionContext(self, job)
        self._contexts[job.id] = (job, ctx)
        try:
            async with session_factory()() as session:
                job = await lifecycle.start_job(session, job.id, job.lease_token,
                                                self.worker_id)
            if job.state != "RUNNING":
                return  # cancelled before start
            handler = HANDLERS.get(job.job_type)
            if handler is None:
                raise NonRetryableError(f"no handler for job_type {job.job_type!r}")
            timeout = job.timeout_seconds or 300
            try:
                result = await asyncio.wait_for(handler(job.payload or {}, ctx),
                                                timeout=timeout)
            except asyncio.TimeoutError:
                async with session_factory()() as session:
                    await lifecycle.fail_job(
                        session, job.id, job.lease_token, self.worker_id,
                        error={"type": "Timeout",
                               "message": f"handler exceeded {timeout}s"},
                        error_category="timeout", timed_out=True)
                return
            except asyncio.CancelledError:
                async with session_factory()() as session:
                    await lifecycle.fail_job(
                        session, job.id, job.lease_token, self.worker_id,
                        error={"type": "Cancelled", "message": "cooperatively cancelled"},
                        error_category="cancelled")
                return
            async with session_factory()() as session:
                await lifecycle.complete_job(session, job.id, job.lease_token,
                                             self.worker_id, result=result)
        except StaleLeaseError:
            log.warning("job %s: lease lost during execution; result discarded", job.id)
        except NonRetryableError as exc:
            with contextlib.suppress(StaleLeaseError):
                async with session_factory()() as session:
                    await lifecycle.fail_job(
                        session, job.id, job.lease_token, self.worker_id,
                        error={"type": "NonRetryableError", "message": str(exc)},
                        error_category="non_retryable")
        except Exception as exc:
            with contextlib.suppress(StaleLeaseError):
                async with session_factory()() as session:
                    await lifecycle.fail_job(
                        session, job.id, job.lease_token, self.worker_id,
                        error={"type": type(exc).__name__, "message": str(exc)[:2000],
                               "traceback": traceback.format_exc()[-4000:]},
                        error_category="retryable")
        finally:
            self._contexts.pop(job.id, None)

    async def _claim_loop(self) -> None:
        from sqlalchemy import select
        from app.models import Worker
        while not self.draining.is_set():
            if len(self._active) >= self.capacity:
                await asyncio.sleep(IDLE_SLEEP)
                continue
            async with session_factory()() as session:
                me = (await session.execute(
                    select(Worker).where(Worker.id == self.worker_id))).scalar_one()
                job = await claim_next_job(session, me)
            if job is None:
                await asyncio.sleep(IDLE_SLEEP)
                continue
            task = asyncio.create_task(self._execute(job))
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def run(self) -> None:
        await self.register()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.draining.set)
        hb = asyncio.create_task(self._heartbeat_loop())
        await self._claim_loop()          # exits when draining begins
        log.info("draining: waiting for %d active job(s)", len(self._active))
        if self._active:
            await asyncio.wait(self._active)
        await hb
        async with session_factory()() as session:
            await set_status(session, self.worker_id, "offline")
        log.info("worker %s exited cleanly", self.name)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='{"ts":"%(asctime)s","logger":"%(name)s",'
                               '"level":"%(levelname)s","message":"%(message)s"}')
    runner = WorkerRunner(
        name=os.environ.get("WORKER_NAME", f"worker-{socket.gethostname()}"),
        capacity=int(os.environ.get("WORKER_CAPACITY", "5")),
        tags=[t for t in os.environ.get("WORKER_TAGS", "general").split(",") if t],
        capabilities=[c for c in os.environ.get(
            "WORKER_CAPABILITIES",
            "sleep,math,text_transform,http_check,report,flaky,always_fail").split(",") if c],
        version=os.environ.get("WORKER_VERSION", "1.0.0"))
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
