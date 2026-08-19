"""Analyst-feedback calibration of alert sensitivity.

This is a *damped, bounded* controller on the human-readable alert share
(``alert_top_percent``, e.g. 5.0 for top 5%), not a proportional controller
on confirmed-rate error.

Control law (documented so the direction is not ambiguous):
- Confirmed rate = confirmed / (confirmed + false_positive) on a rolling
  window of analyst-judged alerts. ``new``, ``reviewing``, and ``auto_closed``
  are excluded — same honesty rule as Reports.
- Target band **60–85%**. Below 60% analysts are rejecting too many alerts
  → *tighten* (smaller top-%, fewer alerts). Above 85% almost every alert is
  confirmed, which is a proxy for being too selective / missing fraud
  → *loosen* (larger top-%, more alerts). Inside the band: hold.
- Updates are a **fixed ±0.5 percentage-point step**, never proportional to
  how far the rate sits from the band. That caps overshoot.
- A side of the band must be observed on **two consecutive calibration
  cycles** before the first step (and after a direction flip). Homogeneous
  bias then walks one step per cycle to the clamp; a reviewer who flips
  bulk labels every cycle never accumulates two matching sides, so the
  threshold does not swing.
- Auto-applied values are clamped to **2%–15%**. A manual setting outside
  that range is not snapped back; auto may only step *toward* the clamp.
- Manual Settings changes always win: they become the new baseline and
  reset consecutive-side counters so auto cannot fight the operator.

Cadence: the live detection loop calls ``tick_live_detection_cycle`` every
cycle (~45s). Calibration itself runs every ``CYCLES_PER_CALIBRATION``
ticks (default 10 ≈ 7.5 min), not every detection cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Literal, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.constants import (
    CALIBRATION_CLAMP_HIGH_PERCENT,
    CALIBRATION_CLAMP_LOW_PERCENT,
    CALIBRATION_CONSECUTIVE_SIDE_REQUIRED,
    CALIBRATION_STEP_PERCENT_POINTS,
    CALIBRATION_WINDOW,
    CYCLES_PER_CALIBRATION,
    MIN_REVIEWED_SAMPLE,
    TARGET_BAND_HIGH,
    TARGET_BAND_LOW,
)
from app.models import Alert
from app.settings_store import (
    alert_top_percent,
    get_alert_top_percentile,
    set_alert_top_percentile,
)

logger = logging.getLogger("graphdrift.calibration")

JUDGED_STATUSES = frozenset({"confirmed", "false_positive"})
EXCLUDED_STATUSES = frozenset({"new", "reviewing", "auto_closed"})

MIN_CALIBRATION_SAMPLE = MIN_REVIEWED_SAMPLE
CLAMP_LOW_PERCENT = CALIBRATION_CLAMP_LOW_PERCENT
CLAMP_HIGH_PERCENT = CALIBRATION_CLAMP_HIGH_PERCENT
STEP_PERCENT_POINTS = CALIBRATION_STEP_PERCENT_POINTS
CONSECUTIVE_SIDE_REQUIRED = CALIBRATION_CONSECUTIVE_SIDE_REQUIRED

Side = Literal["low", "high"]
Action = Literal["hold", "tighten", "loosen"]


@dataclass(frozen=True)
class CalibrationConfig:
    min_sample: int = MIN_CALIBRATION_SAMPLE
    window_size: int = CALIBRATION_WINDOW
    target_low: float = TARGET_BAND_LOW
    target_high: float = TARGET_BAND_HIGH
    step_pp: float = STEP_PERCENT_POINTS
    clamp_low: float = CLAMP_LOW_PERCENT
    clamp_high: float = CLAMP_HIGH_PERCENT
    consecutive_required: int = CONSECUTIVE_SIDE_REQUIRED
    cycles_per_calibration: int = CYCLES_PER_CALIBRATION


DEFAULT_CONFIG = CalibrationConfig()


@dataclass
class LastAdjustment:
    at: datetime
    previous_percent: float
    new_percent: float
    action: Action
    confirmed_rate: float
    sample_size: int
    reason: str


@dataclass
class ControllerState:
    enabled: bool = False
    cycle_count: int = 0
    consecutive_same_side: int = 0
    last_side: Side | None = None
    last_adjustment: LastAdjustment | None = None
    skipped_reason: str | None = None
    last_confirmed_rate: float | None = None
    last_sample_size: int = 0
    last_confirmed: int = 0
    last_false_positive: int = 0
    generation: int = 0


@dataclass(frozen=True)
class CalibrationDecision:
    action: Action
    new_percent: float
    applied: bool
    reason: str
    confirmed_rate: float | None
    sample_size: int
    confirmed: int
    false_positive: int
    side: Side | None


_state = ControllerState()
_config = DEFAULT_CONFIG


def get_config() -> CalibrationConfig:
    return _config


def get_state() -> ControllerState:
    return _state


def reset_calibration_state(*, keep_enabled: bool = False) -> None:
    """Test / process helper. Does not change the live threshold."""
    global _state
    enabled = _state.enabled if keep_enabled else False
    _state = ControllerState(enabled=enabled)


def set_calibration_enabled(enabled: bool) -> bool:
    _state.enabled = bool(enabled)
    if not _state.enabled:
        _state.skipped_reason = "auto-calibration is off"
        _state.consecutive_same_side = 0
        _state.last_side = None
        logger.info("Auto-calibration disabled")
    else:
        _state.skipped_reason = "enabled; waiting for next calibration tick"
        logger.info("Auto-calibration enabled")
    return _state.enabled


def on_manual_override() -> None:
    """Called when an analyst saves a new sensitivity in Settings."""
    _state.consecutive_same_side = 0
    _state.last_side = None
    _state.generation += 1
    _state.skipped_reason = (
        "manual override — controller state reset; auto will not overwrite "
        "this baseline until the next out-of-band calibration tick"
    )
    logger.info(
        "Manual sensitivity override at top %.1f%%; calibration counters reset",
        alert_top_percent(),
    )


def _round_percent(value: float) -> float:
    return round(float(value), 1)


def _step_toward_clamp(
    current: float, action: Action, cfg: CalibrationConfig
) -> float:
    """One fixed step. Never snap a manual-outlier down to the clamp in one jump."""
    if action == "tighten":
        proposed = _round_percent(current - cfg.step_pp)
        if current > cfg.clamp_high:
            return max(cfg.clamp_high, proposed)
        return max(cfg.clamp_low, proposed)
    proposed = _round_percent(current + cfg.step_pp)
    if current < cfg.clamp_low:
        return min(cfg.clamp_low, proposed)
    return min(cfg.clamp_high, proposed)


def confirmed_rate_from_labels(labels: Sequence[str]) -> tuple[float | None, int, int, int]:
    judged = [label for label in labels if label in JUDGED_STATUSES]
    confirmed = sum(1 for label in judged if label == "confirmed")
    false_positive = sum(1 for label in judged if label == "false_positive")
    sample = confirmed + false_positive
    if sample == 0:
        return None, 0, 0, 0
    return confirmed / sample, sample, confirmed, false_positive


def load_recent_judgments(db: Session, window_size: int) -> list[str]:
    judged_at = func.coalesce(Alert.reviewed_at, Alert.updated_at)
    rows = db.execute(
        select(Alert.status)
        .where(Alert.status.in_(tuple(JUDGED_STATUSES)))
        .order_by(judged_at.desc(), Alert.id.desc())
        .limit(window_size)
    ).all()
    return [row[0] for row in rows]


def _side_for_rate(rate: float, cfg: CalibrationConfig) -> Side | None:
    if rate < cfg.target_low:
        return "low"
    if rate > cfg.target_high:
        return "high"
    return None


def propose_adjustment(
    *,
    current_percent: float,
    labels: Sequence[str],
    state: ControllerState,
    cfg: CalibrationConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> tuple[CalibrationDecision, ControllerState]:
    """Pure update. Returns the decision and a *new* state (does not mutate)."""
    now = now or datetime.now(timezone.utc)
    rate, sample, confirmed, fp = confirmed_rate_from_labels(labels)
    current = _round_percent(current_percent)
    next_state = replace(
        state,
        last_confirmed_rate=rate,
        last_sample_size=sample,
        last_confirmed=confirmed,
        last_false_positive=fp,
    )

    if sample < cfg.min_sample:
        reason = (
            f"sample size {sample} < {cfg.min_sample}; threshold left at {current:.1f}%"
        )
        next_state.skipped_reason = reason
        return (
            CalibrationDecision(
                action="hold",
                new_percent=current,
                applied=False,
                reason=reason,
                confirmed_rate=rate,
                sample_size=sample,
                confirmed=confirmed,
                false_positive=fp,
                side=None,
            ),
            next_state,
        )

    assert rate is not None
    side = _side_for_rate(rate, cfg)
    if side is None:
        reason = (
            f"confirmed rate {rate:.1%} (n={sample}) inside {cfg.target_low:.0%}–"
            f"{cfg.target_high:.0%} target band; holding {current:.1f}%"
        )
        next_state.consecutive_same_side = 0
        next_state.last_side = None
        next_state.skipped_reason = reason
        return (
            CalibrationDecision(
                action="hold",
                new_percent=current,
                applied=False,
                reason=reason,
                confirmed_rate=rate,
                sample_size=sample,
                confirmed=confirmed,
                false_positive=fp,
                side=None,
            ),
            next_state,
        )

    if next_state.last_side == side:
        next_state.consecutive_same_side += 1
    else:
        next_state.consecutive_same_side = 1
        next_state.last_side = side

    if next_state.consecutive_same_side < cfg.consecutive_required:
        reason = (
            f"confirmed rate {rate:.1%} (n={sample}) is "
            f"{'below' if side == 'low' else 'above'} the target band, but "
            f"only {next_state.consecutive_same_side}/"
            f"{cfg.consecutive_required} consecutive cycles on that side; "
            f"holding {current:.1f}% to damp noise"
        )
        next_state.skipped_reason = reason
        return (
            CalibrationDecision(
                action="hold",
                new_percent=current,
                applied=False,
                reason=reason,
                confirmed_rate=rate,
                sample_size=sample,
                confirmed=confirmed,
                false_positive=fp,
                side=side,
            ),
            next_state,
        )

    if side == "low":
        action: Action = "tighten"
        verb = "Lowered"
        why = (
            f"confirmed rate was {rate:.1%} (n={sample}), below the "
            f"{cfg.target_low:.0%}–{cfg.target_high:.0%} target band"
        )
    else:
        action = "loosen"
        verb = "Raised"
        why = (
            f"confirmed rate was {rate:.1%} (n={sample}), above the "
            f"{cfg.target_low:.0%}–{cfg.target_high:.0%} target band"
        )

    proposed = _step_toward_clamp(current, action, cfg)
    if proposed == current:
        bound = "upper" if action == "loosen" else "lower"
        reason = (
            f"at {bound} clamp ({current:.1f}%); confirmed rate {rate:.1%} "
            f"(n={sample}) still out of band — holding, not oscillating"
        )
        next_state.skipped_reason = reason
        return (
            CalibrationDecision(
                action="hold",
                new_percent=current,
                applied=False,
                reason=reason,
                confirmed_rate=rate,
                sample_size=sample,
                confirmed=confirmed,
                false_positive=fp,
                side=side,
            ),
            next_state,
        )

    reason = f"{verb} to {proposed:.1f}% — {why}"
    next_state.skipped_reason = None
    next_state.last_adjustment = LastAdjustment(
        at=now,
        previous_percent=current,
        new_percent=proposed,
        action=action,
        confirmed_rate=rate,
        sample_size=sample,
        reason=reason,
    )
    return (
        CalibrationDecision(
            action=action,
            new_percent=proposed,
            applied=True,
            reason=reason,
            confirmed_rate=rate,
            sample_size=sample,
            confirmed=confirmed,
            false_positive=fp,
            side=side,
        ),
        next_state,
    )


def apply_decision(decision: CalibrationDecision) -> float:
    """Write an auto-calibrated top-% without treating it as a manual override."""
    percentile = 1.0 - decision.new_percent / 100.0
    set_alert_top_percentile(percentile, source="calibration")
    logger.info(decision.reason)
    return decision.new_percent


def run_calibration_step(
    labels: Sequence[str],
    *,
    cfg: CalibrationConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
    apply: bool = True,
) -> CalibrationDecision:
    global _state
    current = alert_top_percent()
    decision, next_state = propose_adjustment(
        current_percent=current,
        labels=labels,
        state=_state,
        cfg=cfg,
        now=now,
    )
    _state = next_state
    if apply and decision.applied:
        apply_decision(decision)
    elif not decision.applied:
        logger.info(decision.reason)
    return decision


def run_calibration_from_db(
    db: Session,
    *,
    cfg: CalibrationConfig = DEFAULT_CONFIG,
    apply: bool = True,
) -> CalibrationDecision:
    labels = load_recent_judgments(db, cfg.window_size)
    return run_calibration_step(labels, cfg=cfg, apply=apply)


def tick_live_detection_cycle(db: Session) -> CalibrationDecision | None:
    """Called from the live detection loop once per cycle.

    Returns a decision when a calibration tick actually ran, else None.
    """
    _state.cycle_count += 1
    if not _state.enabled:
        _state.skipped_reason = "auto-calibration is off"
        return None
    cfg = _config
    if _state.cycle_count % cfg.cycles_per_calibration != 0:
        remaining = cfg.cycles_per_calibration - (
            _state.cycle_count % cfg.cycles_per_calibration
        )
        _state.skipped_reason = (
            f"waiting {remaining} detection cycle(s) until next calibration tick"
        )
        return None
    return run_calibration_from_db(db, cfg=cfg, apply=True)


def calibration_status_payload() -> dict:
    last = _state.last_adjustment
    last_payload = None
    if last is not None:
        last_payload = {
            "at": last.at.isoformat(),
            "previous_percent": last.previous_percent,
            "new_percent": last.new_percent,
            "action": last.action,
            "confirmed_rate": last.confirmed_rate,
            "sample_size": last.sample_size,
            "reason": last.reason,
        }
    remaining = None
    if _state.enabled:
        mod = _state.cycle_count % _config.cycles_per_calibration
        remaining = (
            _config.cycles_per_calibration
            if mod == 0 and _state.cycle_count == 0
            else (_config.cycles_per_calibration - mod) % _config.cycles_per_calibration
        )
    return {
        "enabled": _state.enabled,
        "confirmed_rate": _state.last_confirmed_rate,
        "sample_size": _state.last_sample_size,
        "confirmed": _state.last_confirmed,
        "false_positive": _state.last_false_positive,
        "min_sample": _config.min_sample,
        "window_size": _config.window_size,
        "target_band_low": _config.target_low,
        "target_band_high": _config.target_high,
        "step_percent_points": _config.step_pp,
        "clamp_low_percent": _config.clamp_low,
        "clamp_high_percent": _config.clamp_high,
        "cycles_per_calibration": _config.cycles_per_calibration,
        "cycles_until_next": remaining,
        "skipped_reason": _state.skipped_reason,
        "last_adjustment": last_payload,
        "alert_top_percent": alert_top_percent(),
        "alert_top_percentile": get_alert_top_percentile(),
    }


# ---------------------------------------------------------------------------
# Stress-test harness (synthetic streams; no database)
# ---------------------------------------------------------------------------


@dataclass
class StreamStep:
    cycle: int
    top_percent: float
    confirmed_rate: float | None
    sample_size: int
    action: Action
    applied: bool
    reason: str


@dataclass
class StreamResult:
    name: str
    description: str
    steps: list[StreamStep]
    oscillation_reversals: int
    min_percent: float
    max_percent: float
    outside_clamp: bool
    n_applied: int
    first_applied_cycle: int | None
    settled_percent: float
    time_to_stabilize: int | None
    pass_ok: bool
    notes: list[str] = field(default_factory=list)


def _count_reversals(values: Sequence[float]) -> int:
    """Count sign flips of consecutive deltas (ignore holds)."""
    deltas: list[float] = []
    for prev, cur in zip(values, values[1:]):
        d = round(cur - prev, 10)
        if d != 0:
            deltas.append(d)
    reversals = 0
    for prev, cur in zip(deltas, deltas[1:]):
        if prev * cur < 0:
            reversals += 1
    return reversals


def _time_to_stabilize(values: Sequence[float], *, hold: int = 8, tol: float = 1e-9) -> int | None:
    """First index after which the next `hold` values stay within tol of that value."""
    if len(values) < hold:
        return None
    for i in range(len(values) - hold + 1):
        window = values[i : i + hold]
        if max(window) - min(window) <= tol:
            return i
    return None


def simulate_feedback_stream(
    name: str,
    description: str,
    label_batches: Iterable[Sequence[str]],
    *,
    start_percent: float = 5.0,
    cfg: CalibrationConfig = DEFAULT_CONFIG,
    n_cycles: int | None = None,
) -> StreamResult:
    """Run the pure controller against a sequence of judgment windows.

    Each item in ``label_batches`` is the *full rolling window* observed at
    that calibration cycle (not a delta). The live path queries the last N
    judged alerts the same way.
    """
    batches = list(label_batches)
    if n_cycles is not None:
        batches = batches[:n_cycles]
    state = ControllerState(enabled=True)
    current = _round_percent(start_percent)
    steps: list[StreamStep] = []
    for i, labels in enumerate(batches, start=1):
        decision, state = propose_adjustment(
            current_percent=current,
            labels=labels,
            state=state,
            cfg=cfg,
            now=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        if decision.applied:
            current = decision.new_percent
        steps.append(
            StreamStep(
                cycle=i,
                top_percent=current,
                confirmed_rate=decision.confirmed_rate,
                sample_size=decision.sample_size,
                action=decision.action,
                applied=decision.applied,
                reason=decision.reason,
            )
        )

    series = [s.top_percent for s in steps]
    reversals = _count_reversals(series)
    min_p = min(series)
    max_p = max(series)
    outside = min_p < cfg.clamp_low - 1e-9 or max_p > cfg.clamp_high + 1e-9
    applied_ix = [s.cycle for s in steps if s.applied]
    notes: list[str] = []
    pass_ok = not outside and reversals == 0
    if outside:
        notes.append("FAIL: trajectory left the 2–15% clamp")
    if reversals:
        notes.append(f"FAIL: {reversals} direction reversal(s)")
    return StreamResult(
        name=name,
        description=description,
        steps=steps,
        oscillation_reversals=reversals,
        min_percent=min_p,
        max_percent=max_p,
        outside_clamp=outside,
        n_applied=len(applied_ix),
        first_applied_cycle=applied_ix[0] if applied_ix else None,
        settled_percent=series[-1],
        time_to_stabilize=_time_to_stabilize(series),
        pass_ok=pass_ok,
        notes=notes,
    )


def _window_from_history(history: Sequence[str], window_size: int) -> list[str]:
    if not history:
        return []
    return list(history[-window_size:])


def build_standard_streams(
    *,
    n_cycles: int = 40,
    start_percent: float = 5.0,
    cfg: CalibrationConfig = DEFAULT_CONFIG,
    seed: int = 20260819,
) -> list[StreamResult]:
    import random

    rng = random.Random(seed)
    window = cfg.window_size

    def run(
        name: str,
        description: str,
        history_builder,
        extra_check,
    ) -> StreamResult:
        history: list[str] = []
        batches: list[list[str]] = []
        for cycle in range(n_cycles):
            history.extend(history_builder(cycle, rng))
            batches.append(_window_from_history(history, window))
        result = simulate_feedback_stream(
            name,
            description,
            batches,
            start_percent=start_percent,
            cfg=cfg,
            n_cycles=n_cycles,
        )
        extra_check(result)
        result.pass_ok = result.pass_ok and not any(
            n.startswith("FAIL") for n in result.notes
        )
        return result

    def realistic_builder(cycle: int, rng: random.Random) -> list[str]:
        # Full window at 64% confirmed — inside 60–85%, matching fusion precision.
        return ["confirmed"] * 32 + ["false_positive"] * 18

    def realistic_check(result: StreamResult) -> None:
        span = result.max_percent - result.min_percent
        if result.n_applied != 0:
            result.notes.append(
                f"FAIL: realistic stream moved the threshold {result.n_applied} time(s); "
                "in-band 60–85% traffic should hold"
            )
        if span > 0:
            result.notes.append(
                f"FAIL: realistic span {span:.1f} pp (expected 0 while in band)"
            )
        if result.time_to_stabilize == 0 or (
            result.time_to_stabilize is not None and result.settled_percent == start_percent
        ):
            result.notes.append(
                f"stable at {result.settled_percent:.1f}% from cycle "
                f"{result.time_to_stabilize}"
            )
        else:
            result.notes.append(f"held {start_percent:.1f}% for all {len(result.steps)} cycles")

    def all_confirm(_cycle: int, _rng: random.Random) -> list[str]:
        return ["confirmed"] * 10

    def deg_a_check(result: StreamResult) -> None:
        series = [s.top_percent for s in result.steps]
        if any(b < a - 1e-9 for a, b in zip(series, series[1:])):
            result.notes.append("FAIL: degenerate-A (100% confirm) decreased top-%")
        if result.settled_percent != cfg.clamp_high:
            result.notes.append(
                f"FAIL: expected to rest at upper clamp {cfg.clamp_high:.1f}%, "
                f"got {result.settled_percent:.1f}%"
            )
        else:
            result.notes.append(
                f"walked to {cfg.clamp_high:.1f}% clamp and held "
                f"(first step cycle {result.first_applied_cycle})"
            )
        tail = series[-5:]
        if len(set(tail)) != 1 or tail[0] != cfg.clamp_high:
            result.notes.append("FAIL: did not hold at upper clamp (possible oscillation)")

    def all_fp(_cycle: int, _rng: random.Random) -> list[str]:
        return ["false_positive"] * 10

    def deg_b_check(result: StreamResult) -> None:
        series = [s.top_percent for s in result.steps]
        if any(b > a + 1e-9 for a, b in zip(series, series[1:])):
            result.notes.append("FAIL: degenerate-B (100% FP) increased top-%")
        if result.settled_percent != cfg.clamp_low:
            result.notes.append(
                f"FAIL: expected to rest at lower clamp {cfg.clamp_low:.1f}%, "
                f"got {result.settled_percent:.1f}%"
            )
        else:
            result.notes.append(
                f"walked to {cfg.clamp_low:.1f}% clamp and held "
                f"(first step cycle {result.first_applied_cycle})"
            )
        tail = series[-5:]
        if len(set(tail)) != 1 or tail[0] != cfg.clamp_low:
            result.notes.append("FAIL: did not hold at lower clamp (possible oscillation)")

    def noisy_builder(cycle: int, _rng: random.Random) -> list[str]:
        # Replace the whole window each cycle with a homogeneous flip.
        label = "confirmed" if cycle % 2 == 0 else "false_positive"
        return [label] * cfg.window_size

    def noisy_check(result: StreamResult) -> None:
        if result.n_applied != 0:
            result.notes.append(
                f"FAIL: noisy flip stream applied {result.n_applied} step(s); "
                "two-cycle side confirmation should absorb this"
            )
        else:
            result.notes.append("no steps — damping absorbed alternating bulk labels")

    def sparse_builder(cycle: int, rng: random.Random) -> list[str]:
        # One judgment every other cycle until well after min-sample.
        if cycle % 2 == 1:
            return []
        return ["confirmed" if rng.random() < 0.65 else "false_positive"]

    def sparse_check(result: StreamResult) -> None:
        moved_early = [
            s for s in result.steps if s.applied and s.sample_size < cfg.min_sample
        ]
        if moved_early:
            result.notes.append(
                f"FAIL: {len(moved_early)} adjustment(s) below min sample"
            )
        first_ready = next(
            (s.cycle for s in result.steps if s.sample_size >= cfg.min_sample),
            None,
        )
        if first_ready is None:
            result.notes.append(
                "never reached min sample — threshold correctly frozen "
                f"at {start_percent:.1f}%"
            )
        else:
            pre = [s for s in result.steps if s.cycle < first_ready]
            if any(s.applied for s in pre):
                result.notes.append("FAIL: moved before min sample accumulated")
            else:
                result.notes.append(
                    f"held {start_percent:.1f}% until sample reached "
                    f"{cfg.min_sample} at cycle {first_ready}"
                )

    # Noisy stream is a full-window replacement, not an append. Special-case it.
    results = [
        run(
            "realistic",
            "p(confirm)=0.64 (32/50), ~window-sized batch/cycle — should stay in band at 5%",
            realistic_builder,
            realistic_check,
        ),
        run(
            "degenerate_all_confirm",
            "100% confirmed — should loosen to 15% and stop",
            all_confirm,
            deg_a_check,
        ),
        run(
            "degenerate_all_false_positive",
            "100% false positive — should tighten to 2% and stop",
            all_fp,
            deg_b_check,
        ),
    ]

    noisy_batches = []
    for cycle in range(n_cycles):
        label = "confirmed" if cycle % 2 == 0 else "false_positive"
        noisy_batches.append([label] * window)
    noisy = simulate_feedback_stream(
        "adversarial_noisy",
        "Window flips 100% confirm ↔ 100% FP every cycle",
        noisy_batches,
        start_percent=start_percent,
        cfg=cfg,
    )
    noisy_check(noisy)
    noisy.pass_ok = noisy.pass_ok and not any(n.startswith("FAIL") for n in noisy.notes)
    results.append(noisy)

    results.append(
        run(
            "sparse",
            "One judgment every other cycle — no move until n≥20",
            sparse_builder,
            sparse_check,
        )
    )
    return results


def stream_result_to_dict(result: StreamResult) -> dict:
    return {
        "name": result.name,
        "description": result.description,
        "pass_ok": result.pass_ok,
        "oscillation_reversals": result.oscillation_reversals,
        "min_percent": result.min_percent,
        "max_percent": result.max_percent,
        "outside_clamp": result.outside_clamp,
        "n_applied": result.n_applied,
        "first_applied_cycle": result.first_applied_cycle,
        "settled_percent": result.settled_percent,
        "time_to_stabilize": result.time_to_stabilize,
        "notes": result.notes,
        "trajectory": [
            {
                "cycle": s.cycle,
                "top_percent": s.top_percent,
                "confirmed_rate": s.confirmed_rate,
                "sample_size": s.sample_size,
                "action": s.action,
                "applied": s.applied,
            }
            for s in result.steps
        ],
    }
