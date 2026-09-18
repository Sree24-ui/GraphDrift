import axios from 'axios'

import type {
  AccountDetail,
  AlertDetail,
  AlertListResponse,
  AlertStatusUpdate,
  AppSettings,
  AppSettingsUpdate,
  AuthSession,
  CurrentUser,
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
  const configuredUrl = import.meta.env.VITE_API_BASE_URL?.trim()
  if (configuredUrl) {
    return configuredUrl
  }
  // A configured dev proxy keeps requests same-origin and avoids browser CORS.
  if (import.meta.env.DEV && import.meta.env.VITE_DEV_BACKEND_URL?.trim()) {
    return ''
  }
  // A production deployment may serve its API from the same origin.
  return ''
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

const TOKEN_KEY = 'graphdrift.sessionToken'
// Fired whenever the server rejects the session (REST 401 or WebSocket 1008).
export const UNAUTHORIZED_EVENT = 'graphdrift:unauthorized'

export function getStoredToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function storeToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearStoredToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

// The WebSocket handshake cannot carry an Authorization header, so the session
// token travels as a query parameter and is validated like the REST bearer.
// Every live-feed connection must be built here so none can omit the token.
export function liveFeedUrl(): string {
  const token = getStoredToken()
  const base = `${WS_BASE_URL}/ws/live-feed`
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

api.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/** Human-readable text for a failed request, for toasts and inline errors. */
export function errorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') return detail
    if (!err.response) return 'Network error: the backend is unreachable'
    if (err.response.status === 403) {
      return 'Not allowed: your role cannot perform this action'
    }
    return `Request failed (HTTP ${err.response.status})`
  }
  return err instanceof Error ? err.message : fallback
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    return Promise.reject(error)
  },
)

export async function loginUser(
  username: string,
  password: string,
): Promise<AuthSession> {
  const { data } = await api.post<AuthSession>('/api/auth/login', {
    username,
    password,
  })
  return data
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const { data } = await api.get<CurrentUser>('/api/auth/me')
  return data
}

export async function logoutUser(): Promise<void> {
  await api.post('/api/auth/logout')
}

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
