// Mirrors app/api/schemas.py — keep field names/types in sync.

export type AlertStatus =
  | 'new'
  | 'reviewing'
  | 'confirmed'
  | 'false_positive'
  | 'auto_closed'
export type ConfidenceLevel = 'low' | 'medium' | 'high'
export type ReportPeriod = '24h' | '7d' | '30d'
export type TransactionDirection = 'sent' | 'received'
export type AlertAction = 'CREATE' | 'ESCALATE'

export interface GraphNode {
  account_id: string
  fused_score: number | null
  confidence: ConfidenceLevel | null
}

export interface GraphEdge {
  sender: string
  receiver: string
  weight: number
  total_amount: number
}

export interface GraphSnapshot {
  window_start: string
  window_end: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface PaginationMeta {
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface AlertAccountRef {
  account_id: string
}

export interface Alert {
  id: number
  account: AlertAccountRef
  risk_score: number
  pattern_type: string
  detected_at: string
  status: AlertStatus
  confidence: ConfidenceLevel
  updated_at: string
  is_escalated?: boolean
}

export interface AlertListResponse {
  items: Alert[]
  pagination: PaginationMeta
}

export interface EscalationHistoryEntry {
  escalated_at: string
  previous_confidence: ConfidenceLevel | null
  new_confidence: ConfidenceLevel
  previous_risk_score: number | null
  new_risk_score: number
}

export interface AlertDetail {
  id: number
  account: AlertAccountRef
  risk_score: number
  pattern_type: string
  detected_at: string
  status: AlertStatus
  confidence: ConfidenceLevel
  analyst_notes: string | null
  updated_at: string
  reviewed_at: string | null
  feature_breakdown: Record<string, unknown> | null
  escalation_history: EscalationHistoryEntry[]
}

export interface AlertStatusUpdate {
  status: AlertStatus
  analyst_notes?: string | null
}

export interface TransactionItem {
  id: number
  direction: TransactionDirection
  counterparty_id: string
  amount: number
  timestamp: string
  is_synthetic_attack: boolean
}

export interface AccountDetail {
  account_id: string
  created_at: string
  last_active_at: string
  fused_score: number | null
  confidence: ConfidenceLevel | null
  transactions: TransactionItem[]
  connected_accounts: string[]
  pagination: PaginationMeta
}

export interface ScoreHistoryPoint {
  recorded_at: string
  score: number
}

export interface ScoreHistoryResponse {
  account_id: string
  points: ScoreHistoryPoint[]
}

export interface StatusBreakdown {
  new: number
  reviewing: number
  confirmed: number
  false_positive: number
  auto_closed: number
}

export interface ConfidenceBreakdown {
  low: number
  medium: number
  high: number
}

export interface DailyAlertCount {
  date: string
  count: number
}

export interface ReportSummary {
  period: ReportPeriod
  period_start: string
  period_end: string
  total_alerts: number
  by_status: StatusBreakdown
  by_confidence: ConfidenceBreakdown
  average_time_to_review_seconds: number | null
  daily_counts: DailyAlertCount[]
}

// WebSocket live-feed message types (app/api/websocket.py)

export interface LiveTransaction {
  id: number
  sender: string
  receiver: string
  amount: number
  timestamp: string
  is_synthetic_attack: boolean
}

export interface LiveTransactionMessage {
  type: 'transaction'
  timestamp: string
  data: LiveTransaction
}

export interface LiveAlertData {
  action: AlertAction
  id: number
  account_id: string
  risk_score: number
  confidence: ConfidenceLevel
  pattern_type: string
  status: AlertStatus
  detected_at: string
}

export interface LiveAlertMessage {
  type: 'alert'
  timestamp: string
  data: LiveAlertData
}

export interface MetricsUpdate {
  active_node_count: number
  live_edge_count: number
  peak_fused_score: number
  active_alert_count: number
}

export interface LiveMetricsMessage {
  type: 'metrics_update'
  timestamp: string
  data: MetricsUpdate
}

export type LiveFeedMessage =
  | LiveTransactionMessage
  | LiveAlertMessage
  | LiveMetricsMessage

export interface GetAlertsParams {
  status?: AlertStatus
  statuses?: AlertStatus[]
  account_id?: string
  confidence?: ConfidenceLevel
  min_confidence?: ConfidenceLevel
  detected_after?: string
  sort_by?: 'detected_at' | 'risk_score'
  sort_dir?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export interface AppSettings {
  alert_top_percentile: number
  alert_top_percent: number
  mule_attack_probability: number
  slow_drip_attack_probability: number
}

export interface AppSettingsUpdate {
  alert_top_percentile?: number
  alert_top_percent?: number
}

export interface GetAccountDetailParams {
  page?: number
  page_size?: number
}
