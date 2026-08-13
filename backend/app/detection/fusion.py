"""
Two-layer fusion scoring: combines Layer 1 (node GDI) and Layer 2 (ring risk)
into a unified per-account assessment with explainability.

Fusion uses percentile ranks within the current detection cycle so neither
layer's absolute score scale dominates the combined result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
from scipy.stats import rankdata
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.detection.community import get_ring_alerts
from app.detection.features import WINDOW_MINUTES, extract_all_features, MIN_TRANSACTIONS_FOR_SCORING
from app.detection.lifecycle import auto_expire_stale_alerts
from app.detection.node_anomaly import compute_baseline, compute_gdi_scores, explain_score
from app.models import AccountScoreHistory, Alert, Transaction

from app.settings_store import get_alert_top_percentile

# Default alert percentile when settings store is at its initial value (top 5%).
DEFAULT_ALERT_TOP_PERCENTILE = 0.95

# In-memory debug flag for demo diagnostics (--debug).
_DEBUG_ALERT_CREATION = False

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
SCORE_ESCALATION_RELATIVE_THRESHOLD = 0.10

AlertAction = Literal["CREATE", "ESCALATE"]


@dataclass(frozen=True)
class AlertActionResult:
    alert: Alert
    action: AlertAction


# 50/50 weighting on percentile ranks: fusion reflects relative anomaly within
# the current window ("how unusual vs peers right now"), not arbitrary absolutes.
FUSION_GDI_WEIGHT = 0.5
FUSION_RING_WEIGHT = 0.5


def _window_bounds(
    as_of: datetime, window_minutes: int
) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def _percentile_ranks(values: list[float]) -> list[float]:
    """Map values to percentile ranks on [0, 1] using average rank tie-breaking."""
    if not values:
        return []
    if len(values) == 1:
        return [1.0]

    ranks = rankdata(values, method="average")
    return [float((rank - 1) / (len(values) - 1)) for rank in ranks]


def top_anomaly_budget(
    account_count: int,
    percentile: float | None = None,
) -> int:
    """How many accounts to flag for a given population size and percentile setting."""
    if account_count <= 0:
        return 0
    if percentile is None:
        percentile = get_alert_top_percentile()
    return max(1, int(np.ceil(account_count * (1 - percentile))))


def compute_alert_threshold(fused_results: list[dict]) -> float:
    """Return the fused_score of the lowest account in the top-anomaly budget."""
    if not fused_results:
        return 5.0

    k = top_anomaly_budget(len(fused_results))
    sorted_scores = sorted(
        (float(row["fused_score"]) for row in fused_results),
        reverse=True,
    )
    return sorted_scores[k - 1]


def select_top_anomaly_accounts(
    results: list[dict],
    score_key: str,
    *,
    percentile: float | None = None,
) -> tuple[set[str], float, int]:
    """
    Rank-based top-fraction selection (tie-safe).

    Uses exact top-k by score rather than ``score >= np.percentile`` which
    over-flags when many accounts share the same score (common with min tx=1).
    """
    if not results:
        return set(), 5.0, 0

    if percentile is None:
        percentile = get_alert_top_percentile()

    sorted_rows = sorted(
        results,
        key=lambda row: float(row[score_key]),
        reverse=True,
    )
    k = top_anomaly_budget(len(sorted_rows), percentile)
    selected = sorted_rows[:k]
    threshold = float(selected[-1][score_key])
    return {row["account_id"] for row in selected}, threshold, k


def _confidence_label(gdi_percentile: float, ring_percentile: float) -> str:
    gdi_strong = gdi_percentile > 0.9
    ring_strong = ring_percentile > 0.9

    if gdi_strong and ring_strong:
        return "high"
    if gdi_strong or ring_strong:
        return "medium"
    return "low"


def _build_ring_lookup(ring_alerts: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}

    for alert in ring_alerts:
        ring_risk_score = float(alert["risk_score"])
        for account_id in alert["member_accounts"]:
            existing = lookup.get(account_id)
            if existing is None or ring_risk_score > existing["ring_risk_score"]:
                lookup[account_id] = {
                    "community_id": int(alert["community_id"]),
                    "ring_risk_score": ring_risk_score,
                    "hub_account_id": alert.get("hub_account_id"),
                    "hub_concentration": float(alert.get("hub_concentration", 0.0)),
                    "member_count": int(alert["member_count"]),
                    "external_edge_ratio": float(alert.get("external_edge_ratio", 0.0)),
                    "formed_recently": bool(alert.get("formed_recently", False)),
                    "reason": str(alert.get("reason", "")),
                    "member_accounts": list(alert["member_accounts"]),
                }

    return lookup


def compute_fused_scores(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int = WINDOW_MINUTES,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
) -> list[dict]:
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    gdi_results = compute_gdi_scores(features)
    ring_alerts = get_ring_alerts(db, as_of, window_minutes)
    ring_lookup = _build_ring_lookup(ring_alerts)

    gdi_by_account = {row["account_id"]: row for row in gdi_results}
    account_ids = sorted(set(gdi_by_account) | set(ring_lookup))

    raw_rows: list[dict] = []
    for account_id in account_ids:
        gdi_row = gdi_by_account.get(account_id)
        gdi_score = float(gdi_row["gdi_score"]) if gdi_row else 0.0

        ring_info = ring_lookup.get(account_id)
        ring_risk_score = float(ring_info["ring_risk_score"]) if ring_info else 0.0
        community_id = int(ring_info["community_id"]) if ring_info else None

        raw_rows.append(
            {
                "account_id": account_id,
                "gdi_score": gdi_score,
                "ring_risk_score": ring_risk_score,
                "community_id": community_id,
                "feature_vector": gdi_row["feature_vector"] if gdi_row else None,
                "ring_info": ring_info,
            }
        )

    gdi_percentiles = _percentile_ranks([row["gdi_score"] for row in raw_rows])
    ring_percentiles = _percentile_ranks([row["ring_risk_score"] for row in raw_rows])

    fused_results: list[dict] = []
    for row, gdi_pct, ring_pct in zip(raw_rows, gdi_percentiles, ring_percentiles):
        fused_score = 5.0 * (
            (FUSION_GDI_WEIGHT * gdi_pct) + (FUSION_RING_WEIGHT * ring_pct)
        )

        fused_results.append(
            {
                "account_id": row["account_id"],
                "fused_score": float(fused_score),
                "gdi_percentile": float(gdi_pct),
                "ring_percentile": float(ring_pct),
                "confidence": _confidence_label(gdi_pct, ring_pct),
                "gdi_score": row["gdi_score"],
                "ring_risk_score": row["ring_risk_score"],
                "community_id": row["community_id"],
                "feature_vector": row["feature_vector"],
                "ring_info": row["ring_info"],
            }
        )

    fused_results.sort(key=lambda item: item["fused_score"], reverse=True)
    return fused_results


def build_explanation(
    account_id: str,
    fused_result: dict,
    db: Session,
    as_of: datetime,
) -> dict:
    features = extract_all_features(db, as_of)
    baseline = compute_baseline(features) if features else None

    layer1_breakdown: list[dict] = []
    if fused_result.get("feature_vector") and baseline is not None:
        layer1_breakdown = [
            {
                "feature": item["feature"],
                "contribution_pct": float(item["contribution_pct"]),
                "z_score": float(item["z_score"]),
            }
            for item in explain_score(fused_result["feature_vector"], baseline)
        ]

    ring_info = fused_result.get("ring_info")
    layer2_detail = None
    if ring_info is not None:
        layer2_detail = {
            "community_id": int(ring_info["community_id"]),
            "hub_account_id": ring_info.get("hub_account_id"),
            "hub_concentration": float(ring_info["hub_concentration"]),
            "member_count": int(ring_info["member_count"]),
            "ring_risk_score": float(ring_info["ring_risk_score"]),
            "ring_percentile": float(fused_result.get("ring_percentile", 0.0)),
            "external_edge_ratio": float(ring_info["external_edge_ratio"]),
            "formed_recently": bool(ring_info["formed_recently"]),
            "reason": str(ring_info["reason"]),
            "is_hub_account": account_id == ring_info.get("hub_account_id"),
        }

    gdi_pct = float(fused_result.get("gdi_percentile", 0.0))
    ring_pct = float(fused_result.get("ring_percentile", 0.0))

    if ring_pct > gdi_pct and layer2_detail is not None:
        primary_reason = layer2_detail["reason"]
        if layer1_breakdown:
            top = layer1_breakdown[0]
            primary_reason += (
                f"; individual anomaly: {top['feature']} contributed "
                f"{top['contribution_pct']:.1f}% of node score"
            )
    elif layer1_breakdown:
        top = layer1_breakdown[0]
        primary_reason = (
            f"Individual transaction pattern anomaly: {top['feature']} contributed "
            f"{top['contribution_pct']:.1f}% of node anomaly score"
        )
        if layer2_detail is not None:
            primary_reason += f"; also member of {layer2_detail['reason']}"
    elif layer2_detail is not None:
        primary_reason = layer2_detail["reason"]
    else:
        primary_reason = "Elevated relative anomaly vs current active accounts"

    return {
        "account_id": account_id,
        "fused_score": float(fused_result["fused_score"]),
        "confidence": str(fused_result["confidence"]),
        "gdi_score": float(fused_result["gdi_score"]),
        "gdi_percentile": float(gdi_pct),
        "ring_risk_score": float(fused_result["ring_risk_score"]),
        "ring_percentile": float(ring_pct),
        "community_id": fused_result.get("community_id"),
        "primary_reason": primary_reason,
        "layer1_breakdown": layer1_breakdown,
        "layer2_detail": layer2_detail,
    }


def _determine_pattern_type(explanation: dict) -> str:
    if explanation.get("detection_method") == "peripheral_structural":
        return "peripheral_structural"
    layer2 = explanation.get("layer2_detail")
    if layer2 and layer2.get("is_hub_account"):
        return "fan_in_fan_out"
    if layer2:
        return "community_ring"
    return "node_anomaly"


def _confidence_rank(confidence: str | None) -> int:
    return CONFIDENCE_ORDER.get(confidence or "low", 0)


def _confidence_increased(old_confidence: str | None, new_confidence: str) -> bool:
    return _confidence_rank(new_confidence) > _confidence_rank(old_confidence)


def _score_escalated(old_score: float, new_score: float) -> bool:
    if old_score <= 0:
        return new_score > 0
    return new_score > old_score * (1.0 + SCORE_ESCALATION_RELATIVE_THRESHOLD)


def _alert_confidence(alert: Alert) -> str:
    if alert.confidence:
        return alert.confidence
    breakdown = alert.feature_breakdown or {}
    return str(breakdown.get("confidence", "low"))


def _should_escalate_alert(
    existing_alert: Alert,
    fused_score: float,
    new_confidence: str,
) -> bool:
    old_confidence = _alert_confidence(existing_alert)
    if _confidence_increased(old_confidence, new_confidence):
        return True
    return _score_escalated(float(existing_alert.risk_score), fused_score)


def _apply_escalation(
    db: Session,
    existing_alert: Alert,
    fused_score: float,
    explanation: dict,
    as_of: datetime,
) -> Alert:
    old_confidence = _alert_confidence(existing_alert)
    old_score = float(existing_alert.risk_score)
    new_confidence = str(explanation.get("confidence", "low"))

    breakdown = dict(existing_alert.feature_breakdown or {})
    history = list(breakdown.get("escalation_history", []))
    history.append(
        {
            "escalated_at": as_of.isoformat(),
            "old_risk_score": old_score,
            "new_risk_score": float(fused_score),
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
        }
    )

    breakdown.update(explanation)
    breakdown["confidence_at_creation"] = breakdown.get(
        "confidence_at_creation", old_confidence
    )
    breakdown["escalation_history"] = history

    existing_alert.risk_score = float(fused_score)
    existing_alert.confidence = new_confidence
    existing_alert.updated_at = as_of
    existing_alert.feature_breakdown = breakdown
    flag_modified(existing_alert, "feature_breakdown")

    db_pattern = _determine_pattern_type(explanation)
    existing_alert.pattern_type = db_pattern

    db.commit()
    db.refresh(existing_alert)
    return existing_alert


def create_alert_if_needed(
    db: Session,
    account_id: str,
    fused_result: dict,
    explanation: dict,
    as_of: datetime,
    alert_threshold: float,
    *,
    rank: int | None = None,
) -> AlertActionResult | None:
    fused_score = float(fused_result["fused_score"])
    new_confidence = str(explanation.get("confidence", "low"))
    rank_text = f"rank={rank} " if rank is not None else ""

    if fused_score < alert_threshold:
        if _DEBUG_ALERT_CREATION:
            print(
                f"[alert-debug] SKIP {account_id}: {rank_text}"
                f"below threshold fused={fused_score:.3f} < {alert_threshold:.3f}"
            )
        return None

    # Case-management dedup: one open alert per account until resolved or auto-closed.
    # The previous 15-minute window allowed the same account to be re-alerted every
    # cycle after the window rolled, inflating queue volume without new information.
    existing_alert = db.scalar(
        select(Alert).where(
            Alert.account_id == account_id,
            Alert.status.in_(["new", "reviewing"]),
        )
    )
    if existing_alert is not None:
        old_confidence = _alert_confidence(existing_alert)
        old_score = float(existing_alert.risk_score)
        if _should_escalate_alert(existing_alert, fused_score, new_confidence):
            escalated = _apply_escalation(
                db, existing_alert, fused_score, explanation, as_of
            )
            if _DEBUG_ALERT_CREATION:
                print(
                    f"[alert-debug] ESCALATE {account_id}: {rank_text}"
                    f"alert_id={escalated.id}, status={escalated.status}, "
                    f"detected_at={escalated.detected_at} (unchanged), "
                    f"score {old_score:.3f}->{fused_score:.3f}, "
                    f"confidence {old_confidence}->{new_confidence}"
                )
            return AlertActionResult(alert=escalated, action="ESCALATE")

        if _DEBUG_ALERT_CREATION:
            print(
                f"[alert-debug] SKIP {account_id}: {rank_text}"
                f"existing open alert id={existing_alert.id}, "
                f"status={existing_alert.status}, detected_at={existing_alert.detected_at}, "
                f"risk_score={existing_alert.risk_score:.3f}, confidence={old_confidence}; "
                f"current fused={fused_score:.3f}, current confidence={new_confidence} "
                f"(no meaningful escalation)"
            )
        return None

    breakdown = dict(explanation)
    breakdown["confidence_at_creation"] = new_confidence
    breakdown["escalation_history"] = []

    alert = Alert(
        account_id=account_id,
        risk_score=fused_score,
        confidence=new_confidence,
        pattern_type=_determine_pattern_type(explanation),
        detected_at=as_of,
        updated_at=as_of,
        status="new",
        feature_breakdown=breakdown,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    if _DEBUG_ALERT_CREATION:
        print(
            f"[alert-debug] CREATE {account_id}: {rank_text}"
            f"fused={fused_score:.3f}, confidence={new_confidence}, "
            f"alert_id={alert.id}"
        )

    return AlertActionResult(alert=alert, action="CREATE")


def persist_score_history(
    db: Session,
    fused_results: list[dict],
    as_of: datetime,
) -> None:
    for row in fused_results:
        db.add(
            AccountScoreHistory(
                account_id=row["account_id"],
                score=float(row["fused_score"]),
                recorded_at=as_of,
            )
        )
    db.commit()


def run_detection_cycle(
    db: Session,
    as_of: datetime | None = None,
    *,
    return_diagnostics: bool = False,
) -> list[AlertActionResult] | tuple[list[AlertActionResult], dict]:
    if as_of is None:
        as_of = datetime.now()

    expired_count = auto_expire_stale_alerts(db, as_of)

    fused_results = compute_fused_scores(db, as_of)
    persist_score_history(db, fused_results, as_of)
    alert_budget = top_anomaly_budget(len(fused_results))
    alert_threshold = compute_alert_threshold(fused_results)
    alert_actions: list[AlertActionResult] = []

    eligible_count = min(alert_budget, len(fused_results))

    for rank, fused_result in enumerate(fused_results, start=1):
        if rank > alert_budget:
            break

        account_id = fused_result["account_id"]
        explanation = build_explanation(account_id, fused_result, db, as_of)
        action_result = create_alert_if_needed(
            db,
            account_id,
            fused_result,
            explanation,
            as_of,
            alert_threshold,
            rank=rank,
        )
        if action_result is not None:
            alert_actions.append(action_result)

    top_anomaly_accounts, _, _ = select_top_anomaly_accounts(
        fused_results, "fused_score"
    )
    from app.detection.structural_pass import (
        build_peripheral_explanation,
        peripheral_as_fused_result,
        score_peripheral_accounts,
    )

    peripheral_results = score_peripheral_accounts(
        db, as_of, WINDOW_MINUTES, top_anomaly_accounts
    )
    for peripheral in peripheral_results:
        explanation = build_peripheral_explanation(peripheral)
        pseudo_fused = peripheral_as_fused_result(peripheral)
        action_result = create_alert_if_needed(
            db,
            peripheral["account_id"],
            pseudo_fused,
            explanation,
            as_of,
            alert_threshold=0.0,
        )
        if action_result is not None:
            alert_actions.append(action_result)

    diagnostics = {
        "as_of": as_of,
        "alert_threshold": alert_threshold,
        "accounts_scored": len(fused_results),
        "eligible_count": eligible_count,
        "expired_count": expired_count,
        "fused_results": fused_results,
        "peak_fused_score": max(
            (float(row["fused_score"]) for row in fused_results),
            default=0.0,
        ),
    }

    if return_diagnostics:
        return alert_actions, diagnostics
    return alert_actions


def _synthetic_attack_accounts(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> set[str]:
    window_start, window_end = _window_bounds(as_of, window_minutes)
    rows = db.execute(
        select(Transaction.sender_id, Transaction.receiver_id).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                Transaction.is_synthetic_attack.is_(True),
            )
        )
    ).all()

    accounts: set[str] = set()
    for sender_id, receiver_id in rows:
        accounts.add(sender_id)
        accounts.add(receiver_id)
    return accounts


def _slow_drip_synthetic_accounts(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
    min_span_minutes: float = 10.0,
) -> set[str]:
    """
  Accounts involved in synthetic attacks whose transaction span exceeds
  min_span_minutes — a proxy for slow-drip (12–15 min) vs burst (2–3 min) attacks.
    """
    window_start, window_end = _window_bounds(as_of, window_minutes)
    rows = db.execute(
        select(
            Transaction.sender_id,
            Transaction.receiver_id,
            Transaction.timestamp,
        ).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                Transaction.is_synthetic_attack.is_(True),
            )
        )
    ).all()

    account_timestamps: dict[str, list[datetime]] = defaultdict(list)
    for sender_id, receiver_id, timestamp in rows:
        account_timestamps[sender_id].append(timestamp)
        account_timestamps[receiver_id].append(timestamp)

    slow_drip_accounts: set[str] = set()
    for account_id, timestamps in account_timestamps.items():
        if len(timestamps) < 3:
            continue
        span_minutes = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
        if span_minutes >= min_span_minutes:
            slow_drip_accounts.add(account_id)

    return slow_drip_accounts


def _ensure_alert_schema(db: Session) -> None:
    columns = db.execute(text("PRAGMA table_info(alerts)")).all()
    column_names = {row[1] for row in columns}
    if "confidence" not in column_names:
        db.execute(
            text(
                "ALTER TABLE alerts ADD COLUMN confidence VARCHAR "
                "NOT NULL DEFAULT 'low'"
            )
        )
        db.commit()
    if "updated_at" not in column_names:
        db.execute(text("ALTER TABLE alerts ADD COLUMN updated_at DATETIME"))
        db.execute(
            text("UPDATE alerts SET updated_at = detected_at WHERE updated_at IS NULL")
        )
        db.commit()
    if "reviewed_at" not in column_names:
        db.execute(text("ALTER TABLE alerts ADD COLUMN reviewed_at DATETIME"))
        db.commit()


def ensure_db_schema(db: Session) -> None:
    """Apply lightweight SQLite migrations for columns added after initial deploy."""
    _ensure_alert_schema(db)


def _account_has_synthetic_activity(db: Session, account_id: str, as_of: datetime) -> bool:
    window_start, window_end = _window_bounds(as_of, WINDOW_MINUTES)
    stmt = (
        select(Transaction.id)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                Transaction.is_synthetic_attack.is_(True),
                or_(
                    Transaction.sender_id == account_id,
                    Transaction.receiver_id == account_id,
                ),
            )
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


if __name__ == "__main__":
    import argparse
    import time

    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Run GraphDrift fusion detection demo")
    parser.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Pause before running so the simulator can populate the window",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print per-account alert create/skip/escalate reasons",
    )
    parser.add_argument(
        "--two-cycle",
        action="store_true",
        help="Run two consecutive detection cycles to demonstrate create then escalate",
    )
    args = parser.parse_args()

    import app.detection.fusion as fusion_module

    fusion_module._DEBUG_ALERT_CREATION = args.debug or args.two_cycle

    if args.wait > 0:
        print(
            f"Waiting {args.wait}s — ensure the FastAPI server is running "
            f"(uvicorn app.main:app --reload)."
        )
        time.sleep(args.wait)

    db = SessionLocal()
    try:
        _ensure_alert_schema(db)

        if args.two_cycle:
            import time as time_module

            print("=== Cycle 1 (create) ===")
            cycle1_as_of = datetime.now()
            cycle1_actions, _ = run_detection_cycle(
                db, cycle1_as_of, return_diagnostics=True
            )
            print(
                f"Cycle 1 complete: {len(cycle1_actions)} alerts created/escalated"
            )
            cnigam_cycle1 = next(
                (
                    action.alert
                    for action in cycle1_actions
                    if action.alert.account_id == "cnigam@paytm"
                ),
                db.scalar(
                    select(Alert)
                    .where(Alert.account_id == "cnigam@paytm")
                    .order_by(Alert.detected_at.desc())
                ),
            )
            if cnigam_cycle1:
                print(
                    f"cnigam@paytm after cycle 1: id={cnigam_cycle1.id}, "
                    f"risk={cnigam_cycle1.risk_score:.3f}, confidence={_alert_confidence(cnigam_cycle1)}, "
                    f"detected_at={cnigam_cycle1.detected_at}"
                )
            print()
            print("Waiting 3s before cycle 2...")
            time_module.sleep(3)
            print("=== Cycle 2 (escalate expected) ===")
            cycle2_as_of = datetime.now()
            cycle2_actions, _ = run_detection_cycle(
                db, cycle2_as_of, return_diagnostics=True
            )
            print(
                f"Cycle 2 complete: {len(cycle2_actions)} alerts created/escalated"
            )
            cnigam = db.scalar(
                select(Alert)
                .where(Alert.account_id == "cnigam@paytm")
                .order_by(Alert.detected_at.desc())
            )
            if cnigam:
                breakdown = cnigam.feature_breakdown or {}
                print()
                print("Final cnigam@paytm alert row:")
                print(f"  id={cnigam.id}")
                print(f"  detected_at={cnigam.detected_at}")
                print(f"  risk_score={cnigam.risk_score:.4f}")
                print(f"  confidence={cnigam.confidence}")
                print(f"  status={cnigam.status}")
                print(f"  escalation_history={breakdown.get('escalation_history', [])}")
            raise SystemExit(0)

        as_of = datetime.now()
        alert_actions, diagnostics = run_detection_cycle(
            db, as_of, return_diagnostics=True
        )
        fused_results = diagnostics["fused_results"]
        alert_threshold = diagnostics["alert_threshold"]
        new_alerts = [action.alert for action in alert_actions]

        attack_accounts = _synthetic_attack_accounts(db, as_of)
        slow_drip_accounts = _slow_drip_synthetic_accounts(db, as_of)

        print(f"Fusion detection cycle as_of={as_of.isoformat()} window={WINDOW_MINUTES}m")
        print(
            f"Alert rule: top {(1 - get_alert_top_percentile()) * 100:.0f}% of fused scores "
            f"(threshold={alert_threshold:.3f} this cycle)"
        )
        print(f"Accounts scored: {len(fused_results)} | New alerts created: {len(new_alerts)}")
        print(
            f"Eligible for alerting (fused >= {alert_threshold:.3f}): "
            f"{diagnostics['eligible_count']}"
        )
        print()

        watch_account = "cnigam@paytm"
        watch_row = next(
            (row for row in fused_results if row["account_id"] == watch_account),
            None,
        )
        if watch_row is not None:
            watch_rank = fused_results.index(watch_row) + 1
            print(f"Watch account {watch_account}:")
            print(
                f"  rank={watch_rank}/{len(fused_results)}, "
                f"fused={watch_row['fused_score']:.3f}, "
                f"threshold={alert_threshold:.3f}, "
                f"eligible={'yes' if watch_row['fused_score'] >= alert_threshold else 'no'}, "
                f"confidence={watch_row['confidence']}, "
                f"gdi%={watch_row['gdi_percentile']:.2f}, ring%={watch_row['ring_percentile']:.2f}"
            )
            from sqlalchemy import select as sql_select

            window_start, _ = _window_bounds(as_of, WINDOW_MINUTES)
            existing = db.scalar(
                sql_select(Alert).where(
                    Alert.account_id == watch_account,
                    Alert.status.in_(["new", "reviewing"]),
                    Alert.detected_at >= window_start,
                )
            )
            if existing:
                prior = existing.feature_breakdown or {}
                print(
                    f"  open alert in dedup window: id={existing.id}, status={existing.status}, "
                    f"created_at={existing.detected_at}, "
                    f"risk_score={existing.risk_score:.3f}, "
                    f"confidence_at_creation={prior.get('confidence', 'unknown')}"
                )
            else:
                print("  no open alert in dedup window")
            print()

        print("Top 10 by fused score (percentile-based):")
        print(
            f"{'account_id':<28} | {'fused':>5} | {'gdi':>5} | {'ring':>5} | "
            f"{'gdi%':>5} | {'ring%':>5} | confidence"
        )
        print("-" * 90)
        for row in fused_results[:10]:
            print(
                f"{row['account_id']:<28} | {row['fused_score']:5.2f} | "
                f"{row['gdi_score']:5.2f} | {row['ring_risk_score']:5.2f} | "
                f"{row['gdi_percentile']:5.2f} | {row['ring_percentile']:5.2f} | "
                f"{row['confidence']}"
            )
        print()

        confidence_counts = {"high": 0, "medium": 0, "low": 0}
        synthetic_hits = 0
        high_conf_synthetic = 0
        slow_drip_alerted = 0
        slow_drip_total = len(slow_drip_accounts)

        if not new_alerts:
            print("No new alerts above threshold.")
        else:
            print("New alerts:")
            for alert in new_alerts:
                explanation = alert.feature_breakdown or {}
                confidence = explanation.get("confidence", "low")
                confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1

                is_synthetic = _account_has_synthetic_activity(db, alert.account_id, as_of)
                is_slow_drip = alert.account_id in slow_drip_accounts
                if is_synthetic:
                    synthetic_hits += 1
                if confidence == "high" and is_synthetic:
                    high_conf_synthetic += 1
                if is_slow_drip:
                    slow_drip_alerted += 1

                flags = []
                if is_synthetic:
                    flags.append("synthetic")
                if is_slow_drip:
                    flags.append("slow-drip")
                flag_text = f" [{', '.join(flags)}]" if flags else ""

                print(
                    f"  {alert.account_id}: fused={alert.risk_score:.2f}, "
                    f"confidence={confidence}, pattern={alert.pattern_type}{flag_text}"
                )
                print(f"    {explanation.get('primary_reason', '')}")

        print()
        print("Confidence summary (new alerts):")
        for level in ("high", "medium", "low"):
            print(f"  {level}: {confidence_counts.get(level, 0)}")

        high_conf_all = sum(1 for row in fused_results if row["confidence"] == "high")
        print(f"High-confidence accounts in full scored set: {high_conf_all}")

        if new_alerts:
            print()
            print(
                f"Synthetic attack cross-check: {synthetic_hits}/{len(new_alerts)} "
                f"alerts involve synthetic accounts ({synthetic_hits / len(new_alerts):.0%})"
            )
            print(f"High-confidence + synthetic: {high_conf_synthetic}")

        print()
        print("Slow-drip attack check:")
        print(f"  Slow-drip synthetic accounts in window: {slow_drip_total}")
        print(f"  Slow-drip accounts alerted this cycle: {slow_drip_alerted}")
        if slow_drip_total:
            missed = slow_drip_accounts - {a.account_id for a in new_alerts}
            coverage = slow_drip_alerted / slow_drip_total
            print(f"  Slow-drip coverage: {coverage:.0%}")
            if missed:
                sample = sorted(missed)[:5]
                print(
                    f"  Sample missed slow-drip accounts: {', '.join(sample)}"
                )
                print(
                    "  (Likely cause: ring not fully formed within 15-minute window "
                    "before scoring — known limitation for stretched attacks.)"
                )
        else:
            print("  No slow-drip synthetic activity detected in current window.")
    finally:
        db.close()
