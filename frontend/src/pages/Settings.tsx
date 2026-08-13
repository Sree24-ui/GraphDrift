import { useCallback, useEffect, useState } from 'react'

import {
  API_BASE_URL,
  getHealthStatus,
  getSettings,
  patchSettings,
  WS_BASE_URL,
} from '../api/client'
import type { AlertStatus, ConfidenceLevel } from '../api/types'
import ErrorBanner from '../components/ErrorBanner'
import LoadingSpinner from '../components/LoadingSpinner'
import MaterialIcon from '../components/MaterialIcon'
import {
  loadDefaultConfidenceFilter,
  loadDefaultStatusFilter,
  saveDefaultConfidenceFilter,
  saveDefaultStatusFilter,
} from '../utils/settingsPreferences'

const STATUS_OPTIONS: { value: AlertStatus | 'all'; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'reviewing', label: 'Reviewing' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'false_positive', label: 'False Positive' },
  { value: 'auto_closed', label: 'Auto-closed' },
  { value: 'all', label: 'All' },
]

const CONFIDENCE_OPTIONS: { value: ConfidenceLevel | 'all'; label: string }[] =
  [
    { value: 'all', label: 'All' },
    { value: 'high', label: 'High' },
    { value: 'medium', label: 'Medium' },
    { value: 'low', label: 'Low' },
  ]

function sectionClass(elevated = false): string {
  return elevated
    ? 'glass-panel-elevated rounded-xl p-6'
    : 'glass-panel rounded-xl p-6'
}

function filterChipClass(active: boolean): string {
  return ['filter-chip', active ? 'filter-chip-active' : 'filter-chip-inactive'].join(
    ' ',
  )
}

type WsStatus = 'connecting' | 'connected' | 'disconnected'

export default function Settings() {
  const [topPercent, setTopPercent] = useState(5)
  const [percentile, setPercentile] = useState(0.95)
  const [simMule, setSimMule] = useState(0)
  const [simSlow, setSimSlow] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [statusDefaults, setStatusDefaults] = useState<Set<AlertStatus | 'all'>>(
    () => loadDefaultStatusFilter(),
  )
  const [confidenceDefault, setConfidenceDefault] = useState<
    ConfidenceLevel | 'all'
  >(() => loadDefaultConfidenceFilter())

  const [healthStatus, setHealthStatus] = useState<string>('checking…')
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting')

  const fetchSettings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSettings()
      setTopPercent(data.alert_top_percent)
      setPercentile(data.alert_top_percentile)
      setSimMule(data.mule_attack_probability)
      setSimSlow(data.slow_drip_attack_probability)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchSettings()
  }, [fetchSettings])

  useEffect(() => {
    let cancelled = false

    async function pingHealth() {
      try {
        const data = await getHealthStatus()
        if (!cancelled) {
          setHealthStatus(data.status)
        }
      } catch {
        if (!cancelled) {
          setHealthStatus('unreachable')
        }
      }
    }

    pingHealth()
    const interval = window.setInterval(pingHealth, 15000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    const ws = new WebSocket(`${WS_BASE_URL}/ws/live-feed`)
    setWsStatus('connecting')
    ws.onopen = () => setWsStatus('connected')
    ws.onclose = () => setWsStatus('disconnected')
    ws.onerror = () => ws.close()
    return () => ws.close()
  }, [])

  const handleSaveSensitivity = async () => {
    setSaving(true)
    setSaveMessage(null)
    setError(null)
    try {
      const updated = await patchSettings({ alert_top_percent: topPercent })
      setPercentile(updated.alert_top_percentile)
      setTopPercent(updated.alert_top_percent)
      setSaveMessage(
        `Saved — alerts will target the top ${updated.alert_top_percent}% of accounts each cycle.`,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const toggleStatusDefault = (value: AlertStatus | 'all') => {
    setStatusDefaults((prev) => {
      let next: Set<AlertStatus | 'all'>
      if (value === 'all') {
        next = new Set(['all'])
      } else {
        next = new Set(prev)
        next.delete('all')
        if (next.has(value)) {
          next.delete(value)
        } else {
          next.add(value)
        }
        if (next.size === 0) {
          next = new Set(['new', 'reviewing'])
        }
      }
      saveDefaultStatusFilter(next)
      return next
    })
  }

  const handleConfidenceDefault = (value: ConfidenceLevel | 'all') => {
    setConfidenceDefault(value)
    saveDefaultConfidenceFilter(value)
  }

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col gap-8 overflow-y-auto">
      <header>
        <h1 className="font-headline text-3xl font-semibold tracking-tight text-on-surface glacier-text-glow">
          System Configuration
        </h1>
        <p className="mt-2 text-on-surface-variant">
          Manage detection parameters and operational preferences.
        </p>
      </header>

      {error && <ErrorBanner message={error} onRetry={fetchSettings} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <section className={`${sectionClass(true)} lg:col-span-2`}>
          <h2 className="mb-6 flex items-center gap-3 text-xl font-semibold text-on-surface">
            <MaterialIcon name="tune" className="text-primary" size={22} />
            Detection Sensitivity
          </h2>

        {loading ? (
          <LoadingSpinner size="sm" label="Loading settings…" className="mt-6 py-8" />
        ) : (
          <div className="mt-4 space-y-4">
            <div>
              <div className="flex items-center justify-between text-xs text-on-surface-variant">
                <span>Top anomaly share</span>
                <span className="tabular-nums text-on-surface">{topPercent}%</span>
              </div>
              <input
                type="range"
                min={1}
                max={25}
                step={1}
                value={topPercent}
                onChange={(e) => setTopPercent(Number(e.target.value))}
                className="mt-2 w-full accent-primary"
              />
              <p className="mt-2 text-[11px] text-on-surface-variant">
                Effective percentile cutoff:{' '}
                <span className="tabular-nums text-on-surface">
                  {percentile.toFixed(3)}
                </span>{' '}
                (higher = fewer alerts)
              </p>
            </div>

            <button
              type="button"
              onClick={handleSaveSensitivity}
              disabled={saving}
              className="rounded-lg border border-primary/30 bg-primary/20 px-6 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/30 disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Apply Calibrations'}
            </button>

            {saveMessage && (
              <p className="text-xs text-primary">{saveMessage}</p>
            )}
          </div>
        )}
        </section>

        <section className={`${sectionClass()} relative overflow-hidden`}>
          <div className="pointer-events-none absolute -right-20 -top-20 h-40 w-40 rounded-full bg-primary/10 blur-3xl" />
          <h2 className="relative z-10 mb-6 flex items-center gap-3 text-xl font-semibold text-on-surface">
            <MaterialIcon name="dns" className="text-primary" size={22} />
            System Status
          </h2>
          <dl className="relative z-10 space-y-4 text-sm">
            <div className="flex items-center justify-between">
              <dt className="text-on-surface-variant">Backend health</dt>
              <dd className="flex items-center gap-2 text-on-surface">
                <span className="h-2 w-2 animate-pulse rounded-full bg-primary shadow-[0_0_8px_rgba(125,211,252,0.8)]" />
                {healthStatus}
              </dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-on-surface-variant">WebSocket feed</dt>
              <dd className="capitalize text-on-surface">{wsStatus}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-on-surface-variant">Mule attack rate</dt>
              <dd className="tabular-nums text-on-surface">
                {(simMule * 100).toFixed(2)}%
              </dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-on-surface-variant">Slow-drip rate</dt>
              <dd className="tabular-nums text-on-surface">
                {(simSlow * 100).toFixed(2)}%
              </dd>
            </div>
            <div className="pt-2">
              <a
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-primary hover:underline"
              >
                {API_BASE_URL}/docs
              </a>
            </div>
          </dl>
        </section>

        <section className={`${sectionClass()} lg:col-span-3`}>
          <h2 className="mb-6 flex items-center gap-3 text-xl font-semibold text-on-surface">
            <MaterialIcon name="dynamic_feed" className="text-primary" size={22} />
            Queue Preferences
          </h2>
          <div className="grid gap-8 md:grid-cols-2">
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                Default status filter
              </p>
              <div className="flex flex-wrap gap-1.5">
                {STATUS_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => toggleStatusDefault(opt.value)}
                    className={filterChipClass(statusDefaults.has(opt.value))}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                Default confidence filter
              </p>
              <div className="flex flex-wrap gap-1.5">
                {CONFIDENCE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => handleConfidenceDefault(opt.value)}
                    className={filterChipClass(confidenceDefault === opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
