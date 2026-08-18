"""Isolation Forest baseline: top-k selection and degeneracy checks."""

from __future__ import annotations

import numpy as np

from app.detection.fusion import top_anomaly_budget
from app.detection.node_anomaly import SCORING_FEATURE_NAMES
from evaluation.isolation_forest_baseline import (
    DEGENERACY_UNIQUE_FLOOR,
    STRUCTURAL_FEATURE_NAMES,
    score_isolation_forest,
)


def test_isolation_forest_feature_names_match_layer1():
    assert SCORING_FEATURE_NAMES == [
        "in_degree",
        "out_degree",
        "in_count",
        "out_count",
        "amount_entropy",
        "counterparty_diversity",
        "fan_ratio",
        "burstiness",
    ]
    assert STRUCTURAL_FEATURE_NAMES == ("hub_concentration", "external_edge_ratio")


def test_isolation_forest_scores_are_diverse_on_random_matrix():
    rng = np.random.default_rng(42)
    matrix = rng.normal(size=(80, 8))
    scores, diag = score_isolation_forest(matrix, random_state=42)
    k = top_anomaly_budget(80)
    assert scores.shape == (80,)
    assert diag["n_unique_scores"] >= max(DEGENERACY_UNIQUE_FLOOR, k)
    assert not diag["degenerate"]
    # Higher score = more anomalous after the sign flip.
    assert scores.max() > scores.min()


def test_isolation_forest_flags_constant_matrix_as_degenerate():
    matrix = np.ones((40, 8))
    scores, diag = score_isolation_forest(matrix, random_state=7)
    assert diag["degenerate"]
    assert diag["n_unique_scores"] < DEGENERACY_UNIQUE_FLOOR
    assert len(set(np.round(scores, 12))) == diag["n_unique_scores"]
