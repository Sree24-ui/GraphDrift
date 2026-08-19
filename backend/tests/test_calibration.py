"""Stability tests for the analyst-feedback calibration loop."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.detection.calibration import (
    CALIBRATION_WINDOW,
    CLAMP_HIGH_PERCENT,
    CLAMP_LOW_PERCENT,
    MIN_CALIBRATION_SAMPLE,
    build_standard_streams,
    load_recent_judgments,
    on_manual_override,
    propose_adjustment,
    reset_calibration_state,
    tick_live_detection_cycle,
    ControllerState,
)
from app.models import Account, Alert
from app.settings_store import (
    alert_top_percent,
    set_alert_top_percentile,
    set_calibration_enabled,
)


@pytest.fixture(autouse=True)
def _reset_calibration():
    reset_calibration_state()
    set_alert_top_percentile(0.95, source="calibration")
    set_calibration_enabled(False)
    yield
    reset_calibration_state()
    set_alert_top_percentile(0.95, source="calibration")
    set_calibration_enabled(False)


def test_five_synthetic_streams_are_stable():
    results = {r.name: r for r in build_standard_streams(n_cycles=40)}
    assert set(results) == {
        "realistic",
        "degenerate_all_confirm",
        "degenerate_all_false_positive",
        "adversarial_noisy",
        "sparse",
    }
    failures = [r for r in results.values() if not r.pass_ok]
    assert not failures, "\n".join(
        f"{r.name}: {r.notes}" for r in failures
    )
    for result in results.values():
        assert result.oscillation_reversals == 0
        assert not result.outside_clamp
        assert CLAMP_LOW_PERCENT <= result.min_percent
        assert result.max_percent <= CLAMP_HIGH_PERCENT


def test_realistic_stream_does_not_drift():
    result = next(r for r in build_standard_streams() if r.name == "realistic")
    assert result.n_applied == 0
    assert result.settled_percent == 5.0
    assert result.time_to_stabilize == 0


def test_all_confirm_walks_to_upper_clamp_and_stops():
    result = next(
        r for r in build_standard_streams() if r.name == "degenerate_all_confirm"
    )
    series = [s.top_percent for s in result.steps]
    assert series[0] == 5.0
    assert result.settled_percent == CLAMP_HIGH_PERCENT
    assert all(b >= a - 1e-9 for a, b in zip(series, series[1:]))
    assert series[-5:] == [CLAMP_HIGH_PERCENT] * 5
    assert result.first_applied_cycle is not None
    assert result.first_applied_cycle >= 2


def test_all_fp_walks_to_lower_clamp_and_stops():
    result = next(
        r
        for r in build_standard_streams()
        if r.name == "degenerate_all_false_positive"
    )
    series = [s.top_percent for s in result.steps]
    assert result.settled_percent == CLAMP_LOW_PERCENT
    assert all(b <= a + 1e-9 for a, b in zip(series, series[1:]))
    assert series[-5:] == [CLAMP_LOW_PERCENT] * 5


def test_noisy_flip_does_not_move_threshold():
    result = next(
        r for r in build_standard_streams() if r.name == "adversarial_noisy"
    )
    assert result.n_applied == 0
    assert set(s.top_percent for s in result.steps) == {5.0}


def test_sparse_does_not_move_before_min_sample():
    result = next(r for r in build_standard_streams() if r.name == "sparse")
    early = [s for s in result.steps if s.sample_size < MIN_CALIBRATION_SAMPLE]
    assert early
    assert all(not s.applied for s in early)
    assert all(s.top_percent == 5.0 for s in early)


def test_load_recent_judgments_excludes_unreviewed_statuses(db_session):
    now = datetime(2026, 8, 19, 12, 0, 0)
    for i in range(6):
        db_session.add(
            Account(id=f"a{i}@ybl", created_at=now, last_active_at=now)
        )
    db_session.flush()
    statuses = [
        "confirmed",
        "false_positive",
        "new",
        "reviewing",
        "auto_closed",
        "confirmed",
    ]
    for i, status in enumerate(statuses):
        db_session.add(
            Alert(
                account_id=f"a{i}@ybl",
                risk_score=1.0,
                pattern_type="test",
                detected_at=now,
                status=status,
                confidence="high",
                updated_at=now + timedelta(seconds=i),
                reviewed_at=now + timedelta(seconds=i)
                if status in {"confirmed", "false_positive"}
                else None,
            )
        )
    db_session.commit()
    labels = load_recent_judgments(db_session, CALIBRATION_WINDOW)
    assert labels == ["confirmed", "false_positive", "confirmed"]
    assert "new" not in labels
    assert "reviewing" not in labels
    assert "auto_closed" not in labels


def test_manual_override_resets_consecutive_side_gate():
    high = ["confirmed"] * MIN_CALIBRATION_SAMPLE
    state = ControllerState(enabled=True)
    d1, state = propose_adjustment(
        current_percent=5.0, labels=high, state=state
    )
    assert not d1.applied
    assert state.consecutive_same_side == 1
    d2, state = propose_adjustment(
        current_percent=5.0, labels=high, state=state
    )
    assert d2.applied
    assert d2.new_percent == 5.5

    # Simulate Settings save: counters clear, threshold stays at the new baseline.
    on_manual_override()
    from app.detection.calibration import get_state

    reset_state = get_state()
    assert reset_state.consecutive_same_side == 0
    d3, after = propose_adjustment(
        current_percent=5.5, labels=high, state=reset_state
    )
    assert not d3.applied
    assert after.consecutive_same_side == 1


def test_live_tick_is_idle_until_tenth_cycle_and_when_disabled(db_session):
    set_calibration_enabled(True)
    for _ in range(9):
        assert tick_live_detection_cycle(db_session) is None
    decision = tick_live_detection_cycle(db_session)
    assert decision is not None
    assert not decision.applied
    assert decision.sample_size == 0

    set_calibration_enabled(False)
    for _ in range(20):
        assert tick_live_detection_cycle(db_session) is None


def test_auto_step_does_not_trigger_manual_override_reset():
    set_calibration_enabled(True)
    set_alert_top_percentile(0.945, source="calibration")  # 5.5%
    from app.detection.calibration import get_state

    # source=calibration must not bump generation / clear counters
    on_manual_override()
    gen = get_state().generation
    set_alert_top_percentile(0.94, source="calibration")
    assert get_state().generation == gen
    assert alert_top_percent() == pytest.approx(6.0)
