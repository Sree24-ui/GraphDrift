"""Isolation Forest baselines on the same scored universe as Layer 1 / fusion.

Fits a fresh sklearn IsolationForest per detection window (no persistent
training set), then applies GraphDrift's rank-based top-5% cut — not
``contamination`` — so alerting methodology matches GDI and fusion.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sklearn.ensemble import IsolationForest
from sqlalchemy.orm import Session

from app.detection.community import get_ring_alerts
from app.detection.features import (
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
    extract_all_features,
)
from app.detection.fusion import _build_ring_lookup, select_top_anomaly_accounts
from app.detection.node_anomaly import SCORING_FEATURE_NAMES

STRUCTURAL_FEATURE_NAMES = ("hub_concentration", "external_edge_ratio")
# Flag rather than silently rank when IsolationForest collapses (same class of
# bug as tied GDI / PaySim percentile). Rank-based top-k is arbitrary if
# unique scores < k.
DEGENERACY_UNIQUE_FLOOR = 5


def _l1_vector(row: dict) -> list[float]:
    return [float(row[name]) for name in SCORING_FEATURE_NAMES]


def _structural_pair(account_id: str, ring_lookup: dict[str, dict]) -> tuple[float, float]:
    info = ring_lookup.get(account_id)
    if not info:
        return 0.0, 0.0
    return (
        float(info.get("hub_concentration", 0.0)),
        float(info.get("external_edge_ratio", 0.0)),
    )


def _feature_matrix(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int,
    min_transactions: int,
    include_structural: bool,
) -> tuple[list[str], np.ndarray, dict]:
    features = extract_all_features(
        db, as_of, window_minutes, min_transactions=min_transactions
    )
    if not features:
        return [], np.zeros((0, 0)), {"n_with_structural": 0, "feature_names": []}

    ring_lookup: dict[str, dict] = {}
    if include_structural:
        ring_lookup = _build_ring_lookup(get_ring_alerts(db, as_of, window_minutes))

    ids: list[str] = []
    rows: list[list[float]] = []
    n_struct = 0
    names = list(SCORING_FEATURE_NAMES)
    if include_structural:
        names.extend(STRUCTURAL_FEATURE_NAMES)

    for row in features:
        account_id = str(row["account_id"])
        vec = _l1_vector(row)
        if include_structural:
            hub, ext = _structural_pair(account_id, ring_lookup)
            if hub != 0.0 or ext != 0.0:
                n_struct += 1
            vec.extend([hub, ext])
        ids.append(account_id)
        rows.append(vec)

    return (
        ids,
        np.asarray(rows, dtype=float),
        {"n_with_structural": n_struct, "feature_names": names},
    )


def default_forest_params() -> dict:
    """sklearn IsolationForest defaults used in the original eval.

    n_jobs=1 is the only non-default we set (sklearn default is None) so
    scoring is single-threaded and more likely to be deterministic.
    """
    return {
        "n_estimators": 100,
        "max_samples": "auto",
        "max_features": 1.0,
        "contamination": "auto",
        "n_jobs": 1,
        "bootstrap": False,
    }


def score_isolation_forest(
    matrix: np.ndarray,
    *,
    random_state: int,
    n_estimators: int = 100,
    max_samples: int | str = "auto",
    max_features: float = 1.0,
) -> tuple[np.ndarray, dict]:
    """Return higher-is-more-anomalous scores and degeneracy diagnostics."""
    n = int(matrix.shape[0])
    if n == 0:
        return np.asarray([], dtype=float), {
            "n_scored": 0,
            "n_unique_scores": 0,
            "degenerate": True,
            "degeneracy_reason": "empty matrix",
        }
    if n == 1:
        return np.asarray([0.0], dtype=float), {
            "n_scored": 1,
            "n_unique_scores": 1,
            "degenerate": True,
            "degeneracy_reason": "single account",
        }

    params = default_forest_params()
    params.update(
        {
            "n_estimators": int(n_estimators),
            "max_samples": max_samples,
            "max_features": max_features,
            "random_state": int(random_state),
        }
    )
    model = IsolationForest(**params)
    model.fit(matrix)
    # sklearn: lower score_samples => more abnormal. Flip so top-k matches GDI.
    scores = -np.asarray(model.score_samples(matrix), dtype=float)
    unique = np.unique(np.round(scores, 12))
    n_unique = int(unique.size)
    degenerate = n_unique < DEGENERACY_UNIQUE_FLOOR
    reason = None
    if degenerate:
        reason = (
            f"only {n_unique} unique IsolationForest scores "
            f"(floor={DEGENERACY_UNIQUE_FLOOR}) on n={n}"
        )
    return scores, {
        "n_scored": n,
        "n_unique_scores": n_unique,
        "score_min": float(scores.min()),
        "score_max": float(scores.max()),
        "degenerate": degenerate,
        "degeneracy_reason": reason,
        "forest_params": {
            "n_estimators": params["n_estimators"],
            "max_samples": params["max_samples"],
            "max_features": params["max_features"],
            "n_jobs": params["n_jobs"],
            "contamination": params["contamination"],
            "bootstrap": params["bootstrap"],
            "random_state": params["random_state"],
        },
    }


def select_if_topk(
    ids: list[str],
    scores: np.ndarray,
    diag: dict,
) -> tuple[set[str], dict]:
    if not ids:
        return set(), diag
    rows = [
        {"account_id": account_id, "if_score": float(score)}
        for account_id, score in zip(ids, scores)
    ]
    selected, threshold, k = select_top_anomaly_accounts(rows, "if_score")
    diag = dict(diag)
    diag["k"] = k
    diag["threshold"] = threshold
    diag["n_selected"] = len(selected)
    if diag["n_unique_scores"] < k:
        diag["degenerate"] = True
        diag["degeneracy_reason"] = (
            f"unique scores ({diag['n_unique_scores']}) < top-k budget (k={k}); "
            "rank-based selection is underdetermined"
        )
    return selected, diag


def predict_isolation_forest(
    db: Session,
    as_of: datetime,
    *,
    random_state: int,
    include_structural: bool,
    window_minutes: int = WINDOW_MINUTES,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
    n_estimators: int = 100,
    max_samples: int | str = "auto",
    max_features: float = 1.0,
) -> tuple[set[str], dict]:
    ids, matrix, feat_meta = _feature_matrix(
        db,
        as_of,
        window_minutes=window_minutes,
        min_transactions=min_transactions,
        include_structural=include_structural,
    )
    scores, diag = score_isolation_forest(
        matrix,
        random_state=random_state,
        n_estimators=n_estimators,
        max_samples=max_samples,
        max_features=max_features,
    )
    diag.update(feat_meta)
    return select_if_topk(ids, scores, diag)
