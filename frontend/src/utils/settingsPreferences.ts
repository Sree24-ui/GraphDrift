import type { AlertStatus, ConfidenceLevel } from '../api/types'

const STATUS_FILTER_KEY = 'graphdrift.alertQueue.defaultStatusFilter'
const CONFIDENCE_FILTER_KEY = 'graphdrift.alertQueue.defaultConfidenceFilter'

export const DEFAULT_STATUS_FILTER: AlertStatus[] = ['new', 'reviewing']

export function loadDefaultStatusFilter(): Set<AlertStatus | 'all'> {
  try {
    const raw = localStorage.getItem(STATUS_FILTER_KEY)
    if (!raw) {
      return new Set(DEFAULT_STATUS_FILTER)
    }
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed) || parsed.length === 0) {
      return new Set(DEFAULT_STATUS_FILTER)
    }
    return new Set(parsed as Array<AlertStatus | 'all'>)
  } catch {
    return new Set(DEFAULT_STATUS_FILTER)
  }
}

export function saveDefaultStatusFilter(
  filter: Set<AlertStatus | 'all'>,
): void {
  localStorage.setItem(
    STATUS_FILTER_KEY,
    JSON.stringify(Array.from(filter)),
  )
}

export function loadDefaultConfidenceFilter(): ConfidenceLevel | 'all' {
  try {
    const raw = localStorage.getItem(CONFIDENCE_FILTER_KEY)
    if (!raw) {
      return 'all'
    }
    if (raw === 'all' || raw === 'high' || raw === 'medium' || raw === 'low') {
      return raw
    }
    return 'all'
  } catch {
    return 'all'
  }
}

export function saveDefaultConfidenceFilter(
  filter: ConfidenceLevel | 'all',
): void {
  localStorage.setItem(CONFIDENCE_FILTER_KEY, filter)
}

export function resetDefaultFilters(): void {
  saveDefaultStatusFilter(new Set(DEFAULT_STATUS_FILTER))
  saveDefaultConfidenceFilter('all')
}
