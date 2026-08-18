"""Unit tests for IBM HI-Small AML loader helpers (no full dataset required)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from evaluation.ibm_aml_config import IBM_AML_TIME_SCALE
from evaluation.ibm_aml_patterns import load_pattern_instances, recommended_time_scale
from evaluation.load_ibm_aml import (
    choose_time_scale,
    compress_timestamps,
    densest_fraud_window,
    inspect_schema,
)


def test_inspect_schema_maps_canonical_ibm_headers():
    df = pd.DataFrame(
        {
            "Timestamp": ["2022/09/01 00:20"],
            "From Bank": [10],
            "Account": ["8000EBD30"],
            "To Bank": [10],
            "Account.1": ["8000F5340"],
            "Amount Received": [100.0],
            "Receiving Currency": ["US Dollar"],
            "Amount Paid": [100.0],
            "Payment Currency": ["US Dollar"],
            "Payment Format": ["Cheque"],
            "Is Laundering": [0],
        }
    )
    mapping = inspect_schema(df)
    assert mapping["from_account"] == "Account"
    assert mapping["to_account"] == "Account.1"
    assert mapping["is_laundering"] == "Is Laundering"


def test_densest_fraud_window_picks_cluster():
    times = pd.Series(
        [
            datetime(2022, 9, 1, 0, 0),
            datetime(2022, 9, 3, 10, 0),
            datetime(2022, 9, 3, 10, 5),
            datetime(2022, 9, 3, 10, 20),
            datetime(2022, 9, 8, 0, 0),
        ]
    )
    start, end = densest_fraud_window(times, window=timedelta(hours=1))
    assert start == datetime(2022, 9, 3, 10, 0)
    assert end - start == timedelta(hours=1)


def test_choose_time_scale_skips_compression_on_short_span():
    assert choose_time_scale(timedelta(hours=4)) == 1.0
    assert choose_time_scale(timedelta(days=10)) == float(IBM_AML_TIME_SCALE)


def test_parse_pattern_instance_duration(tmp_path: Path):
    sample = tmp_path / "patterns.txt"
    sample.write_text(
        "BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 3-degree Fan-Out\n"
        "2022/09/01 00:00,1,A,2,B,1.0,US Dollar,1.0,US Dollar,ACH,1\n"
        "2022/09/01 06:00,1,A,3,C,1.0,US Dollar,1.0,US Dollar,ACH,1\n"
        "END LAUNDERING ATTEMPT - FAN-OUT\n"
    )
    instances = load_pattern_instances(sample)
    assert len(instances) == 1
    assert instances[0].typology == "FAN-OUT"
    assert instances[0].duration_minutes == 360.0


def test_recommended_scale_is_slow_drip_analog_not_paysim():
    # Median ring ~4484 min → 13.5 min compressed (slow-drip in 60-min window).
    assert recommended_time_scale(4484.0) == 332
    assert IBM_AML_TIME_SCALE == 332
    assert recommended_time_scale(4484.0) != 20


def test_compress_timestamps_preserves_order_and_ratios():
    ts = pd.Series(
        [
            datetime(2022, 9, 1, 0, 0),
            datetime(2022, 9, 1, 1, 0),
            datetime(2022, 9, 1, 2, 0),
        ]
    )
    compressed = compress_timestamps(ts, time_scale=20)
    gaps = compressed.diff().dropna()
    assert all(gaps == gaps.iloc[0])
    assert gaps.iloc[0] == timedelta(minutes=3)
