"""Provisioned-user authentication endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.schemas import AuthLoginRequest, AuthSessionResponse, CurrentUserResponse
from app.models import User
from app.rate_limit import limiter
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid username or password",
)


@router.post("/login", response_model=AuthSessionResponse)
@limiter.limit("10/minute")
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
def logout() -> None:
    """Stateless sessions are invalidated by the client discarding its JWT."""


@router.get("/me", response_model=CurrentUserResponse)
def me(current_user: User = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,  # type: ignore[arg-type]
    )
