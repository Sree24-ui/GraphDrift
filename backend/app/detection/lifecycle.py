"""
Alert lifecycle helpers — staleness and automatic queue hygiene.

`auto_closed` means the alert aged out of the active queue without analyst
action. It does NOT mean the activity was benign; analysts can still reopen
investigations from historical views if needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert

# Untouched `new` alerts older than this leave the active queue (auto_closed).
ALERT_STALENESS_HOURS = 2


def auto_expire_stale_alerts(
    db: Session,
    as_of: datetime | None = None,
) -> int:
    """
    Transition stale `new` alerts to `auto_closed`.

    An alert is considered stale when:
    - status is still `new` (analyst never started review),
    - `reviewed_at` is unset,
    - `detected_at` is older than ALERT_STALENESS_HOURS.

    Returns the number of alerts auto-closed this cycle.
    """
    as_of = as_of or datetime.now()
    cutoff = as_of - timedelta(hours=ALERT_STALENESS_HOURS)

    stale_alerts = db.scalars(
        select(Alert).where(
            Alert.status == "new",
            Alert.reviewed_at.is_(None),
            Alert.detected_at < cutoff,
        )
    ).all()

    if not stale_alerts:
        return 0

    for alert in stale_alerts:
        alert.status = "auto_closed"
        alert.updated_at = as_of
        breakdown = dict(alert.feature_breakdown or {})
        breakdown["auto_closed_at"] = as_of.isoformat()
        breakdown["auto_close_reason"] = (
            f"No analyst action within {ALERT_STALENESS_HOURS}h of detection"
        )
        alert.feature_breakdown = breakdown

    db.commit()
    return len(stale_alerts)
