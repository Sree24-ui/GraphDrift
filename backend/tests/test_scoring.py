"""Unit tests for core GraphDrift scoring functions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.detection.community import community_risk_score
from app.detection.fusion import compute_fused_scores
from app.detection.node_anomaly import (
    compute_baseline,
    compute_gdi_scores,
    mahalanobis_distance,
)


def _feature_row(
    account_id: str,
    *,
    in_degree: float = 1,
    out_degree: float = 1,
    in_count: float = 1,
    out_count: float = 1,
    amount_entropy: float = 1.0,
    counterparty_diversity: float = 0.5,
    fan_ratio: float = 0.5,
    burstiness: float = 0.3,
    velocity: float = 0.1,
) -> dict:
    return {
        "account_id": account_id,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "in_count": in_count,
        "out_count": out_count,
        "amount_entropy": amount_entropy,
        "counterparty_diversity": counterparty_diversity,
        "fan_ratio": fan_ratio,
        "burstiness": burstiness,
        "velocity": velocity,
    }


def test_mahalanobis_distance_zero_at_mean():
    rows = [_feature_row("a"), _feature_row("b", in_degree=2)]
    baseline = compute_baseline(rows)
    vector = baseline["mean"]
    assert mahalanobis_distance(vector, baseline) == pytest.approx(0.0, abs=1e-6)


def test_compute_gdi_scores_orders_outlier_higher():
    normals = [
        _feature_row(f"normal{i}", in_degree=1, out_degree=1, fan_ratio=0.5)
        for i in range(4)
    ]
    outlier = _feature_row(
        "outlier",
        in_degree=12,
        out_degree=0,
        in_count=20,
        out_count=0,
        fan_ratio=12.0,
        burstiness=0.95,
        amount_entropy=0.1,
    )
    results = {
        r["account_id"]: r for r in compute_gdi_scores([*normals, outlier])
    }
    normal_scores = [results[f"normal{i}"]["gdi_score"] for i in range(4)]
    assert results["outlier"]["gdi_score"] > max(normal_scores)
    assert all(0.5 <= s <= 5.0 for s in normal_scores)
    assert 0.5 <= results["outlier"]["gdi_score"] <= 5.0


def test_community_risk_score_high_for_hub_spoke():
    metrics = {
        "member_count": 6,
        "hub_concentration": 0.9,
        "external_edge_ratio": 8.0,
        "formed_recently": True,
    }
    score = community_risk_score(metrics)
    assert score >= 2.0
    assert score <= 5.0


def test_community_risk_score_low_for_distributed_group():
    metrics = {
        "member_count": 6,
        "hub_concentration": 0.15,
        "external_edge_ratio": 0.2,
        "formed_recently": False,
    }
    score = community_risk_score(metrics)
    assert score < 1.0


def test_community_risk_score_ignores_recent_flag_for_small_communities():
    small_recent = {
        "member_count": 2,
        "hub_concentration": 0.5,
        "external_edge_ratio": 1.0,
        "formed_recently": True,
    }
    small_old = {**small_recent, "formed_recently": False}
    assert community_risk_score(small_recent) == pytest.approx(
        community_risk_score(small_old)
    )


def test_compute_fused_scores_prefers_high_gdi_when_no_rings(db_session):
    """Fusion should rank a clear GDI outlier above a normal peer."""
    from app.models import Account, Transaction

    now = datetime(2026, 8, 12, 12, 0, 0)
    accounts = ["hub@ybl", "sender1@ybl", "sender2@ybl", "sender3@ybl"]
    for acct in accounts:
        db_session.add(Account(id=acct, created_at=now, last_active_at=now))

    # hub receives from three senders (high in-degree / fan-in pattern)
    txs = [
        ("sender1@ybl", "hub@ybl", 45000),
        ("sender2@ybl", "hub@ybl", 46000),
        ("sender3@ybl", "hub@ybl", 47000),
        ("hub@ybl", "sender1@ybl", 100),  # give hub some outbound too
    ]
    for i, (s, r, amt) in enumerate(txs):
        db_session.add(
            Transaction(
                sender_id=s,
                receiver_id=r,
                amount=float(amt),
                timestamp=now - timedelta(minutes=10 - i),
                is_synthetic_attack=False,
            )
        )
    db_session.commit()

    fused = compute_fused_scores(db_session, now, window_minutes=15, min_transactions=1)
    by_id = {row["account_id"]: row for row in fused}
    assert by_id["hub@ybl"]["fused_score"] >= by_id["sender1@ybl"]["fused_score"]
    assert by_id["hub@ybl"]["gdi_score"] > 0.5
