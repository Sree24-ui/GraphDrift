#!/usr/bin/env python3
"""Sweep MIN_TRANSACTIONS_FOR_SCORING + hybrid peripheral pass on snapshot."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import and_, func, or_, select

from app.detection.features import WINDOW_MINUTES, extract_all_features, get_active_accounts
from app.detection.fusion import (
    _synthetic_attack_accounts,
    compute_fused_scores,
    select_top_anomaly_accounts,
)
from app.detection.node_anomaly import compute_gdi_scores
from app.detection.structural_pass import score_peripheral_accounts
from app.models import Transaction
from app.settings_store import get_alert_top_percentile
from evaluation.baseline_rule import detect_baseline_accounts
from evaluation.db import init_eval_db
from evaluation.metrics import compute_metrics

SNAPSHOT = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-12.db"


def _tx_count(db, account_id: str, as_of, window_minutes: int) -> int:
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


def predict_hybrid(
    db,
    as_of,
    *,
    window_minutes: int,
    min_transactions: int,
) -> tuple[set[str], set[str], set[str]]:
    """Return (combined, main_fusion, peripheral_only) predicted account sets."""
    fused = compute_fused_scores(
        db,
        as_of,
        window_minutes=window_minutes,
        min_transactions=min_transactions,
    )
    main_pred, _, _ = select_top_anomaly_accounts(fused, "fused_score")
    peripheral_rows = score_peripheral_accounts(
        db, as_of, window_minutes, main_pred
    )
    peripheral_pred = {row["account_id"] for row in peripheral_rows}
    return main_pred | peripheral_pred, main_pred, peripheral_pred


def eval_snapshot(min_transactions: int, *, include_hybrid: bool = False) -> dict:
    factory = init_eval_db(SNAPSHOT)
    db = factory()
    try:
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        universe = {
            f["account_id"]
            for f in extract_all_features(
                db, as_of, WINDOW_MINUTES, min_transactions=min_transactions
            )
        }
        all_pos = _synthetic_attack_accounts(db, as_of, WINDOW_MINUTES)
        active_universe = set(get_active_accounts(db, as_of, WINDOW_MINUTES))

        rows: dict = {}
        detectors = ["baseline", "layer1", "fusion"]
        if include_hybrid:
            detectors.append("hybrid")

        for detector in detectors:
            if detector == "baseline":
                pred = detect_baseline_accounts(db, as_of, window_minutes=WINDOW_MINUTES)
                eval_universe = universe
            elif detector == "layer1":
                features = extract_all_features(
                    db, as_of, window_minutes=WINDOW_MINUTES, min_transactions=min_transactions
                )
                gdi = compute_gdi_scores(features)
                pred, _, _ = select_top_anomaly_accounts(gdi, "gdi_score")
                eval_universe = universe
            elif detector == "fusion":
                fused = compute_fused_scores(
                    db, as_of, window_minutes=WINDOW_MINUTES, min_transactions=min_transactions
                )
                pred, _, _ = select_top_anomaly_accounts(fused, "fused_score")
                eval_universe = universe
            else:
                combined, main_pred, peripheral_only = predict_hybrid(
                    db,
                    as_of,
                    window_minutes=WINDOW_MINUTES,
                    min_transactions=min_transactions,
                )
                pred = combined
                # Hybrid can flag 1–2 tx peripherals; evaluate over all active accounts.
                eval_universe = active_universe
                rows["hybrid_detail"] = {
                    "main_count": len(main_pred),
                    "peripheral_count": len(peripheral_only),
                    "peripheral_only_pred": peripheral_only,
                }

            positives = all_pos & eval_universe
            m = compute_metrics(positives, pred, eval_universe)
            rows[detector] = {
                **m.as_dict(),
                "accounts_evaluated": len(eval_universe),
                "ground_truth_fraud_in_window": len(all_pos),
                "ground_truth_fraud_scored": len(positives),
                "ground_truth_filtered_out": len(all_pos) - len(positives),
            }

        return rows
    finally:
        db.close()


def print_hybrid_analysis() -> None:
    factory = init_eval_db(SNAPSHOT)
    db = factory()
    try:
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        all_pos = _synthetic_attack_accounts(db, as_of, WINDOW_MINUTES)
        combined, main_pred, peripheral_pred = predict_hybrid(
            db, as_of, window_minutes=WINDOW_MINUTES, min_transactions=3
        )
        peripheral_only = peripheral_pred - main_pred
        tp_peripheral = peripheral_only & all_pos
        fp_peripheral = peripheral_only - all_pos

        print("\n=== Hybrid peripheral breakdown (min_tx=3) ===")
        print(f"Main fusion flagged: {len(main_pred)}")
        print(f"Peripheral-only flagged: {len(peripheral_only)}")
        print(f"Peripheral TP: {len(tp_peripheral)}  FP: {len(fp_peripheral)}")
        if tp_peripheral:
            dist = Counter(
                _tx_count(db, a, as_of, WINDOW_MINUTES) for a in tp_peripheral
            )
            print(f"Peripheral TP tx-count dist: {dict(sorted(dist.items()))}")
            print("Peripheral TP accounts:", sorted(tp_peripheral))
        if fp_peripheral:
            print(f"Peripheral FP sample (up to 5): {sorted(fp_peripheral)[:5]}")

        single_tx_fraud = {a for a in all_pos if _tx_count(db, a, as_of, WINDOW_MINUTES) == 1}
        caught_single = single_tx_fraud & peripheral_only
        print(
            f"\nSingle-tx fraud accounts: {len(single_tx_fraud)} | "
            f"caught by peripheral pass: {len(caught_single)}"
        )
        if caught_single:
            print("Caught:", sorted(caught_single))
    finally:
        db.close()


def print_table(results: dict[int, dict]) -> None:
    print(
        f"\n{'min_tx':>6} {'detector':<9} {'P':>6} {'R':>6} {'F1':>6} {'FPR':>7} "
        f"{'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4} {'fraud':>5} {'eval':>5} {'filt':>5}"
    )
    print("-" * 88)
    for min_tx in sorted(results):
        block = results[min_tx]
        detectors = [k for k in block if k not in ("hybrid_detail",)]
        for det in detectors:
            r = block[det]
            print(
                f"{min_tx:>6} {det:<9} {r['precision']:6.3f} {r['recall']:6.3f} "
                f"{r['f1']:6.3f} {r['fpr']:7.4f} {r['tp']:4d} {r['fp']:4d} "
                f"{r['fn']:4d} {r['tn']:4d} {r['ground_truth_fraud_scored']:5d} "
                f"{r['accounts_evaluated']:5d} {r['ground_truth_filtered_out']:5d}"
            )


def main() -> None:
    print(f"Snapshot: {SNAPSHOT.name}")
    print(f"Window: {WINDOW_MINUTES}m | ALERT_TOP_PERCENTILE={get_alert_top_percentile()}")

    results = {}
    for min_tx in (1, 2, 3):
        results[min_tx] = eval_snapshot(min_tx, include_hybrid=(min_tx == 3))

    print_table(results)
    print_hybrid_analysis()


if __name__ == "__main__":
    main()
