import { useCallback, useEffect, useRef, useState } from 'react'

import {
  API_BASE_URL,
  getHealthStatus,
  getSettings,
  patchSettings,
  WS_BASE_URL,
} from '../api/client'
import type { AlertStatus, AppSettings, ConfidenceLevel } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import ErrorBanner from '../components/ErrorBanner'
import LoadingSpinner from '../components/LoadingSpinner'
import MaterialIcon from '../components/MaterialIcon'
import {
  loadDefaultConfidenceFilter,
  loadDefaultStatusFilter,
  saveDefaultConfidenceFilter,
  saveDefaultStatusFilter,
} from '../utils/settingsPreferences'
import {
  DEFAULT_ALERT_TOP_PERCENT,
  DEFAULT_ALERT_TOP_PERCENTILE,
  MANUAL_ALERT_TOP_PERCENT_MAX,
  MANUAL_ALERT_TOP_PERCENT_MIN,
} from '../knobs'

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
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [topPercent, setTopPercent] = useState(DEFAULT_ALERT_TOP_PERCENT)
  const [percentile, setPercentile] = useState(DEFAULT_ALERT_TOP_PERCENTILE)
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

  const [calibrationOn, setCalibrationOn] = useState(false)
  const [calibration, setCalibration] = useState<AppSettings['calibration']>(null)
  const [system, setSystem] = useState<AppSettings['system']>(null)
  const [togglingCalibration, setTogglingCalibration] = useState(false)
  const sliderDirtyRef = useRef(false)
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
      setCalibrationOn(data.calibration_enabled)
      setCalibration(data.calibration)
      setSystem(data.system)
      sliderDirtyRef.current = false
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchSettingsQuiet = useCallback(async () => {
    try {
      const data = await getSettings()
      if (!sliderDirtyRef.current) {
        setTopPercent(data.alert_top_percent)
        setPercentile(data.alert_top_percentile)
      }
      setCalibrationOn(data.calibration_enabled)
      setCalibration(data.calibration)
      setSystem(data.system)
    } catch {
      /* keep last good snapshot */
    }
  }, [])

  useEffect(() => {
    void fetchSettings()
    const interval = window.setInterval(() => {
      void fetchSettingsQuiet()
    }, 20000)
    return () => window.clearInterval(interval)
  }, [fetchSettings, fetchSettingsQuiet])

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
        `Saved — alerts will target the top ${updated.alert_top_percent}% of accounts each cycle. Auto-calibration counters reset so this manual baseline is not overwritten this tick.`,
      )
      setCalibration(updated.calibration)
      setCalibrationOn(updated.calibration_enabled)
      sliderDirtyRef.current = false
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const handleToggleCalibration = async (enabled: boolean) => {
    setTogglingCalibration(true)
    setError(null)
    try {
      const updated = await patchSettings({ calibration_enabled: enabled })
      setCalibrationOn(updated.calibration_enabled)
      setCalibration(updated.calibration)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to update auto-calibration',
      )
    } finally {
      setTogglingCalibration(false)
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

      {!isAdmin && (
        <div className="rounded-xl border border-amber-soft/30 bg-amber-soft/10 px-4 py-3 text-sm text-amber-soft">
          <span className="font-semibold">Admin only:</span> detection sensitivity and auto-calibration controls are read-only for analysts.
        </div>
      )}

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
                min={system?.manual_alert_top_percent_min ?? MANUAL_ALERT_TOP_PERCENT_MIN}
                max={system?.manual_alert_top_percent_max ?? MANUAL_ALERT_TOP_PERCENT_MAX}
                step={calibration?.step_percent_points ?? 0.5}
                value={topPercent}
                disabled={!isAdmin}
                onChange={(e) => {
                  sliderDirtyRef.current = true
                  setTopPercent(Number(e.target.value))
                }}
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
              disabled={saving || !isAdmin}
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

        <section className={`${sectionClass(true)} lg:col-span-3`}>
          <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
            <h2 className="flex items-center gap-3 text-xl font-semibold text-on-surface">
              <MaterialIcon name="sync_alt" className="text-primary" size={22} />
              Auto-calibration
            </h2>
            <label className="flex items-center gap-3 text-sm text-on-surface">
              <span className="text-on-surface-variant">
                {calibrationOn ? 'On' : 'Off'} (opt-in)
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={calibrationOn}
                disabled={togglingCalibration || loading || !isAdmin}
                onClick={() => void handleToggleCalibration(!calibrationOn)}
                className={[
                  'relative h-6 w-11 rounded-full transition-colors',
                  calibrationOn ? 'bg-primary' : 'bg-outline/40',
                  togglingCalibration ? 'opacity-40' : '',
                ].join(' ')}
              >
                <span
                  className={[
                    'absolute top-0.5 h-5 w-5 rounded-full bg-on-primary transition-transform',
                    calibrationOn ? 'left-5' : 'left-0.5',
                  ].join(' ')}
                />
              </button>
            </label>
          </div>
          <p className="mb-4 text-sm text-on-surface-variant">
            Uses confirmed vs false-positive judgments only (same rule as Reports).
            {calibration ? (
              <>
                {' '}
                Target confirmed-rate band is{' '}
                {(calibration.target_band_low * 100).toFixed(0)}–
                {(calibration.target_band_high * 100).toFixed(0)}%. Below the
                band the queue is too noisy and the top-share tightens by{' '}
                {calibration.step_percent_points} pp; above it loosens by the
                same step to recover recall. Requires {calibration.min_sample}{' '}
                judged alerts, two consecutive out-of-band ticks, auto clamp{' '}
                {calibration.clamp_low_percent}–{calibration.clamp_high_percent}
                %, and runs once per {calibration.cycles_per_calibration}{' '}
                detection cycles. Manual Apply always wins and resets the
                controller. Default is off.
              </>
            ) : (
              ' Auto-calibration is opt-in and off by default.'
            )}
          </p>
          {calibration && (
            <dl className="grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                  Confirmed rate
                </dt>
                <dd className="mt-1 tabular-nums text-on-surface">
                  {calibration.confirmed_rate == null
                    ? '—'
                    : `${(calibration.confirmed_rate * 100).toFixed(1)}%`}
                  <span className="ml-2 text-xs text-on-surface-variant">
                    target {(calibration.target_band_low * 100).toFixed(0)}–
                    {(calibration.target_band_high * 100).toFixed(0)}%
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                  Sample size
                </dt>
                <dd className="mt-1 tabular-nums text-on-surface">
                  {calibration.sample_size} / {calibration.min_sample} min
                  <span className="ml-2 text-xs text-on-surface-variant">
                    window {calibration.window_size}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                  Next tick
                </dt>
                <dd className="mt-1 text-on-surface">
                  {calibrationOn
                    ? calibration.cycles_until_next == null ||
                      calibration.cycles_until_next === 0
                      ? 'this / just-ran cycle'
                      : `${calibration.cycles_until_next} detection cycle(s)`
                    : 'disabled'}
                </dd>
              </div>
            </dl>
          )}
          {calibration?.last_adjustment && (
            <p className="mt-4 rounded-lg border border-primary/20 bg-primary/10 px-3 py-2 text-sm text-on-surface">
              {calibration.last_adjustment.reason}
              <span className="mt-1 block text-xs text-on-surface-variant">
                {new Date(calibration.last_adjustment.at).toLocaleString()}
              </span>
            </p>
          )}
          {calibration?.skipped_reason && (
            <p className="mt-3 text-xs text-on-surface-variant">
              {calibration.skipped_reason}
            </p>
          )}
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
