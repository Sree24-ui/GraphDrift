"""Single source of detection / demo knobs.

Values live in ``graphdrift/shared/detection_knobs.json`` so the frontend
can import the same file. Do not copy these numbers into pages or APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

_KNOBS_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "detection_knobs.json"
)
KNOBS: dict = json.loads(_KNOBS_PATH.read_text())

WINDOW_MINUTES: int = int(KNOBS["window_minutes"])
SECONDARY_WINDOW_MINUTES: int = int(KNOBS["secondary_window_minutes"])
MIN_TRANSACTIONS_FOR_SCORING: int = int(KNOBS["min_transactions_for_scoring"])
AMOUNT_ENTROPY_BINS: int = int(KNOBS["amount_entropy_bins"])

GDI_MIN: float = float(KNOBS["gdi_min"])
GDI_MAX: float = float(KNOBS["gdi_max"])
RAW_DISTANCE_CLIP: float = float(KNOBS["raw_distance_clip"])
MIN_ACCOUNTS_FOR_FULL_COV: int = int(KNOBS["min_accounts_for_full_cov"])
VARIANCE_FLOOR: float = float(KNOBS["variance_floor"])
SHRINKAGE_ALPHA: float = float(KNOBS["shrinkage_alpha"])

FUSION_GDI_WEIGHT: float = float(KNOBS["fusion_gdi_weight"])
FUSION_RING_WEIGHT: float = float(KNOBS["fusion_ring_weight"])
SCORE_ESCALATION_RELATIVE_THRESHOLD: float = float(
    KNOBS["score_escalation_relative_threshold"]
)

DEFAULT_ALERT_TOP_PERCENT: float = float(KNOBS["default_alert_top_percent"])
MANUAL_ALERT_TOP_PERCENT_MIN: float = float(KNOBS["manual_alert_top_percent_min"])
MANUAL_ALERT_TOP_PERCENT_MAX: float = float(KNOBS["manual_alert_top_percent_max"])
DEFAULT_ALERT_TOP_PERCENTILE: float = 1.0 - DEFAULT_ALERT_TOP_PERCENT / 100.0

DETECTION_CYCLE_INTERVAL_SECONDS: int = int(
    KNOBS["detection_cycle_interval_seconds"]
)
METRICS_BROADCAST_INTERVAL_SECONDS: int = int(
    KNOBS["metrics_broadcast_interval_seconds"]
)

SIMULATION_INTERVAL_SECONDS: float = float(KNOBS["simulation_interval_seconds"])
MULE_ATTACK_PROBABILITY: float = float(KNOBS["mule_attack_probability"])
SLOW_DRIP_ATTACK_PROBABILITY: float = float(KNOBS["slow_drip_attack_probability"])
POOL_SIZE: int = int(KNOBS["pool_size"])

ALERT_STALENESS_HOURS: int = int(KNOBS["alert_staleness_hours"])

MIN_RING_MEMBER_COUNT: int = int(KNOBS["min_ring_member_count"])
RISK_THRESHOLD: float = float(KNOBS["risk_threshold"])
LOUVAIN_RESOLUTION: float = float(KNOBS["louvain_resolution"])
COMMUNITY_SIMILARITY_THRESHOLD: float = float(
    KNOBS["community_similarity_threshold"]
)
MAX_PARTITION_SNAPSHOTS: int = int(KNOBS["max_partition_snapshots"])
RING_HUB_WEIGHT: float = float(KNOBS["ring_hub_weight"])
RING_EXTERNAL_WEIGHT: float = float(KNOBS["ring_external_weight"])
RING_RECENT_WEIGHT: float = float(KNOBS["ring_recent_weight"])

CONFIDENCE_STRONG_PERCENTILE: float = float(KNOBS["confidence_strong_percentile"])
PERIPHERAL_HUB_CONNECTION_BASE: float = float(
    KNOBS["peripheral_hub_connection_base"]
)
PERIPHERAL_PATTERN_CONSISTENCY_BONUS: float = float(
    KNOBS["peripheral_pattern_consistency_bonus"]
)
PERIPHERAL_MIN_QUALIFYING_SCORE: float = float(
    KNOBS["peripheral_min_qualifying_score"]
)

# Off by default: co-hub scoring closes the diluted_hub evasion but measurably
# lowers baseline accuracy. See "Co-hub scoring (optional, off by default)" in
# evaluation/RESULTS.md for the full measured trade-off.
ENABLE_COHUB_SCORING: bool = bool(KNOBS["enable_cohub_scoring"])
CO_HUB_MAX_SET_SIZE: int = int(KNOBS["co_hub_max_set_size"])
CO_HUB_SIMILARITY_RATIO: float = float(KNOBS["co_hub_similarity_ratio"])
CO_HUB_SEPARATION_RATIO: float = float(KNOBS["co_hub_separation_ratio"])

MIN_REVIEWED_SAMPLE: int = int(KNOBS["min_reviewed_sample"])
CALIBRATION_WINDOW: int = int(KNOBS["calibration_window"])
TARGET_BAND_LOW: float = float(KNOBS["target_band_low"])
TARGET_BAND_HIGH: float = float(KNOBS["target_band_high"])
CALIBRATION_STEP_PERCENT_POINTS: float = float(
    KNOBS["calibration_step_percent_points"]
)
CALIBRATION_CLAMP_LOW_PERCENT: float = float(KNOBS["calibration_clamp_low_percent"])
CALIBRATION_CLAMP_HIGH_PERCENT: float = float(
    KNOBS["calibration_clamp_high_percent"]
)
CALIBRATION_CONSECUTIVE_SIDE_REQUIRED: int = int(
    KNOBS["calibration_consecutive_side_required"]
)
CYCLES_PER_CALIBRATION: int = int(KNOBS["cycles_per_calibration"])
REPLAY_STEP_SECONDS: int = int(KNOBS["replay_step_seconds"])


def top_percent_to_percentile(percent: float) -> float:
    return 1.0 - float(percent) / 100.0


def percentile_to_top_percent(percentile: float) -> float:
    return round((1.0 - float(percentile)) * 100.0, 2)


def system_knobs_payload() -> dict:
    """Frozen knobs for GET /api/settings (frontend must not re-hardcode)."""
    return {
        "window_minutes": WINDOW_MINUTES,
        "secondary_window_minutes": SECONDARY_WINDOW_MINUTES,
        "min_transactions_for_scoring": MIN_TRANSACTIONS_FOR_SCORING,
        "gdi_min": GDI_MIN,
        "gdi_max": GDI_MAX,
        "default_alert_top_percent": DEFAULT_ALERT_TOP_PERCENT,
        "default_alert_top_percentile": DEFAULT_ALERT_TOP_PERCENTILE,
        "manual_alert_top_percent_min": MANUAL_ALERT_TOP_PERCENT_MIN,
        "manual_alert_top_percent_max": MANUAL_ALERT_TOP_PERCENT_MAX,
        "detection_cycle_interval_seconds": DETECTION_CYCLE_INTERVAL_SECONDS,
        "metrics_broadcast_interval_seconds": METRICS_BROADCAST_INTERVAL_SECONDS,
        "simulation_interval_seconds": SIMULATION_INTERVAL_SECONDS,
        "pool_size": POOL_SIZE,
        "alert_staleness_hours": ALERT_STALENESS_HOURS,
        "min_reviewed_sample": MIN_REVIEWED_SAMPLE,
        "replay_step_seconds": REPLAY_STEP_SECONDS,
        "min_ring_member_count": MIN_RING_MEMBER_COUNT,
    }
