"""Ring-level case API: group alerts that share a deterministic ring_id."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.alerts import (
    ALLOWED_TRANSITIONS,
    CONFIDENCE_ORDER,
    TERMINAL_STATUSES,
    apply_alert_status_change,
    _is_escalated,
)
from app.api.deps import get_current_user, get_db
from app.api.schemas import (
    AlertStatus,
    ConfidenceLevel,
    PaginationMeta,
    RingBulkUpdateResponse,
    RingDetail,
    RingExplanation,
    RingListItem,
    RingListResponse,
    RingMember,
    RingSkippedAlert,
    RingStatusUpdate,
)
from app.detection.ring_id import infer_member_role
from app.models import Alert, RingReviewAction, User

router = APIRouter(
    prefix="/api/rings",
    tags=["rings"],
    dependencies=[Depends(get_current_user)],
)

OPEN_STATUSES = frozenset({"new", "reviewing"})


def _hub_from_alerts(alerts: list[Alert]) -> str | None:
    for alert in sorted(alerts, key=lambda item: item.risk_score, reverse=True):
        breakdown = alert.feature_breakdown or {}
        layer2 = breakdown.get("layer2_detail") or {}
        hub = layer2.get("hub_account_id")
        if hub:
            return str(hub)
        peripheral = breakdown.get("peripheral_detail") or {}
        linked = peripheral.get("linked_hub_account_id")
        if linked:
            return str(linked)
    if not alerts:
        return None
    return max(alerts, key=lambda item: item.risk_score).account_id


def _member_accounts_from_alerts(alerts: list[Alert]) -> set[str]:
    accounts: set[str] = set()
    for alert in alerts:
        accounts.add(alert.account_id)
        breakdown = alert.feature_breakdown or {}
        layer2 = breakdown.get("layer2_detail") or {}
        for member in layer2.get("member_accounts") or []:
            accounts.add(str(member))
    return accounts


def _aggregate_status(alerts: list[Alert]) -> AlertStatus:
    statuses = {alert.status for alert in alerts}
    if "new" in statuses:
        return "new"
    if "reviewing" in statuses:
        return "reviewing"
    if statuses == {"confirmed"}:
        return "confirmed"
    if statuses == {"false_positive"}:
        return "false_positive"
    if statuses == {"auto_closed"}:
        return "auto_closed"
    return "reviewing"


def _aggregate_confidence(alerts: list[Alert]) -> ConfidenceLevel:
    if not alerts:
        return "low"
    best = max(alerts, key=lambda item: CONFIDENCE_ORDER.get(item.confidence, 0))
    return best.confidence  # type: ignore[return-value]


def _merged_explanation(alerts: list[Alert], members: list[RingMember]) -> RingExplanation:
    hub_alert = None
    for alert in sorted(alerts, key=lambda item: item.risk_score, reverse=True):
        layer2 = (alert.feature_breakdown or {}).get("layer2_detail")
        if layer2:
            hub_alert = alert
            break
    layer2 = (hub_alert.feature_breakdown or {}).get("layer2_detail") if hub_alert else {}
    layer2 = layer2 or {}
    detection_window = None
    for alert in alerts:
        breakdown = alert.feature_breakdown or {}
        if breakdown.get("detection_window") is not None:
            detection_window = int(breakdown["detection_window"])
            break
    core_ids = [m.account_id for m in members if m.role in {"hub", "core"}]
    peripheral_ids = [
        m.account_id for m in members if m.role in {"peripheral", "fan-in", "fan-out"}
    ]
    return RingExplanation(
        hub_concentration=layer2.get("hub_concentration"),
        external_edge_ratio=layer2.get("external_edge_ratio"),
        member_count=layer2.get("member_count") or len(members),
        ring_risk_score=layer2.get("ring_risk_score"),
        reason=layer2.get("reason"),
        detection_window=detection_window,
        core_account_ids=core_ids,
        peripheral_account_ids=peripheral_ids,
    )


def _build_members(alerts: list[Alert], hub_account_id: str | None) -> list[RingMember]:
    by_account = {alert.account_id: alert for alert in alerts}
    accounts = _member_accounts_from_alerts(alerts)
    members: list[RingMember] = []
    for account_id in sorted(accounts):
        alert = by_account.get(account_id)
        role = infer_member_role(
            account_id,
            hub_account_id=hub_account_id,
            pattern_type=alert.pattern_type if alert else None,
            feature_breakdown=alert.feature_breakdown if alert else None,
        )
        members.append(
            RingMember(
                account_id=account_id,
                alert_id=alert.id if alert else None,
                role=role,
                status=alert.status if alert else None,  # type: ignore[arg-type]
                confidence=alert.confidence if alert else None,  # type: ignore[arg-type]
                risk_score=alert.risk_score if alert else None,
                pattern_type=alert.pattern_type if alert else None,
                detected_at=alert.detected_at if alert else None,
                is_escalated=_is_escalated(alert) if alert else False,
            )
        )
    role_rank = {"hub": 0, "core": 1, "fan-in": 2, "fan-out": 3, "peripheral": 4}
    members.sort(key=lambda item: (role_rank.get(item.role, 9), item.account_id))
    return members


def _latest_ring_reviewer(
    ring_id: str, alerts: list[Alert], db: Session | None = None
) -> str | None:
    if db is not None:
        action = db.scalar(
            select(RingReviewAction)
            .options(joinedload(RingReviewAction.reviewed_by))
            .where(RingReviewAction.ring_id == ring_id)
            .order_by(RingReviewAction.created_at.desc(), RingReviewAction.id.desc())
        )
        if action is not None:
            return action.reviewed_by.username
    reviewed = [alert for alert in alerts if alert.reviewed_by is not None]
    if not reviewed:
        return None
    latest = max(reviewed, key=lambda alert: alert.reviewed_at or alert.updated_at)
    return latest.reviewed_by.username if latest.reviewed_by else None


def _build_ring_detail(
    ring_id: str, alerts: list[Alert], db: Session | None = None
) -> RingDetail:
    hub = _hub_from_alerts(alerts)
    members = _build_members(alerts, hub)
    first_detected = min(alert.detected_at for alert in alerts)
    last_updated = max(alert.updated_at for alert in alerts)
    return RingDetail(
        ring_id=ring_id,
        hub_account_id=hub,
        member_count=len(members),
        alert_count=len(alerts),
        aggregate_risk_score=max(alert.risk_score for alert in alerts),
        confidence=_aggregate_confidence(alerts),
        status=_aggregate_status(alerts),
        first_detected_at=first_detected,
        last_updated_at=last_updated,
        open_alert_count=sum(1 for alert in alerts if alert.status in OPEN_STATUSES),
        members=members,
        explanation=_merged_explanation(alerts, members),
        reviewed_by_username=_latest_ring_reviewer(ring_id, alerts, db),
    )


def _to_list_item(detail: RingDetail) -> RingListItem:
    return RingListItem(
        ring_id=detail.ring_id,
        hub_account_id=detail.hub_account_id,
        member_count=detail.member_count,
        alert_count=detail.alert_count,
        aggregate_risk_score=detail.aggregate_risk_score,
        confidence=detail.confidence,
        status=detail.status,
        first_detected_at=detail.first_detected_at,
        last_updated_at=detail.last_updated_at,
        open_alert_count=detail.open_alert_count,
        reviewed_by_username=detail.reviewed_by_username,
    )


def _load_ring_groups(db: Session) -> dict[str, list[Alert]]:
    alerts = list(
        db.scalars(
            select(Alert)
            .options(joinedload(Alert.reviewed_by))
            .where(Alert.ring_id.isnot(None))
        ).unique().all()
    )
    groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in alerts:
        if alert.ring_id:
            groups[alert.ring_id].append(alert)
    return groups


@router.get("", response_model=RingListResponse)
def list_rings(
    status: AlertStatus | None = Query(None),
    statuses: list[AlertStatus] | None = Query(None),
    confidence: ConfidenceLevel | None = Query(None),
    detected_after: datetime | None = Query(None),
    sort_by: Literal["detected_at", "risk_score"] = Query("detected_at"),
    sort_dir: Literal["asc", "desc"] = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RingListResponse:
    groups = _load_ring_groups(db)
    details = [
        _build_ring_detail(ring_id, alerts, db) for ring_id, alerts in groups.items()
    ]

    wanted_statuses: set[str] | None = None
    if statuses:
        wanted_statuses = set(statuses)
    elif status is not None:
        wanted_statuses = {status}

    filtered: list[RingDetail] = []
    for detail in details:
        if wanted_statuses and detail.status not in wanted_statuses:
            continue
        if confidence is not None and detail.confidence != confidence:
            continue
        if detected_after is not None and detail.first_detected_at < detected_after:
            continue
        filtered.append(detail)

    reverse = sort_dir == "desc"
    if sort_by == "risk_score":
        filtered.sort(key=lambda item: item.aggregate_risk_score, reverse=reverse)
    else:
        filtered.sort(key=lambda item: item.first_detected_at, reverse=reverse)

    total = len(filtered)
    total_pages = math.ceil(total / page_size) if total else 0
    offset = (page - 1) * page_size
    page_items = filtered[offset : offset + page_size]

    return RingListResponse(
        items=[_to_list_item(item) for item in page_items],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/{ring_id}", response_model=RingDetail)
def get_ring(ring_id: str, db: Session = Depends(get_db)) -> RingDetail:
    alerts = list(
        db.scalars(
            select(Alert)
            .options(joinedload(Alert.reviewed_by))
            .where(Alert.ring_id == ring_id)
        ).unique().all()
    )
    if not alerts:
        raise HTTPException(status_code=404, detail="Ring not found")
    return _build_ring_detail(ring_id, alerts, db)


@router.patch("/{ring_id}", response_model=RingBulkUpdateResponse)
def update_ring(
    ring_id: str,
    body: RingStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RingBulkUpdateResponse:
    alerts = list(db.scalars(select(Alert).where(Alert.ring_id == ring_id)).all())
    if not alerts:
        raise HTTPException(status_code=404, detail="Ring not found")

    now = datetime.now()
    updated_ids: list[int] = []
    skipped: list[RingSkippedAlert] = []

    for alert in alerts:
        if alert.status == body.status:
            skipped.append(
                RingSkippedAlert(
                    alert_id=alert.id,
                    account_id=alert.account_id,
                    reason="already at target status",
                )
            )
            if body.analyst_notes is not None:
                alert.analyst_notes = body.analyst_notes
                alert.updated_at = now
            continue
        if alert.status in TERMINAL_STATUSES:
            skipped.append(
                RingSkippedAlert(
                    alert_id=alert.id,
                    account_id=alert.account_id,
                    reason=f"terminal status '{alert.status}'",
                )
            )
            continue
        allowed = ALLOWED_TRANSITIONS.get(alert.status, frozenset())
        if body.status not in allowed:
            skipped.append(
                RingSkippedAlert(
                    alert_id=alert.id,
                    account_id=alert.account_id,
                    reason=f"cannot transition {alert.status} → {body.status}",
                )
            )
            continue
        apply_alert_status_change(
            alert,
            body.status,
            body.analyst_notes,
            now=now,
            reviewed_by_user_id=current_user.id if isinstance(current_user, User) else None,
        )
        updated_ids.append(alert.id)

    if updated_ids and isinstance(current_user, User):
        db.add(
            RingReviewAction(
                ring_id=ring_id,
                target_status=body.status,
                analyst_notes=body.analyst_notes,
                reviewed_by_user_id=current_user.id,
            )
        )

    db.commit()
    refreshed = list(
        db.scalars(
            select(Alert)
            .options(joinedload(Alert.reviewed_by))
            .where(Alert.ring_id == ring_id)
        ).unique().all()
    )
    return RingBulkUpdateResponse(
        ring_id=ring_id,
        target_status=body.status,
        updated_alert_ids=updated_ids,
        skipped=skipped,
        ring=_build_ring_detail(ring_id, refreshed, db),
    )
