#!/usr/bin/env python3
"""Run baseline / Layer-1 / fusion / hybrid on each multi-seed snapshot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.features import (  # noqa: E402
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
    get_active_accounts,
)
from app.detection.fusion import (  # noqa: E402
    compute_fused_scores_multiscale,
)
from app.detection.structural_pass import score_peripheral_accounts  # noqa: E402
from app.models import Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.generate_multi_seed_snapshots import SEEDS, SNAPSHOT_DIR  # noqa: E402
from evaluation.metrics import compute_metrics  # noqa: E402
from evaluation.run_eval import (  # noqa: E402
    ground_truth_accounts,
    max_timestamp,
    predict_baseline,
    predict_fusion,
    predict_layer1,
    scored_universe,
)
from evaluation.snapshot_analysis import classify_attack_types  # noqa: E402

RESULTS_MD = BACKEND_ROOT / "evaluation" / "RESULTS.md"
REPORT_JSON = BACKEND_ROOT / "evaluation" / "data" / "multiseed_eval.json"
ORIGINAL_FUSION_F1 = 0.625
DETECTORS = ("baseline", "layer1", "fusion", "fusion_multiscale", "hybrid")
DETECTOR_LABELS = {
    "fusion_multiscale": "fusion_multiscale (union)",
    "hybrid": "hybrid (union+peri)",
}


def predict_fusion_multiscale(db, as_of, *, min_transactions: int) -> set[str]:
    # Returned rows are already the union of per-scale top-k. Do not re-cut.
    fused = compute_fused_scores_multiscale(db, as_of, min_transactions=min_transactions)
    return {row["account_id"] for row in fused}


def predict_hybrid_multiscale(db, as_of, *, min_transactions: int) -> set[str]:
    fused = compute_fused_scores_multiscale(db, as_of, min_transactions=min_transactions)
    main_pred = {row["account_id"] for row in fused}
    peripheral = score_peripheral_accounts(db, as_of, WINDOW_MINUTES, main_pred)
    return main_pred | {row["account_id"] for row in peripheral}


def eval_one(path: Path, seed: int, detectors: tuple[str, ...] = DETECTORS) -> list[dict]:
    factory = init_eval_db(path)
    db = factory()
    rows = []
    try:
        as_of = max_timestamp(db)
        universe = scored_universe(
            db,
            as_of,
            window_minutes=WINDOW_MINUTES,
            min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
        )
        all_pos = ground_truth_accounts(db, as_of, window_minutes=WINDOW_MINUTES)
        active = set(get_active_accounts(db, as_of, WINDOW_MINUTES))
        _, fast, slow = classify_attack_types(db, as_of, window_minutes=WINDOW_MINUTES)
        print(
            f"\n=== seed {seed} {path.name} ===\n"
            f"  as_of={as_of.isoformat()}  scored={len(universe)}  "
            f"active={len(active)}  GT={len(all_pos)}  "
            f"fast={len(fast)} slow={len(slow)}",
            flush=True,
        )
        preds = {}
        if "baseline" in detectors:
            preds["baseline"] = predict_baseline(db, as_of, window_minutes=WINDOW_MINUTES)
        if "layer1" in detectors:
            preds["layer1"] = predict_layer1(
                db,
                as_of,
                window_minutes=WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            )
        if "fusion" in detectors:
            preds["fusion"] = predict_fusion(
                db,
                as_of,
                window_minutes=WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            )
        if "fusion_multiscale" in detectors:
            preds["fusion_multiscale"] = predict_fusion_multiscale(
                db, as_of, min_transactions=MIN_TRANSACTIONS_FOR_SCORING
            )
        if "hybrid" in detectors:
            preds["hybrid"] = predict_hybrid_multiscale(
                db, as_of, min_transactions=MIN_TRANSACTIONS_FOR_SCORING
            )
        for name in detectors:
            eval_universe = active if name == "hybrid" else universe
            positives = all_pos & eval_universe
            metrics = compute_metrics(positives, preds[name], eval_universe)
            row = {
                "seed": seed,
                "detector": name,
                "accounts_evaluated": len(eval_universe),
                "ground_truth_fraud_scored": len(positives),
                "window_fast": len(fast),
                "window_slow": len(slow),
                **metrics.as_dict(),
            }
            rows.append(row)
            print(
                f"  {name:20s} P={metrics.precision:.3f} R={metrics.recall:.3f} "
                f"F1={metrics.f1:.3f} FPR={metrics.fpr:.4f} "
                f"TP={metrics.tp} FP={metrics.fp} FN={metrics.fn} TN={metrics.tn}",
                flush=True,
            )
        return rows
    finally:
        db.close()


def _agg(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def aggregate(rows: list[dict], detectors: tuple[str, ...] | None = None) -> dict[str, dict]:
    out = {}
    names = detectors or DETECTORS
    for det in names:
        subset = [r for r in rows if r["detector"] == det]
        if not subset:
            continue
        out[det] = {
            metric: _agg([float(r[metric]) for r in subset])
            for metric in ("precision", "recall", "f1", "fpr")
        }
        out[det]["n"] = len(subset)
    return out


def _fmt(stat: dict) -> str:
    return f"{stat['mean']:.3f} ± {stat['std']:.3f} [{stat['min']:.3f}, {stat['max']:.3f}]"


def patch_results_md(per_seed: list[dict], agg: dict) -> None:
    text = RESULTS_MD.read_text()
    marker = "## Multi-seed synthetic snapshot robustness"
    end_marker = "<!-- /multi-seed -->"
    block = _results_section(per_seed, agg).rstrip() + f"\n\n{end_marker}\n\n"
    if marker in text and end_marker in text:
        start = text.find(marker)
        end = text.find(end_marker) + len(end_marker)
        text = text[:start] + block + text[end:].lstrip("\n")
    elif marker in text:
        start = text.find(marker)
        nxt = text.find("\n## ", start + 3)
        text = text[:start] + block + (text[nxt + 1 :] if nxt >= 0 else "")
    else:
        ibm = text.find("## IBM HI-Small evaluation corpus")
        interp = text.find("## Interpretation")
        insert_at = interp if interp >= 0 else ibm
        if insert_at >= 0:
            text = text[:insert_at] + block + text[insert_at:]
        else:
            text = text.rstrip() + "\n\n" + block
    RESULTS_MD.write_text(text)


def _results_section(per_seed: list[dict], agg: dict) -> str:
    fusion = agg["fusion"]["f1"]
    orig = ORIGINAL_FUSION_F1
    mean_f1 = fusion["mean"]
    if orig > fusion["max"]:
        vs = (
            f"optimistic relative to this distribution (above all 5 seeds; "
            f"max={fusion['max']:.3f})"
        )
    elif orig < fusion["min"]:
        vs = (
            f"pessimistic relative to this distribution (below all 5 seeds; "
            f"min={fusion['min']:.3f})"
        )
    elif abs(orig - mean_f1) <= fusion["std"]:
        vs = f"roughly typical (within 1 std of the 5-seed mean {mean_f1:.3f})"
    elif orig > mean_f1:
        vs = f"somewhat optimistic vs the 5-seed mean {mean_f1:.3f}"
    else:
        vs = f"somewhat pessimistic vs the 5-seed mean {mean_f1:.3f}"

    high_var = fusion["std"] > 0.1
    var_note = (
        f"Fusion F1 **std={fusion['std']:.3f} > 0.1** — performance is unstable "
        "across seeds; a single-run headline is not a stable system property."
        if high_var
        else f"Fusion F1 std={fusion['std']:.3f} (≤ 0.1); seed-to-seed spread is modest."
    )

    seed_lines = [
        "| Seed | Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud | Eval |",
        "|------|----------|---|---|----|-----|----|----|----|----|-------|------|",
    ]
    for r in per_seed:
        label = DETECTOR_LABELS.get(r["detector"], r["detector"])
        seed_lines.append(
            f"| {r['seed']} | {label} | {r['precision']:.3f} | {r['recall']:.3f} | "
            f"{r['f1']:.3f} | {r['fpr']:.4f} | {r['tp']} | {r['fp']} | {r['fn']} | {r['tn']} | "
            f"{r['ground_truth_fraud_scored']} | {r['accounts_evaluated']} |"
        )

    agg_lines = [
        "| Detector | Precision | Recall | F1 | FPR |",
        "|----------|-----------|--------|----|-----|",
    ]
    for det in DETECTORS:
        a = agg[det]
        label = DETECTOR_LABELS.get(det, det)
        agg_lines.append(
            f"| {label} | {_fmt(a['precision'])} | {_fmt(a['recall'])} | "
            f"{_fmt(a['f1'])} | {_fmt(a['fpr'])} |"
        )

    return f"""## Multi-seed synthetic snapshot robustness

The snapshot rows in the table above are **one historical run** (`graphdrift_snapshot_2026-08-12.db`, fusion F1=0.625). This section replaces that single-run framing with **mean ± std (min, max) across 5 seeded offline traces**.

Each seed runs the same simulator for 5,551 steps (185.0 min at 2s/loop), matching that snapshot's duration. Detection uses a 15-minute window at `max(timestamp)`. **Universe:** ≥3 transactions for baseline / Layer 1 / fusion / fusion_multiscale (same as the original fusion F1=0.625 row). Hybrid = multi-scale fusion + peripheral cascade on **all active** accounts (same universe as the original hybrid row).

Seeds: 42, 123, 7, 2026, 99. Reproduce: `python -m evaluation.generate_multi_seed_snapshots` then `python -m evaluation.eval_multi_seed`.

### Per-seed metrics

{chr(10).join(seed_lines)}

### Aggregated (mean ± std [min, max], n=5)

{chr(10).join(agg_lines)}

**Headline fusion (15-min, ≥3-tx universe) vs original 0.625:** the original single-snapshot fusion F1 is **{vs}**. {var_note} The paper should report **fusion F1 = {mean_f1:.3f} ± {fusion['std']:.3f}** (range {fusion['min']:.3f}–{fusion['max']:.3f}) rather than 0.625 as a point estimate.
"""


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--detectors",
        default=",".join(DETECTORS),
        help="Comma-separated detector names to run",
    )
    parser.add_argument(
        "--no-patch-results",
        action="store_true",
        help="Print metrics only; do not rewrite RESULTS.md",
    )
    parser.add_argument(
        "--force-patch-results",
        action="store_true",
        help="Overwrite the multi-seed RESULTS.md block (drops methodology A/B notes).",
    )
    args = parser.parse_args()
    detectors = tuple(name.strip() for name in args.detectors.split(",") if name.strip())
    unknown = [name for name in detectors if name not in DETECTORS]
    if unknown:
        raise SystemExit(f"Unknown detectors: {unknown}")

    all_rows: list[dict] = []
    for seed in SEEDS:
        path = SNAPSHOT_DIR / f"multiseed_seed{seed}.db"
        if not path.exists():
            raise SystemExit(f"Missing {path}; run python -m evaluation.generate_multi_seed_snapshots")
        all_rows.extend(eval_one(path, seed, detectors=detectors))

    agg = aggregate(all_rows, detectors=detectors)
    print("\n========== aggregate mean ± std [min, max] ==========")
    for det in detectors:
        a = agg[det]
        print(f"  {det:20s} F1={_fmt(a['f1'])}  P={_fmt(a['precision'])}  R={_fmt(a['recall'])}  FPR={_fmt(a['fpr'])}")

    if args.no_patch_results:
        print("Skipped RESULTS.md patch (--no-patch-results)")
        return

    if set(detectors) != set(DETECTORS):
        raise SystemExit("Refusing to patch RESULTS.md with a partial detector set")

    text = RESULTS_MD.read_text()
    if "Methodology check: live freeze" in text and not args.force_patch_results:
        print(
            "Skipped RESULTS.md patch: multi-seed section has methodology notes. "
            "Re-run with --force-patch-results only if you intend to replace them."
        )
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps({"per_seed": all_rows, "aggregate": agg}, indent=2))
        print(f"Wrote {REPORT_JSON}")
        return

    fusion_mean = agg["fusion"]["f1"]["mean"]
    fusion_std = agg["fusion"]["f1"]["std"]
    print(
        f"\nOriginal snapshot fusion F1=0.625 vs 5-seed mean {fusion_mean:.3f} ± {fusion_std:.3f} "
        f"[{agg['fusion']['f1']['min']:.3f}, {agg['fusion']['f1']['max']:.3f}]"
    )

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps({"per_seed": all_rows, "aggregate": agg}, indent=2))
    patch_results_md(all_rows, agg)
    print(f"Wrote {REPORT_JSON}")
    print(f"Updated {RESULTS_MD}")


if __name__ == "__main__":
    main()
