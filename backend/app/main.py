"""ChronosGrid API service."""
import contextlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from .config import get_settings
from .db import Base, get_engine, session_factory
from .errors import ApiError
from .events import bus
from .routers import (
    ai, audit_router, auth, dlq, jobs, metrics_router, orgs, projects, queues,
    recurring, webhooks, workers, workflows, ws,
)
from .services.scheduler_service import SchedulerService
from .state_machine import InvalidTransition

log = logging.getLogger("chronosgrid.api")


def _configure_logging() -> None:
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            entry = {"ts": self.formatTime(record), "level": record.levelname,
                     "logger": record.name, "message": record.getMessage()}
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)
            return json.dumps(entry)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


scheduler = SchedulerService()


def create_app(embedded_scheduler: bool | None = None) -> FastAPI:
    _configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="ChronosGrid",
        version="1.0.0",
        description="Multi-tenant distributed job scheduler. "
                    "Delivery semantics: **at-least-once** — use idempotency keys.",
        docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True, allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"])

    # ---- API rate limiting (fixed window per client) ---------------------- #
    _hits: dict[str, deque] = defaultdict(deque)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        corr = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = corr
        if request.url.path.startswith("/api/") and not request.url.path.endswith(
                ("/health", "/ready")):
            key = (request.headers.get("X-API-Key")
                   or request.headers.get("Authorization", "")[-24:]
                   or (request.client.host if request.client else "anon"))
            window = _hits[key]
            now = time.monotonic()
            while window and window[0] < now - 60:
                window.popleft()
            if len(window) >= settings.api_rate_limit_per_minute:
                retry = 60 - (now - window[0])
                return JSONResponse(status_code=429, headers={
                    "Retry-After": str(max(1, int(retry))),
                    "X-Correlation-ID": corr}, content={"error": {
                        "code": "RATE_LIMITED",
                        "message": "API rate limit exceeded",
                        "details": {"limit_per_minute": settings.api_rate_limit_per_minute},
                        "correlation_id": corr}})
            window.append(now)
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") and "docs" not in request.url.path:
            log.info(json.dumps({"method": request.method, "path": request.url.path,
                                 "status": response.status_code,
                                 "duration_ms": round((time.monotonic() - start) * 1000, 2),
                                 "correlation_id": corr}))
        return response

    # ---- Structured error handling ---------------------------------------- #
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        body = exc.body()
        body["error"]["correlation_id"] = getattr(
            request.state, "correlation_id", body["error"]["correlation_id"])
        return JSONResponse(status_code=exc.status_code, content=body,
                            headers=exc.headers)

    @app.exception_handler(InvalidTransition)
    async def transition_error_handler(request: Request, exc: InvalidTransition):
        return JSONResponse(status_code=409, content={"error": {
            "code": "INVALID_STATE_TRANSITION",
            "message": str(exc),
            "details": {"from": exc.from_state, "to": exc.to_state},
            "correlation_id": getattr(request.state, "correlation_id", "")}})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "request validation failed",
            "details": {"errors": json.loads(json.dumps(exc.errors(), default=str))},
            "correlation_id": getattr(request.state, "correlation_id", "")}})

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": {
            "code": "INVALID_VALUE", "message": str(exc), "details": {},
            "correlation_id": getattr(request.state, "correlation_id", "")}})

    # ---- Routers ----------------------------------------------------------- #
    api_prefix = "/api/v1"
    for r in (auth.router, orgs.router, projects.router, queues.router, jobs.router,
              workflows.router, recurring.router, workers.router, dlq.router,
              webhooks.router, audit_router.router, metrics_router.router, ai.router):
        app.include_router(r, prefix=api_prefix)
    app.include_router(ws.router)

    # ---- Health ------------------------------------------------------------ #
    @app.get("/api/v1/health", tags=["health"])
    async def health():
        return {"status": "ok", "service": "chronosgrid-api"}

    @app.get("/api/v1/ready", tags=["health"])
    async def ready():
        checks = {}
        try:
            async with session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis
                r = aioredis.from_url(settings.redis_url)
                await r.ping()
                await r.aclose()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"error: {exc}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503,
                            content={"status": "ready" if ok else "degraded",
                                     "checks": checks})

    @app.get("/metrics", response_class=PlainTextResponse, tags=["metrics"])
    async def prometheus():
        from .services.metrics import prometheus_text
        async with session_factory()() as session:
            return await prometheus_text(session)

    # ---- Lifecycle ---------------------------------------------------------- #
    run_scheduler = (embedded_scheduler if embedded_scheduler is not None
                     else os.environ.get("EMBEDDED_SCHEDULER", "1") == "1")

    @app.on_event("startup")
    async def startup():
        engine = get_engine()
        if not settings.database_url.startswith("postgresql"):
            # dev/test convenience; PostgreSQL deployments use Alembic
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        await bus.start()
        if settings.seed_demo_data:
            from .services.seed import ensure_demo_account, seed_demo_data
            async with session_factory()() as session:
                try:
                    await seed_demo_data(session)
                except Exception:
                    log.exception("seed failed (continuing)")
        if run_scheduler:
            scheduler.start()

    @app.on_event("shutdown")
    async def shutdown():
        if run_scheduler:
            with contextlib.suppress(Exception):
                await scheduler.stop()
        await bus.stop()

    return app


app = create_app()
