import axios from 'axios'

import type {
  AccountDetail,
  AlertDetail,
  AlertListResponse,
  AlertStatusUpdate,
  AppSettings,
  AppSettingsUpdate,
  GetAccountDetailParams,
  GetAlertsParams,
  GetRingsParams,
  GraphSnapshot,
  ReportPeriod,
  ReportSummary,
  RingBulkUpdateResponse,
  RingDetail,
  RingListResponse,
  RingStatusUpdate,
  ScoreHistoryResponse,
} from './types'

function resolveApiBaseUrl(): string {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL
  }
  // In dev, use same-origin requests so Vite proxies to the backend (no CORS issues).
  if (import.meta.env.DEV) {
    return ''
  }
  return 'http://localhost:8000'
}

function resolveWsBaseUrl(apiBaseUrl: string): string {
  if (import.meta.env.VITE_WS_BASE_URL) {
    return import.meta.env.VITE_WS_BASE_URL
  }
  if (import.meta.env.DEV && !import.meta.env.VITE_API_BASE_URL) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}`
  }
  return apiBaseUrl.replace(/^http/, 'ws')
}

export const API_BASE_URL = resolveApiBaseUrl()
export const WS_BASE_URL = resolveWsBaseUrl(API_BASE_URL)

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

export async function getAlerts(
  params: GetAlertsParams = {},
): Promise<AlertListResponse> {
  const { data } = await api.get<AlertListResponse>('/api/alerts', {
    params,
    paramsSerializer: {
      indexes: null,
    },
  })
  return data
}

export async function getAlertDetail(id: number): Promise<AlertDetail> {
  const { data } = await api.get<AlertDetail>(`/api/alerts/${id}`)
  return data
}

export async function patchAlert(
  id: number,
  body: AlertStatusUpdate,
): Promise<AlertDetail> {
  const { data } = await api.patch<AlertDetail>(`/api/alerts/${id}`, body)
  return data
}

export async function getRings(
  params: GetRingsParams = {},
): Promise<RingListResponse> {
  const { data } = await api.get<RingListResponse>('/api/rings', {
    params,
    paramsSerializer: {
      indexes: null,
    },
  })
  return data
}

export async function getRingDetail(ringId: string): Promise<RingDetail> {
  const { data } = await api.get<RingDetail>(
    `/api/rings/${encodeURIComponent(ringId)}`,
  )
  return data
}

export async function patchRing(
  ringId: string,
  body: RingStatusUpdate,
): Promise<RingBulkUpdateResponse> {
  const { data } = await api.patch<RingBulkUpdateResponse>(
    `/api/rings/${encodeURIComponent(ringId)}`,
    body,
  )
  return data
}

export async function getGraphCurrent(): Promise<GraphSnapshot> {
  const { data } = await api.get<GraphSnapshot>('/api/graph/current')
  return data
}

export async function getGraphReplay(
  start: string,
  end: string,
): Promise<GraphSnapshot> {
  const { data } = await api.get<GraphSnapshot>('/api/graph/replay', {
    params: { start, end },
  })
  return data
}

export async function getAccountDetail(
  accountId: string,
  params: GetAccountDetailParams = {},
): Promise<AccountDetail> {
  const { data } = await api.get<AccountDetail>(
    `/api/accounts/${encodeURIComponent(accountId)}`,
    { params },
  )
  return data
}

export async function getAccountScoreHistory(
  accountId: string,
): Promise<ScoreHistoryResponse> {
  const { data } = await api.get<ScoreHistoryResponse>(
    `/api/accounts/${encodeURIComponent(accountId)}/score-history`,
  )
  return data
}

export async function getReportsSummary(
  period: ReportPeriod = '24h',
): Promise<ReportSummary> {
  const { data } = await api.get<ReportSummary>('/api/reports/summary', {
    params: { period },
  })
  return data
}

export async function exportReportsCsv(
  period: ReportPeriod = '24h',
): Promise<Blob> {
  const { data } = await api.get<Blob>('/api/reports/export', {
    params: { period },
    responseType: 'blob',
  })
  return data
}

export async function getSettings(): Promise<AppSettings> {
  const { data } = await api.get<AppSettings>('/api/settings')
  return data
}

export async function patchSettings(
  body: AppSettingsUpdate,
): Promise<AppSettings> {
  const { data } = await api.patch<AppSettings>('/api/settings', body)
  return data
}

export async function getHealthStatus(): Promise<{ status: string }> {
  const { data } = await api.get<{ status: string }>('/health')
  return data
}

export default api
