"""Ring case ID assignment, grouping, and bulk status updates."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.api.rings import _build_ring_detail, update_ring
from app.api.schemas import RingStatusUpdate
from app.detection.fusion import create_alert_if_needed
from app.detection.ring_id import get_or_create_ring_id, make_ring_id
from app.detection.structural_pass import build_peripheral_explanation
from app.models import Account, Alert


def test_make_ring_id_is_deterministic():
    first = make_ring_id(hub_account_id="mule@ybl")
    second = make_ring_id(hub_account_id="Mule@ybl")
    other = make_ring_id(hub_account_id="other@ybl")
    assert first == second
    assert first.startswith("rng_")
    assert first != other


def _seed_account(db, account_id: str, now: datetime) -> None:
    db.add(Account(id=account_id, created_at=now, last_active_at=now))


def test_create_alert_sets_stable_ring_id(db_session):
    now = datetime(2026, 8, 16, 12, 0, 0)
    later = now + timedelta(seconds=45)
    _seed_account(db_session, "hub@ybl", now)
    db_session.commit()

    ring_id = make_ring_id(hub_account_id="hub@ybl")
    explanation = {
        "confidence": "high",
        "primary_reason": "ring",
        "ring_id": ring_id,
        "layer2_detail": {
            "hub_account_id": "hub@ybl",
            "is_hub_account": True,
            "member_accounts": ["hub@ybl", "a@ybl"],
            "hub_concentration": 0.8,
            "external_edge_ratio": 2.0,
            "reason": "hub-and-spoke",
        },
    }
    fused = {
        "account_id": "hub@ybl",
        "fused_score": 4.5,
        "confidence": "high",
        "gdi_score": 3.0,
        "ring_risk_score": 4.0,
        "gdi_percentile": 0.95,
        "ring_percentile": 0.95,
        "community_id": 1,
        "ring_id": ring_id,
        "ring_info": {
            "hub_account_id": "hub@ybl",
            "member_accounts": ["hub@ybl", "a@ybl"],
        },
    }

    created = create_alert_if_needed(
        db_session, "hub@ybl", fused, explanation, now, alert_threshold=0.0
    )
    skipped = create_alert_if_needed(
        db_session, "hub@ybl", fused, explanation, later, alert_threshold=0.0
    )
    assert created is not None
    assert created.action == "CREATE"
    assert created.alert.ring_id == ring_id
    assert skipped is None
    alert = db_session.get(Alert, created.alert.id)
    assert alert is not None
    assert alert.ring_id == ring_id
    assert alert.detected_at == now


def test_peripheral_inherits_hub_ring_id(db_session):
    now = datetime(2026, 8, 16, 12, 0, 0)
    _seed_account(db_session, "spoke@ybl", now)
    db_session.commit()
    hub_ring = make_ring_id(hub_account_id="hub@ybl")
    peripheral = {
        "account_id": "spoke@ybl",
        "peripheral_risk_score": 4.0,
        "linked_hub_account_id": "hub@ybl",
        "leg_pattern": "fan_in_sender",
        "tx_count": 1,
        "in_degree": 0,
        "out_degree": 1,
        "ring_id": hub_ring,
    }
    explanation = build_peripheral_explanation(peripheral)
    fused = {
        "account_id": "spoke@ybl",
        "fused_score": 4.0,
        "confidence": "low",
        "gdi_score": 0.0,
        "ring_risk_score": 0.0,
        "gdi_percentile": 0.0,
        "ring_percentile": 0.0,
        "community_id": None,
        "ring_id": hub_ring,
    }
    result = create_alert_if_needed(
        db_session, "spoke@ybl", fused, explanation, now, alert_threshold=0.0
    )
    assert result is not None
    assert result.alert.ring_id == hub_ring
    assert explanation["ring_id"] == hub_ring


def test_bulk_ring_confirm_cascades_and_skips_terminal(db_session):
    now = datetime(2026, 8, 16, 12, 0, 0)
    ring_id = make_ring_id(hub_account_id="hub@ybl")
    for acct in ("hub@ybl", "a@ybl", "b@ybl"):
        _seed_account(db_session, acct, now)
    db_session.add_all(
        [
            Alert(
                account_id="hub@ybl",
                risk_score=4.8,
                pattern_type="fan_in_fan_out",
                detected_at=now,
                updated_at=now,
                status="new",
                confidence="high",
                ring_id=ring_id,
                feature_breakdown={
                    "layer2_detail": {
                        "hub_account_id": "hub@ybl",
                        "member_accounts": ["hub@ybl", "a@ybl", "b@ybl"],
                        "hub_concentration": 0.9,
                        "external_edge_ratio": 3.0,
                        "reason": "test ring",
                    }
                },
            ),
            Alert(
                account_id="a@ybl",
                risk_score=3.1,
                pattern_type="community_ring",
                detected_at=now + timedelta(seconds=45),
                updated_at=now + timedelta(seconds=45),
                status="reviewing",
                confidence="medium",
                ring_id=ring_id,
                feature_breakdown={"layer2_detail": {"hub_account_id": "hub@ybl"}},
            ),
            Alert(
                account_id="b@ybl",
                risk_score=3.5,
                pattern_type="peripheral_structural",
                detected_at=now + timedelta(minutes=1),
                updated_at=now + timedelta(minutes=1),
                status="false_positive",
                confidence="low",
                ring_id=ring_id,
                feature_breakdown={
                    "peripheral_detail": {
                        "linked_hub_account_id": "hub@ybl",
                        "leg_pattern": "fan_in_sender",
                    }
                },
            ),
        ]
    )
    db_session.commit()

    from sqlalchemy import select

    result = update_ring(
        ring_id,
        RingStatusUpdate(status="confirmed"),
        db_session,
    )
    assert set(result.updated_alert_ids)
    rows = list(db_session.scalars(select(Alert).where(Alert.ring_id == ring_id)).all())
    statuses = {row.account_id: row.status for row in rows}
    assert statuses["hub@ybl"] == "confirmed"
    assert statuses["a@ybl"] == "confirmed"
    assert statuses["b@ybl"] == "false_positive"

    detail = _build_ring_detail(ring_id, rows)
    roles = {m.account_id: m.role for m in detail.members}
    assert roles["hub@ybl"] == "hub"
    assert roles["b@ybl"] == "fan-in"
    timestamps = {row.detected_at for row in rows}
    assert len(timestamps) >= 2


def _ring_alert(
    *,
    account_id: str,
    ring_id: str,
    now: datetime,
    status: str = "new",
    hub: str = "hub@ybl",
    pattern: str = "community_ring",
) -> Alert:
    return Alert(
        account_id=account_id,
        risk_score=4.0,
        pattern_type=pattern,
        detected_at=now,
        updated_at=now,
        status=status,
        confidence="high",
        ring_id=ring_id,
        feature_breakdown={"layer2_detail": {"hub_account_id": hub}},
    )


def test_get_or_create_reuses_open_ring_id(db_session):
    now = datetime(2026, 8, 16, 12, 0, 0)
    hub = "hub@ybl"
    _seed_account(db_session, hub, now)
    _seed_account(db_session, "spoke@ybl", now)
    ring_id = make_ring_id(hub_account_id=hub)
    db_session.add(_ring_alert(account_id=hub, ring_id=ring_id, now=now, hub=hub))
    db_session.commit()

    assert get_or_create_ring_id(db_session, hub) == ring_id
    later = now + timedelta(seconds=45)
    db_session.add(
        _ring_alert(
            account_id="spoke@ybl",
            ring_id=get_or_create_ring_id(db_session, hub),
            now=later,
            hub=hub,
        )
    )
    db_session.commit()
    assert get_or_create_ring_id(db_session, hub) == ring_id
    from sqlalchemy import select

    ids = {
        row.ring_id
        for row in db_session.scalars(select(Alert).where(Alert.account_id.in_([hub, "spoke@ybl"])))
    }
    assert ids == {ring_id}


def test_closed_ring_mints_new_id_and_leaves_old_members(db_session):
    now = datetime(2026, 8, 16, 12, 0, 0)
    hub = "hub@ybl"
    old_members = [hub, "old_a@ybl", "old_b@ybl"]
    new_members = [hub, "new_a@ybl", "new_b@ybl"]
    for acct in {*old_members, *new_members}:
        _seed_account(db_session, acct, now)
    closed_id = make_ring_id(hub_account_id=hub)
    db_session.add_all(
        [
            _ring_alert(
                account_id=acct,
                ring_id=closed_id,
                now=now,
                status="confirmed",
                hub=hub,
            )
            for acct in old_members
        ]
    )
    db_session.commit()

    minted = get_or_create_ring_id(db_session, hub)
    assert minted != closed_id
    assert minted == make_ring_id(hub_account_id=hub, occurrence=2)

    later = now + timedelta(minutes=20)
    for acct in new_members:
        explanation = {
            "confidence": "high",
            "primary_reason": "new ring",
            "layer2_detail": {
                "hub_account_id": hub,
                "member_accounts": new_members,
                "is_hub_account": acct == hub,
            },
        }
        fused = {
            "account_id": acct,
            "fused_score": 4.4,
            "confidence": "high",
            "gdi_score": 3.0,
            "ring_risk_score": 4.0,
            "gdi_percentile": 0.9,
            "ring_percentile": 0.9,
            "community_id": 2,
            "ring_info": {
                "hub_account_id": hub,
                "member_accounts": new_members,
            },
        }
        create_alert_if_needed(
            db_session, acct, fused, explanation, later, alert_threshold=0.0
        )

    from sqlalchemy import select

    old_rows = list(
        db_session.scalars(select(Alert).where(Alert.ring_id == closed_id)).all()
    )
    new_rows = list(
        db_session.scalars(select(Alert).where(Alert.ring_id == minted)).all()
    )
    assert {row.account_id for row in old_rows} == set(old_members)
    assert all(row.status == "confirmed" for row in old_rows)
    assert {row.account_id for row in new_rows} == set(new_members)
    assert all(row.status == "new" for row in new_rows)
    assert get_or_create_ring_id(db_session, hub) == minted
