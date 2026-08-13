"""Snapshot recall breakdown and miss diagnosis for paper-ready reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.detection.features import (
    MIN_TRANSACTIONS_FOR_SCORING,
    extract_features,
    extract_all_features,
)
from app.detection.fusion import (
    _slow_drip_synthetic_accounts,
    _synthetic_attack_accounts,
    compute_fused_scores,
    select_top_anomaly_accounts,
)
from app.detection.node_anomaly import compute_gdi_scores


@dataclass
class AttackTypeRecall:
    attack_type: str
    total_in_window: int
    in_scored_universe: int
    detected: int
    recall_scored: float
    filtered_out: int


@dataclass
class MissDiagnosis:
    account_id: str
    attack_type: str
    tx_count_in_window: int
    in_scored_universe: bool
    gdi_score: float | None
    ring_risk_score: float | None
    fused_score: float | None
    alert_threshold: float | None
    fused_rank: int | None
    accounts_scored: int
    reason: str
    features: dict | None


def classify_attack_types(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int,
    slow_drip_min_span_minutes: float = 10.0,
) -> tuple[set[str], set[str], set[str]]:
    """Return (all_synthetic, fast_burst, slow_drip) account sets in window."""
    all_synth = _synthetic_attack_accounts(db, as_of, window_minutes)
    slow = _slow_drip_synthetic_accounts(
        db, as_of, window_minutes, min_span_minutes=slow_drip_min_span_minutes
    )
    fast = all_synth - slow
    return all_synth, fast, slow


def attack_type_recall(
    positives: set[str],
    universe: set[str],
    predicted: set[str],
    *,
    attack_type: str,
    total_in_window: set[str],
) -> AttackTypeRecall:
    in_universe = total_in_window & universe
    detected = len(in_universe & predicted)
    total_scored = len(in_universe)
    return AttackTypeRecall(
        attack_type=attack_type,
        total_in_window=len(total_in_window),
        in_scored_universe=total_scored,
        detected=detected,
        recall_scored=detected / total_scored if total_scored else 0.0,
        filtered_out=len(total_in_window) - total_scored,
    )


def diagnose_fusion_misses(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
    universe: set[str],
    predicted: set[str],
    max_examples: int = 3,
) -> list[MissDiagnosis]:
    all_synth, fast, slow = classify_attack_types(db, as_of, window_minutes=window_minutes)
    positives = all_synth & universe
    missed = positives - predicted

    fused_results = compute_fused_scores(
        db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
    )
    _, threshold, alert_k = select_top_anomaly_accounts(fused_results, "fused_score")
    fused_by_account = {row["account_id"]: row for row in fused_results}
    rank_by_account = {
        row["account_id"]: idx + 1 for idx, row in enumerate(fused_results)
    }

    diagnoses: list[MissDiagnosis] = []
    # Prioritize missed fast (burst) attacks — primary designed pattern.
    missed_fast = sorted(missed & fast)
    missed_slow = sorted(missed & slow)
    candidates = missed_fast + missed_slow

    for account_id in candidates[:max_examples]:
        attack_type = "fast_fan_in_fan_out" if account_id in fast else "slow_drip"
        feats = extract_features(
            db, account_id, as_of, window_minutes, min_transactions=min_transactions
        )
        tx_count = feats["in_count"] + feats["out_count"] if feats else _tx_count(
            db, account_id, as_of, window_minutes
        )
        in_universe = account_id in universe
        fused_row = fused_by_account.get(account_id)

        if not in_universe:
            reason = (
                f"Filtered by MIN_TRANSACTIONS_FOR_SCORING "
                f"(<{MIN_TRANSACTIONS_FOR_SCORING} tx in {window_minutes}m window)"
            )
            diagnoses.append(
                MissDiagnosis(
                    account_id=account_id,
                    attack_type=attack_type,
                    tx_count_in_window=tx_count,
                    in_scored_universe=False,
                    gdi_score=None,
                    ring_risk_score=None,
                    fused_score=None,
                    alert_threshold=threshold,
                    fused_rank=None,
                    accounts_scored=len(universe),
                    reason=reason,
                    features=feats,
                )
            )
            continue

        fused_score = float(fused_row["fused_score"]) if fused_row else None
        gdi_score = float(fused_row["gdi_score"]) if fused_row else None
        ring_score = float(fused_row["ring_risk_score"]) if fused_row else None
        rank = rank_by_account.get(account_id)

        if fused_score is not None and account_id not in select_top_anomaly_accounts(
            fused_results, "fused_score"
        )[0]:
            reason = (
                f"Scored but outside top-{alert_k} alert budget "
                f"(fused={fused_score:.3f}, cutoff={threshold:.3f}, "
                f"rank {rank}/{len(fused_results)})"
            )
        elif fused_row is None:
            reason = "Not present in fused score list (no GDI or ring signal)"
        else:
            reason = "Unknown miss (in universe, above threshold, but not predicted)"

        diagnoses.append(
            MissDiagnosis(
                account_id=account_id,
                attack_type=attack_type,
                tx_count_in_window=tx_count,
                in_scored_universe=True,
                gdi_score=gdi_score,
                ring_risk_score=ring_score,
                fused_score=fused_score,
                alert_threshold=threshold,
                fused_rank=rank,
                accounts_scored=len(universe),
                reason=reason,
                features=feats,
            )
        )

    return diagnoses


def _tx_count(db: Session, account_id: str, as_of: datetime, window_minutes: int) -> int:
    from datetime import timedelta
    from sqlalchemy import and_, or_, select, func
    from app.models import Transaction

    window_start = as_of - timedelta(minutes=window_minutes)
    return (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                and_(
                    Transaction.timestamp >= window_start,
                    Transaction.timestamp <= as_of,
                    or_(
                        Transaction.sender_id == account_id,
                        Transaction.receiver_id == account_id,
                    ),
                )
            )
        )
        or 0
    )


def format_attack_breakdown(
    breakdowns: list[AttackTypeRecall],
    detector: str,
) -> str:
    lines = [f"### {detector} — recall by attack type", ""]
    lines.append(
        "| Attack type | In window | Scored (≥3 tx) | Filtered out | Detected | Recall (scored) |"
    )
    lines.append(
        "|-------------|-----------|----------------|--------------|----------|-----------------|"
    )
    for row in breakdowns:
        lines.append(
            f"| {row.attack_type} | {row.total_in_window} | {row.in_scored_universe} | "
            f"{row.filtered_out} | {row.detected} | {row.recall_scored:.1%} |"
        )
    return "\n".join(lines)


def format_miss_diagnoses(diagnoses: list[MissDiagnosis]) -> str:
    if not diagnoses:
        return "_No scored misses to diagnose (all positives detected or filtered before scoring)._"

    lines = [
        "### Missed attack account diagnosis (fusion)",
        "",
    ]
    for idx, miss in enumerate(diagnoses, start=1):
        lines.append(f"#### {idx}. `{miss.account_id}` ({miss.attack_type})")
        lines.append(f"- **Reason:** {miss.reason}")
        lines.append(f"- Transactions in window: {miss.tx_count_in_window}")
        if miss.features:
            feat = miss.features
            lines.append(
                "- Feature vector: "
                f"in_deg={feat['in_degree']}, out_deg={feat['out_degree']}, "
                f"in_cnt={feat['in_count']}, out_cnt={feat['out_count']}, "
                f"velocity={feat['velocity']:.2f}, fan_ratio={feat['fan_ratio']:.2f}, "
                f"burstiness={feat['burstiness']:.2f}"
            )
        if miss.fused_score is not None:
            lines.append(
                f"- Scores: GDI={miss.gdi_score:.3f}, ring={miss.ring_risk_score:.3f}, "
                f"fused={miss.fused_score:.3f} (threshold {miss.alert_threshold:.3f}, "
                f"rank {miss.fused_rank}/{miss.accounts_scored})"
            )
        lines.append("")
    return "\n".join(lines)
