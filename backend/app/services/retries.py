"""Retry delay calculation: fixed, linear, exponential — with optional jitter,
max delay cap and error-category awareness."""
import random

VALID_STRATEGIES = ("fixed", "linear", "exponential")

NON_RETRYABLE_CATEGORIES = {"non_retryable", "validation", "cancelled"}


def normalize_policy(policy: dict | None, fallback: dict | None = None) -> dict:
    p = dict(fallback or {"strategy": "exponential", "base_delay": 5,
                          "max_delay": 300, "jitter": True})
    if policy:
        p.update({k: v for k, v in policy.items() if v is not None})
    if p.get("strategy") not in VALID_STRATEGIES:
        raise ValueError(f"invalid retry strategy: {p.get('strategy')!r}")
    p["base_delay"] = max(0.0, float(p.get("base_delay", 5)))
    p["max_delay"] = max(p["base_delay"], float(p.get("max_delay", 300)))
    p["jitter"] = bool(p.get("jitter", False))
    return p


def compute_delay(policy: dict, attempt: int, *, rng: random.Random | None = None) -> float:
    """Delay before retry following ``attempt`` (1-based failed attempt)."""
    base, max_delay = policy["base_delay"], policy["max_delay"]
    strategy = policy["strategy"]
    if strategy == "fixed":
        delay = base
    elif strategy == "linear":
        delay = base * attempt
    else:  # exponential
        delay = base * (2 ** (attempt - 1))
    delay = min(delay, max_delay)
    if policy.get("jitter"):
        r = rng or random
        delay = delay * (0.5 + r.random() * 0.5)  # 50-100% of computed delay
    return round(delay, 3)


def is_retryable(error_category: str | None) -> bool:
    return (error_category or "retryable") not in NON_RETRYABLE_CATEGORIES
