"""
Graph-context structural scoring for peripheral (1–2 transaction) accounts.

These accounts are excluded from Mahalanobis GDI because velocity, burstiness,
and amount_entropy are degenerate at low n. Instead we flag them when they
exhibit a fan-in/fan-out leg pattern (in_degree/out_degree directionality)
connected to an account already in the current cycle's top-anomaly set.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.detection.features import (
    WINDOW_MINUTES,
    get_active_accounts,
)
from app.models import Transaction

DETECTION_METHOD = "peripheral_structural"
PATTERN_TYPE = "peripheral_structural"

# Score components on 0–5 scale (aligned with fused_score display range).
HUB_CONNECTION_BASE = 3.5
PATTERN_CONSISTENCY_BONUS = 1.0
MIN_QUALIFYING_SCORE = 3.5


def _window_bounds(
    as_of: datetime, window_minutes: int
) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def _account_transactions(
    db: Session,
    account_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                or_(
                    Transaction.sender_id == account_id,
                    Transaction.receiver_id == account_id,
                ),
            )
        )
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
    )
    return list(db.scalars(stmt).all())


def extract_structural_features(
    db: Session,
    account_id: str,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> dict | None:
    """Structural features only, for accounts with 1–2 transactions in the window."""
    window_start, window_end = _window_bounds(as_of, window_minutes)
    transactions = _account_transactions(db, account_id, window_start, window_end)
    total_count = len(transactions)
    if total_count < 1 or total_count > 2:
        return None

    inbound = [tx for tx in transactions if tx.receiver_id == account_id]
    outbound = [tx for tx in transactions if tx.sender_id == account_id]

    in_degree = len({tx.sender_id for tx in inbound})
    out_degree = len({tx.receiver_id for tx in outbound})

    inbound_from: dict[str, list[Transaction]] = {}
    outbound_to: dict[str, list[Transaction]] = {}
    for tx in inbound:
        inbound_from.setdefault(tx.sender_id, []).append(tx)
    for tx in outbound:
        outbound_to.setdefault(tx.receiver_id, []).append(tx)

    return {
        "account_id": account_id,
        "tx_count": total_count,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "in_count": len(inbound),
        "out_count": len(outbound),
        "inbound_from": inbound_from,
        "outbound_to": outbound_to,
    }


def _fan_in_sender_to_hub(features: dict, hub_id: str) -> bool:
    """Single/few outbound payments into a flagged hub, minimal inbound."""
    return (
        hub_id in features["outbound_to"]
        and features["in_degree"] == 0
        and features["out_degree"] >= 1
    )


def _fan_out_receiver_from_hub(features: dict, hub_id: str) -> bool:
    """Inbound from a flagged hub, minimal outbound (fan-out leg)."""
    return (
        hub_id in features["inbound_from"]
        and features["out_degree"] == 0
        and features["in_degree"] >= 1
    )


def _compute_peripheral_score(
    features: dict,
    hub_id: str,
    leg_pattern: str,
) -> float:
    score = HUB_CONNECTION_BASE
    if leg_pattern in ("fan_in_sender", "fan_out_receiver"):
        score += PATTERN_CONSISTENCY_BONUS
    return min(5.0, score)


def score_peripheral_accounts(
    db: Session,
    as_of: datetime,
    window_minutes: int,
    top_anomaly_accounts: set[str],
) -> list[dict]:
    """
    Score 1–2 tx accounts connected to already-selected hubs.

    ``top_anomaly_accounts`` must be the main pipeline's alert set. In the
    live cycle that is the **union of independent per-scale top-k** from
    ``compute_fused_scores_multiscale`` — never a second global cut on
    max-pooled 15m/60m percentiles. This function does not re-rank hubs;
    if no hub is in that set, no spoke can fire.
    """
    if not top_anomaly_accounts:
        return []

    window_start, window_end = _window_bounds(as_of, window_minutes)
    active = get_active_accounts(db, as_of, window_minutes)
    results: list[dict] = []

    for account_id in active:
        if account_id in top_anomaly_accounts:
            continue

        features = extract_structural_features(db, account_id, as_of, window_minutes)
        if features is None:
            continue

        best: dict | None = None
        for hub_id in top_anomaly_accounts:
            if hub_id == account_id:
                continue

            leg_pattern: str | None = None
            if _fan_in_sender_to_hub(features, hub_id):
                leg_pattern = "fan_in_sender"
            elif _fan_out_receiver_from_hub(features, hub_id):
                leg_pattern = "fan_out_receiver"

            if leg_pattern is None:
                continue

            score = _compute_peripheral_score(features, hub_id, leg_pattern)
            if score < MIN_QUALIFYING_SCORE:
                continue

            candidate = {
                "account_id": account_id,
                "peripheral_risk_score": score,
                "detection_method": DETECTION_METHOD,
                "linked_hub_account_id": hub_id,
                "leg_pattern": leg_pattern,
                "tx_count": features["tx_count"],
                "in_degree": features["in_degree"],
                "out_degree": features["out_degree"],
            }
            if best is None or score > best["peripheral_risk_score"]:
                best = candidate

        if best is not None:
            results.append(best)

    results.sort(key=lambda row: row["peripheral_risk_score"], reverse=True)
    return results


def build_peripheral_explanation(peripheral: dict) -> dict:
    hub = peripheral["linked_hub_account_id"]
    pattern = peripheral["leg_pattern"]
    if pattern == "fan_in_sender":
        role = f"fan-in sender paying into flagged hub {hub}"
    else:
        role = f"fan-out receiver paid from flagged hub {hub}"

    return {
        "account_id": peripheral["account_id"],
        "detection_method": DETECTION_METHOD,
        "fused_score": float(peripheral["peripheral_risk_score"]),
        "confidence": "low",
        "gdi_score": 0.0,
        "gdi_percentile": 0.0,
        "ring_risk_score": 0.0,
        "ring_percentile": 0.0,
        "community_id": None,
        "ring_id": peripheral.get("ring_id"),
        "primary_reason": (
            f"Peripheral structural alert: {role} "
            f"({peripheral['tx_count']} tx in window; "
            f"in_deg={peripheral['in_degree']}, out_deg={peripheral['out_degree']}). "
            "Thinner evidence than full Mahalanobis+ring scoring."
        ),
        "layer1_breakdown": [],
        "layer2_detail": None,
        "peripheral_detail": {
            "linked_hub_account_id": hub,
            "leg_pattern": pattern,
            "tx_count": peripheral["tx_count"],
            "in_degree": peripheral["in_degree"],
            "out_degree": peripheral["out_degree"],
        },
    }


def peripheral_as_fused_result(peripheral: dict) -> dict:
    """Shape expected by create_alert_if_needed."""
    return {
        "account_id": peripheral["account_id"],
        "fused_score": float(peripheral["peripheral_risk_score"]),
        "confidence": "low",
        "gdi_score": 0.0,
        "ring_risk_score": 0.0,
        "gdi_percentile": 0.0,
        "ring_percentile": 0.0,
        "community_id": None,
        "feature_vector": None,
        "ring_info": None,
        "detection_method": DETECTION_METHOD,
        "detection_window": WINDOW_MINUTES,
        "ring_id": peripheral.get("ring_id"),
    }
