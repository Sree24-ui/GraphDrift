"""Precision / recall / F1 / FPR helpers for account-level detection eval."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalMetrics:
    precision: float
    recall: float
    f1: float
    fpr: float
    tp: int
    fp: int
    fn: int
    tn: int
    positives: int
    negatives: int
    predicted_positive: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "fpr": self.fpr,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "positives": self.positives,
            "negatives": self.negatives,
            "predicted_positive": self.predicted_positive,
        }


def compute_metrics(
    y_true: set[str],
    y_pred: set[str],
    universe: set[str],
) -> EvalMetrics:
    evaluated_true = y_true & universe
    evaluated_pred = y_pred & universe

    tp = len(evaluated_true & evaluated_pred)
    fp = len(evaluated_pred - evaluated_true)
    fn = len(evaluated_true - evaluated_pred)
    tn = len(universe) - tp - fp - fn

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    fpr = fp / (fp + tn) if (fp + tn) else 0.0

    return EvalMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        fpr=fpr,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        positives=len(evaluated_true),
        negatives=len(universe) - len(evaluated_true),
        predicted_positive=len(evaluated_pred),
    )
