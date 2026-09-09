import math
from datetime import datetime

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user, get_db
from app.api.schemas import (
    AlertAccountRef,
    AlertDetail,
    AlertListItem,
    AlertListResponse,
    AlertStatus,
    AlertStatusUpdate,
    ConfidenceLevel,
    EscalationHistoryEntry,
    PaginationMeta,
)
from app.models import Alert, User

router = APIRouter(
    prefix="/api/alerts",
    tags=["alerts"],
    dependencies=[Depends(get_current_user)],
)

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

# Valid status transitions:
# - new → reviewing | confirmed | false_positive
# - reviewing → confirmed | false_positive
# - confirmed, false_positive, and auto_closed are terminal (status cannot change)
TERMINAL_STATUSES = frozenset({"confirmed", "false_positive", "auto_closed"})
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"reviewing", "confirmed", "false_positive"}),
    "reviewing": frozenset({"confirmed", "false_positive"}),
    "confirmed": frozenset(),
    "false_positive": frozenset(),
    "auto_closed": frozenset(),
}


def _is_escalated(alert: Alert) -> bool:
    if not alert.feature_breakdown:
        return False
    history = alert.feature_breakdown.get("escalation_history", [])
    return bool(history)


def _alert_to_list_item(alert: Alert) -> AlertListItem:
    return AlertListItem(
        id=alert.id,
        account=AlertAccountRef(account_id=alert.account_id),
        risk_score=alert.risk_score,
        pattern_type=alert.pattern_type,
        detected_at=alert.detected_at,
        status=alert.status,  # type: ignore[arg-type]
        confidence=alert.confidence,  # type: ignore[arg-type]
        updated_at=alert.updated_at,
        is_escalated=_is_escalated(alert),
        ring_id=alert.ring_id,
        reviewed_by_username=alert.reviewed_by.username if alert.reviewed_by else None,
    )


def _extract_escalation_history(alert: Alert) -> list[EscalationHistoryEntry]:
    if not alert.feature_breakdown:
        return []

    raw_history = alert.feature_breakdown.get("escalation_history", [])
    entries: list[EscalationHistoryEntry] = []
    for item in raw_history:
        escalated_at = item.get("escalated_at")
        if isinstance(escalated_at, str):
            escalated_at = datetime.fromisoformat(escalated_at)
        entries.append(
            EscalationHistoryEntry(
                escalated_at=escalated_at,
                previous_confidence=item.get("previous_confidence"),
                new_confidence=item["new_confidence"],
                previous_risk_score=item.get("previous_risk_score"),
                new_risk_score=item["new_risk_score"],
            )
        )
    return entries


def _alert_to_detail(alert: Alert) -> AlertDetail:
    return AlertDetail(
        id=alert.id,
        account=AlertAccountRef(account_id=alert.account_id),
        risk_score=alert.risk_score,
        pattern_type=alert.pattern_type,
        detected_at=alert.detected_at,
        status=alert.status,  # type: ignore[arg-type]
        confidence=alert.confidence,  # type: ignore[arg-type]
        analyst_notes=alert.analyst_notes,
        updated_at=alert.updated_at,
        reviewed_at=alert.reviewed_at,
        feature_breakdown=alert.feature_breakdown,
        escalation_history=_extract_escalation_history(alert),
        ring_id=alert.ring_id,
        reviewed_by_username=alert.reviewed_by.username if alert.reviewed_by else None,
    )


def apply_alert_status_change(
    alert: Alert,
    new_status: AlertStatus,
    analyst_notes: str | None,
    *,
    now: datetime | None = None,
    reviewed_by_user_id: int | None = None,
) -> None:
    """Mutate ``alert`` in place. Caller is responsible for commit."""
    as_of = now or datetime.now()
    if new_status != alert.status:
        if alert.status in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot change status from terminal state '{alert.status}'. "
                    "Only analyst_notes can be updated."
                ),
            )

        allowed = ALLOWED_TRANSITIONS.get(alert.status, frozenset())
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid status transition from '{alert.status}' to '{new_status}'. "
                    f"Allowed targets: {sorted(allowed) or 'none'}."
                ),
            )

        if alert.status in {"new", "reviewing"} and alert.reviewed_at is None:
            alert.reviewed_at = as_of

        alert.status = new_status
        if reviewed_by_user_id is not None:
            alert.reviewed_by_user_id = reviewed_by_user_id

    if analyst_notes is not None:
        alert.analyst_notes = analyst_notes

    alert.updated_at = as_of


@router.get("", response_model=AlertListResponse)
def list_alerts(
    status: AlertStatus | None = Query(None),
    statuses: list[AlertStatus] | None = Query(None),
    confidence: ConfidenceLevel | None = Query(None),
    min_confidence: ConfidenceLevel | None = Query(None),
    detected_after: datetime | None = Query(None),
    account_id: str | None = Query(None),
    sort_by: Literal["detected_at", "risk_score"] = Query("detected_at"),
    sort_dir: Literal["asc", "desc"] = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AlertListResponse:
    stmt = select(Alert).options(joinedload(Alert.account), joinedload(Alert.reviewed_by))

    if statuses:
        stmt = stmt.where(Alert.status.in_(statuses))
    elif status is not None:
        stmt = stmt.where(Alert.status == status)

    if confidence is not None:
        stmt = stmt.where(Alert.confidence == confidence)
    elif min_confidence is not None:
        allowed = [
            level
            for level, rank in CONFIDENCE_ORDER.items()
            if rank >= CONFIDENCE_ORDER[min_confidence]
        ]
        stmt = stmt.where(Alert.confidence.in_(allowed))

    if detected_after is not None:
        stmt = stmt.where(Alert.detected_at >= detected_after)

    if account_id is not None:
        stmt = stmt.where(Alert.account_id == account_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    sort_column = Alert.detected_at if sort_by == "detected_at" else Alert.risk_score
    order = sort_column.desc() if sort_dir == "desc" else sort_column.asc()

    offset = (page - 1) * page_size
    alerts = (
        db.scalars(stmt.order_by(order).offset(offset).limit(page_size))
        .unique()
        .all()
    )

    total_pages = math.ceil(total / page_size) if total else 0

    return AlertListResponse(
        items=[_alert_to_list_item(alert) for alert in alerts],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/{alert_id}", response_model=AlertDetail)
def get_alert(alert_id: int, db: Session = Depends(get_db)) -> AlertDetail:
    alert = db.scalar(
        select(Alert).options(joinedload(Alert.reviewed_by)).where(Alert.id == alert_id)
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_to_detail(alert)


@router.patch("/{alert_id}", response_model=AlertDetail)
def update_alert(
    alert_id: int,
    body: AlertStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AlertDetail:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if body.status != alert.status or body.analyst_notes is not None:
        apply_alert_status_change(
            alert,
            body.status,
            body.analyst_notes,
            now=datetime.now(),
            reviewed_by_user_id=current_user.id,
        )

    db.commit()
    db.refresh(alert)
    refreshed = db.scalar(
        select(Alert).options(joinedload(Alert.reviewed_by)).where(Alert.id == alert.id)
    )
    assert refreshed is not None
    return _alert_to_detail(refreshed)
