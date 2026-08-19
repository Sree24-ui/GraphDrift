"""Shared knobs file is the single source of detection constants."""

from app.constants import (
    DEFAULT_ALERT_TOP_PERCENT,
    DEFAULT_ALERT_TOP_PERCENTILE,
    KNOBS,
    MANUAL_ALERT_TOP_PERCENT_MAX,
    MANUAL_ALERT_TOP_PERCENT_MIN,
    WINDOW_MINUTES,
    percentile_to_top_percent,
    top_percent_to_percentile,
)
from app.detection.features import WINDOW_MINUTES as FEATURES_WINDOW
from app.detection.fusion import SECONDARY_WINDOW_MINUTES
from app.settings_store import DEFAULT_ALERT_TOP_PERCENTILE as STORE_DEFAULT


def test_knobs_file_has_required_keys():
    assert WINDOW_MINUTES == 15
    assert FEATURES_WINDOW == WINDOW_MINUTES
    assert SECONDARY_WINDOW_MINUTES == int(KNOBS["secondary_window_minutes"])
    assert DEFAULT_ALERT_TOP_PERCENTILE == 0.95
    assert STORE_DEFAULT == DEFAULT_ALERT_TOP_PERCENTILE


def test_percent_percentile_roundtrip():
    for percent in (1.0, 2.0, 5.0, 15.0, 25.0):
        percentile = top_percent_to_percentile(percent)
        assert percentile_to_top_percent(percentile) == percent


def test_manual_range_contains_auto_clamp():
    assert MANUAL_ALERT_TOP_PERCENT_MIN <= float(KNOBS["calibration_clamp_low_percent"])
    assert MANUAL_ALERT_TOP_PERCENT_MAX >= float(KNOBS["calibration_clamp_high_percent"])
    assert DEFAULT_ALERT_TOP_PERCENT == 5.0
