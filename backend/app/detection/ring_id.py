"""Deterministic ring case IDs for grouping alerts from the same mule community."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Alert

OPEN_ALERT_STATUSES = ("new", "reviewing")
TERMINAL_ALERT_STATUSES = ("confirmed", "false_positive", "auto_closed")


def make_ring_id(
    *,
    hub_account_id: str | None = None,
    member_accounts: Iterable[str] | None = None,
    occurrence: int = 1,
) -> str:
    """
    Stable ID stem for a hub. Occurrence 1 is ``rng_{hash}``; later closed-then-
    reopened cases use ``rng_{hash}_{n}`` so they do not merge with the prior case.
    """
    if hub_account_id:
        key = hub_account_id.strip().lower()
    else:
        members = sorted(
            {account.strip().lower() for account in (member_accounts or []) if account}
        )
        if not members:
            raise ValueError("make_ring_id requires a hub or at least one member")
        key = "|".join(members)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    if occurrence <= 1:
        return f"rng_{digest}"
    return f"rng_{digest}_{occurrence}"


def ring_id_from_ring_info(ring_info: dict | None) -> str | None:
    if not ring_info:
        return None
    hub = ring_info.get("hub_account_id")
    members = ring_info.get("member_accounts") or []
    if not hub and not members:
        return None
    return make_ring_id(hub_account_id=hub, member_accounts=members)


def _hub_ring_prefix(hub_account_id: str) -> str:
    return make_ring_id(hub_account_id=hub_account_id, occurrence=1)


def _ring_belongs_to_hub(ring_id: str, prefix: str) -> bool:
    return ring_id == prefix or ring_id.startswith(prefix + "_")


def _existing_ring_ids_for_hub(db: Session, hub_account_id: str) -> set[str]:
    prefix = _hub_ring_prefix(hub_account_id)
    rows = db.scalars(
        select(Alert.ring_id).where(
            Alert.ring_id.isnot(None),
            or_(Alert.ring_id == prefix, Alert.ring_id.like(f"{prefix}_%")),
        )
    ).all()
    return {row for row in rows if row and _ring_belongs_to_hub(row, prefix)}


def get_or_create_ring_id(db: Session, hub_account_id: str) -> str:
    """
    Reuse the hub's ring_id while any member alert is still open (new/reviewing).

    If every alert on that hub's most recent ring is terminal, mint a new id
    with a monotonic suffix so a later fraud event is a new case.
    """
    hub = hub_account_id.strip()
    prefix = _hub_ring_prefix(hub)

    open_ring_id = db.scalar(
        select(Alert.ring_id)
        .where(
            Alert.status.in_(OPEN_ALERT_STATUSES),
            Alert.ring_id.isnot(None),
            or_(Alert.ring_id == prefix, Alert.ring_id.like(f"{prefix}_%")),
        )
        .order_by(Alert.detected_at.desc())
        .limit(1)
    )
    if open_ring_id:
        return str(open_ring_id)

    existing = _existing_ring_ids_for_hub(db, hub)
    if prefix not in existing:
        return prefix

    occurrence = 2
    while make_ring_id(hub_account_id=hub, occurrence=occurrence) in existing:
        occurrence += 1
    return make_ring_id(hub_account_id=hub, occurrence=occurrence)


def infer_member_role(
    account_id: str,
    *,
    hub_account_id: str | None,
    pattern_type: str | None,
    feature_breakdown: dict | None,
) -> str:
    breakdown = feature_breakdown or {}
    peripheral = breakdown.get("peripheral_detail") or {}
    leg = peripheral.get("leg_pattern")
    layer2 = breakdown.get("layer2_detail") or {}

    if hub_account_id and account_id == hub_account_id:
        return "hub"
    if pattern_type == "peripheral_structural" or leg:
        if leg == "fan_in_sender":
            return "fan-in"
        if leg == "fan_out_receiver":
            return "fan-out"
        return "peripheral"
    if not hub_account_id and layer2.get("is_hub_account"):
        return "hub"
    return "core"
