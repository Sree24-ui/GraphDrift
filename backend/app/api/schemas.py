from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertStatus = Literal[
    "new", "reviewing", "confirmed", "false_positive", "auto_closed"
]
ConfidenceLevel = Literal["low", "medium", "high"]
ReportPeriod = Literal["24h", "7d", "30d"]


class GraphNodeSnapshot(BaseModel):
    account_id: str
    fused_score: float | None = None
    confidence: ConfidenceLevel | None = None


class GraphEdgeSnapshot(BaseModel):
    sender: str
    receiver: str
    weight: int
    total_amount: float


class GraphSnapshotResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    nodes: list[GraphNodeSnapshot]
    edges: list[GraphEdgeSnapshot]


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class AlertAccountRef(BaseModel):
    account_id: str


class AlertListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account: AlertAccountRef
    risk_score: float
    pattern_type: str
    detected_at: datetime
    status: AlertStatus
    confidence: ConfidenceLevel
    updated_at: datetime
    is_escalated: bool = False


class AlertListResponse(BaseModel):
    items: list[AlertListItem]
    pagination: PaginationMeta


class EscalationHistoryEntry(BaseModel):
    escalated_at: datetime
    previous_confidence: ConfidenceLevel | None = None
    new_confidence: ConfidenceLevel
    previous_risk_score: float | None = None
    new_risk_score: float


class AlertDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account: AlertAccountRef
    risk_score: float
    pattern_type: str
    detected_at: datetime
    status: AlertStatus
    confidence: ConfidenceLevel
    analyst_notes: str | None = None
    updated_at: datetime
    reviewed_at: datetime | None = None
    feature_breakdown: dict | None = None
    escalation_history: list[EscalationHistoryEntry] = Field(default_factory=list)


class AlertStatusUpdate(BaseModel):
    status: AlertStatus
    analyst_notes: str | None = None


class TransactionItem(BaseModel):
    id: int
    direction: Literal["sent", "received"]
    counterparty_id: str
    amount: float
    timestamp: datetime
    is_synthetic_attack: bool


class AccountDetail(BaseModel):
    account_id: str
    created_at: datetime
    last_active_at: datetime
    fused_score: float | None = None
    confidence: ConfidenceLevel | None = None
    transactions: list[TransactionItem]
    connected_accounts: list[str]
    pagination: PaginationMeta


class ScoreHistoryPoint(BaseModel):
    recorded_at: datetime
    score: float


class ScoreHistoryResponse(BaseModel):
    account_id: str
    points: list[ScoreHistoryPoint]


class StatusBreakdown(BaseModel):
    new: int = 0
    reviewing: int = 0
    confirmed: int = 0
    false_positive: int = 0
    auto_closed: int = 0


class ConfidenceBreakdown(BaseModel):
    low: int = 0
    medium: int = 0
    high: int = 0


class DailyAlertCount(BaseModel):
    date: date
    count: int


class ReportSummaryResponse(BaseModel):
    period: ReportPeriod
    period_start: datetime
    period_end: datetime
    total_alerts: int
    by_status: StatusBreakdown
    by_confidence: ConfidenceBreakdown
    average_time_to_review_seconds: float | None = None
    daily_counts: list[DailyAlertCount] = []


class AppSettingsResponse(BaseModel):
    alert_top_percentile: float
    alert_top_percent: float
    mule_attack_probability: float
    slow_drip_attack_probability: float


class AppSettingsUpdate(BaseModel):
    alert_top_percentile: float | None = None
    alert_top_percent: float | None = None
