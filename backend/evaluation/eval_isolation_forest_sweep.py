#!/usr/bin/env python3
"""Isolation Forest hyperparameter sensitivity + seed-42 reproducibility check."""

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
from evaluation.eval_isolation_forest import GRAPHDRIFT  # noqa: E402
from evaluation.eval_multi_seed import _agg, _fmt  # noqa: E402
from evaluation.generate_multi_seed_snapshots import SEEDS, SNAPSHOT_DIR  # noqa: E402
from evaluation.isolation_forest_baseline import (  # noqa: E402
    _feature_matrix,
    score_isolation_forest,
    select_if_topk,
)
from evaluation.metrics import compute_metrics  # noqa: E402
from evaluation.run_eval import (  # noqa: E402
    ground_truth_accounts,
    max_timestamp,
    scored_universe,
)

RESULTS_MD = BACKEND_ROOT / "evaluation" / "RESULTS.md"
REPORT_JSON = BACKEND_ROOT / "evaluation" / "data" / "isolation_forest_sweep.json"

CONFIGS = (
    {
        "name": "sklearn_default",
        "cite": True,
        "n_estimators": 100,
        "max_samples": "auto",
        "max_features": 1.0,
        "note": "Original eval; sklearn IsolationForest defaults (n_jobs=1 only extra)",
    },
    {
        "name": "n_estimators_300",
        "cite": False,
        "n_estimators": 300,
        "max_samples": "auto",
        "max_features": 1.0,
        "note": "More trees at n≈140",
    },
    {
        "name": "max_samples_64",
        "cite": False,
        "n_estimators": 100,
        "max_samples": 64,
        "max_features": 1.0,
        "note": "Smaller bootstrap sample than auto≈n",
    },
    {
        "name": "max_features_0.7",
        "cite": False,
        "n_estimators": 100,
        "max_samples": "auto",
        "max_features": 0.7,
        "note": "Partial feature subsample per split",
    },
)

VARIANTS = (
    ("isolation_forest_l1_features", False),
    ("isolation_forest_all_features", True),
)


def _load_seed(path: Path, seed: int) -> dict:
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
        packed = {}
        for name, structural in VARIANTS:
            ids, matrix, meta = _feature_matrix(
                db,
                as_of,
                window_minutes=WINDOW_MINUTES,
                min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
                include_structural=structural,
            )
            packed[name] = {"ids": ids, "matrix": matrix, "meta": meta}
        return {
            "seed": seed,
            "universe": universe,
            "positives": positives,
            "variants": packed,
        }
    finally:
        db.close()


def _run_config(bundle: dict, variant: str, cfg: dict) -> dict:
    packed = bundle["variants"][variant]
    scores, diag = score_isolation_forest(
        packed["matrix"],
        random_state=bundle["seed"],
        n_estimators=cfg["n_estimators"],
        max_samples=cfg["max_samples"],
        max_features=cfg["max_features"],
    )
    diag.update(packed["meta"])
    selected, diag = select_if_topk(packed["ids"], scores, diag)
    metrics = compute_metrics(bundle["positives"], selected, bundle["universe"])
    return {
        "seed": bundle["seed"],
        "detector": variant,
        "config": cfg["name"],
        "selected": sorted(selected),
        "scores": scores.tolist(),
        **metrics.as_dict(),
        "if_diagnostics": diag,
    }


def format_section(sweep: dict) -> str:
    gdi = GRAPHDRIFT["layer1"]["f1"]
    fusion = GRAPHDRIFT["fusion"]["f1"]
    rows = [
        "| Config | n_estimators | max_samples | max_features | IF-L1 mean F1 | IF-all mean F1 | vs GDI 0.251 | vs fusion 0.284 |",
        "|--------|--------------|-------------|--------------|---------------|----------------|--------------|-----------------|",
    ]
    l1_means = []
    all_means = []
    for cfg in CONFIGS:
        name = cfg["name"]
        block = sweep["by_config"][name]
        l1 = block["isolation_forest_l1_features"]["f1"]["mean"]
        allf = block["isolation_forest_all_features"]["f1"]["mean"]
        l1_means.append(l1)
        all_means.append(allf)
        mark = " (cite default)" if cfg["cite"] else ""
        rows.append(
            f"| `{name}`{mark} | {cfg['n_estimators']} | {cfg['max_samples']} | "
            f"{cfg['max_features']} | {l1:.3f} | {allf:.3f} | "
            f"{gdi - l1:+.3f} | {fusion - allf:+.3f} |"
        )

    repro = sweep["repro"]
    identical = repro["selected_identical"] and repro["scores_allclose"]
    rec = sweep["recommendation"]

    return f"""## Isolation Forest sensitivity

Original eval used sklearn **defaults** except `random_state=seed` and `n_jobs=1`:
`n_estimators=100`, `max_samples='auto'` (min(256, n) ≈ all ~140 rows), `max_features=1.0`,
`contamination='auto'` (labels unused; we rank `-score_samples` with the same top-5% cut).
This is a 4-config sensitivity check, not a search to beat GraphDrift.

### Sweep (mean F1, n=5 seeds)

{chr(10).join(rows)}

IF-L1 F1 range across configs: **{min(l1_means):.3f}–{max(l1_means):.3f}** (median {np.median(l1_means):.3f}).
IF-all F1 range: **{min(all_means):.3f}–{max(all_means):.3f}** (median {np.median(all_means):.3f}).
Cite **`sklearn_default`** (most defensible: published sklearn defaults, not the best-of-sweep).

### Reproducibility (seed 42, sklearn_default, twice)

Selected-set identical: **{repro['selected_identical']}**. Score arrays allclose: **{repro['scores_allclose']}**.
L1 F1 both runs: {repro['l1_f1_a']:.6f} / {repro['l1_f1_b']:.6f}. All-features F1: {repro['all_f1_a']:.6f} / {repro['all_f1_b']:.6f}.

### Recommendation for §4.4

{rec}

Reproduce: `python -m evaluation.eval_isolation_forest_sweep`.

<!-- /isolation-forest-sweep -->
"""


def patch_results_md(block: str) -> None:
    marker = "## Isolation Forest sensitivity"
    end_marker = "<!-- /isolation-forest-sweep -->"
    text = RESULTS_MD.read_text()
    if marker in text and end_marker in text:
        start = text.find(marker)
        end = text.find(end_marker) + len(end_marker)
        text = text[:start] + block.rstrip() + "\n" + text[end:].lstrip("\n")
    else:
        insert = text.find("<!-- /isolation-forest -->")
        if insert >= 0:
            insert = insert + len("<!-- /isolation-forest -->")
            text = text[:insert] + "\n\n" + block.rstrip() + "\n" + text[insert:]
        else:
            text = text.rstrip() + "\n\n" + block
    RESULTS_MD.write_text(text)


def _recommend(by_config: dict) -> str:
    gdi = GRAPHDRIFT["layer1"]["f1"]
    fusion = GRAPHDRIFT["fusion"]["f1"]
    l1_vals = [
        by_config[c["name"]]["isolation_forest_l1_features"]["f1"]["mean"]
        for c in CONFIGS
    ]
    all_vals = [
        by_config[c["name"]]["isolation_forest_all_features"]["f1"]["mean"]
        for c in CONFIGS
    ]
    default_l1 = by_config["sklearn_default"]["isolation_forest_l1_features"]["f1"]["mean"]
    default_all = by_config["sklearn_default"]["isolation_forest_all_features"]["f1"]["mean"]
    best_l1 = max(l1_vals)
    best_all = max(all_vals)
    worst_gap_l1 = gdi - best_l1
    worst_gap_all = fusion - best_all

    parts = [
        f"Cite Isolation Forest at sklearn defaults: L1 F1 **{default_l1:.3f}**, "
        f"L1+structural F1 **{default_all:.3f}** (GraphDrift Layer 1 **{gdi:.3f}**, "
        f"fusion **{fusion:.3f}**)."
    ]
    if worst_gap_l1 > 0.01 and worst_gap_all > 0.01:
        parts.append(
            f"The GraphDrift edge holds across the sweep: even the best IF-L1 "
            f"({best_l1:.3f}) stays {worst_gap_l1:.3f} below GDI, and the best IF-all "
            f"({best_all:.3f}) stays {worst_gap_all:.3f} below fusion. Range is tight "
            f"(L1 {min(l1_vals):.3f}–{max(l1_vals):.3f}; all {min(all_vals):.3f}–{max(all_vals):.3f}), "
            "so the original +0.029 / +0.049 is not an artifact of n_estimators=100."
        )
    elif worst_gap_l1 <= 0 or worst_gap_all <= 0:
        winner = "IF-L1" if worst_gap_l1 <= 0 else "IF-all"
        parts.append(
            f"**A tested configuration closes or reverses the gap** ({winner}). "
            "Do not cite the default-only delta as robust. Cite the sweep range "
            "and drop the claim that GraphDrift's algorithm beats Isolation Forest "
            "on these features."
        )
    else:
        parts.append(
            f"The default-config edge shrinks under some settings (best IF-L1 {best_l1:.3f}, "
            f"best IF-all {best_all:.3f}). Report the range, cite sklearn defaults, "
            "and do not overstate the algorithm win."
        )
    parts.append(
        "Do not replace the cited IF row with the best-of-sweep number."
    )
    return " ".join(parts)


def main() -> None:
    bundles = []
    for seed in SEEDS:
        path = SNAPSHOT_DIR / f"multiseed_seed{seed}.db"
        if not path.exists():
            raise SystemExit(f"Missing {path}")
        print(f"Loading seed {seed}…", flush=True)
        bundles.append(_load_seed(path, seed))

    by_config: dict = {}
    per_rows: list[dict] = []
    for cfg in CONFIGS:
        print(f"\n=== config {cfg['name']} ===", flush=True)
        variant_rows: dict[str, list[dict]] = {v: [] for v, _ in VARIANTS}
        for bundle in bundles:
            for variant, _ in VARIANTS:
                row = _run_config(bundle, variant, cfg)
                slim = {k: v for k, v in row.items() if k not in ("scores", "selected")}
                per_rows.append(slim)
                variant_rows[variant].append(row)
                print(
                    f"  seed={bundle['seed']} {variant:34s} F1={row['f1']:.3f} "
                    f"unique={row['if_diagnostics']['n_unique_scores']}",
                    flush=True,
                )
        by_config[cfg["name"]] = {
            variant: {
                metric: _agg([float(r[metric]) for r in rows])
                for metric in ("precision", "recall", "f1", "fpr")
            }
            for variant, rows in variant_rows.items()
        }
        for variant, _ in VARIANTS:
            a = by_config[cfg["name"]][variant]
            print(f"  MEAN {variant:34s} F1={_fmt(a['f1'])}", flush=True)

    # Repro: seed 42, default config, twice (new IF fits on cached matrix).
    b42 = next(b for b in bundles if b["seed"] == 42)
    cfg0 = CONFIGS[0]
    first = {v: _run_config(b42, v, cfg0) for v, _ in VARIANTS}
    second = {v: _run_config(b42, v, cfg0) for v, _ in VARIANTS}
    repro = {
        "selected_identical": all(
            first[v]["selected"] == second[v]["selected"] for v, _ in VARIANTS
        ),
        "scores_allclose": all(
            np.allclose(first[v]["scores"], second[v]["scores"], rtol=0, atol=0)
            for v, _ in VARIANTS
        ),
        "l1_f1_a": first["isolation_forest_l1_features"]["f1"],
        "l1_f1_b": second["isolation_forest_l1_features"]["f1"],
        "all_f1_a": first["isolation_forest_all_features"]["f1"],
        "all_f1_b": second["isolation_forest_all_features"]["f1"],
        "n_score_diffs_l1": int(
            np.sum(
                np.asarray(first["isolation_forest_l1_features"]["scores"])
                != np.asarray(second["isolation_forest_l1_features"]["scores"])
            )
        ),
    }
    print(
        f"\nRepro seed 42: selected_identical={repro['selected_identical']} "
        f"scores_allclose={repro['scores_allclose']} "
        f"score_diffs_l1={repro['n_score_diffs_l1']}",
        flush=True,
    )

    recommendation = _recommend(by_config)
    print("\n" + recommendation)
    payload = {
        "original_run": {
            "n_estimators": 100,
            "max_samples": "auto",
            "max_features": 1.0,
            "contamination": "auto",
            "n_jobs": 1,
            "bootstrap": False,
            "note": "sklearn defaults except random_state=seed and n_jobs=1",
        },
        "configs": list(CONFIGS),
        "by_config": by_config,
        "per_seed": per_rows,
        "repro": repro,
        "recommendation": recommendation,
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    patch_results_md(format_section(payload))
    print(f"Wrote {REPORT_JSON}")


if __name__ == "__main__":
    main()
