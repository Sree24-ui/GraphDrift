from fastapi import APIRouter, HTTPException

from app.api.schemas import AppSettingsResponse, AppSettingsUpdate
from app.settings_store import (
    alert_top_percent,
    get_alert_top_percentile,
    set_alert_top_percentile,
)
from app.simulation.constants import (
    MULE_ATTACK_PROBABILITY,
    SLOW_DRIP_ATTACK_PROBABILITY,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _build_settings_response() -> AppSettingsResponse:
    percentile = get_alert_top_percentile()
    return AppSettingsResponse(
        alert_top_percentile=percentile,
        alert_top_percent=alert_top_percent(),
        mule_attack_probability=MULE_ATTACK_PROBABILITY,
        slow_drip_attack_probability=SLOW_DRIP_ATTACK_PROBABILITY,
    )


@router.get("", response_model=AppSettingsResponse)
def get_settings() -> AppSettingsResponse:
    return _build_settings_response()


@router.patch("", response_model=AppSettingsResponse)
def update_settings(body: AppSettingsUpdate) -> AppSettingsResponse:
    if body.alert_top_percentile is not None:
        try:
            set_alert_top_percentile(body.alert_top_percentile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif body.alert_top_percent is not None:
        if not 0.5 <= body.alert_top_percent <= 50.0:
            raise HTTPException(
                status_code=400,
                detail="alert_top_percent must be between 0.5 and 50.0",
            )
        try:
            set_alert_top_percentile(1.0 - body.alert_top_percent / 100.0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _build_settings_response()
