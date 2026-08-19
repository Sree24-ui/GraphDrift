"""In-memory runtime settings for demo / analyst tuning."""

from __future__ import annotations

from typing import Literal

from app.constants import (
    DEFAULT_ALERT_TOP_PERCENTILE,
    MANUAL_ALERT_TOP_PERCENT_MAX,
    MANUAL_ALERT_TOP_PERCENT_MIN,
    percentile_to_top_percent,
    top_percent_to_percentile,
)

__all__ = [
    "DEFAULT_ALERT_TOP_PERCENTILE",
    "alert_top_percent",
    "get_alert_top_percentile",
    "get_calibration_enabled",
    "set_alert_top_percentile",
    "set_calibration_enabled",
]

_settings: dict[str, float | bool] = {
    "alert_top_percentile": DEFAULT_ALERT_TOP_PERCENTILE,
    "calibration_enabled": False,
}


def get_alert_top_percentile() -> float:
    return float(_settings["alert_top_percentile"])


def set_alert_top_percentile(
    value: float,
    *,
    source: Literal["manual", "calibration"] = "manual",
) -> float:
    percent = percentile_to_top_percent(value)
    if not MANUAL_ALERT_TOP_PERCENT_MIN <= percent <= MANUAL_ALERT_TOP_PERCENT_MAX:
        raise ValueError(
            "alert_top_percent must be between "
            f"{MANUAL_ALERT_TOP_PERCENT_MIN} and {MANUAL_ALERT_TOP_PERCENT_MAX}"
        )
    previous = float(_settings["alert_top_percentile"])
    _settings["alert_top_percentile"] = top_percent_to_percentile(percent)
    if source == "manual" and abs(previous - float(_settings["alert_top_percentile"])) > 1e-12:
        from app.detection.calibration import on_manual_override

        on_manual_override()
    return float(_settings["alert_top_percentile"])


def alert_top_percent() -> float:
    """Human-readable top-X% (e.g. 5.0 for the default)."""
    return percentile_to_top_percent(get_alert_top_percentile())


def get_calibration_enabled() -> bool:
    return bool(_settings["calibration_enabled"])


def set_calibration_enabled(enabled: bool) -> bool:
    from app.detection.calibration import set_calibration_enabled as _set_loop

    _settings["calibration_enabled"] = bool(enabled)
    return _set_loop(bool(enabled))
