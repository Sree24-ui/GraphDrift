#!/usr/bin/env python3
"""Stress-test the calibration controller against five synthetic analyst streams."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.constants import (  # noqa: E402
    CALIBRATION_CLAMP_HIGH_PERCENT,
    CALIBRATION_CLAMP_LOW_PERCENT,
    CALIBRATION_STEP_PERCENT_POINTS,
    DEFAULT_ALERT_TOP_PERCENT,
    MIN_REVIEWED_SAMPLE,
    TARGET_BAND_HIGH,
    TARGET_BAND_LOW,
)
from app.detection.calibration import (  # noqa: E402
    build_standard_streams,
    stream_result_to_dict,
)

OUT = BACKEND_ROOT / "evaluation" / "data" / "calibration_stress.json"


def main() -> None:
    results = build_standard_streams(n_cycles=40)
    payload = {
        "n_cycles": 40,
        "start_percent": DEFAULT_ALERT_TOP_PERCENT,
        "target_band": [TARGET_BAND_LOW, TARGET_BAND_HIGH],
        "step_pp": CALIBRATION_STEP_PERCENT_POINTS,
        "clamp": [CALIBRATION_CLAMP_LOW_PERCENT, CALIBRATION_CLAMP_HIGH_PERCENT],
        "min_sample": MIN_REVIEWED_SAMPLE,
        "window": 50,
        "consecutive_side_required": 2,
        "streams": [stream_result_to_dict(r) for r in results],
        "all_pass": all(r.pass_ok for r in results),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT}")
    print()
    print(
        f"{'stream':<32} {'pass':<6} {'rev':>4} {'min':>6} {'max':>6} "
        f"{'settled':>8} {'steps':>6} {'t_stab':>7}"
    )
    for r in results:
        flag = "PASS" if r.pass_ok else "FAIL"
        tstab = "-" if r.time_to_stabilize is None else str(r.time_to_stabilize)
        print(
            f"{r.name:<32} {flag:<6} {r.oscillation_reversals:>4} "
            f"{r.min_percent:>6.1f} {r.max_percent:>6.1f} "
            f"{r.settled_percent:>8.1f} {r.n_applied:>6} {tstab:>7}"
        )
        for note in r.notes:
            print(f"  {note}")
    if not payload["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
