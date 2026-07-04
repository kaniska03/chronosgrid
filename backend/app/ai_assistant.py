"""AI failure assistant — optional, isolated, advisory-only.

Guarantees:
* The scheduler never reads this module's output; it can only annotate.
* Sensitive payload fields are masked before any model call.
* With no ANTHROPIC_API_KEY a deterministic local analyzer produces the
  summary, so the feature always works offline.
"""
import json
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .masking import mask_sensitive
from .models import FailureAnalysis, Job, JobExecution, JobLog

RULES = [
    (re.compile(r"timeout|timed out", re.I),
     "The job exceeded its execution timeout.",
     ["Execution time exceeds the configured timeout",
      "Downstream dependency responding slowly"],
     ["Increase timeout_seconds on the job or queue default",
      "Profile the handler to find the slow section",
      "Split the work into smaller batch jobs"]),
    (re.compile(r"connection|refused|unreachable|dns|network", re.I),
     "The job failed due to a network connectivity problem.",
     ["Target service unavailable", "DNS or firewall misconfiguration"],
     ["Verify the target host is reachable from the worker network",
      "Check allowlist configuration for http_check jobs",
      "Retries with exponential backoff usually recover transient network errors"]),
    (re.compile(r"non.?retryable|validation|must be|unknown (operation|transform|job_type)", re.I),
     "The job failed a permanent validation check — retrying will not help.",
     ["Malformed payload", "Unsupported operation requested"],
     ["Fix the payload and clone the job",
      "Add client-side validation before enqueueing"]),
    (re.compile(r"lease expired|crash", re.I),
     "The worker executing this job stopped renewing its lease (likely crash).",
     ["Worker process crashed or was OOM-killed", "Network partition"],
     ["Check worker logs and memory limits",
      "The scheduler already requeued the job; verify idempotency of the handler"]),
    (re.compile(r"flaky|transient", re.I),
     "A transient failure occurred; this handler is expected to succeed on retry.",
     ["Intermittent dependency failure"],
     ["No action usually required — verify the retry policy allows enough attempts"]),
]


def local_analysis(error: dict | None, logs: list[str]) -> dict:
    text = " ".join(filter(None, [json.dumps(error or {}), *logs]))
    for pattern, summary, causes, suggestions in RULES:
        if pattern.search(text):
            return {"summary": summary, "likely_causes": causes,
                    "suggestions": suggestions}
    etype = (error or {}).get("type", "UnknownError")
    return {"summary": f"The job failed with {etype}: "
                       f"{(error or {}).get('message', 'no message')[:200]}",
            "likely_causes": ["Unhandled exception in the job handler"],
            "suggestions": ["Inspect the execution traceback and job logs",
                            "Reproduce locally by cloning the job with the same payload"]}


async def analyze_job(db: AsyncSession, job: Job) -> FailureAnalysis:
    logs = (await db.execute(
        select(JobLog).where(JobLog.job_id == job.id)
        .order_by(JobLog.id.desc()).limit(20))).scalars().all()
    executions = (await db.execute(
        select(JobExecution).where(JobExecution.job_id == job.id)
        .order_by(JobExecution.attempt_number))).scalars().all()
    log_lines = [f"[{l.level}] {l.message}" for l in reversed(logs)]
    retry_pattern = [{"attempt": e.attempt_number, "state": e.state,
                      "error_category": e.error_category,
                      "delay": e.retry_delay_seconds} for e in executions]

    settings = get_settings()
    source = "local"
    result = None
    if settings.anthropic_api_key:
        try:
            masked_payload = mask_sensitive(job.payload or {})
            prompt = (
                "Analyze this failed background job and reply as JSON with keys "
                "summary (string), likely_causes (list), suggestions (list).\n"
                f"job_type: {job.job_type}\nerror: {json.dumps(mask_sensitive(job.error or {}))}\n"
                f"payload (masked): {json.dumps(masked_payload)[:2000]}\n"
                f"retry history: {json.dumps(retry_pattern)}\n"
                f"recent logs:\n" + "\n".join(log_lines[-10:]))
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": settings.anthropic_api_key,
                             "anthropic-version": "2023-06-01"},
                    json={"model": settings.ai_model, "max_tokens": 800,
                          "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]
            match = re.search(r"\{.*\}", text, re.S)
            result = json.loads(match.group(0)) if match else None
            source = "ai"
        except Exception:
            result = None
            source = "local"
    if result is None:
        result = local_analysis(job.error, log_lines)
        source = "local"

    analysis = FailureAnalysis(
        job_id=job.id, source=source,
        summary=str(result.get("summary", ""))[:4000],
        likely_causes=list(result.get("likely_causes", []))[:10],
        suggestions=list(result.get("suggestions", []))[:10],
        log_line_ids=[l.id for l in logs])
    db.add(analysis)
    await db.commit()
    return analysis
