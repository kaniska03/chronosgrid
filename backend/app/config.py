"""Application configuration with environment validation."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ChronosGrid"
    environment: str = "development"

    # PostgreSQL is the source of truth in deployed environments.
    # SQLite is supported only for lightweight local test runs.
    database_url: str = "sqlite+aiosqlite:///./chronosgrid.db"

    # Optional Redis (cross-process event fan-out; system works without it)
    redis_url: str | None = None

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    # Scheduler tuning
    lease_seconds: int = 30
    heartbeat_seconds: int = 10
    worker_offline_after_seconds: int = 45
    reaper_interval_seconds: float = 2.0
    promoter_interval_seconds: float = 1.0

    # Fairness: +1 effective priority per interval waited, capped
    priority_aging_interval_seconds: int = 60
    priority_aging_max_boost: int = 5

    # Limits
    max_payload_bytes: int = 64 * 1024
    max_batch_size: int = 500
    api_rate_limit_per_minute: int = 240

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # AI assistant (optional; scheduler never depends on it)
    anthropic_api_key: str | None = None
    ai_model: str = "claude-haiku-4-5"

    seed_demo_data: bool = True
    demo_email: str = "demo@chronosgrid.dev"
    demo_password: str = "Demo@1234"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
