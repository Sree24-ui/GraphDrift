import type { AlertStatus } from '../api/types'

export const TERMINAL_STATUSES: AlertStatus[] = [
  'confirmed',
  'false_positive',
  'auto_closed',
]

const ALLOWED_TRANSITIONS: Record<AlertStatus, AlertStatus[]> = {
  new: ['reviewing', 'confirmed', 'false_positive'],
  reviewing: ['confirmed', 'false_positive'],
  confirmed: [],
  false_positive: [],
  auto_closed: [],
}

export function canTransitionTo(
  current: AlertStatus,
  target: AlertStatus,
): boolean {
  return ALLOWED_TRANSITIONS[current].includes(target)
}

export interface QuickAction {
  label: string
  target: AlertStatus
  variant: 'primary' | 'danger' | 'neutral'
}

export function getQuickActions(status: AlertStatus): QuickAction[] {
  const actions: QuickAction[] = []

  if (canTransitionTo(status, 'reviewing')) {
    actions.push({
      label: 'Start Review',
      target: 'reviewing',
      variant: 'primary',
    })
  }
  if (canTransitionTo(status, 'confirmed')) {
    actions.push({
      label: 'Confirm Fraud',
      target: 'confirmed',
      variant: 'danger',
    })
  }
  if (canTransitionTo(status, 'false_positive')) {
    actions.push({
      label: 'Mark False Positive',
      target: 'false_positive',
      variant: 'neutral',
    })
  }

  return actions
}

export type DatePreset = '1h' | '24h' | '7d' | 'all'

export function datePresetToIso(preset: DatePreset): string | undefined {
  if (preset === 'all') {
    return undefined
  }

  const offsets: Record<Exclude<DatePreset, 'all'>, number> = {
    '1h': 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
    '7d': 7 * 24 * 60 * 60 * 1000,
  }

  return new Date(Date.now() - offsets[preset]).toISOString()
}
