"""Runtime configuration loaded from environment variables.

Keep deployment-specific values here instead of embedding hosts, ports, or
origins in application modules.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from limits import parse as parse_rate_limit


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


def _rate_limit_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    try:
        parse_rate_limit(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a rate limit such as '10/minute'") from exc
    return value


_DEV_SESSION_SECRET = secrets.token_urlsafe(48)

MIN_PRODUCTION_SECRET_LENGTH = 32
# Anything that has ever been published as an example is public knowledge; a
# deployment signing JWTs with it lets anyone forge an admin session.
_PUBLISHED_EXAMPLE_SECRETS = frozenset(
    {"replace-with-a-long-random-secret-in-production"}
)


def _check_production_secrets(
    session_secret: str, allowed_origins: tuple[str, ...]
) -> None:
    if not session_secret:
        raise ValueError("SESSION_SECRET must be set when ENVIRONMENT=production")
    if session_secret in _PUBLISHED_EXAMPLE_SECRETS or "replace-with" in session_secret:
        raise ValueError(
            "SESSION_SECRET is still the published example value; generate one with "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if len(session_secret) < MIN_PRODUCTION_SECRET_LENGTH:
        raise ValueError(
            f"SESSION_SECRET must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters "
            "when ENVIRONMENT=production"
        )
    if "*" in allowed_origins:
        # With credentials enabled, Starlette echoes any requesting origin.
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins when ENVIRONMENT=production, not '*'"
        )


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    allowed_origins: tuple[str, ...]
    environment: str
    session_secret: str
    session_ttl_seconds: int
    login_rate_limit: str


def load_runtime_config() -> RuntimeConfig:
    """Load configuration after dotenv has populated the environment."""
    database_url = os.getenv("DATABASE_URL", "sqlite:///./graphdrift.db").strip()
    if not database_url:
        raise ValueError("DATABASE_URL must not be empty")
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    session_secret = os.getenv("SESSION_SECRET", "").strip()
    allowed_origins = _csv_env("ALLOWED_ORIGINS")
    if environment == "production":
        _check_production_secrets(session_secret, allowed_origins)
    if not session_secret:
        session_secret = _DEV_SESSION_SECRET
    return RuntimeConfig(
        database_url=database_url,
        allowed_origins=allowed_origins,
        environment=environment,
        session_secret=session_secret,
        session_ttl_seconds=_positive_int_env("SESSION_TTL_SECONDS", 12 * 60 * 60),
        # Per client IP. Behind a reverse proxy, set FORWARDED_ALLOW_IPS so uvicorn
        # reports the real client, or every user shares one bucket.
        login_rate_limit=_rate_limit_env("LOGIN_RATE_LIMIT", "10/minute"),
    )
