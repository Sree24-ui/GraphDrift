import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.schemas import (
    ConfidenceBreakdown,
    DailyAlertCount,
    ReportPeriod,
    ReportSummaryResponse,
    StatusBreakdown,
)
from app.constants import MIN_REVIEWED_SAMPLE
from app.models import Alert

router = APIRouter(
    prefix="/api/reports",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
)

PERIOD_DELTAS: dict[ReportPeriod, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _alerts_for_period(
    db: Session, period: ReportPeriod
) -> tuple[datetime, datetime, list[Alert]]:
    period_end = datetime.now()
    period_start = period_end - PERIOD_DELTAS[period]
    alerts = db.scalars(
        select(Alert).where(
            Alert.detected_at >= period_start,
            Alert.detected_at <= period_end,
        )
    ).all()
    return period_start, period_end, alerts


def _build_daily_counts(
    alerts: list[Alert],
    period_start: datetime,
    period_end: datetime,
) -> list[DailyAlertCount]:
    counts_by_day: dict[date, int] = {}
    for alert in alerts:
        day = alert.detected_at.date()
        counts_by_day[day] = counts_by_day.get(day, 0) + 1

    daily_counts: list[DailyAlertCount] = []
    current = period_start.date()
    end_day = period_end.date()
    while current <= end_day:
        daily_counts.append(
            DailyAlertCount(date=current, count=counts_by_day.get(current, 0))
        )
        current += timedelta(days=1)
    return daily_counts


def _summarize_alerts(
    alerts: list[Alert],
    period: ReportPeriod,
    period_start: datetime,
    period_end: datetime,
) -> ReportSummaryResponse:
    by_status = StatusBreakdown()
    by_confidence = ConfidenceBreakdown()
    review_durations: list[float] = []

    for alert in alerts:
        status_value = getattr(by_status, alert.status, None)
        if status_value is not None:
            setattr(by_status, alert.status, status_value + 1)

        confidence_value = getattr(by_confidence, alert.confidence, None)
        if confidence_value is not None:
            setattr(by_confidence, alert.confidence, confidence_value + 1)

        if alert.reviewed_at is not None:
            review_durations.append(
                (alert.reviewed_at - alert.detected_at).total_seconds()
            )

    average_time_to_review: float | None = None
    if review_durations:
        average_time_to_review = sum(review_durations) / len(review_durations)

    return ReportSummaryResponse(
        period=period,
        period_start=period_start,
        period_end=period_end,
        total_alerts=len(alerts),
        by_status=by_status,
        by_confidence=by_confidence,
        average_time_to_review_seconds=average_time_to_review,
        daily_counts=_build_daily_counts(alerts, period_start, period_end),
        min_reviewed_sample=MIN_REVIEWED_SAMPLE,
    )


@router.get("/summary", response_model=ReportSummaryResponse)
def report_summary(
    period: ReportPeriod = Query("24h"),
    db: Session = Depends(get_db),
) -> ReportSummaryResponse:
    period_start, period_end, alerts = _alerts_for_period(db, period)
    return _summarize_alerts(alerts, period, period_start, period_end)


@router.get("/export")
def export_report(
    period: ReportPeriod = Query("24h"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    period_start, period_end, alerts = _alerts_for_period(db, period)
    sorted_alerts = sorted(alerts, key=lambda alert: alert.detected_at)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["account_id", "risk_score", "pattern_type", "status", "detected_at"]
    )
    for alert in sorted_alerts:
        writer.writerow(
            [
                alert.account_id,
                f"{alert.risk_score:.4f}",
                alert.pattern_type,
                alert.status,
                alert.detected_at.isoformat(),
            ]
        )

    buffer.seek(0)
    filename = f"graphdrift-alerts-{period}-{period_end.strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
