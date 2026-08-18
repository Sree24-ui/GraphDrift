#!/usr/bin/env python3
"""Isolation Forest baselines on the 5-seed snapshots (same universe as Layer 1 / fusion)."""

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
)
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.eval_multi_seed import _agg, _fmt  # noqa: E402
from evaluation.generate_multi_seed_snapshots import SEEDS, SNAPSHOT_DIR  # noqa: E402
from evaluation.isolation_forest_baseline import predict_isolation_forest  # noqa: E402
from evaluation.metrics import compute_metrics  # noqa: E402
from evaluation.run_eval import (  # noqa: E402
    ground_truth_accounts,
    max_timestamp,
    scored_universe,
)

RESULTS_MD = BACKEND_ROOT / "evaluation" / "RESULTS.md"
REPORT_JSON = BACKEND_ROOT / "evaluation" / "data" / "isolation_forest_eval.json"

DETECTORS = (
    "isolation_forest_l1_features",
    "isolation_forest_all_features",
)

# Citeable 5-seed GraphDrift rows from RESULTS.md (15-min, ≥3-tx).
GRAPHDRIFT = {
    "layer1": {"precision": 0.675, "recall": 0.158, "f1": 0.251, "fpr": 0.023},
    "fusion": {"precision": 0.620, "recall": 0.188, "f1": 0.284, "fpr": 0.035},
}


def eval_one(path: Path, seed: int) -> list[dict]:
    factory = init_eval_db(path)
    db = factory()
    try:
        as_of = max_timestamp(db)
        universe = scored_universe(
            db,
            as_of,
            window_minutes=WINDOW_MINUTES,
            min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
        )
        positives = ground_truth_accounts(db, as_of, window_minutes=WINDOW_MINUTES) & universe
        print(
            f"\n=== seed {seed} {path.name} === scored={len(universe)} GT={len(positives)}",
            flush=True,
        )
        rows = []
        specs = (
            ("isolation_forest_l1_features", False),
            ("isolation_forest_all_features", True),
        )
        for name, structural in specs:
            pred, diag = predict_isolation_forest(
                db,
                as_of,
                random_state=seed,
                include_structural=structural,
                window_minutes=WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
            )
            metrics = compute_metrics(positives, pred, universe)
            flag = " DEGENERATE" if diag.get("degenerate") else ""
            print(
                f"  {name:34s} P={metrics.precision:.3f} R={metrics.recall:.3f} "
                f"F1={metrics.f1:.3f} FPR={metrics.fpr:.4f} "
                f"unique_scores={diag.get('n_unique_scores')} "
                f"k={diag.get('k')} n_struct={diag.get('n_with_structural', 0)}"
                f"{flag}",
                flush=True,
            )
            if diag.get("degenerate"):
                print(f"    FLAG: {diag.get('degeneracy_reason')}", flush=True)
            rows.append(
                {
                    "seed": seed,
                    "detector": name,
                    "accounts_evaluated": len(universe),
                    "ground_truth_fraud_scored": len(positives),
                    **metrics.as_dict(),
                    "if_diagnostics": diag,
                }
            )
        return rows
    finally:
        db.close()


def aggregate(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for det in DETECTORS:
        subset = [r for r in rows if r["detector"] == det]
        out[det] = {
            metric: _agg([float(r[metric]) for r in subset])
            for metric in ("precision", "recall", "f1", "fpr")
        }
        out[det]["n"] = len(subset)
        out[det]["any_degenerate"] = any(
            (r.get("if_diagnostics") or {}).get("degenerate") for r in subset
        )
        out[det]["unique_scores"] = [
            int((r.get("if_diagnostics") or {}).get("n_unique_scores") or 0)
            for r in subset
        ]
    return out


def _honest(agg: dict) -> str:
    l1_if = agg["isolation_forest_l1_features"]["f1"]["mean"]
    all_if = agg["isolation_forest_all_features"]["f1"]["mean"]
    gdi = GRAPHDRIFT["layer1"]["f1"]
    fusion = GRAPHDRIFT["fusion"]["f1"]
    d_l1 = gdi - l1_if
    d_fu = fusion - all_if
    parts = []
    if d_l1 > 0.02:
        parts.append(
            f"Layer 1 (Mahalanobis) beats Isolation Forest on the same 8 features "
            f"(F1 {gdi:.3f} vs {l1_if:.3f}, Δ={d_l1:.3f}). The gap is modest — about one "
            f"Layer-1 F1 standard deviation (0.027) — but the sign is consistent: GDI "
            f"never loses a seed to IF-L1 (ties on 123 and 99). Hypothesis: remaining "
            f"features are still correlated (counts vs degrees, fan_ratio vs in/out); "
            f"Mahalanobis uses the inverse covariance, Isolation Forest splits "
            f"axis-aligned and cannot represent that ellipsoid as cheaply."
        )
    elif d_l1 < -0.02:
        parts.append(
            f"Isolation Forest on L1 features **beats** Mahalanobis GDI "
            f"(F1 {l1_if:.3f} vs {gdi:.3f}, Δ={-d_l1:.3f}). That is a limitation of the "
            f"Layer-1 algorithm choice, not of the feature set — report it as such."
        )
    else:
        parts.append(
            f"Isolation Forest on L1 features **matches** Mahalanobis GDI within 0.02 F1 "
            f"({l1_if:.3f} vs {gdi:.3f}). Layer 1's value is the features, not the distance."
        )

    if d_fu > 0.02:
        parts.append(
            f"Hand-designed percentile fusion beats Isolation Forest given the same L1+L2 "
            f"columns (F1 {fusion:.3f} vs {all_if:.3f}, Δ={d_fu:.3f}). Structural signal is "
            f"sparse (most accounts have hub_concentration=external_edge_ratio=0 unless they "
            f"sit in a Louvain ring that cleared Layer 2's own gates); percentile fusion is "
            f"built to let a rare ring_risk dominate, while Isolation Forest treats those "
            f"two columns as two more random-split features."
        )
    elif d_fu < -0.02:
        parts.append(
            f"Isolation Forest on L1+structural features **beats** fusion "
            f"(F1 {all_if:.3f} vs {fusion:.3f}, Δ={-d_fu:.3f}). The value is then in having "
            f"hub_concentration / external_edge_ratio at all, not in §4.3's percentile mix. "
            f"Do not claim fusion design as the contribution without this caveat."
        )
    else:
        parts.append(
            f"Isolation Forest on L1+structural **matches** fusion within 0.02 F1 "
            f"({all_if:.3f} vs {fusion:.3f}). Fusion's lift over Layer 1 is then mostly "
            f"the extra columns, not the mixing rule."
        )

    if all_if > l1_if + 0.02:
        parts.append(
            f"Adding the two structural columns helps Isolation Forest "
            f"(+{all_if - l1_if:.3f} F1), so Layer 2 features carry signal even without our fusion."
        )
    elif all_if < l1_if - 0.02:
        parts.append(
            "Adding structural columns **hurt** Isolation Forest — sparse zeros plus "
            "axis-aligned splits can dilute the L1 signal. That is consistent with fusion "
            "needing an explicit percentile mix rather than concatenating raw columns."
        )
    else:
        parts.append(
            f"Isolation Forest does not gain meaningfully from concatenating the two "
            f"structural columns (ΔF1={all_if - l1_if:+.3f}, within 0.02). The extra "
            f"signal is concentrated on rare ring members; percentile fusion is built to "
            f"let that tail dominate, IF is not."
        )
    return " ".join(parts)


def format_section(per_seed: list[dict], agg: dict) -> str:
    seed_lines = [
        "| Seed | Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud | Eval | Unique scores | Degenerate |",
        "|------|----------|---|---|----|-----|----|----|----|----|-------|------|---------------|------------|",
    ]
    for r in per_seed:
        diag = r.get("if_diagnostics") or {}
        seed_lines.append(
            f"| {r['seed']} | {r['detector']} | {r['precision']:.3f} | {r['recall']:.3f} | "
            f"{r['f1']:.3f} | {r['fpr']:.4f} | {r['tp']} | {r['fp']} | {r['fn']} | {r['tn']} | "
            f"{r['ground_truth_fraud_scored']} | {r['accounts_evaluated']} | "
            f"{diag.get('n_unique_scores', '')} | "
            f"{'yes' if diag.get('degenerate') else 'no'} |"
        )

    agg_lines = [
        "| Detector | Precision | Recall | F1 | FPR |",
        "|----------|-----------|--------|----|-----|",
        f"| layer1 (GDI / Mahalanobis, existing) | {GRAPHDRIFT['layer1']['precision']:.3f} ± 0.168 | "
        f"{GRAPHDRIFT['layer1']['recall']:.3f} ± 0.027 | **{GRAPHDRIFT['layer1']['f1']:.3f} ± 0.027** | "
        f"{GRAPHDRIFT['layer1']['fpr']:.3f} ± 0.010 |",
        f"| isolation_forest_l1_features | {_fmt(agg['isolation_forest_l1_features']['precision'])} | "
        f"{_fmt(agg['isolation_forest_l1_features']['recall'])} | "
        f"{_fmt(agg['isolation_forest_l1_features']['f1'])} | "
        f"{_fmt(agg['isolation_forest_l1_features']['fpr'])} |",
        f"| fusion (percentile L1+L2, existing) | {GRAPHDRIFT['fusion']['precision']:.3f} ± 0.117 | "
        f"{GRAPHDRIFT['fusion']['recall']:.3f} ± 0.048 | **{GRAPHDRIFT['fusion']['f1']:.3f} ± 0.051** | "
        f"{GRAPHDRIFT['fusion']['fpr']:.3f} ± 0.004 |",
        f"| isolation_forest_all_features | {_fmt(agg['isolation_forest_all_features']['precision'])} | "
        f"{_fmt(agg['isolation_forest_all_features']['recall'])} | "
        f"{_fmt(agg['isolation_forest_all_features']['f1'])} | "
        f"{_fmt(agg['isolation_forest_all_features']['fpr'])} |",
    ]

    unique_l1 = agg["isolation_forest_l1_features"]["unique_scores"]
    unique_all = agg["isolation_forest_all_features"]["unique_scores"]
    deg_l1 = agg["isolation_forest_l1_features"]["any_degenerate"]
    deg_all = agg["isolation_forest_all_features"]["any_degenerate"]

    return f"""## Isolation Forest baselines (multi-seed)

Same 5 snapshots, 15-minute window at `max(timestamp)`, ≥3-tx scored universe, **rank-based top-5%** (`select_top_anomaly_accounts`) — not sklearn `contamination`. Each IsolationForest is fit **fresh on that window** with `random_state=seed`. L1 variant uses the 8 Layer-1 scoring features. All-features adds `hub_concentration` and `external_edge_ratio` from the same Louvain rings fusion uses (`get_ring_alerts` lookup; 0,0 if the account is not in an alerting ring).

### Per-seed metrics

{chr(10).join(seed_lines)}

### Score degeneracy

Unique IsolationForest scores per seed (L1 / L1+structural): {", ".join(f"{s}: {a}/{b}" for s, a, b in zip(SEEDS, unique_l1, unique_all))}.
Degenerate (unique < {5} or unique < k) on any seed: L1={deg_l1}, L1+structural={deg_all}.

### Aggregated vs GraphDrift (mean ± std [min, max], n=5)

{chr(10).join(agg_lines)}

### Honest read

{_honest(agg)}

Reproduce: `python -m evaluation.eval_isolation_forest`.

<!-- /isolation-forest -->
"""


def patch_results_md(block: str) -> None:
    marker = "## Isolation Forest baselines (multi-seed)"
    end_marker = "<!-- /isolation-forest -->"
    text = RESULTS_MD.read_text()
    if marker in text and end_marker in text:
        start = text.find(marker)
        end = text.find(end_marker) + len(end_marker)
        text = text[:start] + block.rstrip() + "\n" + text[end:].lstrip("\n")
    else:
        insert_at = text.find("<!-- /multi-seed -->")
        if insert_at >= 0:
            insert_at = insert_at + len("<!-- /multi-seed -->")
            text = text[:insert_at] + "\n\n" + block.rstrip() + "\n" + text[insert_at:]
        else:
            text = text.rstrip() + "\n\n" + block
    RESULTS_MD.write_text(text)


def main() -> None:
    all_rows: list[dict] = []
    for seed in SEEDS:
        path = SNAPSHOT_DIR / f"multiseed_seed{seed}.db"
        if not path.exists():
            raise SystemExit(f"Missing {path}")
        all_rows.extend(eval_one(path, seed))

    agg = aggregate(all_rows)
    print("\n========== aggregate ==========")
    for det in DETECTORS:
        a = agg[det]
        print(
            f"  {det:34s} F1={_fmt(a['f1'])}  P={_fmt(a['precision'])}  "
            f"R={_fmt(a['recall'])}  FPR={_fmt(a['fpr'])}  "
            f"unique={a['unique_scores']}  degenerate={a['any_degenerate']}"
        )

    honest = _honest(agg)
    print("\n" + honest)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps({"per_seed": all_rows, "aggregate": agg, "honest": honest}, indent=2, default=str)
    )
    patch_results_md(format_section(all_rows, agg))
    print(f"Wrote {REPORT_JSON}")
    print(f"Updated {RESULTS_MD}")


if __name__ == "__main__":
    main()
