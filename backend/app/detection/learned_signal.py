"""Opt-in third fusion signal learned from analyst review decisions.

Off by default (``enable_learned_signal`` in ``detection_knobs.json``). When on,
a LightGBM classifier trained on judged alerts (``confirmed`` vs
``false_positive``) predicts P(confirmed) for every account the fusion step
scores, and its percentile rank becomes a third fusion input.

It learns only from what the two layers already computed when the alert was
scored: the Layer-1 per-feature z-scores and GDI, and the Layer-2 ring risk and
hub concentration, read from the alert's persisted explanation. Live rows go
through the same ``explain_score`` path, so training and scoring inputs match.
Peripheral-cascade alerts are excluded: fusion never scored them, so their zero
GDI / ring fields are placeholders, not measurements.

Even when enabled it refuses to activate, and logs why, until there are
``learned_signal_min_labels_per_class`` (50) labels of each class **and**
repeated cross-validation shows it beating both trivial baselines. 50 per class
puts at least 10 of each class in every test fold of 5-fold CV, so no fold's
precision or recall is decided by one or two alerts, and gives about five
examples per class for each of the 11 inputs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy import stats
from sklearn.dummy import DummyClassifier
from sklearn.metrics import make_scorer, precision_score
from sklearn.model_selection import StratifiedGroupKFold, cross_validate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import ENABLE_LEARNED_SIGNAL, LEARNED_SIGNAL_MIN_LABELS_PER_CLASS
from app.detection.calibration import JUDGED_STATUSES
from app.detection.node_anomaly import SCORING_FEATURE_NAMES, explain_score
from app.models import Alert

logger = logging.getLogger("graphdrift.learned_signal")

FEATURE_NAMES: list[str] = [f"z_{name}" for name in SCORING_FEATURE_NAMES] + [
    "gdi_score",
    "ring_risk_score",
    "hub_concentration",
]
PERIPHERAL_PATTERN = "peripheral_structural"
CV_SPLITS = 5
CV_REPEATS = 5
SIGNIFICANCE = 0.05


@dataclass(frozen=True)
class LearnedSignalState:
    active: bool
    reason: str
    evaluation: dict | None = None


_cache: dict = {"key": None, "model": None, "state": None}
# Set if training or scoring ever raised. An opt-in signal must not be able to
# take the detection cycle down, so it disables itself until the next restart
# (the usual cause is LightGBM failing to import: macOS needs brew libomp).
_failed = False


def features_from_breakdown(breakdown: dict) -> list[float]:
    """Model inputs from an alert explanation (``build_explanation`` output).

    Accounts scored only as ring members have no Layer-1 z-scores; those are NaN,
    which LightGBM routes natively. No ring means zero ring risk and hub
    concentration, exactly as the fusion step treats it.
    """
    z = {
        item["feature"]: item["z_score"]
        for item in breakdown.get("layer1_breakdown") or []
    }
    layer2 = breakdown.get("layer2_detail") or {}
    return [float(z.get(name, math.nan)) for name in SCORING_FEATURE_NAMES] + [
        float(breakdown["gdi_score"]),
        float(breakdown["ring_risk_score"]),
        float(layer2.get("hub_concentration", 0.0)),
    ]


def features_from_fused_row(row: dict) -> list[float]:
    """The same inputs for a live ``compute_fused_scores`` row."""
    vector = row.get("feature_vector")
    baseline = row.get("layer1_baseline")
    return features_from_breakdown(
        {
            "layer1_breakdown": (
                explain_score(vector, baseline) if vector and baseline is not None else []
            ),
            "gdi_score": row["gdi_score"],
            "ring_risk_score": row["ring_risk_score"],
            "layer2_detail": row.get("ring_info"),
        }
    )


def _matrix(rows: list[list[float]]) -> np.ndarray:
    return np.array(rows, dtype=float).reshape(len(rows), len(FEATURE_NAMES))


def _judged_fusion_alerts():
    return select(Alert).where(
        Alert.status.in_(JUDGED_STATUSES),
        Alert.pattern_type != PERIPHERAL_PATTERN,
    )


def training_set(db: Session) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inputs, labels (1 = confirmed) and account ids of judged fusion alerts."""
    alerts = [
        alert
        for alert in db.scalars(_judged_fusion_alerts().order_by(Alert.id))
        if alert.feature_breakdown
    ]
    X = _matrix([features_from_breakdown(alert.feature_breakdown) for alert in alerts])
    y = np.array([alert.status == "confirmed" for alert in alerts], dtype=int)
    return X, y, np.array([alert.account_id for alert in alerts])


def _new_model():
    # Imported here so the default pipeline never loads LightGBM.
    from lightgbm import LGBMClassifier

    # ponytail: fixed small-data hyperparameters, no tuning; add nested-CV
    # tuning once labels number in the thousands.
    # Deliberately unweighted. class_weight="balanced" optimises balanced
    # accuracy, which moves the 0.5 decision threshold and costs F1 and accuracy
    # at an unchanged AUC - the two metrics the activation gate scores. Measured
    # in evaluation/RESULTS.md, "Analyst-feedback learned signal".
    return LGBMClassifier(
        n_estimators=100,
        learning_rate=0.05,
        num_leaves=7,
        min_child_samples=10,
        n_jobs=1,
        random_state=0,
        verbose=-1,
    )


def _corrected_p_value(diff: np.ndarray) -> float:
    """One-sided p that mean(diff) > 0: Nadeau-Bengio corrected resampled t-test.

    Repeated-CV folds share training data, so a plain t-test overstates
    confidence; the correction inflates the variance by test/train size.
    """
    mean = float(np.mean(diff))
    var = float(np.var(diff, ddof=1))
    if var == 0.0:
        return 0.0 if mean > 0 else 1.0
    t = mean / math.sqrt((1 / len(diff) + 1 / (CV_SPLITS - 1)) * var)
    return float(stats.t.sf(t, df=len(diff) - 1))


def evaluate(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict:
    """Repeated stratified 5-fold CV of the model against two trivial baselines.

    Folds are grouped by account: an account alerted and judged in several
    cycles must not sit in both train and test, or CV rewards memorising it.
    ``always_confirmed`` is the F1 baseline (recall 1, precision = base rate);
    ``majority`` is the accuracy baseline. All three see identical folds, so
    per-fold differences are paired. Beating the baselines means both one-sided
    corrected p-values are below 0.05.
    """
    cv = [
        split
        for repeat in range(CV_REPEATS)
        for split in StratifiedGroupKFold(
            n_splits=CV_SPLITS, shuffle=True, random_state=repeat
        ).split(X, y, groups)
    ]
    scoring = {
        "precision": make_scorer(precision_score, zero_division=0),
        "recall": "recall",
        "f1": "f1",
        "accuracy": "accuracy",
    }
    estimators = {
        "model": _new_model(),
        "always_confirmed": DummyClassifier(strategy="constant", constant=1),
        "majority": DummyClassifier(strategy="most_frequent"),
    }
    runs = {name: cross_validate(est, X, y, cv=cv, scoring=scoring) for name, est in estimators.items()}
    p_f1 = _corrected_p_value(runs["model"]["test_f1"] - runs["always_confirmed"]["test_f1"])
    p_accuracy = _corrected_p_value(
        runs["model"]["test_accuracy"] - runs["majority"]["test_accuracy"]
    )
    return {
        "n_confirmed": int(y.sum()),
        "n_false_positive": int(len(y) - y.sum()),
        "n_accounts": int(len(set(groups))),
        "cv": f"{CV_REPEATS}x repeated stratified {CV_SPLITS}-fold, grouped by account",
        **{
            name: {
                metric: {
                    "mean": float(run[f"test_{metric}"].mean()),
                    "std": float(run[f"test_{metric}"].std(ddof=1)),
                }
                for metric in scoring
            }
            for name, run in runs.items()
        },
        "p_f1_vs_always_confirmed": p_f1,
        "p_accuracy_vs_majority": p_accuracy,
        "beats_baseline": p_f1 < SIGNIFICANCE and p_accuracy < SIGNIFICANCE,
    }


def _label_key(db: Session) -> tuple:
    # confirmed / false_positive are terminal statuses, so per-status counts and
    # max id change whenever the label set does.
    return tuple(
        db.execute(
            _judged_fusion_alerts()
            .with_only_columns(Alert.status, func.count(), func.max(Alert.id))
            .group_by(Alert.status)
            .order_by(Alert.status)
        ).all()
    )


def refresh(db: Session) -> LearnedSignalState:
    """Train, or refuse to, from the current labels. Cached until labels change."""
    # ponytail: re-runs the full CV on every label change; debounce if review
    # volume makes it show up in cycle timing.
    key = _label_key(db)
    if key == _cache["key"]:
        return _cache["state"]

    X, y, groups = training_set(db)
    n_confirmed, n_false_positive = int(y.sum()), int(len(y) - y.sum())
    counts = f"{n_confirmed} confirmed / {n_false_positive} false_positive fusion-scored labels"
    model = None
    if min(n_confirmed, n_false_positive) < LEARNED_SIGNAL_MIN_LABELS_PER_CLASS:
        state = LearnedSignalState(
            False,
            f"inactive (cold start): {counts}; needs at least "
            f"{LEARNED_SIGNAL_MIN_LABELS_PER_CLASS} of each",
        )
    elif len(set(groups)) < CV_SPLITS:
        state = LearnedSignalState(
            False,
            f"inactive: {counts} come from {len(set(groups))} accounts; "
            f"account-grouped CV needs at least {CV_SPLITS}",
        )
    else:
        ev = evaluate(X, y, groups)
        summary = (
            f"CV F1 {ev['model']['f1']['mean']:.3f} vs always-confirmed "
            f"{ev['always_confirmed']['f1']['mean']:.3f} (p={ev['p_f1_vs_always_confirmed']:.3g}), "
            f"accuracy {ev['model']['accuracy']['mean']:.3f} vs majority "
            f"{ev['majority']['accuracy']['mean']:.3f} (p={ev['p_accuracy_vs_majority']:.3g})"
        )
        if ev["beats_baseline"]:
            model = _new_model().fit(X, y)
            state = LearnedSignalState(True, f"active: trained on {counts}; {summary}", ev)
        else:
            state = LearnedSignalState(
                False, f"inactive (no better than trivial baselines): {counts}; {summary}", ev
            )

    (logger.info if state.active else logger.warning)("Learned signal %s", state.reason)
    _cache.update(key=key, model=model, state=state)
    return state


def predict_confirmed_proba(db: Session, rows: list[dict]) -> list[float] | None:
    """P(confirmed) per fusion row, or None when the signal is off or inactive."""
    global _failed
    if not ENABLE_LEARNED_SIGNAL or _failed or not rows:
        return None
    try:
        if not refresh(db).active:
            return None
        X = _matrix([features_from_fused_row(row) for row in rows])
        return _cache["model"].predict_proba(X)[:, 1].tolist()
    except Exception:
        _failed = True
        logger.exception(
            "Learned signal failed and is disabled until restart; "
            "detection continues on the two default layers"
        )
        return None
