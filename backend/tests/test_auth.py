"""Authentication, authorization, and reviewer audit integration tests."""

from __future__ import annotations

from datetime import datetime

import jwt
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db import Base
from app.main import app
from app.models import Account, Alert, RingReviewAction, User
from app.rate_limit import limiter
from app.security import hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def auth_env(monkeypatch):
    limiter.reset()
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-enough-entropy")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        db.add_all(
            [
                User(
                    username="admin",
                    password_hash=hash_password(PASSWORD),
                    role="admin",
                ),
                User(
                    username="analyst",
                    password_hash=hash_password(PASSWORD),
                    role="analyst",
                ),
                Account(
                    id="case@ybl",
                    created_at=datetime(2026, 8, 30),
                    last_active_at=datetime(2026, 8, 30),
                ),
            ]
        )
        db.flush()
        db.add(
            Alert(
                account_id="case@ybl",
                risk_score=4.2,
                pattern_type="node_anomaly",
                detected_at=datetime(2026, 8, 30),
                updated_at=datetime(2026, 8, 30),
                status="new",
                confidence="high",
                ring_id="ring-test",
            )
        )
        db.commit()
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()
        limiter.reset()
        engine.dispose()


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_success_failure_and_me(auth_env):
    client, Session = auth_env
    bad = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert bad.status_code == 401

    token = login(client, "admin")
    me = client.get("/api/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["role"] == "admin"
    assert me.headers["X-Content-Type-Options"] == "nosniff"
    assert me.headers["X-Frame-Options"] == "DENY"
    assert me.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    with Session() as db:
        assert db.scalar(select(User).where(User.username == "admin")).last_login_at


def test_missing_token_is_401_and_analyst_admin_action_is_403(auth_env):
    client, _ = auth_env
    assert client.patch("/api/alerts/1", json={"status": "confirmed"}).status_code == 401
    analyst = login(client, "analyst")
    forbidden = client.patch(
        "/api/settings",
        json={"alert_top_percent": 4.5},
        headers=bearer(analyst),
    )
    assert forbidden.status_code == 403


def test_expired_token_is_401(auth_env):
    client, Session = auth_env
    with Session() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        expired = jwt.encode(
            {"sub": str(user.id), "role": "admin", "exp": 1},
            "test-session-secret-with-enough-entropy",
            algorithm="HS256",
        )
    assert client.get("/api/auth/me", headers=bearer(expired)).status_code == 401


def test_login_is_rate_limited(auth_env):
    client, _ = auth_env
    responses = [
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        for _ in range(11)
    ]
    assert responses[9].status_code == 401
    assert responses[10].status_code == 429


def test_alert_review_records_and_exposes_username(auth_env):
    client, Session = auth_env
    analyst = login(client, "analyst")
    response = client.patch(
        "/api/alerts/1",
        json={"status": "confirmed", "analyst_notes": "verified transfer chain"},
        headers=bearer(analyst),
    )
    assert response.status_code == 200
    assert response.json()["reviewed_by_username"] == "analyst"
    with Session() as db:
        alert = db.get(Alert, 1)
        reviewer = db.scalar(select(User).where(User.username == "analyst"))
        assert alert.reviewed_by_user_id == reviewer.id


def test_ring_bulk_review_records_alert_and_ring_audit(auth_env):
    client, Session = auth_env
    admin = login(client, "admin")
    response = client.patch(
        "/api/rings/ring-test",
        json={"status": "confirmed"},
        headers=bearer(admin),
    )
    assert response.status_code == 200
    assert response.json()["ring"]["reviewed_by_username"] == "admin"
    with Session() as db:
        action = db.scalar(select(RingReviewAction))
        reviewer = db.scalar(select(User).where(User.username == "admin"))
        assert action.reviewed_by_user_id == reviewer.id


# Every read used to be public. These assert they are not, so the gap cannot
# silently reopen.
READ_ENDPOINTS = (
    "/api/alerts",
    "/api/alerts/1",
    "/api/rings",
    "/api/settings",
    "/api/accounts/case@ybl",
    "/api/graph/current",
    "/api/reports/summary",
    "/api/reports/export",
)


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_require_authentication(auth_env, path):
    client, _ = auth_env
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_succeed_for_analyst_and_admin(auth_env, path):
    client, _ = auth_env
    for username in ("analyst", "admin"):
        response = client.get(path, headers=bearer(login(client, username)))
        assert response.status_code == 200, (path, username, response.text)


def test_live_feed_websocket_requires_a_valid_token(auth_env):
    client, _ = auth_env
    for query in ("", "?token=not-a-jwt"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/live-feed{query}"):
                pass

    token = login(client, "analyst")
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        assert ws is not None
