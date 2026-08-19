from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    AppSettingsResponse,
    AppSettingsUpdate,
    CalibrationStatus,
    SystemKnobs,
)
from app.constants import (
    MANUAL_ALERT_TOP_PERCENT_MAX,
    MANUAL_ALERT_TOP_PERCENT_MIN,
    system_knobs_payload,
    top_percent_to_percentile,
)
from app.detection.calibration import (
    calibration_status_payload,
    run_calibration_from_db,
)
from app.settings_store import (
    alert_top_percent,
    get_alert_top_percentile,
    get_calibration_enabled,
    set_alert_top_percentile,
    set_calibration_enabled,
)
from app.simulation.constants import (
    MULE_ATTACK_PROBABILITY,
    SLOW_DRIP_ATTACK_PROBABILITY,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _build_settings_response() -> AppSettingsResponse:
    percentile = get_alert_top_percentile()
    payload = calibration_status_payload()
    return AppSettingsResponse(
        alert_top_percentile=percentile,
        alert_top_percent=alert_top_percent(),
        mule_attack_probability=MULE_ATTACK_PROBABILITY,
        slow_drip_attack_probability=SLOW_DRIP_ATTACK_PROBABILITY,
        calibration_enabled=get_calibration_enabled(),
        calibration=CalibrationStatus.model_validate(payload),
        system=SystemKnobs.model_validate(system_knobs_payload()),
    )


@router.get("", response_model=AppSettingsResponse)
def get_settings() -> AppSettingsResponse:
    return _build_settings_response()


@router.patch("", response_model=AppSettingsResponse)
def update_settings(body: AppSettingsUpdate) -> AppSettingsResponse:
    if body.calibration_enabled is not None:
        set_calibration_enabled(body.calibration_enabled)

    if body.alert_top_percentile is not None:
        try:
            set_alert_top_percentile(body.alert_top_percentile, source="manual")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif body.alert_top_percent is not None:
        if not MANUAL_ALERT_TOP_PERCENT_MIN <= body.alert_top_percent <= MANUAL_ALERT_TOP_PERCENT_MAX:
            raise HTTPException(
                status_code=400,
                detail=(
                    "alert_top_percent must be between "
                    f"{MANUAL_ALERT_TOP_PERCENT_MIN} and {MANUAL_ALERT_TOP_PERCENT_MAX}"
                ),
            )
        try:
            set_alert_top_percentile(
                top_percent_to_percentile(body.alert_top_percent), source="manual"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_settings_response()


@router.post("/calibration/tick", response_model=AppSettingsResponse)
def force_calibration_tick(db: Session = Depends(get_db)) -> AppSettingsResponse:
    """Run one calibration step now (still no-ops if auto is off or sample is small)."""
    if not get_calibration_enabled():
        raise HTTPException(
            status_code=400,
            detail="auto-calibration is off; enable it before forcing a tick",
        )
    run_calibration_from_db(db, apply=True)
    return _build_settings_response()
