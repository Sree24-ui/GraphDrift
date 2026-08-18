#!/usr/bin/env python3
"""
IBM HI-Small dense-slice evaluation.

1. Confirm single-window occupancy after ÷332 compression
2. Audit Layer-1 feature distributions vs the simulator snapshot
3. Run baseline / Layer-1 / fusion on ibm_aml_eval.db

Does not rewrite snapshot/PaySim RESULTS.md tables.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
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
)
from app.models import Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.ibm_aml_config import IBM_AML_TIME_SCALE, IBM_AML_WINDOW_MINUTES  # noqa: E402
from evaluation.metrics import compute_metrics  # noqa: E402
from evaluation.run_eval import (  # noqa: E402
    predict_baseline,
    predict_fusion,
    predict_layer1,
)

IBM_DB = BACKEND_ROOT / "evaluation" / "data" / "ibm_aml_eval.db"
SNAPSHOT_DB = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-12.db"
REPORT_JSON = BACKEND_ROOT / "evaluation" / "data" / "ibm_aml_eval_report.json"
RESULTS_MD = BACKEND_ROOT / "evaluation" / "RESULTS.md"

FEATURE_KEYS = [
    "burstiness",
    "velocity",
    "in_degree",
    "out_degree",
    "fan_ratio",
    "amount_entropy",
    "counterparty_diversity",
]


def _pct(arr: np.ndarray, q: float) -> float:
    if len(arr) == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def _summarize(arr: np.ndarray) -> dict[str, float]:
    if len(arr) == 0:
        return {
            "n": 0,
            "mean": float("nan"),
            "std": float("nan"),
            "min": float("nan"),
            "p10": float("nan"),
            "median": float("nan"),
            "p90": float("nan"),
            "max": float("nan"),
            "frac_zero": float("nan"),
            "n_unique": 0,
        }
    return {
        "n": int(len(arr)),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "p10": _pct(arr, 10),
        "median": float(np.median(arr)),
        "p90": _pct(arr, 90),
        "max": float(arr.max()),
        "frac_zero": float(np.mean(arr == 0.0)),
        "n_unique": int(len(np.unique(np.round(arr, 6)))),
    }


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(pos > neg) + 0.5 P(equal). 0.5 = no separation."""
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    try:
        from scipy.stats import mannwhitneyu

        u = mannwhitneyu(pos, neg, alternative="greater").statistic
        return float(u / (len(pos) * len(neg)))
    except Exception:
        # Rank-sum fallback
        combined = np.concatenate([pos, neg])
        order = combined.argsort(kind="mergesort")
        ranks = np.empty(len(combined), dtype=float)
        ranks[order] = np.arange(1, len(combined) + 1)
        # average ties
        _, inv, counts = np.unique(combined, return_inverse=True, return_counts=True)
        if np.any(counts > 1):
            sums = np.bincount(inv, weights=ranks)
            avg = sums / counts
            ranks = avg[inv]
        u = ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2.0
        return float(u / (len(pos) * len(neg)))


def _hist(arr: np.ndarray, edges: np.ndarray) -> list[int]:
    counts, _ = np.histogram(arr, bins=edges)
    return [int(c) for c in counts]


def labeled_fraud_accounts(db, as_of: datetime, window_minutes: int) -> set[str]:
    window_start = as_of - timedelta(minutes=window_minutes)
    rows = db.execute(
        select(Transaction.sender_id, Transaction.receiver_id).where(
            Transaction.timestamp >= window_start,
            Transaction.timestamp <= as_of,
            Transaction.is_labeled_fraud.is_(True),
        )
    ).all()
    accounts: set[str] = set()
    for sender_id, receiver_id in rows:
        accounts.add(sender_id)
        accounts.add(receiver_id)
    return accounts


def synthetic_accounts(db, as_of: datetime, window_minutes: int) -> set[str]:
    from app.detection.fusion import _synthetic_attack_accounts

    return _synthetic_attack_accounts(db, as_of, window_minutes)


def window_occupancy(db) -> dict:
    n = db.scalar(select(func.count()).select_from(Transaction))
    tmin = db.scalar(select(func.min(Transaction.timestamp)))
    tmax = db.scalar(select(func.max(Transaction.timestamp)))
    span_min = (tmax - tmin).total_seconds() / 60.0

    # Non-overlapping 15-minute bins from tmin.
    bin_width = timedelta(minutes=WINDOW_MINUTES)
    occupied = set()
    rows = db.execute(select(Transaction.timestamp)).all()
    for (ts,) in rows:
        offset = ts - tmin
        bin_idx = int(offset.total_seconds() // (WINDOW_MINUTES * 60))
        occupied.add(bin_idx)

    # Sliding as_of = max(ts): production eval window.
    eval_start = tmax - timedelta(minutes=WINDOW_MINUTES)
    in_15 = db.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.timestamp >= eval_start,
            Transaction.timestamp <= tmax,
        )
    )
    eval_start_60 = tmax - timedelta(minutes=60)
    in_60 = db.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.timestamp >= eval_start_60,
            Transaction.timestamp <= tmax,
        )
    )
    fraud_total = db.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.is_labeled_fraud.is_(True)
        )
    )
    fraud_in_15 = db.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.is_labeled_fraud.is_(True),
            Transaction.timestamp >= eval_start,
            Transaction.timestamp <= tmax,
        )
    )

    return {
        "n_transactions": int(n),
        "min_ts": tmin.isoformat(),
        "max_ts": tmax.isoformat(),
        "span_minutes": span_min,
        "nonoverlapping_15m_bins_occupied": len(occupied),
        "occupied_bin_indices": sorted(occupied),
        "eval_as_of": tmax.isoformat(),
        "frac_all_tx_in_15m_eval_window": in_15 / n if n else 0.0,
        "frac_all_tx_in_60m_eval_window": in_60 / n if n else 0.0,
        "n_in_15m": int(in_15),
        "n_in_60m": int(in_60),
        "fraud_tx_total": int(fraud_total),
        "fraud_tx_in_15m": int(fraud_in_15),
        "time_scale": IBM_AML_TIME_SCALE,
    }


def feature_audit(
    db,
    as_of: datetime,
    positives: set[str],
    *,
    window_minutes: int,
    min_transactions: int,
    label: str,
) -> dict:
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    fraud_rows = [r for r in features if r["account_id"] in positives]
    legit_rows = [r for r in features if r["account_id"] not in positives]

    per_feature = {}
    for key in FEATURE_KEYS:
        pos = np.array([float(r[key]) for r in fraud_rows], dtype=float)
        neg = np.array([float(r[key]) for r in legit_rows], dtype=float)
        auc = _auc(pos, neg)
        auc_inv = _auc(neg, pos)
        best_auc = max(auc, auc_inv) if auc == auc else float("nan")
        direction = "higher=fraud" if auc >= auc_inv else "lower=fraud"
        combined = np.concatenate([pos, neg]) if len(pos) and len(neg) else pos
        if len(combined):
            lo, hi = float(combined.min()), float(combined.max())
            if lo == hi:
                edges = np.array([lo, hi + 1e-9])
            else:
                edges = np.linspace(lo, hi, 9)
        else:
            edges = np.array([0.0, 1.0])
        degenerate = bool(
            (len(pos) and pos.std() < 1e-9)
            or (np.isfinite(best_auc) and best_auc < 0.55)
        )
        per_feature[key] = {
            "fraud": _summarize(pos),
            "legit": _summarize(neg),
            "auc_higher_is_fraud": auc,
            "auc_best": best_auc,
            "direction": direction,
            "hist_edges": [float(x) for x in edges],
            "hist_fraud": _hist(pos, edges) if len(pos) else [],
            "hist_legit": _hist(neg, edges) if len(neg) else [],
            "degenerate": degenerate,
            "note": _feature_note(key, pos, neg, best_auc),
        }

    samples = []
    rng = np.random.default_rng(0)
    for rows, tag in ((fraud_rows, "labeled_fraud"), (legit_rows, "labeled_legit")):
        if not rows:
            continue
        idx = rng.choice(len(rows), size=min(5, len(rows)), replace=False)
        for i in idx:
            row = rows[int(i)]
            samples.append(
                {
                    "class": tag,
                    "account_id": row["account_id"],
                    **{k: float(row[k]) for k in FEATURE_KEYS},
                }
            )

    return {
        "label": label,
        "as_of": as_of.isoformat(),
        "n_scored": len(features),
        "n_fraud_scored": len(fraud_rows),
        "n_legit_scored": len(legit_rows),
        "n_fraud_in_window": len(positives),
        "features": per_feature,
        "samples": samples,
    }


def _feature_note(key: str, pos: np.ndarray, neg: np.ndarray, auc: float) -> str:
    if len(pos) == 0:
        return "no scored fraud accounts"
    if pos.std() < 1e-9 and (len(neg) == 0 or neg.std() < 1e-9):
        return "near-constant on both classes"
    if pos.std() < 1e-9:
        return "near-constant on fraud"
    if not np.isfinite(auc) or auc < 0.55:
        return "does not separate fraud from legit (AUC≈0.5)"
    if auc < 0.65:
        return "weak separation"
    return "separates at least weakly"


def run_detectors(db, as_of: datetime, positives: set[str]) -> list[dict]:
    universe = {
        row["account_id"]
        for row in extract_all_features(
            db, as_of, IBM_AML_WINDOW_MINUTES, min_transactions=MIN_TRANSACTIONS_FOR_SCORING
        )
    }
    scored_pos = positives & universe
    print("  scoring baseline / layer1 / fusion…", flush=True)
    rows = []
    for name, pred_fn in (
        ("baseline", lambda: predict_baseline(db, as_of, window_minutes=IBM_AML_WINDOW_MINUTES)),
        (
            "layer1",
            lambda: predict_layer1(
                db,
                as_of,
                window_minutes=IBM_AML_WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            ),
        ),
        (
            "fusion",
            lambda: predict_fusion(
                db,
                as_of,
                window_minutes=IBM_AML_WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            ),
        ),
    ):
        print(f"  running {name}…", flush=True)
        pred = pred_fn()
        metrics = compute_metrics(scored_pos, pred, universe)
        rows.append(
            {
                "dataset": "ibm_aml_dense_slice",
                "detector": name,
                "as_of": as_of.isoformat(),
                "window_minutes": IBM_AML_WINDOW_MINUTES,
                "accounts_evaluated": len(universe),
                "ground_truth_fraud_in_window": len(positives),
                "ground_truth_fraud_scored": len(scored_pos),
                "ground_truth_filtered_out": len(positives) - len(scored_pos),
                **metrics.as_dict(),
            }
        )
        print(
            f"  {name:8s}  P={metrics.precision:.3f}  R={metrics.recall:.3f}  "
            f"F1={metrics.f1:.3f}  FPR={metrics.fpr:.4f}  "
            f"TP={metrics.tp} FP={metrics.fp} FN={metrics.fn} TN={metrics.tn}"
        )
    return rows


def _fmt_sum(s: dict) -> str:
    return (
        f"n={s['n']} mean={s['mean']:.3f} median={s['median']:.3f} "
        f"p10={s['p10']:.3f} p90={s['p90']:.3f} frac0={s['frac_zero']:.2%} "
        f"unique={s['n_unique']}"
    )


def print_audit(occ: dict, ibm: dict, snap: dict) -> None:
    print("\n========== STEP 1 — window occupancy ==========")
    print(f"  transactions: {occ['n_transactions']:,}")
    print(f"  span: {occ['span_minutes']:.2f} eval minutes  ({occ['min_ts']} → {occ['max_ts']})")
    print(
        f"  distinct occupied 15-min bins (non-overlapping from tmin): "
        f"{occ['nonoverlapping_15m_bins_occupied']}"
    )
    print(
        f"  fraction of ALL txs in 15-min eval window at max(ts): "
        f"{occ['frac_all_tx_in_15m_eval_window']:.4%}  "
        f"({occ['n_in_15m']:,}/{occ['n_transactions']:,})"
    )
    print(
        f"  fraction of ALL txs in 60-min eval window: "
        f"{occ['frac_all_tx_in_60m_eval_window']:.4%}"
    )
    print(
        f"  labeled-fraud txs in that 15-min window: "
        f"{occ['fraud_tx_in_15m']}/{occ['fraud_tx_total']}"
    )
    print(
        "  CONCERN CONFIRMED"
        if occ["nonoverlapping_15m_bins_occupied"] <= 1
        and occ["frac_all_tx_in_15m_eval_window"] > 0.999
        else "  concern NOT matched — multiple windows occupied"
    )

    print("\n========== STEP 3 — feature distributions (scored accounts, ≥3 tx) ==========")
    for audit in (ibm, snap):
        print(
            f"\n  --- {audit['label']}  scored={audit['n_scored']}  "
            f"fraud={audit['n_fraud_scored']}  legit={audit['n_legit_scored']} ---"
        )
        for key in FEATURE_KEYS:
            f = audit["features"][key]
            flag = "DEGENERATE/WEAK" if f["degenerate"] else "ok"
            print(f"  {key:24s} [{flag}]  AUC_best={f['auc_best']:.3f} ({f['direction']})  {f['note']}")
            print(f"      fraud  {_fmt_sum(f['fraud'])}")
            print(f"      legit  {_fmt_sum(f['legit'])}")
        print("  samples:")
        for s in audit["samples"]:
            print(
                f"    {s['class']:14s} burst={s['burstiness']:.3f} vel={s['velocity']:.3f} "
                f"fan={s['fan_ratio']:.2f} in_deg={s['in_degree']:.0f}"
            )


def patch_results_md(occ: dict, ibm: dict, snap: dict, detector_rows: list[dict]) -> None:
    text = RESULTS_MD.read_text()
    start = text.find("## IBM HI-Small evaluation corpus")
    if start < 0:
        raise SystemExit("RESULTS.md missing IBM section to patch")
    next_h2 = text.find("\n## Reproduce", start)
    if next_h2 < 0:
        next_h2 = len(text)

    ibm_burst = ibm["features"]["burstiness"]
    ibm_vel = ibm["features"]["velocity"]
    snap_burst = snap["features"]["burstiness"]
    snap_vel = snap["features"]["velocity"]

    det_lines = [
        "| Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud† | Eval‡ |",
        "|----------|---|---|----|-----|----|----|----|----|--------|-------|",
    ]
    for r in detector_rows:
        det_lines.append(
            f"| {r['detector']} | {r['precision']:.3f} | {r['recall']:.3f} | "
            f"{r['f1']:.3f} | {r['fpr']:.4f} | {r['tp']} | {r['fp']} | {r['fn']} | "
            f"{r['tn']} | {r['ground_truth_fraud_scored']} | {r['accounts_evaluated']} |"
        )

    feat_lines = [
        "| Feature | IBM fraud median | IBM legit median | IBM AUC | Snapshot fraud median | Snapshot legit median | Snapshot AUC | IBM verdict |",
        "|---------|------------------|------------------|---------|-----------------------|-----------------------|--------------|-------------|",
    ]
    for key in FEATURE_KEYS:
        a = ibm["features"][key]
        b = snap["features"][key]
        verdict = "degenerate/weak" if a["degenerate"] else "still informative"
        feat_lines.append(
            f"| {key} | {a['fraud']['median']:.3f} | {a['legit']['median']:.3f} | "
            f"{a['auc_best']:.3f} | {b['fraud']['median']:.3f} | {b['legit']['median']:.3f} | "
            f"{b['auc_best']:.3f} | {verdict} |"
        )

    section = f"""## IBM HI-Small evaluation corpus

**Patterns file:** `HI-Small_Patterns.txt` is included on the HuggingFace mirror (`OsamaMIT/IBM-AML-HI-Small`) and was used to measure real laundering-ring durations (370 labeled typology instances). Median ring duration is **74.7 hours**. Compression is **÷{IBM_AML_TIME_SCALE}** so that median sits at ~13.5 compressed minutes (slow-drip analog in the 60-min window).

`ibm_aml_eval.db`: 250,000 txs / 1,058 labeled-fraud txs / densest 48 IBM hours `2022-09-08 02:11` → `2022-09-10 02:11`.

### Evaluation mode — dense-slice, single-window (not temporal-burst-against-background)

This is a **different evaluation mode** from the simulator snapshot and from PaySim. Do **not** read IBM numbers in the same sense as snapshot/PaySim rows in the table above.

| Mode | What the detector sees | Example |
|------|------------------------|---------|
| **Temporal-burst-against-background** (simulator / intended PaySim) | Attacks are localized bursts. A 15-min window contains the ring *plus* surrounding normal traffic from the same period; other windows contain mostly background. Time-relative features (velocity, burstiness) contrast burst vs quiet. | Simulator fan-in/out 2–3 min inside a 15-min slice of ongoing UPI traffic |
| **Dense-slice, single-window** (this IBM subsample) | After ÷{IBM_AML_TIME_SCALE}, the entire 48h dense slice occupies **{occ['span_minutes']:.2f} eval minutes**. There is **{occ['nonoverlapping_15m_bins_occupied']}** occupied 15-min bin. **{occ['frac_all_tx_in_15m_eval_window']:.1%}** of all {occ['n_transactions']:,} transactions (fraud and legitimate) fall in the single 15-min eval window at `max(timestamp)`. There is no second window of “just background.” | 250k txs, mix of 48h of IBM activity, scored together |

Compressing *less* would spread txs across more windows but would put the 74.7h median ring outside every detection window — the original problem. We accept the single-window mode rather than fake a simulator-like backdrop.

**Limitation — laundering-dense 48-hour slice.** The window was chosen because it is laundering-dense, not randomly sampled. Full HI-Small fraud rate is **~0.10%**; this slice is **~0.42%**. Results are evaluation on a dense slice, not a claim about corpus base rates.

### Layer-1 feature audit (scored accounts, ≥3 tx in the 15-min window)

Compared to snapshot `{SNAPSHOT_DB.name}` (temporal-burst mode). AUC is Mann–Whitney P(class A > class B); 0.50 = no ranking signal. “Degenerate/weak” = near-constant or AUC < 0.55.

IBM scored: {ibm['n_scored']} accounts ({ibm['n_fraud_scored']} labeled-fraud, {ibm['n_legit_scored']} legit). Snapshot scored: {snap['n_scored']} ({snap['n_fraud_scored']} synthetic-fraud, {snap['n_legit_scored']} legit).

{chr(10).join(feat_lines)}

**Burstiness (IBM):** fraud median {ibm_burst['fraud']['median']:.3f} vs legit {ibm_burst['legit']['median']:.3f}, AUC {ibm_burst['auc_best']:.3f}, {ibm_burst['fraud']['frac_zero']:.0%} of fraud and {ibm_burst['legit']['frac_zero']:.0%} of legit at 0. {ibm_burst['note']}. On the snapshot, burstiness fraud median {snap_burst['fraud']['median']:.3f} vs legit {snap_burst['legit']['median']:.3f}, AUC {snap_burst['auc_best']:.3f}.

**Velocity (IBM):** fraud median {ibm_vel['fraud']['median']:.3f} vs legit {ibm_vel['legit']['median']:.3f}, AUC {ibm_vel['auc_best']:.3f}. Velocity is `tx_count / 15` over a window that already contains the entire subsample, so it is a **global degree/count feature**, not a local burst rate. Snapshot velocity AUC {snap_vel['auc_best']:.3f} (fraud median {snap_vel['fraud']['median']:.3f} vs legit {snap_vel['legit']['median']:.3f}).

### Detector comparison — IBM dense-slice, single-window only

Ground truth: accounts touching `Is Laundering=1` transactions (`is_labeled_fraud`). Same 15-min `as_of=max(timestamp)` protocol as other evals, **interpreted under the mode above**. Not comparable without that qualification to snapshot/PaySim F1.

{chr(10).join(det_lines)}

† Fraud = labeled-laundering accounts with ≥3 transactions in the (single) window. ‡ Eval = scored universe.

Reproduce: `python -m evaluation.analyze_ibm_aml_timing` then `python -m evaluation.load_ibm_aml` then `python -m evaluation.run_ibm_aml_eval`.

"""
    RESULTS_MD.write_text(text[:start] + section + text[next_h2 + 1 :])


def main() -> None:
    print("Starting IBM AML dense-slice eval…", flush=True)
    if not IBM_DB.exists():
        raise SystemExit(f"Missing {IBM_DB}")

    ibm_sf = init_eval_db(IBM_DB)
    ibm_db = ibm_sf()
    try:
        occ = window_occupancy(ibm_db)
        print(
            f"Occupancy: span={occ['span_minutes']:.2f}m bins={occ['nonoverlapping_15m_bins_occupied']} "
            f"frac_15={occ['frac_all_tx_in_15m_eval_window']:.4%}",
            flush=True,
        )
        as_of = datetime.fromisoformat(occ["eval_as_of"])
        ibm_pos = labeled_fraud_accounts(ibm_db, as_of, IBM_AML_WINDOW_MINUTES)
        print(f"IBM as_of={as_of.isoformat()} labeled-fraud accounts in window={len(ibm_pos)}", flush=True)
        print("Extracting IBM features…", flush=True)
        ibm_audit = feature_audit(
            ibm_db,
            as_of,
            ibm_pos,
            window_minutes=IBM_AML_WINDOW_MINUTES,
            min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            label="ibm_aml_eval.db (÷332 dense slice)",
        )
    finally:
        ibm_db.close()

    snap_audit = {
        "label": "snapshot (unavailable)",
        "n_scored": 0,
        "n_fraud_scored": 0,
        "n_legit_scored": 0,
        "features": {
            k: {
                "fraud": _summarize(np.array([])),
                "legit": _summarize(np.array([])),
                "auc_best": float("nan"),
                "direction": "n/a",
                "degenerate": True,
                "note": "snapshot db missing",
                "hist_edges": [],
                "hist_fraud": [],
                "hist_legit": [],
            }
            for k in FEATURE_KEYS
        },
        "samples": [],
    }
    if SNAPSHOT_DB.exists():
        snap_sf = init_eval_db(SNAPSHOT_DB)
        snap_db = snap_sf()
        try:
            tmax = snap_db.scalar(select(func.max(Transaction.timestamp)))
            snap_pos = synthetic_accounts(snap_db, tmax, WINDOW_MINUTES)
            snap_audit = feature_audit(
                snap_db,
                tmax,
                snap_pos,
                window_minutes=WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
                label=f"{SNAPSHOT_DB.name} (simulator)",
            )
        finally:
            snap_db.close()

    print_audit(occ, ibm_audit, snap_audit)

    print("\n========== STEP 4 — detectors (after feature audit) ==========")
    ibm_sf = init_eval_db(IBM_DB)
    ibm_db = ibm_sf()
    try:
        as_of = datetime.fromisoformat(occ["eval_as_of"])
        ibm_pos = labeled_fraud_accounts(ibm_db, as_of, IBM_AML_WINDOW_MINUTES)
        detector_rows = run_detectors(ibm_db, as_of, ibm_pos)
    finally:
        ibm_db.close()

    report = {
        "occupancy": occ,
        "ibm_features": ibm_audit,
        "snapshot_features": snap_audit,
        "detectors": detector_rows,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str))
    patch_results_md(occ, ibm_audit, snap_audit, detector_rows)
    print(f"\nWrote {REPORT_JSON}")
    print(f"Updated IBM section in {RESULTS_MD}")


if __name__ == "__main__":
    main()
