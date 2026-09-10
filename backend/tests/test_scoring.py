"""Unit tests for core GraphDrift scoring functions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.detection.community import community_risk_score
from app.detection.fusion import (
    WINDOW_MINUTES,
    SECONDARY_WINDOW_MINUTES,
    compute_fused_scores,
    compute_fused_scores_multiscale,
    create_alert_if_needed,
)
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
    assert by_id["hub@ybl"]["detection_window"] == 15


def test_compute_fused_scores_multiscale_union_tags_window(db_session):
    from app.models import Account, Transaction

    now = datetime(2026, 8, 12, 12, 0, 0)
    accounts = [
        "slowhub@ybl",
        "fasthub@ybl",
        *[f"s{i}@ybl" for i in range(6)],
        *[f"f{i}@ybl" for i in range(4)],
        "n1@ybl",
        "n2@ybl",
        "n3@ybl",
    ]
    for acct in accounts:
        db_session.add(Account(id=acct, created_at=now, last_active_at=now))

    # Slow-drip hub: fan-in only in the 15–60 minute band.
    for i in range(6):
        db_session.add(
            Transaction(
                sender_id=f"s{i}@ybl",
                receiver_id="slowhub@ybl",
                amount=45000.0 + i,
                timestamp=now - timedelta(minutes=50 - i),
                is_synthetic_attack=True,
            )
        )

    # Fast hub: fan-in inside the 15-minute window.
    for i in range(4):
        db_session.add(
            Transaction(
                sender_id=f"f{i}@ybl",
                receiver_id="fasthub@ybl",
                amount=44000.0 + i,
                timestamp=now - timedelta(minutes=5 - i * 0.2),
                is_synthetic_attack=True,
            )
        )

    # Benign triangle so both windows have a GDI baseline.
    for i, (s, r) in enumerate(
        [("n1@ybl", "n2@ybl"), ("n2@ybl", "n3@ybl"), ("n3@ybl", "n1@ybl")]
    ):
        db_session.add(
            Transaction(
                sender_id=s,
                receiver_id=r,
                amount=120.0 + i,
                timestamp=now - timedelta(minutes=3),
                is_synthetic_attack=False,
            )
        )
        db_session.add(
            Transaction(
                sender_id=s,
                receiver_id=r,
                amount=130.0 + i,
                timestamp=now - timedelta(minutes=2),
                is_synthetic_attack=False,
            )
        )
        db_session.add(
            Transaction(
                sender_id=s,
                receiver_id=r,
                amount=140.0 + i,
                timestamp=now - timedelta(minutes=1),
                is_synthetic_attack=False,
            )
        )
    db_session.commit()

    merged = compute_fused_scores_multiscale(
        db_session, now, min_transactions=3
    )
    by_id = {row["account_id"]: row for row in merged}

    assert "slowhub@ybl" in by_id
    assert by_id["slowhub@ybl"]["detection_window"] == SECONDARY_WINDOW_MINUTES
    assert by_id["slowhub@ybl"]["fused_score_by_window"][WINDOW_MINUTES] == 0.0
    assert by_id["slowhub@ybl"]["fused_score_by_window"][SECONDARY_WINDOW_MINUTES] > 0

    assert "fasthub@ybl" in by_id
    assert by_id["fasthub@ybl"]["detection_window"] == WINDOW_MINUTES


def test_multiscale_union_keeps_fast_star_that_global_cut_drops():
    """15m and 60m percentiles must not share one top-k cut."""
    from app.detection.fusion import (
        compute_fused_scores_multiscale_max_merge,
        select_top_anomaly_accounts,
        select_top_anomaly_accounts_multiscale,
    )

    primary = [{"account_id": f"p{i}", "fused_score": 1.0} for i in range(40)]
    primary[0] = {"account_id": "star15", "fused_score": 4.26}
    secondary = [
        {"account_id": f"busy{i}", "fused_score": 4.90 - i * 0.01} for i in range(40)
    ]

    selected, _, _ = select_top_anomaly_accounts_multiscale(primary, secondary)
    assert "star15" in selected

    pooled = []
    by_15 = {row["account_id"]: row for row in primary}
    by_60 = {row["account_id"]: row for row in secondary}
    for account_id in set(by_15) | set(by_60):
        score_15 = float(by_15[account_id]["fused_score"]) if account_id in by_15 else 0.0
        score_60 = float(by_60[account_id]["fused_score"]) if account_id in by_60 else 0.0
        pooled.append({"account_id": account_id, "fused_score": max(score_15, score_60)})
    global_sel, _, _ = select_top_anomaly_accounts(pooled, "fused_score")
    assert "star15" not in global_sel

    with pytest.raises(RuntimeError, match="not comparable"):
        compute_fused_scores_multiscale_max_merge()


def test_cycle_timing_accumulates_only_when_recording():
    from app.detection.cycle_timing import phase, record_cycle_timing

    with phase("layer2"):
        pass
    with record_cycle_timing() as spans:
        with phase("layer2"):
            _ = sum(range(20_000))
        with phase("layer2"):
            _ = sum(range(20_000))
    assert spans["layer2"] > 0
    assert spans["total"] >= spans["layer2"]
    assert spans["features"] == 0.0


def test_create_alert_if_needed_dedups_across_window_scales(db_session):
    from app.models import Account

    now = datetime(2026, 8, 12, 12, 0, 0)
    db_session.add(Account(id="dup@ybl", created_at=now, last_active_at=now))
    db_session.commit()

    fused_15 = {
        "account_id": "dup@ybl",
        "fused_score": 4.2,
        "confidence": "high",
        "gdi_score": 3.0,
        "ring_risk_score": 3.0,
        "gdi_percentile": 0.9,
        "ring_percentile": 0.9,
        "community_id": 1,
        "detection_window": 15,
    }
    fused_60 = {**fused_15, "fused_score": 4.1, "detection_window": 60}
    explanation_15 = {
        "confidence": "high",
        "primary_reason": "fast scale",
        "detection_window": 15,
    }
    explanation_60 = {
        "confidence": "high",
        "primary_reason": "slow scale",
        "detection_window": 60,
    }

    first = create_alert_if_needed(
        db_session, "dup@ybl", fused_15, explanation_15, now, alert_threshold=0.0
    )
    second = create_alert_if_needed(
        db_session, "dup@ybl", fused_60, explanation_60, now, alert_threshold=0.0
    )
    assert first is not None
    assert first.action == "CREATE"
    assert second is None


def test_compute_gdi_scores_attaches_shared_baseline():
    rows = [_feature_row(f"n{i}") for i in range(12)]
    scored = compute_gdi_scores(rows)
    assert scored[0]["layer1_baseline"] is scored[-1]["layer1_baseline"]
    assert "mean" in scored[0]["layer1_baseline"]


def test_build_explanation_reuses_scoring_baseline(monkeypatch, db_session):
    from app.detection.fusion import build_explanation

    now = datetime(2026, 8, 12, 12, 0, 0)
    rows = [_feature_row(f"n{i}", in_degree=1 + i * 0.01) for i in range(12)]
    scored = compute_gdi_scores(rows)
    fused = {
        "account_id": scored[0]["account_id"],
        "fused_score": 4.0,
        "confidence": "high",
        "gdi_score": scored[0]["gdi_score"],
        "gdi_percentile": 0.99,
        "ring_risk_score": 0.0,
        "ring_percentile": 0.0,
        "feature_vector": scored[0]["feature_vector"],
        "layer1_baseline": scored[0]["layer1_baseline"],
        "ring_info": None,
        "detection_window": WINDOW_MINUTES,
    }

    def _boom(*_args, **_kwargs):
        raise AssertionError("extract_all_features must not run when baseline is cached")

    monkeypatch.setattr("app.detection.fusion.extract_all_features", _boom)
    explanation = build_explanation(fused["account_id"], fused, db_session, now)
    assert explanation["layer1_breakdown"]
    assert explanation["fused_score"] == 4.0


def test_co_hub_detects_split_mule_role_but_not_benign_shapes():
    """Dilution evasion: 3 co-mules each score 0.38 alone, ~1.0 as one hub."""
    from app.detection.community import _detect_co_hub

    diluted = {**{f"m{i}": 8 for i in range(3)}, **{f"s{i}": 1 for i in range(18)}}
    members, share = _detect_co_hub(diluted, 21)
    assert len(members) == 3
    assert share > 0.9  # vs 8/21 = 0.381 for any single co-mule

    # A real single-hub star already scores 1.0; do not double-count it.
    assert _detect_co_hub({"hub": 10, **{f"s{i}": 1 for i in range(10)}}, 10) == ([], 0.0)
    # Benign communities must not trip the gates (this is the false-positive risk).
    assert _detect_co_hub({"a": 3, "b": 3, "c": 2, "d": 2, "e": 2, "f": 2}, 7) == ([], 0.0)
    assert _detect_co_hub({"a": 6, "b": 5, "c": 4, "d": 3, "e": 2, "f": 1}, 11) == ([], 0.0)
