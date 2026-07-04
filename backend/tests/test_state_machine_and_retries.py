"""State machine validity + retry delay mathematics (spec tests 4, 13-adjacent)."""
import random

import pytest

from app.services.retries import compute_delay, is_retryable, normalize_policy
from app.models import JOB_STATES
from app.state_machine import ALLOWED, InvalidTransition, validate


def test_all_states_covered():
    assert set(ALLOWED) == set(JOB_STATES)


def test_valid_transitions_pass():
    validate("QUEUED", "CLAIMED")
    validate("CLAIMED", "RUNNING")
    validate("RUNNING", "COMPLETED")
    validate("RUNNING", "RETRY_SCHEDULED")
    validate("RETRY_SCHEDULED", "QUEUED")
    validate("FAILED", "DEAD_LETTERED")


@pytest.mark.parametrize("frm,to", [
    ("COMPLETED", "RUNNING"), ("QUEUED", "COMPLETED"), ("CREATED", "RUNNING"),
    ("DEAD_LETTERED", "COMPLETED"), ("SKIPPED", "QUEUED"), ("QUEUED", "RUNNING"),
])
def test_invalid_transitions_rejected(frm, to):
    with pytest.raises(InvalidTransition):
        validate(frm, to)


def test_fixed_delay():
    p = normalize_policy({"strategy": "fixed", "base_delay": 7, "jitter": False})
    assert [compute_delay(p, a) for a in (1, 2, 5)] == [7, 7, 7]


def test_linear_backoff():
    p = normalize_policy({"strategy": "linear", "base_delay": 3,
                          "max_delay": 100, "jitter": False})
    assert [compute_delay(p, a) for a in (1, 2, 3, 4)] == [3, 6, 9, 12]


def test_exponential_backoff_with_cap():
    p = normalize_policy({"strategy": "exponential", "base_delay": 2,
                          "max_delay": 20, "jitter": False})
    assert [compute_delay(p, a) for a in (1, 2, 3, 4, 5)] == [2, 4, 8, 16, 20]


def test_jitter_bounds():
    p = normalize_policy({"strategy": "exponential", "base_delay": 8,
                          "max_delay": 300, "jitter": True})
    rng = random.Random(42)
    for attempt in range(1, 6):
        nominal = min(8 * 2 ** (attempt - 1), 300)
        for _ in range(50):
            d = compute_delay(p, attempt, rng=rng)
            assert nominal * 0.5 <= d <= nominal


def test_invalid_strategy_rejected():
    with pytest.raises(ValueError):
        normalize_policy({"strategy": "quantum"})


def test_error_categories():
    assert is_retryable("retryable")
    assert is_retryable(None)
    assert not is_retryable("non_retryable")
    assert not is_retryable("validation")
    assert not is_retryable("cancelled")
