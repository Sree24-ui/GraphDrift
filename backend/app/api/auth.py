"""Provisioned-user authentication endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import bearer_scheme, get_current_user, get_db
from app.api.schemas import AuthLoginRequest, AuthSessionResponse, CurrentUserResponse
from app.config import load_runtime_config
from app.models import RevokedToken, User
from app.rate_limit import limiter
from app.security import create_access_token, decode_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid username or password",
)


@router.post("/login", response_model=AuthSessionResponse)
@limiter.limit(lambda: load_runtime_config().login_rate_limit)
def login(
    request: Request,
    body: AuthLoginRequest,
    db: Session = Depends(get_db),
) -> AuthSessionResponse:
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise INVALID_CREDENTIALS
    user.last_login_at = datetime.now()
    db.commit()
    return AuthSessionResponse(
        access_token=create_access_token(user),
        token_type="bearer",
        username=user.username,
        role=user.role,  # type: ignore[arg-type]
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> None:
    """Revoke this token server-side, so logout outlives the client.

    Idempotent: an absent, malformed or already-expired token is a no-op, since
    there is nothing left to revoke.
    """
    if credentials is None:
        return
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        return
    jti = payload.get("jti")
    if not jti:
        return
    now = datetime.now(timezone.utc)
    # Housekeeping on the only write path: a revocation is dead weight once the
    # token it names would have expired anyway.
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now.replace(tzinfo=None)))
    if db.get(RevokedToken, jti) is None:
        db.add(
            RevokedToken(
                jti=jti,
                user_id=int(payload.get("sub", 0)),
                expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc).replace(tzinfo=None),
            )
        )
    db.commit()


@router.get("/me", response_model=CurrentUserResponse)
def me(current_user: User = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,  # type: ignore[arg-type]
    )
