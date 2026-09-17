"""Opt-in learned signal: features, baselines, cold start, and the default path."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.constants import ENABLE_LEARNED_SIGNAL, GDI_MAX, LEARNED_SIGNAL_MIN_LABELS_PER_CLASS
from app.detection import fusion, learned_signal
from app.detection.node_anomaly import SCORING_FEATURE_NAMES
from app.models import Account, Alert, Transaction

NOW = datetime(2026, 8, 12, 12, 0, 0)

# compute_fused_scores output for _seed_ring_and_crowd, captured from the
# pre-learned-signal pipeline (commit f5694eb). Flag off must reproduce it exactly.
GOLDEN_DEFAULT_FUSED = [
    ("hub@ybl", 4.6875, 1.0, 0.875),
    ("n0@ybl", 3.203125, 0.9375, 0.34375),
    ("n2@ybl", 3.046875, 0.875, 0.34375),
    ("n11@ybl", 2.890625, 0.8125, 0.34375),
    ("n10@ybl", 2.734375, 0.75, 0.34375),
    ("n1@ybl", 2.578125, 0.6875, 0.34375),
    ("s0@ybl", 2.421875, 0.09375, 0.875),
    ("s1@ybl", 2.421875, 0.09375, 0.875),
    ("s2@ybl", 2.421875, 0.09375, 0.875),
    ("s3@ybl", 2.421875, 0.09375, 0.875),
    ("n5@ybl", 2.34375, 0.59375, 0.34375),
    ("n8@ybl", 2.34375, 0.59375, 0.34375),
    ("n6@ybl", 2.03125, 0.46875, 0.34375),
    ("n9@ybl", 2.03125, 0.46875, 0.34375),
    ("n3@ybl", 1.796875, 0.375, 0.34375),
    ("n4@ybl", 1.640625, 0.3125, 0.34375),
    ("n7@ybl", 1.484375, 0.25, 0.34375),
]


def _seed_ring_and_crowd(db) -> None:
    """A 5-account hub-and-spoke ring (spokes are ring-only rows) plus 12
    ordinary accounts with enough transactions for a Layer-1 baseline."""
    crowd = [f"n{i}@ybl" for i in range(12)]
    ring = ["hub@ybl", *[f"s{i}@ybl" for i in range(4)]]
    for acct in crowd + ring:
        db.add(Account(id=acct, created_at=NOW, last_active_at=NOW))

    def tx(sender, receiver, amount, minutes_ago):
        db.add(
            Transaction(
                sender_id=sender,
                receiver_id=receiver,
                amount=float(amount),
                timestamp=NOW - timedelta(minutes=minutes_ago),
                is_synthetic_attack=False,
            )
        )

    for i, acct in enumerate(crowd):
        for j in range(3 + i % 3):
            tx(acct, crowd[(i + 1 + j) % 12], 100 + 37 * i + 11 * j, 14 - (i + j) % 13)
    for i in range(4):
        tx(f"s{i}@ybl", "hub@ybl", 45000 + i, 9 - i)
    for i in range(3):
        tx("hub@ybl", crowd[i], 44000 + i, 3 - i * 0.5)
    db.commit()


def _fused(db):
    return fusion.compute_fused_scores(db, NOW, window_minutes=15)


def _breakdown(signal: float) -> dict:
    return {
        "gdi_score": signal,
        "ring_risk_score": 0.0,
        "layer1_breakdown": [
            {"feature": name, "contribution_pct": 0.0, "z_score": signal * (k + 1)}
            for k, name in enumerate(SCORING_FEATURE_NAMES)
        ],
        "layer2_detail": None,
    }


def _add_labels(db, statuses_and_signals, pattern_type="node_anomaly") -> None:
    start = db.query(Alert).count()
    for i, (status, signal) in enumerate(statuses_and_signals, start=start):
        db.add(
            Alert(
                account_id=f"acct{i}@ybl",
                risk_score=4.0,
                pattern_type=pattern_type,
                detected_at=NOW,
                status=status,
                feature_breakdown=_breakdown(signal),
            )
        )
    db.commit()


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(learned_signal, "ENABLE_LEARNED_SIGNAL", True)
    monkeypatch.setattr(learned_signal, "_cache", {"key": None, "model": None, "state": None})
    monkeypatch.setattr(learned_signal, "_failed", False)


def test_a_failure_disables_the_signal_instead_of_breaking_the_cycle(
    db_session, enabled, monkeypatch, caplog
):
    calls = []

    def boom(_db):
        calls.append(1)
        raise RuntimeError("liblightgbm.so: cannot open shared object file")

    monkeypatch.setattr(learned_signal, "refresh", boom)
    with caplog.at_level(logging.ERROR, logger="graphdrift.learned_signal"):
        assert learned_signal.predict_confirmed_proba(db_session, [{}]) is None
    assert "disabled until restart" in caplog.text
    # Stays off rather than raising once per cycle.
    assert learned_signal.predict_confirmed_proba(db_session, [{}]) is None
    assert len(calls) == 1


def test_off_by_default_and_never_touches_the_database():
    assert ENABLE_LEARNED_SIGNAL is False
    # object() has no .execute: any database access would raise.
    assert learned_signal.predict_confirmed_proba(object(), [{"account_id": "a"}]) is None


def test_default_fusion_is_unchanged_two_layer_formula(db_session):
    _seed_ring_and_crowd(db_session)
    rows = _fused(db_session)
    for row in rows:
        assert "learned_percentile" not in row
        assert row["fused_score"] == GDI_MAX * (
            0.5 * row["gdi_percentile"] + 0.5 * row["ring_percentile"]
        )
    assert [
        (r["account_id"], r["fused_score"], r["gdi_percentile"], r["ring_percentile"])
        for r in rows
    ] == GOLDEN_DEFAULT_FUSED


def test_enabled_signal_is_a_third_percentile_in_fusion(db_session, monkeypatch):
    _seed_ring_and_crowd(db_session)
    default = {r["account_id"]: r for r in _fused(db_session)}
    ids = sorted(default)
    # Stub model: probability rises with account id order, so ranks are known.
    monkeypatch.setattr(
        learned_signal,
        "predict_confirmed_proba",
        lambda _db, rows: [ids.index(r["account_id"]) / len(ids) for r in rows],
    )
    rows = _fused(db_session)
    for row in rows:
        learned_pct = ids.index(row["account_id"]) / (len(ids) - 1)
        assert row["learned_percentile"] == pytest.approx(learned_pct)
        assert row["fused_score"] == pytest.approx(
            GDI_MAX * (row["gdi_percentile"] + row["ring_percentile"] + learned_pct) / 3
        )
        explanation = fusion.build_explanation(row["account_id"], row, db_session, NOW)
        assert explanation["learned_percentile"] == row["learned_percentile"]


def test_live_features_match_the_persisted_explanation(db_session):
    """Training reads alert explanations; scoring reads live rows. Same numbers."""
    _seed_ring_and_crowd(db_session)
    rows = _fused(db_session)
    kinds = set()
    for row in rows:
        explanation = fusion.build_explanation(row["account_id"], row, db_session, NOW)
        db_session.add(
            Alert(
                account_id=row["account_id"],
                risk_score=row["fused_score"],
                pattern_type="community_ring",
                detected_at=NOW,
                status="confirmed",
                feature_breakdown=explanation,
            )
        )
        live = learned_signal.features_from_fused_row(row)
        assert len(live) == len(learned_signal.FEATURE_NAMES) == 11
        np.testing.assert_array_equal(
            live, learned_signal.features_from_breakdown(explanation)
        )
        ring = row["ring_info"] or {}
        assert live[-3:] == [
            row["gdi_score"],
            row["ring_risk_score"],
            ring.get("hub_concentration", 0.0),
        ]
        if row["feature_vector"] is None:
            kinds.add("ring-only")
            assert all(math.isnan(v) for v in live[:8])
        else:
            kinds.add("layer1")
            z = {i["feature"]: i["z_score"] for i in explanation["layer1_breakdown"]}
            assert live[:8] == [z[name] for name in SCORING_FEATURE_NAMES]
    assert kinds == {"ring-only", "layer1"}

    # Through the JSON column and back: the training matrix is the live matrix.
    db_session.commit()
    X, y, _ = learned_signal.training_set(db_session)
    live_matrix = [learned_signal.features_from_fused_row(r) for r in rows]
    np.testing.assert_array_equal(X, live_matrix)
    assert y.tolist() == [1] * len(rows)


def test_training_set_uses_only_judged_fusion_alerts(db_session):
    _add_labels(db_session, [("confirmed", 1.0), ("false_positive", 0.0)])
    _add_labels(db_session, [("new", 1.0), ("reviewing", 1.0), ("auto_closed", 1.0)])
    _add_labels(db_session, [("confirmed", 1.0)], pattern_type="peripheral_structural")
    X, y, groups = learned_signal.training_set(db_session)
    assert y.tolist() == [1, 0]
    assert X[0, 0] == 1.0 and X[1, 0] == 0.0
    assert groups.tolist() == ["acct0@ybl", "acct1@ybl"]


def test_trivial_baselines_are_computed_exactly():
    rng = np.random.default_rng(0)
    y = np.array([1] * 60 + [0] * 40)
    ev = learned_signal.evaluate(rng.normal(size=(100, 11)), y, np.arange(100))
    # One account per label: stratified folds hold 12 confirmed / 8 false_positive.
    assert ev["always_confirmed"]["precision"]["mean"] == pytest.approx(0.6)
    assert ev["always_confirmed"]["recall"]["mean"] == 1.0
    assert ev["always_confirmed"]["f1"]["mean"] == pytest.approx(0.75)
    assert ev["majority"]["accuracy"]["mean"] == pytest.approx(0.6)
    assert ev["cv"] == "5x repeated stratified 5-fold, grouped by account"


def test_model_beats_baselines_only_when_there_is_signal():
    rng = np.random.default_rng(1)
    y = np.array([1] * 60 + [0] * 60)
    noise = rng.normal(size=(120, 11))
    separable = noise.copy()
    separable[:, 8] = y * 3 + rng.normal(scale=0.3, size=120)

    groups = np.arange(120)
    signal = learned_signal.evaluate(separable, y, groups)
    assert signal["beats_baseline"]
    assert signal["model"]["f1"]["mean"] > signal["always_confirmed"]["f1"]["mean"]

    no_signal = learned_signal.evaluate(noise, y, groups)
    assert not no_signal["beats_baseline"]


def test_account_grouped_folds_never_split_an_account(monkeypatch):
    """An account's repeated alerts must not be in both train and test."""
    rng = np.random.default_rng(4)
    y = np.array([1, 0] * 60)
    groups = np.repeat(np.arange(24), 5)  # 24 accounts, 5 alerts each
    X = rng.normal(size=(120, 11))
    X[:, 0] = groups  # a model could memorise the account from this column
    seen = []
    real_cross_validate = learned_signal.cross_validate

    def spy(estimator, X, y, *, cv, scoring):
        seen.append(cv)
        return real_cross_validate(estimator, X, y, cv=cv, scoring=scoring)

    monkeypatch.setattr(learned_signal, "cross_validate", spy)
    ev = learned_signal.evaluate(X, y, groups)
    assert ev["n_accounts"] == 24
    for train, test in seen[0]:
        assert not set(groups[train]) & set(groups[test])


def test_too_few_accounts_refuses_instead_of_crashing(db_session, enabled):
    n = LEARNED_SIGNAL_MIN_LABELS_PER_CLASS
    _add_labels(db_session, [("confirmed", 2.0)] * n + [("false_positive", 0.0)] * n)
    db_session.query(Alert).update({Alert.account_id: "same@ybl"})
    db_session.commit()
    state = learned_signal.refresh(db_session)
    assert not state.active
    assert "come from 1 accounts" in state.reason


def test_cold_start_refuses_to_activate_and_says_why(db_session, enabled, caplog):
    short = LEARNED_SIGNAL_MIN_LABELS_PER_CLASS - 1
    _add_labels(db_session, [("confirmed", 2.0)] * short + [("false_positive", 0.0)] * 60)
    # Peripheral labels are not fusion-scored and must not count toward the minimum.
    _add_labels(db_session, [("confirmed", 2.0)] * 10, pattern_type="peripheral_structural")

    with caplog.at_level(logging.WARNING, logger="graphdrift.learned_signal"):
        assert learned_signal.predict_confirmed_proba(db_session, [{}]) is None
    state = learned_signal.refresh(db_session)
    assert not state.active
    assert f"(cold start): {short} confirmed / 60 false_positive" in state.reason
    assert f"needs at least {LEARNED_SIGNAL_MIN_LABELS_PER_CLASS} of each" in state.reason
    assert "cold start" in caplog.text


def test_enough_labels_without_signal_still_refuses(db_session, enabled):
    rng = np.random.default_rng(2)
    n = LEARNED_SIGNAL_MIN_LABELS_PER_CLASS
    signals = rng.normal(size=2 * n)
    _add_labels(
        db_session,
        [("confirmed", s) for s in signals[:n]] + [("false_positive", s) for s in signals[n:]],
    )
    state = learned_signal.refresh(db_session)
    assert not state.active
    assert "no better than trivial baselines" in state.reason
    assert state.evaluation is not None


def test_activates_with_enough_separable_labels_and_retrains_only_on_change(
    db_session, enabled, monkeypatch
):
    n = LEARNED_SIGNAL_MIN_LABELS_PER_CLASS
    rng = np.random.default_rng(3)
    _add_labels(
        db_session,
        [("confirmed", 2.0 + rng.normal(scale=0.2)) for _ in range(n)]
        + [("false_positive", rng.normal(scale=0.2)) for _ in range(n)],
    )
    calls = []
    real_evaluate = learned_signal.evaluate
    monkeypatch.setattr(
        learned_signal,
        "evaluate",
        lambda X, y, groups: calls.append(1) or real_evaluate(X, y, groups),
    )

    # Unit-variance, zero-mean baseline: explain_score returns the raw values as z.
    baseline = {
        "feature_names": SCORING_FEATURE_NAMES,
        "mean": np.zeros(8),
        "cov_inv": np.eye(8),
    }
    live_rows = [
        {
            "feature_vector": {
                name: signal * (k + 1) for k, name in enumerate(SCORING_FEATURE_NAMES)
            },
            "layer1_baseline": baseline,
            "gdi_score": signal,
            "ring_risk_score": 0.0,
            "ring_info": None,
        }
        for signal in (2.0, 0.0)
    ]
    proba = learned_signal.predict_confirmed_proba(db_session, live_rows)
    assert learned_signal.refresh(db_session).active
    assert proba[0] > proba[1]
    assert len(calls) == 1  # second refresh reused the cached model

    _add_labels(db_session, [("false_positive", 0.0)])
    learned_signal.refresh(db_session)
    assert len(calls) == 2
