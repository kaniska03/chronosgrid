"""Sensitive-data masking for payloads, logs and AI calls."""
import re

SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|auth|credential|"
    r"private[_-]?key|ssn|credit[_-]?card|card[_-]?number|cvv)", re.I)

MASK = "***REDACTED***"


def mask_sensitive(value, _depth: int = 0):
    """Recursively mask values under sensitive-looking keys."""
    if _depth > 8:
        return value
    if isinstance(value, dict):
        return {k: (MASK if SENSITIVE_KEY_RE.search(str(k))
                    else mask_sensitive(v, _depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_sensitive(v, _depth + 1) for v in value]
    return value
