"""Safe, built-in job handlers. No arbitrary code or shell execution: a job's
``job_type`` selects one of these vetted coroutines.

Contract: ``async handler(payload, ctx) -> dict``. ``ctx`` provides
``report_progress(pct)``, ``log(msg)``, ``cancelled()`` (cooperative
cancellation check) and ``attempt``.
"""
import asyncio
import math
import statistics

import httpx

# Only these hosts may be targeted by http_check (SSRF guard).
HTTP_ALLOWLIST = {"httpbin.org", "example.com", "localhost", "api", "postman-echo.com"}


class NonRetryableError(Exception):
    """Handler-declared permanent failure — the scheduler will not retry."""


async def handle_sleep(payload: dict, ctx) -> dict:
    """Delay demonstration with progress + cooperative cancellation."""
    seconds = min(float(payload.get("seconds", 1)), 3600)
    steps = max(1, min(int(seconds * 2), 100))
    for i in range(steps):
        if ctx.cancelled():
            raise asyncio.CancelledError()
        await asyncio.sleep(seconds / steps)
        await ctx.report_progress(100.0 * (i + 1) / steps)
    return {"slept_seconds": seconds}


async def handle_math(payload: dict, ctx) -> dict:
    """Safe math over a numeric series (no eval)."""
    op = payload.get("operation", "sum")
    numbers = payload.get("numbers", [])
    if not isinstance(numbers, list) or not all(isinstance(x, (int, float)) for x in numbers):
        raise NonRetryableError("payload.numbers must be a list of numbers")
    ops = {"sum": sum, "mean": statistics.mean, "median": statistics.median,
           "min": min, "max": max, "stdev": lambda xs: statistics.stdev(xs) if len(xs) > 1 else 0.0,
           "sqrt_sum": lambda xs: math.sqrt(sum(xs))}
    if op not in ops:
        raise NonRetryableError(f"unknown operation {op!r}")
    if not numbers:
        raise NonRetryableError("payload.numbers must not be empty")
    await ctx.log(f"computing {op} over {len(numbers)} numbers")
    return {"operation": op, "result": ops[op](numbers), "count": len(numbers)}


async def handle_text_transform(payload: dict, ctx) -> dict:
    text = str(payload.get("text", ""))[:100_000]
    transform = payload.get("transform", "upper")
    transforms = {"upper": str.upper, "lower": str.lower, "title": str.title,
                  "reverse": lambda s: s[::-1],
                  "word_count": lambda s: str(len(s.split()))}
    if transform not in transforms:
        raise NonRetryableError(f"unknown transform {transform!r}")
    return {"transform": transform, "output": transforms[transform](text)}


async def handle_http_check(payload: dict, ctx) -> dict:
    """HTTP GET against an allowlisted host only."""
    url = payload.get("url", "https://example.com")
    host = httpx.URL(url).host
    if host not in HTTP_ALLOWLIST:
        raise NonRetryableError(f"host {host!r} is not in the allowlist")
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        resp = await client.get(url)
    return {"url": url, "status_code": resp.status_code,
            "content_length": len(resp.content)}


async def handle_report(payload: dict, ctx) -> dict:
    """Simulated report generation with staged progress."""
    rows = min(int(payload.get("rows", 100)), 100_000)
    stages = ["collect", "aggregate", "render"]
    for i, stage in enumerate(stages):
        if ctx.cancelled():
            raise asyncio.CancelledError()
        await ctx.log(f"report stage: {stage}")
        await asyncio.sleep(0.05)
        await ctx.report_progress(100.0 * (i + 1) / len(stages))
    return {"rows": rows, "sections": stages, "format": payload.get("format", "json")}


async def handle_flaky(payload: dict, ctx) -> dict:
    """Controlled failure demo: fails until attempt >= succeed_on_attempt."""
    succeed_on = int(payload.get("succeed_on_attempt", 3))
    if ctx.attempt < succeed_on:
        raise RuntimeError(f"flaky handler failing on attempt {ctx.attempt} "
                           f"(succeeds on {succeed_on})")
    return {"succeeded_on_attempt": ctx.attempt}


async def handle_always_fail(payload: dict, ctx) -> dict:
    if payload.get("non_retryable"):
        raise NonRetryableError(payload.get("message", "permanent failure demo"))
    raise RuntimeError(payload.get("message", "transient failure demo"))


HANDLERS = {
    "sleep": handle_sleep,
    "math": handle_math,
    "text_transform": handle_text_transform,
    "http_check": handle_http_check,
    "report": handle_report,
    "flaky": handle_flaky,
    "always_fail": handle_always_fail,
}
