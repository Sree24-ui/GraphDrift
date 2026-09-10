#!/usr/bin/env python3
"""Time multi-scale fusion and measure slow-drip recall vs 15-minute-only.

Multi-scale here is the shipped UNION of independent per-scale top-k cuts, not
the retired max-then-global-cut merge.

Uses a frozen copy of the live DB so scoring does not mutate production alerts.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select

from app.api.websocket import DETECTION_CYCLE_INTERVAL_SECONDS
from app.detection.features import (
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
    extract_all_features,
    extract_features,
)
from app.detection.fusion import (
    SECONDARY_WINDOW_MINUTES,
    compute_fused_scores,
    compute_fused_scores_multiscale,
    run_detection_cycle,
    select_top_anomaly_accounts,
)
from app.models import Transaction
from evaluation.db import init_eval_db
from evaluation.freeze_snapshot import freeze_snapshot
from evaluation.snapshot_analysis import classify_attack_types

LIVE_DB = BACKEND_ROOT / "graphdrift.db"
SNAPSHOT_DIR = BACKEND_ROOT / "snapshots"
CYCLE_INTERVAL = DETECTION_CYCLE_INTERVAL_SECONDS
DIAGNOSE_LIMIT = 8


def _window_score(row: dict, window: int) -> float:
    by_window = row.get("fused_score_by_window") or {}
    return float(by_window.get(window) or by_window.get(str(window)) or 0.0)


def _print_recall_block(
    title: str,
    *,
    slow: set[str],
    universe: set[str],
    predicted: set[str],
    fused_by_id: dict[str, dict] | None = None,
) -> set[str]:
    scored = slow & universe
    detected = scored & predicted
    missed = scored - predicted
    filtered = slow - universe
    print(f"\n=== {title} ===")
    print(f"slow-drip in window:     {len(slow)}")
    print(f"slow-drip scored (>=3 tx): {len(scored)}")
    print(f"slow-drip filtered (<3 tx): {len(filtered)}")
    print(f"slow-drip detected (TP):  {len(detected)}")
    print(f"slow-drip missed (FN):    {len(missed)}")
    print(
        f"recall on scored subset:  "
        f"{(len(detected) / len(scored) if scored else 0.0):.1%} "
        f"({len(detected)}/{len(scored)})"
    )
    print(
        f"recall of all in-window:  "
        f"{(len(detected) / len(slow) if slow else 0.0):.1%} "
        f"({len(detected)}/{len(slow)})"
    )
    if fused_by_id is not None and detected:
        by_15 = {
            a
            for a in detected
            if int(fused_by_id.get(a, {}).get("detection_window", WINDOW_MINUTES))
            == WINDOW_MINUTES
        }
        by_60 = detected - by_15
        print(f"  tagged detection_window=15: {len(by_15)}")
        print(f"  tagged detection_window=60: {len(by_60)}")
        if by_15:
            print(f"    15-min accounts: {sorted(by_15)}")
        if by_60:
            print(f"    60-min accounts: {sorted(by_60)}")
    return missed


def _diagnose_misses(
    db,
    as_of,
    missed: set[str],
    *,
    fused_15_by_id: dict[str, dict],
    fused_60_by_id: dict[str, dict],
    fused_multi_by_id: dict[str, dict],
    pred_15: set[str],
    pred_60: set[str],
    pred_multi: set[str],
    ranks_15: dict[str, int],
    ranks_60: dict[str, int],
    ranks_multi: dict[str, int],
) -> None:
    if not missed:
        return
    print("\n=== Miss diagnosis (slow-drip scored but not in multi-scale top-k) ===")
    for account_id in sorted(missed)[:DIAGNOSE_LIMIT]:
        feats_15 = extract_features(db, account_id, as_of, WINDOW_MINUTES)
        feats_60 = extract_features(db, account_id, as_of, SECONDARY_WINDOW_MINUTES)
        r15 = fused_15_by_id.get(account_id)
        r60 = fused_60_by_id.get(account_id)
        rm = fused_multi_by_id.get(account_id)
        print(f"\n  {account_id}")
        print(
            f"    tx-scored 15m={feats_15 is not None} 60m={feats_60 is not None}"
        )
        if r15:
            print(
                f"    15m fused={r15['fused_score']:.3f} rank={ranks_15.get(account_id)} "
                f"gdi={r15['gdi_score']:.3f} ring={r15['ring_risk_score']:.3f} "
                f"in_topk={account_id in pred_15}"
            )
        else:
            print("    15m: not scored")
        if r60:
            print(
                f"    60m fused={r60['fused_score']:.3f} rank={ranks_60.get(account_id)} "
                f"gdi={r60['gdi_score']:.3f} ring={r60['ring_risk_score']:.3f} "
                f"in_topk={account_id in pred_60}"
            )
        else:
            print("    60m: not scored")
        if rm:
            print(
                f"    multi fused={rm['fused_score']:.3f} rank={ranks_multi.get(account_id)} "
                f"window={rm.get('detection_window')} "
                f"by_window={rm.get('fused_score_by_window')} "
                f"in_topk={account_id in pred_multi}"
            )


def run_eval(snapshot: Path) -> None:
    factory = init_eval_db(snapshot)
    db = factory()
    try:
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        if as_of is None:
            print("Snapshot has no transactions.")
            return

        print(f"Snapshot: {snapshot}")
        print(f"as_of: {as_of.isoformat()}")
        print(
            f"Windows: {WINDOW_MINUTES}m (fast) / {SECONDARY_WINDOW_MINUTES}m (slow) | "
            f"min_tx={MIN_TRANSACTIONS_FOR_SCORING} | cycle interval={CYCLE_INTERVAL}s"
        )

        t0 = time.time()
        fused_15 = compute_fused_scores(
            db, as_of, window_minutes=WINDOW_MINUTES
        )
        t_15 = time.time() - t0

        t0 = time.time()
        fused_60 = compute_fused_scores(
            db, as_of, window_minutes=SECONDARY_WINDOW_MINUTES
        )
        t_60 = time.time() - t0

        t0 = time.time()
        fused_multi = compute_fused_scores_multiscale(db, as_of)
        t_multi = time.time() - t0

        print("\n=== Step 4 — timing (score-only, no alert writes) ===")
        print(f"compute_fused_scores 15m:          {t_15:.2f}s")
        print(f"compute_fused_scores 60m:          {t_60:.2f}s")
        print(f"compute_fused_scores_multiscale:   {t_multi:.2f}s")
        print(f"15m+60m sequential sum:            {t_15 + t_60:.2f}s")

        cycle_copy = Path(tempfile.mkdtemp()) / "cycle.db"
        shutil.copy2(snapshot, cycle_copy)
        cycle_factory = init_eval_db(cycle_copy)
        cycle_db = cycle_factory()
        try:
            t0 = time.time()
            run_detection_cycle(cycle_db, as_of)
            t_cycle = time.time() - t0
        finally:
            cycle_db.close()

        print(f"run_detection_cycle (multiscale):  {t_cycle:.2f}s")
        if t_cycle > CYCLE_INTERVAL:
            print(
                f"WARNING: cycle runtime {t_cycle:.2f}s exceeds the "
                f"{CYCLE_INTERVAL}s detection interval — cycles can overlap."
            )
        else:
            print(
                f"Cycle runtime is under the {CYCLE_INTERVAL}s interval "
                f"(headroom {CYCLE_INTERVAL - t_cycle:.2f}s)."
            )
        print(
            "Single-scale scoring baseline (15m only) is the 15m line above; "
            "multi-scale adds a full 60m pass, so scoring cost tracks 15m+60m."
        )

        universe_15 = {
            row["account_id"]
            for row in extract_all_features(db, as_of, WINDOW_MINUTES)
        }
        universe_60 = {
            row["account_id"]
            for row in extract_all_features(db, as_of, SECONDARY_WINDOW_MINUTES)
        }

        _, _, slow_15 = classify_attack_types(
            db, as_of, window_minutes=WINDOW_MINUTES
        )
        _, _, slow_60 = classify_attack_types(
            db, as_of, window_minutes=SECONDARY_WINDOW_MINUTES
        )

        pred_15, _, _ = select_top_anomaly_accounts(fused_15, "fused_score")
        pred_60, _, _ = select_top_anomaly_accounts(fused_60, "fused_score")
        pred_multi = {row["account_id"] for row in fused_multi}

        fused_15_by_id = {row["account_id"]: row for row in fused_15}
        fused_60_by_id = {row["account_id"]: row for row in fused_60}
        fused_multi_by_id = {row["account_id"]: row for row in fused_multi}
        ranks_15 = {row["account_id"]: i for i, row in enumerate(fused_15, start=1)}
        ranks_60 = {row["account_id"]: i for i, row in enumerate(fused_60, start=1)}
        ranks_multi = {
            row["account_id"]: i for i, row in enumerate(fused_multi, start=1)
        }

        print("\n=== Step 5 — slow-drip recall ===")
        print(
            "Step 16 baseline (snapshot 2026-08-12, 15m fusion): "
            "slow_drip in window=1, scored=1, detected=1, FN=0 (100% of n=1)."
        )
        print(
            f"Accounts scored: 15m={len(fused_15)} 60m={len(fused_60)} "
            f"multiscale union={len(fused_multi)}"
        )
        print(
            f"Top-k predicted: 15m={len(pred_15)} 60m={len(pred_60)} "
            f"multiscale={len(pred_multi)}"
        )

        _print_recall_block(
            "15-minute fusion (Step 16-style, this snapshot)",
            slow=slow_15,
            universe=universe_15,
            predicted=pred_15,
        )
        _print_recall_block(
            "60-minute fusion only",
            slow=slow_60,
            universe=universe_60,
            predicted=pred_60,
        )
        missed_multi = _print_recall_block(
            "Multi-scale fusion (union of per-scale top-k)",
            slow=slow_60,
            universe=universe_60,
            predicted=pred_multi,
            fused_by_id=fused_multi_by_id,
        )

        newly_caught = (slow_60 & universe_60 & pred_multi) - (
            slow_15 & universe_15 & pred_15
        )
        print(
            f"\nSlow-drip accounts caught by multi-scale but not by 15m-only "
            f"(using 60m ground truth ∩ scored): {len(newly_caught)}"
        )
        if newly_caught:
            print(f"  {sorted(newly_caught)}")
            for account_id in sorted(newly_caught):
                row = fused_multi_by_id[account_id]
                print(
                    f"    {account_id}: window={row.get('detection_window')} "
                    f"fused={row['fused_score']:.3f} "
                    f"15m={_window_score(row, 15):.3f} "
                    f"60m={_window_score(row, 60):.3f}"
                )

        if len(slow_60 & universe_60 & pred_multi) <= len(
            slow_15 & universe_15 & pred_15
        ):
            print(
                "\n60-minute pass did not increase slow-drip TP vs 15m-only "
                "on this snapshot. Rankings for scored slow-drip accounts:"
            )
            for account_id in sorted(slow_60 & universe_60)[:DIAGNOSE_LIMIT]:
                r15 = fused_15_by_id.get(account_id)
                r60 = fused_60_by_id.get(account_id)
                rm = fused_multi_by_id.get(account_id)
                print(f"  {account_id}")
                if r15:
                    print(
                        f"    15m fused={r15['fused_score']:.3f} "
                        f"rank={ranks_15.get(account_id)}"
                    )
                else:
                    print("    15m: not scored")
                if r60:
                    print(
                        f"    60m fused={r60['fused_score']:.3f} "
                        f"rank={ranks_60.get(account_id)}"
                    )
                else:
                    print("    60m: not scored")
                if rm:
                    print(
                        f"    multi fused={rm['fused_score']:.3f} "
                        f"rank={ranks_multi.get(account_id)} "
                        f"window={rm.get('detection_window')} "
                        f"topk15={account_id in pred_15} topk60={account_id in pred_60} "
                        f"topk_multi={account_id in pred_multi}"
                    )

        _diagnose_misses(
            db,
            as_of,
            missed_multi,
            fused_15_by_id=fused_15_by_id,
            fused_60_by_id=fused_60_by_id,
            fused_multi_by_id=fused_multi_by_id,
            pred_15=pred_15,
            pred_60=pred_60,
            pred_multi=pred_multi,
            ranks_15=ranks_15,
            ranks_60=ranks_60,
            ranks_multi=ranks_multi,
        )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-scale slow-drip eval")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Existing snapshot DB. If omitted, freeze live graphdrift.db.",
    )
    parser.add_argument("--no-freeze", action="store_true")
    args = parser.parse_args()

    if args.snapshot is not None:
        snapshot = args.snapshot
    elif args.no_freeze:
        snapshot = LIVE_DB
    else:
        snapshot = freeze_snapshot(LIVE_DB, SNAPSHOT_DIR)
        print(f"Froze live DB -> {snapshot}")

    run_eval(snapshot)


if __name__ == "__main__":
    main()
