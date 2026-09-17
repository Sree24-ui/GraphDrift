#!/usr/bin/env python3
"""Analyst-feedback learned signal: label reality check, then mechanism check.

1. Reality check. For each ``--label-db`` (opened read-only): judged-alert
   counts, who judged them, the fusion-scored labels the model could train on,
   and what the activation guard decides.
2. Mechanism check on oracle labels. The closed-loop calibration harness runs
   the real detector over two seeded simulator traces and judges every new alert
   from the simulator's ground truth. These are NOT analyst decisions: they only
   show that training, evaluation and the activation guard behave correctly once
   labels exist. Trace A gets the account-grouped CV and the guard; a model
   trained on all of A then scores trace B, an independent run.

Writes evaluation/data/learned_signal_eval.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection import learned_signal  # noqa: E402
from app.detection.calibration import JUDGED_STATUSES  # noqa: E402
from app.models import Alert, User  # noqa: E402
from evaluation.closed_loop_calibration import run_closed_loop  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402

REPORT_JSON = BACKEND_ROOT / "evaluation" / "data" / "learned_signal_eval.json"
BOOTSTRAP_RESAMPLES = 1000


def _fresh_state(db) -> learned_signal.LearnedSignalState:
    learned_signal._cache = {"key": None, "model": None, "state": None}
    return learned_signal.refresh(db)


def reality_check(path: Path) -> dict:
    engine = create_engine(f"sqlite:///file:{path.resolve()}?mode=ro&uri=true")
    db = sessionmaker(bind=engine)()
    judged = Alert.status.in_(JUDGED_STATUSES)
    by_status = dict(
        db.execute(select(Alert.status, func.count()).where(judged).group_by(Alert.status)).all()
    )
    reviewers = db.execute(
        select(User.username, User.role, func.count())
        .join(Alert, Alert.reviewed_by_user_id == User.id)
        .where(judged)
        .group_by(User.username, User.role)
    ).all()
    _, y, groups = learned_signal.training_set(db)
    state = _fresh_state(db)
    db.close()
    return {
        "database": path.name,
        "judged_by_status": by_status,
        "judged_with_reviewer": {f"{name} ({role})": n for name, role, n in reviewers},
        "fusion_scored_confirmed": int(y.sum()),
        "fusion_scored_false_positive": int(len(y) - y.sum()),
        "fusion_scored_accounts": int(len(set(groups))),
        "guard": {"active": state.active, "reason": state.reason},
        "evaluation": state.evaluation,
    }


def _scores(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", zero_division=0
    )
    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "accuracy": float(accuracy_score(y_true, y_pred)),
    }


def held_out(model, X: np.ndarray, y: np.ndarray, groups: np.ndarray, fused: np.ndarray) -> dict:
    """Score trace B; 95% CI of the F1 gain from an account-level bootstrap.

    Ranking quality is reported next to the threshold metrics because fusion
    consumes the percentile rank of the probability, not a 0/1 decision, and
    compared with the current fused score, which is the ranking it must add to.
    """
    proba = model.predict_proba(X)[:, 1]
    pred = model.predict(X)
    always = np.ones_like(y)
    rng = np.random.default_rng(0)
    accounts = np.unique(groups)
    rows_of = {a: np.flatnonzero(groups == a) for a in accounts}
    gains = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        idx = np.concatenate([rows_of[a] for a in rng.choice(accounts, len(accounts))])
        gains.append(_scores(y[idx], pred[idx])["f1"] - _scores(y[idx], always[idx])["f1"])
    return {
        "n_confirmed": int(y.sum()),
        "n_false_positive": int(len(y) - y.sum()),
        "n_accounts": int(len(accounts)),
        "model": _scores(y, pred),
        "always_confirmed": _scores(y, always),
        "roc_auc": float(roc_auc_score(y, proba)),
        "average_precision": float(average_precision_score(y, proba)),
        "fused_score_roc_auc": float(roc_auc_score(y, fused)),
        "majority": _scores(y, np.full_like(y, int(y.mean() >= 0.5))),
        "f1_gain_ci95": [float(np.percentile(gains, 2.5)), float(np.percentile(gains, 97.5))],
    }


def oracle_trace(path: Path, seed: int, cycles: int):
    factory = init_eval_db(path)
    with factory() as db:
        run_closed_loop(db, cycles=cycles, seed=seed)
    db = factory()
    fused = np.array(
        [
            alert.feature_breakdown["fused_score"]
            for alert in db.scalars(learned_signal._judged_fusion_alerts().order_by(Alert.id))
            if alert.feature_breakdown
        ]
    )
    peripheral = db.scalar(
        select(func.count()).where(
            Alert.status.in_(JUDGED_STATUSES),
            Alert.pattern_type == learned_signal.PERIPHERAL_PATTERN,
        )
    )
    return db, int(peripheral), fused


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label-db", type=Path, action="append", default=[])
    parser.add_argument("--cycles", type=int, default=60)
    parser.add_argument("--seed-a", type=int, default=20260830)
    parser.add_argument("--seed-b", type=int, default=20260917)
    args = parser.parse_args()

    report = {"reality_check": [reality_check(path) for path in args.label_db]}
    for row in report["reality_check"]:
        print(f"{row['database']}: judged {row['judged_by_status']}, "
              f"reviewers {row['judged_with_reviewer']}, fusion-scored "
              f"{row['fusion_scored_confirmed']}/{row['fusion_scored_false_positive']} "
              f"-> {row['guard']['reason']}")

    with tempfile.TemporaryDirectory(prefix="graphdrift-learned-") as tmp:
        db_a, peripheral_a, _ = oracle_trace(Path(tmp) / "a.db", args.seed_a, args.cycles)
        db_b, peripheral_b, fused_b = oracle_trace(Path(tmp) / "b.db", args.seed_b, args.cycles)
        X_a, y_a, groups_a = learned_signal.training_set(db_a)
        state = _fresh_state(db_a)
        X_b, y_b, groups_b = learned_signal.training_set(db_b)
        model = learned_signal._new_model().fit(X_a, y_a)
        report["oracle_mechanism_check"] = {
            "labels": "simulator ground truth via closed_loop_calibration, not analyst decisions",
            "cycles_per_trace": args.cycles,
            "trace_a": {
                "seed": args.seed_a,
                "peripheral_labels_excluded": peripheral_a,
                "n_accounts": int(len(set(groups_a))),
                "guard": {"active": state.active, "reason": state.reason},
                "evaluation": state.evaluation,
            },
            "trace_b_held_out": {
                "seed": args.seed_b,
                "peripheral_labels_excluded": peripheral_b,
                **held_out(model, X_b, y_b, groups_b, fused_b),
            },
        }
        db_a.close()
        db_b.close()

    check = report["oracle_mechanism_check"]
    print(f"trace A (seed {args.seed_a}): {check['trace_a']['guard']['reason']}")
    b = check["trace_b_held_out"]
    print(f"trace B (seed {args.seed_b}) held out: {b['n_confirmed']}/{b['n_false_positive']} labels, "
          f"model F1 {b['model']['f1']:.3f} vs always-confirmed {b['always_confirmed']['f1']:.3f}, "
          f"gain 95% CI [{b['f1_gain_ci95'][0]:+.3f}, {b['f1_gain_ci95'][1]:+.3f}]; "
          f"accuracy {b['model']['accuracy']:.3f} vs majority {b['majority']['accuracy']:.3f}; "
          f"ROC AUC {b['roc_auc']:.3f} vs fused score {b['fused_score_roc_auc']:.3f}")
    REPORT_JSON.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {REPORT_JSON.relative_to(BACKEND_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
