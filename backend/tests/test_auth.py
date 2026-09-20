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
from app.security import hash_password, verify_password

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


def test_login_rate_limit_is_configurable_per_deployment(auth_env, monkeypatch):
    client, _ = auth_env
    monkeypatch.setenv("LOGIN_RATE_LIMIT", "3/minute")
    codes = [
        client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong"}
        ).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]


def test_invalid_login_rate_limit_fails_at_startup(monkeypatch):
    from app.config import load_runtime_config

    monkeypatch.setenv("LOGIN_RATE_LIMIT", "lots")
    with pytest.raises(ValueError, match="LOGIN_RATE_LIMIT"):
        load_runtime_config()


def test_api_never_exposes_ground_truth_labels(auth_env):
    """Analysts must not be able to see which transactions are simulated attacks."""
    from app.api.websocket import build_transaction_message
    from app.models import Transaction

    client, Session = auth_env
    with Session() as db:
        db.add(Account(id="peer@ybl", created_at=datetime.now(), last_active_at=datetime.now()))
        db.add(
            Transaction(
                sender_id="peer@ybl", receiver_id="case@ybl", amount=10.0,
                timestamp=datetime.now(), is_synthetic_attack=True,
            )
        )
        db.commit()
        tx = db.scalar(select(Transaction))
        assert "is_synthetic_attack" not in build_transaction_message(tx)["data"]

    token = login(client, "analyst")
    body = client.get("/api/accounts/case@ybl", headers=bearer(token)).json()
    assert body["transactions"], body
    assert all("is_synthetic_attack" not in t for t in body["transactions"])
    assert "is_synthetic_attack" not in str(body)
    # case@ybl has the open alert; its counterparty peer@ybl does not.
    assert body["connected_accounts"] == ["peer@ybl"]
    assert body["connected_accounts_with_open_alerts"] == []
    peer = client.get("/api/accounts/peer@ybl", headers=bearer(token)).json()
    assert peer["connected_accounts_with_open_alerts"] == ["case@ybl"]


def test_live_feed_closes_when_the_session_expires(auth_env, monkeypatch):
    """The token is only checked at the handshake; expiry must still end the stream."""
    import time as _time

    client, _ = auth_env
    monkeypatch.setenv("SESSION_TTL_SECONDS", "2")
    token = login(client, "analyst")
    started = _time.monotonic()
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        message = ws.receive()
    assert message["type"] == "websocket.close"
    assert message["code"] == 1008
    assert _time.monotonic() - started < 5


@pytest.mark.parametrize(
    ("secret", "origins", "message"),
    [
        ("", "https://app.example.com", "must be set"),
        ("replace-with-a-long-random-secret-in-production", "https://app.example.com", "published example"),
        ("short-secret", "https://app.example.com", "at least 32"),
        ("x" * 48, "*", "explicit origins"),
        ("x" * 48, "https://app.example.com,*", "explicit origins"),
    ],
)
def test_production_refuses_unsafe_configuration(monkeypatch, secret, origins, message):
    from app.config import load_runtime_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", secret)
    monkeypatch.setenv("ALLOWED_ORIGINS", origins)
    with pytest.raises(ValueError, match=message):
        load_runtime_config()


def test_production_accepts_a_strong_secret_and_explicit_origins(monkeypatch):
    from app.config import load_runtime_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    config = load_runtime_config()
    assert config.allowed_origins == ("https://app.example.com",)


def test_development_still_runs_without_a_secret(monkeypatch):
    from app.config import load_runtime_config

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert len(load_runtime_config().session_secret) >= 32


def test_logout_revokes_the_token_server_side(auth_env):
    """Logout must outlive the client: the same token stops working at once."""
    client, _ = auth_env
    token = login(client, "analyst")
    other = login(client, "analyst")
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200

    assert client.post("/api/auth/logout", headers=bearer(token)).status_code == 204

    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert client.get("/api/alerts", headers=bearer(token)).status_code == 401
    # Only that session: a second login is untouched.
    assert client.get("/api/auth/me", headers=bearer(other)).status_code == 200
    # Idempotent, and harmless without a token.
    assert client.post("/api/auth/logout", headers=bearer(token)).status_code == 204
    assert client.post("/api/auth/logout").status_code == 204


def test_revoked_token_is_refused_by_the_websocket_too(auth_env):
    client, _ = auth_env
    token = login(client, "analyst")
    with client.websocket_connect(f"/ws/live-feed?token={token}") as ws:
        assert ws is not None
    client.post("/api/auth/logout", headers=bearer(token))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/live-feed?token={token}"):
            pass


def test_logout_clears_revocations_that_have_already_expired(auth_env):
    """The table only needs rows for tokens that could still be presented."""
    from datetime import timedelta

    from app.models import RevokedToken

    client, Session = auth_env
    with Session() as db:
        db.add(
            RevokedToken(
                jti="stale-entry",
                user_id=1,
                expires_at=datetime.now() - timedelta(days=1),
            )
        )
        db.commit()

    client.post("/api/auth/logout", headers=bearer(login(client, "analyst")))

    with Session() as db:
        assert db.get(RevokedToken, "stale-entry") is None
        assert db.query(RevokedToken).count() == 1


@pytest.mark.parametrize(
    "environment, expected",
    [("production", "None None None"), ("development", "/docs /redoc /openapi.json")],
)
def test_api_schema_and_docs_are_production_gated(environment, expected):
    """Checked in a subprocess: the app is built once, at import time."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main as m; print(m.app.docs_url, m.app.redoc_url, m.app.openapi_url)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ENVIRONMENT": environment,
            "SESSION_SECRET": "s" * 48,
            "ALLOWED_ORIGINS": "https://app.example.com",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def _provision(tmp_path, password_env_value, *, env_name="ADMIN_PASSWORD"):
    """Run scripts/create_user.py exactly as render.yaml's startCommand does.

    stdin is closed: if the script ever falls back to prompting on a host with
    no shell, the deploy hangs instead of booting, so the test must prove it
    never asks.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "SESSION_SECRET": "s" * 48,
        "ALLOWED_ORIGINS": "https://app.example.com",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'provision.db'}",
    }
    if password_env_value is None:
        env.pop(env_name, None)
    else:
        env[env_name] = password_env_value
    return subprocess.run(
        [
            sys.executable,
            "scripts/create_user.py",
            "--username",
            "admin",
            "--role",
            "admin",
            "--password-env",
            env_name,
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        env=env,
    )


def test_admin_can_be_provisioned_without_a_shell(tmp_path):
    """The deployment's only path to a first login, so it is tested directly."""
    first = _provision(tmp_path, "first-password-1234")
    assert first.returncode == 0, first.stderr
    assert "Created admin user 'admin'" in first.stdout

    # render.yaml runs this on every boot, so a second run must not fail, and
    # must leave the password usable.
    second = _provision(tmp_path, "second-password-5678")
    assert second.returncode == 0, second.stderr
    assert "Updated admin user 'admin'" in second.stdout

    engine = create_engine(f"sqlite:///{tmp_path / 'provision.db'}")
    with sessionmaker(bind=engine)() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user is not None and user.role == "admin"
        assert verify_password("second-password-5678", user.password_hash)
        assert not verify_password("first-password-1234", user.password_hash)


def test_provisioning_fails_loudly_when_the_password_variable_is_missing(tmp_path):
    """Better a failed deploy than a service booting with no way to log in."""
    result = _provision(tmp_path, None)
    assert result.returncode == 2
    assert "ADMIN_PASSWORD is unset or empty" in result.stderr
