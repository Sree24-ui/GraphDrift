"""Runtime configuration loaded from environment variables.

Keep deployment-specific values here instead of embedding hosts, ports, or
origins in application modules.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    )


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


_DEV_SESSION_SECRET = secrets.token_urlsafe(48)


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    allowed_origins: tuple[str, ...]
    environment: str
    session_secret: str
    session_ttl_seconds: int


def load_runtime_config() -> RuntimeConfig:
    """Load configuration after dotenv has populated the environment."""
    database_url = os.getenv("DATABASE_URL", "sqlite:///./graphdrift.db").strip()
    if not database_url:
        raise ValueError("DATABASE_URL must not be empty")
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    session_secret = os.getenv("SESSION_SECRET", "").strip()
    if not session_secret:
        if environment == "production":
            raise ValueError("SESSION_SECRET must be set when ENVIRONMENT=production")
        session_secret = _DEV_SESSION_SECRET
    return RuntimeConfig(
        database_url=database_url,
        allowed_origins=_csv_env("ALLOWED_ORIGINS"),
        environment=environment,
        session_secret=session_secret,
        session_ttl_seconds=_positive_int_env("SESSION_TTL_SECONDS", 12 * 60 * 60),
    )
