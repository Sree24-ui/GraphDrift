"""Password hashing and short-lived signed API sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import load_runtime_config
from app.models import User

# argon2-cffi's defaults track the RFC 9106 recommended parameters; pinning our
# own would silently fall behind when the library raises them.
PASSWORD_HASHER = PasswordHasher()
# Fixed on purpose: making the algorithm configurable invites alg-confusion
# attacks, and every token here is both issued and verified by this service.
JWT_ALGORITHM = "HS256"
# A security floor, deliberately not configurable downward per deployment.
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def create_access_token(user: User) -> str:
    config = load_runtime_config()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(seconds=config.session_ttl_seconds),
    }
    return jwt.encode(payload, config.session_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    config = load_runtime_config()
    return jwt.decode(token, config.session_secret, algorithms=[JWT_ALGORITHM])
