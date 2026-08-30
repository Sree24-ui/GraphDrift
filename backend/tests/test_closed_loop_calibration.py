"""Fast real-pipeline regressions for feedback calibration."""

from datetime import datetime, timedelta

from app.detection.calibration import CalibrationConfig
from app.models import Account, Alert, Transaction
from evaluation.closed_loop_calibration import alert_matches_ground_truth, run_closed_loop
from evaluation.db import init_eval_db


def test_ground_truth_uses_the_alerts_actual_scoring_window(tmp_path):
    factory = init_eval_db(tmp_path / "ground-truth-window.db")
    detected_at = datetime(2026, 1, 1, 12, 0)
    with factory() as db:
        db.add_all(
            [
                Account(id="watched", last_active_at=detected_at),
                Account(id="counterparty", last_active_at=detected_at),
            ]
        )
        db.add(
            Transaction(
                sender_id="watched",
                receiver_id="counterparty",
                amount=100.0,
                timestamp=detected_at - timedelta(minutes=30),
                is_synthetic_attack=True,
            )
        )
        db.flush()

        alert_15m = Alert(
            account_id="watched",
            risk_score=0.99,
            pattern_type="node_anomaly",
            detected_at=detected_at,
            feature_breakdown={"detection_window": 15},
        )
        alert_60m = Alert(
            account_id="watched",
            risk_score=0.99,
            pattern_type="node_anomaly",
            detected_at=detected_at,
            feature_breakdown={"detection_window": 60},
        )

        assert alert_matches_ground_truth(db, alert_15m) is False
        assert alert_matches_ground_truth(db, alert_60m) is True


def test_closed_loop_adjusts_without_riding_a_clamp(tmp_path):
    factory = init_eval_db(tmp_path / "closed-loop-test.db")
    cfg = CalibrationConfig(
        min_sample=8,
        window_size=40,
        cycles_per_calibration=4,
    )
    with factory() as db:
        result = run_closed_loop(
            db,
            cycles=24,
            seed=20260830,
            steps_per_cycle=75,
            cfg=cfg,
        )

    assert result.adjustments >= 1
    assert result.clamp_riding_cycles == 0
    applied_cycles = [step.cycle for step in result.steps if step.adjustment_applied]
    assert applied_cycles[0] >= cfg.cycles_per_calibration * 2
