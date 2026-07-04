"""Explicit, validated job state machine.

Every mutation of ``Job.state`` MUST go through ``transition()`` so that an
invalid edge can never be recorded and every change leaves an audit row in
``job_state_transitions``.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Job, JobStateTransition, utcnow

# from_state -> allowed to_states
ALLOWED: dict[str, set[str]] = {
    "CREATED":         {"QUEUED", "SCHEDULED", "BLOCKED", "CANCELLED"},
    "QUEUED":          {"CLAIMED", "CANCELLED", "SCHEDULED"},
    "SCHEDULED":       {"QUEUED", "CANCELLED"},
    "BLOCKED":         {"QUEUED", "CANCELLED", "SKIPPED"},
    "CLAIMED":         {"RUNNING", "QUEUED", "RETRY_SCHEDULED", "CANCELLED",
                        "FAILED", "DEAD_LETTERED"},
    "RUNNING":         {"COMPLETED", "FAILED", "RETRY_SCHEDULED", "TIMED_OUT",
                        "CANCEL_REQUESTED", "CANCELLED", "QUEUED", "DEAD_LETTERED"},
    "RETRY_SCHEDULED": {"QUEUED", "CANCELLED", "DEAD_LETTERED"},
    "CANCEL_REQUESTED": {"CANCELLED", "COMPLETED", "FAILED", "TIMED_OUT",
                         "RETRY_SCHEDULED", "DEAD_LETTERED"},
    "FAILED":          {"QUEUED", "DEAD_LETTERED"},          # manual retry / DLQ
    "TIMED_OUT":       {"QUEUED", "RETRY_SCHEDULED", "DEAD_LETTERED"},
    "DEAD_LETTERED":   {"QUEUED"},                            # DLQ replay
    "COMPLETED":       set(),
    "CANCELLED":       {"QUEUED"},                            # clone/replay path uses new job
    "SKIPPED":         set(),
}

TERMINAL = {"COMPLETED", "CANCELLED", "DEAD_LETTERED", "SKIPPED"}


class InvalidTransition(Exception):
    def __init__(self, from_state: str, to_state: str):
        self.from_state, self.to_state = from_state, to_state
        super().__init__(f"Invalid job state transition {from_state} -> {to_state}")


def validate(from_state: str, to_state: str) -> None:
    if to_state not in ALLOWED.get(from_state, set()):
        raise InvalidTransition(from_state, to_state)


async def transition(
    session: AsyncSession, job: Job, to_state: str, *,
    reason: str | None = None, worker_id=None, attempt: int | None = None,
) -> None:
    """Validate and apply a state change, recording the transition row.
    Caller owns the surrounding transaction."""
    validate(job.state, to_state)
    session.add(JobStateTransition(
        job_id=job.id, from_state=job.state, to_state=to_state, at=utcnow(),
        worker_id=worker_id, attempt_number=attempt if attempt is not None else job.attempt_count,
        reason=reason, correlation_id=job.correlation_id,
    ))
    job.state = to_state
