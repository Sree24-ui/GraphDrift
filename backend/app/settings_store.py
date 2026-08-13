"""In-memory runtime settings for demo / analyst tuning."""

from __future__ import annotations

DEFAULT_ALERT_TOP_PERCENTILE = 0.95

_settings: dict[str, float] = {
    "alert_top_percentile": DEFAULT_ALERT_TOP_PERCENTILE,
}


def get_alert_top_percentile() -> float:
    return _settings["alert_top_percentile"]


def set_alert_top_percentile(value: float) -> float:
    if not 0.5 <= value <= 0.995:
        raise ValueError("alert_top_percentile must be between 0.5 and 0.995")
    _settings["alert_top_percentile"] = value
    return value


def alert_top_percent() -> float:
    """Human-readable top-X% (e.g. 5.0 for the default 0.95 percentile)."""
    return round((1.0 - get_alert_top_percentile()) * 100.0, 2)
