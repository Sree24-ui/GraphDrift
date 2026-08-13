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

function sectionClass(): string {
  return 'rounded-lg border border-charcoal-lighter bg-charcoal-light p-4'
}

function filterChipClass(active: boolean): string {
  return [
    'rounded border px-2.5 py-1 text-xs font-medium transition-colors',
    active
      ? 'border-teal-muted/60 bg-teal-muted/15 text-teal-accent'
      : 'border-charcoal-lighter text-gray-500 hover:border-gray-600 hover:text-gray-300',
  ].join(' ')
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
    <div className="flex h-full flex-col gap-6 overflow-y-auto">
      <div>
        <h1 className="text-xl font-semibold text-gray-100">Settings</h1>
        <p className="mt-1 text-xs text-gray-500">
          Detection tuning and Alert Queue defaults.
        </p>
      </div>

      {error && (
        <ErrorBanner message={error} onRetry={fetchSettings} />
      )}

      <section className={sectionClass()}>
        <h2 className="text-sm font-medium text-gray-200">
          Detection sensitivity
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          Alerts are raised for the top {topPercent}% most anomalous accounts
          each detection cycle.
        </p>

        {loading ? (
          <LoadingSpinner size="sm" label="Loading settings…" className="mt-6 py-8" />
        ) : (
          <div className="mt-4 space-y-4">
            <div>
              <div className="flex items-center justify-between text-xs text-gray-400">
                <span>Top anomaly share</span>
                <span className="tabular-nums text-gray-200">{topPercent}%</span>
              </div>
              <input
                type="range"
                min={1}
                max={25}
                step={1}
                value={topPercent}
                onChange={(e) => setTopPercent(Number(e.target.value))}
                className="mt-2 w-full accent-teal-accent"
              />
              <p className="mt-2 text-[11px] text-gray-600">
                Effective percentile cutoff:{' '}
                <span className="tabular-nums text-gray-400">
                  {percentile.toFixed(3)}
                </span>{' '}
                (higher = fewer alerts)
              </p>
            </div>

            <button
              type="button"
              onClick={handleSaveSensitivity}
              disabled={saving}
              className="rounded border border-teal-muted/50 px-3 py-1.5 text-xs font-medium text-teal-accent hover:bg-teal-muted/10 disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Apply sensitivity'}
            </button>

            {saveMessage && (
              <p className="text-xs text-teal-accent">{saveMessage}</p>
            )}
          </div>
        )}
      </section>

      <section className={sectionClass()}>
        <h2 className="text-sm font-medium text-gray-200">
          Simulation (read-only)
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          Demo injection rates from the backend simulator — not adjustable at
          runtime.
        </p>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-gray-600">Mule attack probability</dt>
            <dd className="tabular-nums text-gray-200">
              {(simMule * 100).toFixed(2)}%
            </dd>
          </div>
          <div>
            <dt className="text-xs text-gray-600">Slow-drip attack probability</dt>
            <dd className="tabular-nums text-gray-200">
              {(simSlow * 100).toFixed(2)}%
            </dd>
          </div>
        </dl>
      </section>

      <section className={sectionClass()}>
        <h2 className="text-sm font-medium text-gray-200">
          Alert Queue defaults
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          Stored in this browser only. Applied when you open or refresh the
          Alert Queue.
        </p>

        <div className="mt-4 space-y-4">
          <div>
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">
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
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">
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

      <footer className={`${sectionClass()} text-xs text-gray-500`}>
        <h2 className="text-sm font-medium text-gray-300">System</h2>
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          <div>
            <dt>Backend health</dt>
            <dd className="text-gray-300">{healthStatus}</dd>
          </div>
          <div>
            <dt>WebSocket feed</dt>
            <dd className="text-gray-300">{wsStatus}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt>API docs</dt>
            <dd>
              <a
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                className="text-teal-accent hover:underline"
              >
                {API_BASE_URL}/docs
              </a>
            </dd>
          </div>
        </dl>
      </footer>
    </div>
  )
}
