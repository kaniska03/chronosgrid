"""Password hashing (PBKDF2-HMAC-SHA256), JWT issuance, API-key handling.

PBKDF2 via hashlib keeps us free of native wheels while meeting OWASP
guidance (600k iterations, per-user 16-byte salt).
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import jwt

from .config import get_settings
from .models import utcnow

PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _token(sub: str, token_type: str, ttl: timedelta) -> str:
    s = get_settings()
    now = utcnow()
    return jwt.encode(
        {"sub": sub, "type": token_type, "iat": now, "exp": now + ttl,
         "jti": str(uuid.uuid4())},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )


def create_access_token(user_id) -> str:
    return _token(str(user_id), "access",
                  timedelta(minutes=get_settings().access_token_minutes))


def create_refresh_token(user_id) -> str:
    return _token(str(user_id), "refresh",
                  timedelta(days=get_settings().refresh_token_days))


def decode_token(token: str, expected_type: str = "access") -> dict:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# ---- API keys: full key shown once; only SHA-256 digest stored ------------ #
def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, sha256_hash)."""
    raw = f"cg_{secrets.token_urlsafe(32)}"
    return raw, raw[:10], hashlib.sha256(raw.encode()).hexdigest()


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def sign_webhook(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
