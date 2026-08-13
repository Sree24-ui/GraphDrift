"""
Node-level anomaly scoring via Mahalanobis distance over account feature vectors.

GDI score mapping
-----------------
Raw Mahalanobis distances are unbounded. We squash them into the display range
[0.5, 5.0] with a clipped linear scale:

    clipped = min(raw_mahalanobis, RAW_DISTANCE_CLIP)
    gdi_score = GDI_MIN + (clipped / RAW_DISTANCE_CLIP) * (GDI_MAX - GDI_MIN)

RAW_DISTANCE_CLIP = 12.0 is chosen so that moderately anomalous accounts land
around 3–4 GDI and extreme outliers cap at 5.0.
"""

from __future__ import annotations

import numpy as np

# Features used for Mahalanobis distance. velocity is excluded because it is
# linearly derived from in_count + out_count and is highly collinear with them.
SCORING_FEATURE_NAMES: list[str] = [
    "in_degree",
    "out_degree",
    "in_count",
    "out_count",
    "amount_entropy",
    "counterparty_diversity",
    "fan_ratio",
    "burstiness",
]

DISPLAY_FEATURE_NAMES: list[str] = SCORING_FEATURE_NAMES + ["velocity"]

MIN_ACCOUNTS_FOR_FULL_COV = 10
VARIANCE_FLOOR = 1e-6
SHRINKAGE_ALPHA = 0.1
GDI_MIN = 0.5
GDI_MAX = 5.0
RAW_DISTANCE_CLIP = 12.0


def _feature_matrix(
    feature_list: list[dict],
    feature_names: list[str] = SCORING_FEATURE_NAMES,
) -> np.ndarray:
    return np.array(
        [[row[name] for name in feature_names] for row in feature_list],
        dtype=float,
    )


def _apply_shrinkage(cov: np.ndarray, alpha: float = SHRINKAGE_ALPHA) -> np.ndarray:
    """Ledoit-Wolf style shrinkage toward a scaled identity matrix."""
    n_features = cov.shape[0]
    trace_mean = float(np.trace(cov) / n_features)
    scaled_identity = trace_mean * np.eye(n_features)
    return (1.0 - alpha) * cov + alpha * scaled_identity


def _invert_covariance(cov: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        variances = np.clip(np.diag(cov), VARIANCE_FLOOR, None)
        return np.diag(1.0 / variances)


def compute_baseline(feature_list: list[dict]) -> dict:
    """
    Compute the population mean and inverse covariance for Mahalanobis scoring.

    When fewer than MIN_ACCOUNTS_FOR_FULL_COV accounts are present, a full
    covariance matrix is unstable or singular (more parameters than samples).
    In that fallback path we use a diagonal covariance built from per-feature
    variances only, which ignores cross-feature correlations but keeps
    Mahalanobis distance well-defined.

    For the full-covariance path, Ledoit-Wolf style shrinkage (SHRINKAGE_ALPHA)
    is applied to the sample covariance before inversion to guard against
    collinearity among the remaining features.
    """
    if not feature_list:
        raise ValueError("feature_list must contain at least one account")

    matrix = _feature_matrix(feature_list)
    mean = np.mean(matrix, axis=0)
    used_fallback = len(feature_list) < MIN_ACCOUNTS_FOR_FULL_COV

    if used_fallback:
        variances = np.var(matrix, axis=0, ddof=1) if len(feature_list) > 1 else np.zeros(
            len(SCORING_FEATURE_NAMES)
        )
        variances = np.clip(variances, VARIANCE_FLOOR, None)
        cov_raw = np.diag(variances)
        cov = cov_raw.copy()
    else:
        cov_raw = np.cov(matrix, rowvar=False)
        if cov_raw.ndim == 0:
            cov_raw = np.array([[float(cov_raw)]])
        cov_raw = np.atleast_2d(cov_raw)
        cov = _apply_shrinkage(cov_raw)

    cov_inv = _invert_covariance(cov)

    return {
        "mean": mean,
        "cov_inv": cov_inv,
        "cov_matrix_raw": cov_raw,
        "cov_matrix": cov,
        "feature_names": SCORING_FEATURE_NAMES.copy(),
        "used_fallback": used_fallback,
    }


def mahalanobis_distance(feature_vector: np.ndarray, baseline: dict) -> float:
    mean = baseline["mean"]
    cov_inv = baseline["cov_inv"]
    delta = feature_vector - mean
    squared = float(delta.T @ cov_inv @ delta)
    return float(np.sqrt(max(squared, 0.0)))


def _raw_to_gdi(raw_mahalanobis: float) -> float:
    clipped = min(raw_mahalanobis, RAW_DISTANCE_CLIP)
    return GDI_MIN + (clipped / RAW_DISTANCE_CLIP) * (GDI_MAX - GDI_MIN)


def _scoring_vector(feature_vector: dict | np.ndarray) -> np.ndarray:
    if isinstance(feature_vector, dict):
        return np.array(
            [feature_vector[name] for name in SCORING_FEATURE_NAMES], dtype=float
        )
    return np.asarray(feature_vector, dtype=float)


def _display_feature_dict(row: dict) -> dict:
    return {name: float(row[name]) for name in DISPLAY_FEATURE_NAMES if name in row}


def explain_score(feature_vector: dict | np.ndarray, baseline: dict) -> list[dict]:
    """
    Attribute Mahalanobis distance to individual scoring features.

    Uses a simplified per-dimension decomposition: for each feature i,
    contribution_i = ((x_i - mean_i) / std_i)^2, expressed as a percentage
    of the sum across features. This powers "PRIMARY TRIGGER: X contributed Y%"
    explainability panels in the UI.
    """
    vector = _scoring_vector(feature_vector)
    feature_names = baseline["feature_names"]
    mean = baseline["mean"]
    cov_inv = baseline["cov_inv"]
    variances = 1.0 / np.clip(np.diag(cov_inv), VARIANCE_FLOOR, None)
    stds = np.sqrt(variances)

    z_scores = (vector - mean) / stds
    squared_contributions = z_scores**2
    total = float(squared_contributions.sum())
    if total == 0.0:
        return [
            {
                "feature": name,
                "contribution_pct": 0.0,
                "z_score": float(z),
            }
            for name, z in zip(feature_names, z_scores)
        ]

    explanations = [
        {
            "feature": name,
            "contribution_pct": float((sq / total) * 100.0),
            "z_score": float(z),
        }
        for name, sq, z in zip(feature_names, squared_contributions, z_scores)
    ]
    explanations.sort(key=lambda item: item["contribution_pct"], reverse=True)
    return explanations


def compute_gdi_scores(feature_list: list[dict]) -> list[dict]:
    if not feature_list:
        return []

    baseline = compute_baseline(feature_list)
    results: list[dict] = []

    for row in feature_list:
        vector = np.array([row[name] for name in SCORING_FEATURE_NAMES], dtype=float)
        raw = mahalanobis_distance(vector, baseline)

        results.append(
            {
                "account_id": row["account_id"],
                "gdi_score": _raw_to_gdi(raw),
                "raw_mahalanobis": raw,
                "feature_vector": _display_feature_dict(row),
            }
        )

    return results


def _print_labeled_matrix(
    matrix: np.ndarray,
    labels: list[str],
    title: str,
    fmt: str = "{:7.3f}",
) -> None:
    print(title)
    col_width = max(len(label) for label in labels)
    header = " " * col_width + " | " + " | ".join(
        label[: col_width].rjust(col_width) for label in labels
    )
    print(header)
    print("-" * len(header))
    for label, row in zip(labels, matrix):
        values = " | ".join(fmt.format(value).rjust(col_width) for value in row)
        print(f"{label[:col_width].ljust(col_width)} | {values}")
    print()


def _print_gdi_table(scores: list[dict], baseline: dict, limit: int | None = None) -> None:
    if not scores:
        print("No accounts scored.")
        return

    sorted_scores = sorted(scores, key=lambda row: row["gdi_score"], reverse=True)
    if limit is not None:
        sorted_scores = sorted_scores[:limit]

    fallback_note = " (diagonal covariance fallback)" if baseline["used_fallback"] else ""
    print(f"Top {len(sorted_scores)} by GDI score{fallback_note}")
    print()

    header = f"{'account_id':<28} | {'gdi_score':>9} | {'raw_mahal':>9} | top_feature"
    print(header)
    print("-" * len(header))

    for row in sorted_scores:
        explanations = explain_score(row["feature_vector"], baseline)
        top = explanations[0]
        print(
            f"{row['account_id']:<28} | {row['gdi_score']:9.3f} | "
            f"{row['raw_mahalanobis']:9.3f} | "
            f"{top['feature']} ({top['contribution_pct']:.1f}%)"
        )


if __name__ == "__main__":
    import argparse
    import time
    from datetime import datetime

    from app.db import SessionLocal
    from app.detection.features import (
        WINDOW_MINUTES,
        extract_all_features,
        get_active_accounts,
    )

    parser = argparse.ArgumentParser(description="Run GraphDrift node anomaly scoring demo")
    parser.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Pause this many seconds before scoring so the simulator can populate the window",
    )
    args = parser.parse_args()

    if args.wait > 0:
        print(
            f"Waiting {args.wait}s — ensure the FastAPI server is running "
            f"(uvicorn app.main:app --reload) so the simulation loop can fill the window."
        )
        time.sleep(args.wait)

    db = SessionLocal()
    try:
        as_of = datetime.now()
        active_count = len(get_active_accounts(db, as_of))
        print(f"Active accounts in window: {active_count}")

        features = extract_all_features(db, as_of)
        baseline = compute_baseline(features) if features else None
        scores = compute_gdi_scores(features)

        print(f"Node anomaly scoring as_of={as_of.isoformat()} window={WINDOW_MINUTES}m")
        print(f"Baseline accounts (scored): {len(features)}")
        if baseline is not None:
            print(f"used_fallback: {baseline['used_fallback']}")
            print(f"Scoring features: {', '.join(SCORING_FEATURE_NAMES)}")
            print(f"Shrinkage alpha: {SHRINKAGE_ALPHA}")
            print()

            diagnostic_names = DISPLAY_FEATURE_NAMES
            diagnostic_matrix = _feature_matrix(features, diagnostic_names)
            diagnostic_corr = np.corrcoef(diagnostic_matrix, rowvar=False)
            _print_labeled_matrix(
                diagnostic_corr,
                diagnostic_names,
                "Correlation matrix (all extracted features incl. velocity — diagnostic)",
            )

            scoring_matrix = _feature_matrix(features)
            scoring_corr = np.corrcoef(scoring_matrix, rowvar=False)
            _print_labeled_matrix(
                scoring_corr,
                SCORING_FEATURE_NAMES,
                "Correlation matrix (scoring features — velocity excluded)",
            )

            cov_raw = baseline["cov_matrix_raw"]
            cov_reg = baseline["cov_matrix"]
            print(f"Condition number (raw covariance): {np.linalg.cond(cov_raw):.2f}")
            print(f"Condition number (regularized covariance): {np.linalg.cond(cov_reg):.2f}")
            print()

        if baseline is not None:
            _print_gdi_table(scores, baseline, limit=10)
    finally:
        db.close()
