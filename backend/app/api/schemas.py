from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertStatus = Literal[
    "new", "reviewing", "confirmed", "false_positive", "auto_closed"
]
ConfidenceLevel = Literal["low", "medium", "high"]
ReportPeriod = Literal["24h", "7d", "30d"]
UserRole = Literal["analyst", "admin"]


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthSessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    username: str
    role: UserRole


class CurrentUserResponse(BaseModel):
    id: int
    username: str
    role: UserRole


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
    ring_id: str | None = None
    reviewed_by_username: str | None = None


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
    ring_id: str | None = None
    reviewed_by_username: str | None = None


class AlertStatusUpdate(BaseModel):
    status: AlertStatus
    analyst_notes: str | None = None


class RingMember(BaseModel):
    account_id: str
    alert_id: int | None = None
    role: str
    status: AlertStatus | None = None
    confidence: ConfidenceLevel | None = None
    risk_score: float | None = None
    pattern_type: str | None = None
    detected_at: datetime | None = None
    is_escalated: bool = False


class RingExplanation(BaseModel):
    hub_concentration: float | None = None
    external_edge_ratio: float | None = None
    member_count: int | None = None
    ring_risk_score: float | None = None
    reason: str | None = None
    detection_window: int | None = None
    core_account_ids: list[str] = Field(default_factory=list)
    peripheral_account_ids: list[str] = Field(default_factory=list)


class RingListItem(BaseModel):
    ring_id: str
    hub_account_id: str | None = None
    member_count: int
    alert_count: int
    aggregate_risk_score: float
    confidence: ConfidenceLevel
    status: AlertStatus
    first_detected_at: datetime
    last_updated_at: datetime
    open_alert_count: int
    reviewed_by_username: str | None = None


class RingListResponse(BaseModel):
    items: list[RingListItem]
    pagination: PaginationMeta


class RingDetail(BaseModel):
    ring_id: str
    hub_account_id: str | None = None
    member_count: int
    alert_count: int
    aggregate_risk_score: float
    confidence: ConfidenceLevel
    status: AlertStatus
    first_detected_at: datetime
    last_updated_at: datetime
    open_alert_count: int
    members: list[RingMember]
    explanation: RingExplanation
    reviewed_by_username: str | None = None


class RingStatusUpdate(BaseModel):
    status: Literal["reviewing", "confirmed", "false_positive"]
    analyst_notes: str | None = None


class RingSkippedAlert(BaseModel):
    alert_id: int
    account_id: str
    reason: str


class RingBulkUpdateResponse(BaseModel):
    ring_id: str
    target_status: AlertStatus
    updated_alert_ids: list[int]
    skipped: list[RingSkippedAlert]
    ring: RingDetail


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
    min_reviewed_sample: int


class CalibrationLastAdjustment(BaseModel):
    at: str
    previous_percent: float
    new_percent: float
    action: str
    confirmed_rate: float
    sample_size: int
    reason: str


class CalibrationStatus(BaseModel):
    enabled: bool
    confirmed_rate: float | None = None
    sample_size: int = 0
    confirmed: int = 0
    false_positive: int = 0
    min_sample: int
    window_size: int
    target_band_low: float
    target_band_high: float
    step_percent_points: float
    clamp_low_percent: float
    clamp_high_percent: float
    cycles_per_calibration: int
    cycles_until_next: int | None = None
    skipped_reason: str | None = None
    last_adjustment: CalibrationLastAdjustment | None = None
    alert_top_percent: float
    alert_top_percentile: float


class SystemKnobs(BaseModel):
    window_minutes: int
    secondary_window_minutes: int
    min_transactions_for_scoring: int
    gdi_min: float
    gdi_max: float
    default_alert_top_percent: float
    default_alert_top_percentile: float
    manual_alert_top_percent_min: float
    manual_alert_top_percent_max: float
    detection_cycle_interval_seconds: int
    metrics_broadcast_interval_seconds: int
    simulation_interval_seconds: float
    pool_size: int
    alert_staleness_hours: int
    min_reviewed_sample: int
    replay_step_seconds: int
    min_ring_member_count: int


class AppSettingsResponse(BaseModel):
    alert_top_percentile: float
    alert_top_percent: float
    mule_attack_probability: float
    slow_drip_attack_probability: float
    calibration_enabled: bool = False
    calibration: CalibrationStatus | None = None
    system: SystemKnobs | None = None


class AppSettingsUpdate(BaseModel):
    alert_top_percentile: float | None = None
    alert_top_percent: float | None = None
    calibration_enabled: bool | None = None
