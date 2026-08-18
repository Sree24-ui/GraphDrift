#!/usr/bin/env python3
"""Measure IBM HI-Small laundering-ring durations and the implied time scale."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from evaluation.ibm_aml_config import IBM_AML_TIME_SCALE, IBM_AML_STRATIFY_WINDOW_HOURS  # noqa: E402
from evaluation.ibm_aml_patterns import (  # noqa: E402
    DEFAULT_PATTERNS,
    SYNTHETIC_SLOW_DURATION_MIN,
    load_pattern_instances,
    recommended_time_scale,
    summarize_by_typology,
)


def _fmt(summary: dict[str, float]) -> str:
    return (
        f"n={int(summary['n'])}  min={summary['min_min']:.1f}m  "
        f"p25={summary['p25_min']:.1f}m  median={summary['median_min']:.1f}m "
        f"({summary['median_min']/60:.2f}h)  p75={summary['p75_min']:.1f}m  "
        f"p90={summary['p90_min']:.1f}m  p95={summary['p95_min']:.1f}m  "
        f"max={summary['max_min']:.1f}m"
    )


def main() -> None:
    if not DEFAULT_PATTERNS.exists():
        raise SystemExit(
            f"Patterns file not found: {DEFAULT_PATTERNS}\n"
            "Download HI-Small_Patterns.txt from HuggingFace OsamaMIT/IBM-AML-HI-Small"
        )
    instances = load_pattern_instances()
    stats = summarize_by_typology(instances)
    print(f"Patterns file: {DEFAULT_PATTERNS}")
    print(f"Labeled typology instances: {len(instances)}")
    for name, summary in stats.items():
        print(f"  {name:16s} {_fmt(summary)}")

    median = stats["ALL"]["median_min"]
    scale = recommended_time_scale(median)
    print("\nCompression")
    print(f"  PaySim analog (÷20): median compressed = {median/20:.1f} min")
    print(
        f"  Slow-drip analog (median → {SYNTHETIC_SLOW_DURATION_MIN} min): "
        f"÷{scale}"
    )
    print(f"  Config IBM_AML_TIME_SCALE: {IBM_AML_TIME_SCALE}")
    if scale != float(IBM_AML_TIME_SCALE):
        raise SystemExit(
            f"Config scale {IBM_AML_TIME_SCALE} != recommended {scale} — update ibm_aml_config.py"
        )

    win_hours = IBM_AML_STRATIFY_WINDOW_HOURS
    eval_span_min = (win_hours * 60) / scale
    print(f"  48h dense window after ÷{scale:.0f}: {eval_span_min:.2f} eval minutes")
    print(
        f"  compressed ALL: median={median/scale:.1f}m  "
        f"p90={stats['ALL']['p90_min']/scale:.1f}m  "
        f"max={stats['ALL']['max_min']/scale:.1f}m"
    )

    # Overlap with the previously measured densest window (informational).
    win_start = datetime(2022, 9, 8, 2, 11)
    win_end = datetime(2022, 9, 10, 2, 11)
    overlapping = [
        i
        for i in instances
        if not (i.last_ts < win_start or i.first_ts > win_end)
    ]
    print(f"\nInstances overlapping densest 48h window: {len(overlapping)}")


if __name__ == "__main__":
    main()
