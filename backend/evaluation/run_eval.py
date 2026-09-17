#!/usr/bin/env python3
"""
Run offline evaluation: baseline vs Layer-1 vs fusion on frozen datasets.

Usage:
  python -m evaluation.run_eval
  python -m evaluation.run_eval --skip-paysim-load  # if paysim_eval.db exists
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.features import (  # noqa: E402
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
    extract_all_features,
    get_active_accounts,
)
from app.detection.fusion import (  # noqa: E402
    _slow_drip_synthetic_accounts,
    _synthetic_attack_accounts,
    compute_alert_threshold,
    compute_fused_scores,
    select_top_anomaly_accounts,
    top_anomaly_budget,
)
from app.detection.node_anomaly import compute_gdi_scores  # noqa: E402
from app.detection.structural_pass import score_peripheral_accounts  # noqa: E402
from app.models import Transaction  # noqa: E402
from app.settings_store import get_alert_top_percentile  # noqa: E402
from evaluation.baseline_rule import detect_baseline_accounts  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.freeze_snapshot import freeze_snapshot  # noqa: E402
from evaluation.metrics import EvalMetrics, compute_metrics  # noqa: E402
from evaluation.paysim_config import (  # noqa: E402
    PAYSIM_MIN_TRANSACTIONS_FOR_SCORING,
    PAYSIM_WINDOW_MINUTES,
    PAYSIM_WINDOW_STEPS,
)
from evaluation.ring_debug import format_ring_inspection, inspect_ring_detection  # noqa: E402
from evaluation.snapshot_analysis import (  # noqa: E402
    attack_type_recall,
    classify_attack_types,
    diagnose_fusion_misses,
)

PAYSIM_DB = BACKEND_ROOT / "evaluation" / "data" / "paysim_eval.db"
SNAPSHOT_DIR = BACKEND_ROOT / "snapshots"
RESULTS_CSV = BACKEND_ROOT / "evaluation" / "results.csv"
RESULTS_MD = BACKEND_ROOT / "evaluation" / "RESULTS.md"

# Previous run used single-window timestamp compression (invalid for temporal features).
@dataclass(frozen=True)
class DatasetEval:
    name: str
    db_path: Path
    ground_truth_label: str
    window_minutes: int
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING


def max_timestamp(db) -> datetime:
    value = db.scalar(select(func.max(Transaction.timestamp)))
    if value is None:
        raise ValueError("Dataset has no transactions")
    return value


def scored_universe(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
) -> set[str]:
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    return {row["account_id"] for row in features}


def ground_truth_accounts(
    db, as_of: datetime, *, window_minutes: int
) -> set[str]:
    return _synthetic_attack_accounts(db, as_of, window_minutes)


def predict_baseline(db, as_of: datetime, *, window_minutes: int) -> set[str]:
    return detect_baseline_accounts(db, as_of, window_minutes=window_minutes)


def predict_layer1(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
) -> set[str]:
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    if not features:
        return set()

    gdi_results = compute_gdi_scores(features)
    selected, _, _ = select_top_anomaly_accounts(gdi_results, "gdi_score")
    return selected


def predict_fusion(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
) -> set[str]:
    fused_results = compute_fused_scores(
        db,
        as_of,
        window_minutes=window_minutes,
        min_transactions=min_transactions,
    )
    if not fused_results:
        return set()

    selected, _, _ = select_top_anomaly_accounts(fused_results, "fused_score")
    return selected


def predict_hybrid(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
) -> set[str]:
    fused_results = compute_fused_scores(
        db,
        as_of,
        window_minutes=window_minutes,
        min_transactions=min_transactions,
    )
    main_pred, _, _ = select_top_anomaly_accounts(fused_results, "fused_score")
    peripheral = score_peripheral_accounts(db, as_of, window_minutes, main_pred)
    return main_pred | {row["account_id"] for row in peripheral}


def debug_threshold_selection(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
    label: str,
) -> dict:
    percentile = get_alert_top_percentile()
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    gdi_results = compute_gdi_scores(features)
    gdi_selected, gdi_threshold, gdi_k = select_top_anomaly_accounts(
        gdi_results, "gdi_score", percentile=percentile
    )

    fused_results = compute_fused_scores(
        db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
    )
    fusion_selected, fusion_threshold, fusion_k = select_top_anomaly_accounts(
        fused_results, "fused_score", percentile=percentile
    )

    # Legacy broken path for comparison
    gdi_scores = [float(r["gdi_score"]) for r in gdi_results]
    legacy_gdi_thr = float(np.percentile(gdi_scores, percentile * 100)) if gdi_scores else 0.0
    legacy_gdi_flagged = sum(1 for s in gdi_scores if s >= legacy_gdi_thr)

    info = {
        "label": label,
        "percentile": percentile,
        "scored_accounts": len(features),
        "gdi_budget_k": gdi_k,
        "gdi_threshold": gdi_threshold,
        "gdi_flagged": len(gdi_selected),
        "legacy_gdi_threshold": legacy_gdi_thr,
        "legacy_gdi_flagged": legacy_gdi_flagged,
        "fusion_budget_k": fusion_k,
        "fusion_threshold": fusion_threshold,
        "fusion_flagged": len(fusion_selected),
        "unique_gdi_scores": len({round(float(r["gdi_score"]), 4) for r in gdi_results}),
    }
    print(
        f"\n  --- Threshold debug ({label}) ---\n"
        f"  ALERT_TOP_PERCENTILE={percentile} (top {(1 - percentile) * 100:.1f}%)\n"
        f"  scored accounts: {info['scored_accounts']}  unique GDI scores: {info['unique_gdi_scores']}\n"
        f"  Layer1 rank-based: k={gdi_k}  threshold={gdi_threshold:.4f}  flagged={info['gdi_flagged']}\n"
        f"  Layer1 legacy (>= np.percentile): threshold={legacy_gdi_thr:.4f}  "
        f"flagged={legacy_gdi_flagged}  ← tie-broken bug when scores collapse\n"
        f"  Fusion rank-based: k={fusion_k}  threshold={fusion_threshold:.4f}  "
        f"flagged={info['fusion_flagged']}"
    )
    return info


def evaluate_detector(
    db,
    as_of: datetime,
    *,
    detector: str,
    universe: set[str],
    positives: set[str],
    window_minutes: int,
    min_transactions: int,
) -> EvalMetrics:
    if detector == "baseline":
        predicted = predict_baseline(db, as_of, window_minutes=window_minutes)
    elif detector == "layer1":
        predicted = predict_layer1(
            db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
        )
    elif detector == "fusion":
        predicted = predict_fusion(
            db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
        )
    else:
        raise ValueError(f"Unknown detector: {detector}")

    return compute_metrics(positives, predicted, universe)


def run_dataset_eval(dataset: DatasetEval) -> tuple[list[dict], dict]:
    session_factory = init_eval_db(dataset.db_path)
    db = session_factory()
    extras: dict = {}
    try:
        as_of = max_timestamp(db)
        window_minutes = dataset.window_minutes
        min_transactions = dataset.min_transactions

        debug_threshold_selection(
            db,
            as_of,
            window_minutes=window_minutes,
            min_transactions=min_transactions,
            label=dataset.name,
        )

        if dataset.name == "paysim":
            ring_stats = inspect_ring_detection(
                db, as_of, window_minutes=window_minutes
            )
            print("\n" + format_ring_inspection("PaySim", ring_stats))
            extras["ring_stats"] = ring_stats
        elif dataset.name == "snapshot":
            ring_stats = inspect_ring_detection(
                db, as_of, window_minutes=window_minutes
            )
            extras["ring_stats_snapshot"] = ring_stats

        universe = scored_universe(
            db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
        )
        all_positives = ground_truth_accounts(db, as_of, window_minutes=window_minutes)
        positives = all_positives & universe

        active = len(get_active_accounts(db, as_of, window_minutes))
        window_label = (
            f"{PAYSIM_WINDOW_STEPS} step(s) = {window_minutes}m"
            if dataset.name == "paysim"
            else f"{window_minutes}m"
        )
        print(
            f"\n=== {dataset.name} ===\n"
            f"  db: {dataset.db_path.name}\n"
            f"  as_of: {as_of.isoformat()}\n"
            f"  window: {window_label} | active accounts: {active}\n"
            f"  accounts evaluated (≥{min_transactions} tx): {len(universe)}\n"
            f"  ground-truth fraud in window: {len(all_positives)} "
            f"(scored: {len(positives)}, filtered: {len(all_positives) - len(positives)})"
        )

        rows: list[dict] = []
        detectors = ("baseline", "layer1", "fusion")
        if dataset.name == "snapshot":
            detectors = ("baseline", "layer1", "fusion", "hybrid")

        hybrid_universe = (
            set(get_active_accounts(db, as_of, window_minutes))
            if dataset.name == "snapshot"
            else universe
        )

        for detector in detectors:
            eval_universe = hybrid_universe if detector == "hybrid" else universe
            if detector == "baseline":
                predicted = predict_baseline(db, as_of, window_minutes=window_minutes)
            elif detector == "layer1":
                predicted = predict_layer1(
                    db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
                )
            elif detector == "fusion":
                predicted = predict_fusion(
                    db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
                )
            else:
                predicted = predict_hybrid(
                    db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
                )

            positives_eval = all_positives & eval_universe
            metrics = compute_metrics(positives_eval, predicted, eval_universe)
            ties = None
            if detector in ("layer1", "fusion"):
                scored = (
                    compute_gdi_scores(
                        extract_all_features(
                            db, as_of, window_minutes, min_transactions=min_transactions
                        )
                    )
                    if detector == "layer1"
                    else compute_fused_scores(
                        db, as_of, window_minutes=window_minutes,
                        min_transactions=min_transactions,
                    )
                )
                key = "gdi_score" if detector == "layer1" else "fused_score"
                ties = tie_diagnostics(scored, key, positives_eval)
                if ties["k"] and ties["slots_from_tie"] > ties["k"] / 2:
                    print(
                        f"  WARNING {dataset.name}/{detector}: {ties['slots_from_tie']} of "
                        f"{ties['k']} alert slots come from a {ties['tie_size']}-account tie "
                        f"at the cutoff. TP={metrics.tp} is a tie-break artifact; a random "
                        f"tie-break expects TP={ties['expected_tp_random_tiebreak']:.2f}."
                    )
            row = {
                "dataset": dataset.name,
                "detector": detector,
                "as_of": as_of.isoformat(),
                "window_minutes": window_minutes,
                "min_transactions_for_scoring": min_transactions,
                "accounts_evaluated": len(eval_universe),
                "ground_truth_fraud_in_window": len(all_positives),
                "ground_truth_fraud_scored": len(positives_eval),
                "ground_truth_filtered_out": len(all_positives) - len(positives_eval),
                **metrics.as_dict(),
                "tie_slots": ties["slots_from_tie"] if ties else "",
                "tie_size": ties["tie_size"] if ties else "",
                "expected_tp_random_tiebreak": (
                    round(ties["expected_tp_random_tiebreak"], 3) if ties else ""
                ),
            }
            rows.append(row)
            print(
                f"  {detector:8s}  P={metrics.precision:.3f}  R={metrics.recall:.3f}  "
                f"F1={metrics.f1:.3f}  FPR={metrics.fpr:.4f}\n"
                f"           TP={metrics.tp} FP={metrics.fp} FN={metrics.fn} TN={metrics.tn} "
                f"| fraud={metrics.positives} evaluated={len(eval_universe)}"
            )

        if dataset.name == "snapshot":
            extras.update(
                _snapshot_extras(
                    db, as_of, window_minutes, min_transactions, universe, rows
                )
            )

        return rows, extras
    finally:
        db.close()


def _snapshot_extras(
    db,
    as_of: datetime,
    window_minutes: int,
    min_transactions: int,
    universe: set[str],
    rows: list[dict],
) -> dict:
    all_synth, fast, slow = classify_attack_types(
        db, as_of, window_minutes=window_minutes
    )
    fusion_pred = predict_fusion(
        db, as_of, window_minutes=window_minutes, min_transactions=min_transactions
    )

    breakdowns = []
    for attack_type, group in (
        ("fast_fan_in_fan_out", fast),
        ("slow_drip", slow),
        ("all_synthetic", all_synth),
    ):
        breakdowns.append(
            attack_type_recall(
                positives=group & universe,
                universe=universe,
                predicted=fusion_pred,
                attack_type=attack_type,
                total_in_window=group,
            )
        )

    diagnoses = diagnose_fusion_misses(
        db,
        as_of,
        window_minutes=window_minutes,
        min_transactions=min_transactions,
        universe=universe,
        predicted=fusion_pred,
        max_examples=3,
    )

    print("\n  --- Snapshot attack-type recall (fusion) ---")
    for b in breakdowns:
        print(
            f"  {b.attack_type:22s}  in_window={b.total_in_window:3d}  "
            f"scored={b.in_scored_universe:3d}  filtered={b.filtered_out:3d}  "
            f"detected={b.detected:3d}  recall={b.recall_scored:.1%}"
        )

    print("\n  --- Missed account diagnosis (fusion, up to 3) ---")
    for miss in diagnoses:
        print(f"  {miss.account_id} ({miss.attack_type}): {miss.reason}")
        if miss.features:
            f = miss.features
            print(
                f"    features: in_deg={f['in_degree']} out_deg={f['out_degree']} "
                f"in_cnt={f['in_count']} out_cnt={f['out_count']} "
                f"velocity={f['velocity']:.2f} fan_ratio={f['fan_ratio']:.2f}"
            )
        if miss.fused_score is not None:
            print(
                f"    scores: gdi={miss.gdi_score:.3f} ring={miss.ring_risk_score:.3f} "
                f"fused={miss.fused_score:.3f} threshold={miss.alert_threshold:.3f} "
                f"rank={miss.fused_rank}/{miss.accounts_scored}"
            )

    return {
        "breakdowns": breakdowns,
        "diagnoses": diagnoses,
        "fusion_row": next(r for r in rows if r["detector"] == "fusion"),
    }


def print_results_table(rows: list[dict]) -> None:
    metric_rows = [r for r in rows if "f1" in r]
    print("\n" + "=" * 120)
    print("EVALUATION RESULTS (rates + raw counts)")
    print("=" * 120)
    print(
        f"{'dataset':<10} {'detector':<9} {'P':>6} {'R':>6} {'F1':>6} {'FPR':>7} "
        f"{'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4} "
        f"{'fraud':>5} {'eval':>5}"
    )
    print("-" * 120)
    for row in metric_rows:
        print(
            f"{row['dataset']:<10} {row['detector']:<9} "
            f"{row['precision']:6.3f} {row['recall']:6.3f} {row['f1']:6.3f} "
            f"{row['fpr']:7.4f} "
            f"{row['tp']:4d} {row['fp']:4d} {row['fn']:4d} {row['tn']:4d} "
            f"{row['ground_truth_fraud_scored']:5d} {row['accounts_evaluated']:5d}"
        )
    print("=" * 120)
    print("fraud = ground-truth fraud accounts in scored universe | eval = accounts evaluated")


def save_results_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metric_rows = [r for r in rows if "f1" in r]
    if not metric_rows:
        return
    fieldnames = list(metric_rows[0].keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metric_rows)


def patch_results_md(rows: list[dict]) -> None:
    """Update only the table rows this script computed.

    RESULTS.md is a hand-maintained log with sections owned by other scripts,
    so this never rewrites the file: each ``| dataset | detector |`` row it
    measured is replaced in place, and everything else is left untouched.
    """
    text = RESULTS_MD.read_text()
    for r in (r for r in rows if "f1" in r):
        prefix = f"| {r['dataset']} | {r['detector']} |"
        pattern = re.compile(rf"^{re.escape(prefix)}.*$", re.M)
        if not pattern.search(text):
            raise SystemExit(
                f"RESULTS.md has no row starting {prefix!r}; add it by hand first."
            )
        new_line = (
            f"{prefix} {r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} | "
            f"{r['fpr']:.4f} | {r['tp']} | {r['fp']} | {r['fn']} | {r['tn']} | "
            f"{r['ground_truth_fraud_scored']} | {r['accounts_evaluated']} |"
        )
        text = pattern.sub(new_line, text, count=1)
    RESULTS_MD.write_text(text)


def tie_diagnostics(rows: list[dict], key: str, positives: set[str]) -> dict:
    """How much of the top-k budget is decided by a tie at the cutoff score.

    With heavily tied scores the selected set is fixed by row order, not by the
    detector. ``expected_tp_random_tiebreak`` is what a uniformly random pick
    from the tie would score, which is the honest reference for such rows.
    """
    ranked = sorted(rows, key=lambda row: -float(row[key]))
    k = top_anomaly_budget(len(ranked))
    if k == 0:
        return {"k": 0, "tie_size": 0, "slots_from_tie": 0, "expected_tp_random_tiebreak": 0.0}
    cutoff = float(ranked[k - 1][key])
    above = [row for row in ranked if float(row[key]) > cutoff]
    tied = [row for row in ranked if float(row[key]) == cutoff]
    slots = k - len(above)
    fraud_above = sum(row["account_id"] in positives for row in above)
    fraud_tied = sum(row["account_id"] in positives for row in tied)
    return {
        "k": k,
        "tie_size": len(tied),
        "slots_from_tie": slots,
        "expected_tp_random_tiebreak": fraud_above + slots * fraud_tied / len(tied),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GraphDrift offline evaluation")
    parser.add_argument("--skip-paysim-load", action="store_true")
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument("--paysim-rows", type=int, default=250_000)
    parser.add_argument("--snapshot-label", type=str, default="2026-08-12")
    args = parser.parse_args()

    if not args.skip_paysim_load or not PAYSIM_DB.exists():
        print("Loading PaySim sample…")
        from evaluation.load_paysim import main as load_main

        sys.argv = [
            "load_paysim",
            "--max-rows",
            str(args.paysim_rows),
            "--cap-transactions",
            "12000",
            "--output",
            str(PAYSIM_DB),
        ]
        load_main()

    if not args.skip_snapshot:
        print("Freezing internal snapshot…")
        snapshot_path = freeze_snapshot(
            BACKEND_ROOT / "graphdrift.db",
            SNAPSHOT_DIR,
            label=args.snapshot_label,
        )
    else:
        # The cited snapshot rows belong to one specific frozen snapshot; never
        # silently fall back to whichever file sorts last.
        snapshot_path = SNAPSHOT_DIR / f"graphdrift_snapshot_{args.snapshot_label}.db"
        if not snapshot_path.exists():
            raise FileNotFoundError(f"{snapshot_path} not found; run freeze_snapshot first")

    print(f"Using snapshot: {snapshot_path}")

    datasets = [
        DatasetEval(
            "snapshot",
            snapshot_path,
            "is_synthetic_attack",
            WINDOW_MINUTES,
        ),
        DatasetEval(
            "paysim",
            PAYSIM_DB,
            "isFraud→account",
            PAYSIM_WINDOW_MINUTES,
            PAYSIM_MIN_TRANSACTIONS_FOR_SCORING,
        ),
    ]

    all_rows: list[dict] = []
    for dataset in datasets:
        rows, _ = run_dataset_eval(dataset)
        all_rows.extend(rows)

    print_results_table(all_rows)
    save_results_csv(all_rows, RESULTS_CSV)
    patch_results_md(all_rows)
    print(f"\nSaved: {RESULTS_CSV}")
    print(f"Saved: {RESULTS_MD}")


if __name__ == "__main__":
    main()
