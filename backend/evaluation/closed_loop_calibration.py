"""Closed-loop calibration against the real seeded simulator and detector.

This harness intentionally uses ``run_detection_cycle`` and
``tick_live_detection_cycle``. New alerts are judged from synthetic-attack
transactions involving that alert's account inside that alert's own scoring
window: ``detected_at - detection_window <= timestamp <= detected_at``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, or_, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.detection.calibration as calibration  # noqa: E402
from app.detection.calibration import CalibrationConfig  # noqa: E402
from app.detection.fusion import run_detection_cycle  # noqa: E402
from app.models import Alert, Transaction  # noqa: E402
from app.settings_store import (  # noqa: E402
    alert_top_percent,
    set_alert_top_percentile,
    set_calibration_enabled,
)
from app.simulation.generator import generate_offline_trace  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402


@dataclass(frozen=True)
class ClosedLoopStep:
    cycle: int
    as_of: str
    alerts_created: int
    confirmed: int
    false_positive: int
    scored_15m: int
    scored_60m: int
    threshold_percent: float
    confirmed_rate: float | None
    calibration_action: str | None
    adjustment_applied: bool


@dataclass(frozen=True)
class ClosedLoopResult:
    seed: int
    cycles: int
    trace_stats: dict
    steps: list[ClosedLoopStep]
    adjustments: int
    clamp_riding_cycles: int
    final_threshold_percent: float
    equilibrium: str


def alert_matches_ground_truth(db, alert: Alert) -> bool:
    """Judge an alert only within the exact window used to score that alert."""
    breakdown = alert.feature_breakdown or {}
    window_minutes = int(breakdown.get("detection_window", 15))
    window_end = alert.detected_at
    window_start = window_end - timedelta(minutes=window_minutes)
    stmt = (
        select(Transaction.id)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                Transaction.is_synthetic_attack.is_(True),
                or_(
                    Transaction.sender_id == alert.account_id,
                    Transaction.receiver_id == alert.account_id,
                ),
            )
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def run_closed_loop(
    db,
    *,
    cycles: int = 60,
    seed: int = 20260830,
    steps_per_cycle: int = 90,
    warmup_minutes: int = 15,
    cfg: CalibrationConfig | None = None,
) -> ClosedLoopResult:
    cfg = cfg or CalibrationConfig(
        min_sample=12,
        window_size=50,
        cycles_per_calibration=4,
    )
    start = datetime(2026, 1, 1, 0, 0, 0)
    interval_seconds = 2.0
    warmup_steps = int(warmup_minutes * 60 / interval_seconds)
    trace_stats = generate_offline_trace(
        db,
        n_steps=warmup_steps + cycles * steps_per_cycle,
        seed=seed,
        start_time=start,
        interval_seconds=interval_seconds,
        catchup="normals",
    )

    previous_config = calibration._config
    calibration._config = cfg
    calibration.reset_calibration_state()
    set_alert_top_percentile(0.95, source="calibration")
    set_calibration_enabled(True)
    steps: list[ClosedLoopStep] = []
    seen_alert_ids: set[int] = set()
    try:
        for cycle in range(1, cycles + 1):
            offset_steps = warmup_steps + cycle * steps_per_cycle
            as_of = start + timedelta(seconds=offset_steps * interval_seconds)
            actions = run_detection_cycle(db, as_of=as_of)
            created = [
                action.alert
                for action in actions
                if action.action == "CREATE" and action.alert.id not in seen_alert_ids
            ]
            confirmed = 0
            false_positive = 0
            scored_15m = 0
            scored_60m = 0
            for alert in created:
                seen_alert_ids.add(alert.id)
                scoring_window = int(
                    (alert.feature_breakdown or {}).get("detection_window", 15)
                )
                if scoring_window == 15:
                    scored_15m += 1
                elif scoring_window == 60:
                    scored_60m += 1
                if alert_matches_ground_truth(db, alert):
                    alert.status = "confirmed"
                    confirmed += 1
                else:
                    alert.status = "false_positive"
                    false_positive += 1
                alert.reviewed_at = as_of
                alert.updated_at = as_of
            db.commit()

            decision = calibration.tick_live_detection_cycle(db)
            state = calibration.get_state()
            steps.append(
                ClosedLoopStep(
                    cycle=cycle,
                    as_of=as_of.isoformat(),
                    alerts_created=len(created),
                    confirmed=confirmed,
                    false_positive=false_positive,
                    scored_15m=scored_15m,
                    scored_60m=scored_60m,
                    threshold_percent=alert_top_percent(),
                    confirmed_rate=state.last_confirmed_rate,
                    calibration_action=decision.action if decision else None,
                    adjustment_applied=bool(decision and decision.applied),
                )
            )
    finally:
        set_calibration_enabled(False)
        calibration._config = previous_config
        calibration.reset_calibration_state()
        set_alert_top_percentile(0.95, source="calibration")

    thresholds = [step.threshold_percent for step in steps]
    clamp_cycles = sum(
        threshold in {cfg.clamp_low, cfg.clamp_high} for threshold in thresholds
    )
    adjustments = sum(step.adjustment_applied for step in steps)
    final = thresholds[-1]
    recent = thresholds[-8:]
    recent_rates = [
        step.confirmed_rate for step in steps[-8:] if step.confirmed_rate is not None
    ]
    if clamp_cycles:
        equilibrium = "clamp-bound"
    elif (
        adjustments > 0
        and len(recent) == 8
        and len(set(recent)) == 1
        and recent_rates
        and cfg.target_low <= recent_rates[-1] <= cfg.target_high
    ):
        equilibrium = "interior"
    else:
        equilibrium = "not-settled"
    return ClosedLoopResult(
        seed=seed,
        cycles=cycles,
        trace_stats=trace_stats,
        steps=steps,
        adjustments=adjustments,
        clamp_riding_cycles=clamp_cycles,
        final_threshold_percent=final,
        equilibrium=equilibrium,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    temp_dir = None
    database = args.database
    if database is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="graphdrift-closed-loop-")
        database = Path(temp_dir.name) / "closed-loop.db"
    factory = init_eval_db(database)
    with factory() as db:
        result = run_closed_loop(db, cycles=args.cycles, seed=args.seed)
    print(json.dumps(asdict(result), indent=2))
    if temp_dir is not None:
        temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
