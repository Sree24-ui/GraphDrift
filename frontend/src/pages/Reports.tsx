import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { exportReportsCsv, getReportsSummary } from '../api/client'
import type { ReportPeriod, ReportSummary, StatusBreakdown } from '../api/types'
import ErrorBanner from '../components/ErrorBanner'
import LoadingSpinner from '../components/LoadingSpinner'
import MaterialIcon from '../components/MaterialIcon'
import { formatDateTime } from '../utils/format'

const PERIODS: { value: ReportPeriod; label: string }[] = [
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
]

const STATUS_CHART_COLORS: Record<string, string> = {
  new: '#7dd3fc',
  reviewing: '#c8a0f0',
  confirmed: '#ff6b6b',
  false_positive: '#4a6070',
  auto_closed: '#88b4cc',
}

const STATUS_KEYS = [
  'new',
  'reviewing',
  'confirmed',
  'false_positive',
  'auto_closed',
] as const satisfies readonly (keyof StatusBreakdown)[]

type StatusKey = (typeof STATUS_KEYS)[number]

const STATUS_LABELS: Record<StatusKey, string> = {
  new: 'New',
  reviewing: 'Reviewing',
  confirmed: 'Confirmed',
  false_positive: 'False Positive',
  auto_closed: 'Auto-closed',
}

const CONFIDENCE_COLORS: Record<string, string> = {
  high: '#ff6b6b',
  medium: '#c8a0f0',
  low: '#7dd3fc',
}

const MIN_REVIEWED_SAMPLE = 20

function formatDuration(seconds: number | null): string {
  if (seconds == null) {
    return '—'
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) {
    return `${minutes}m`
  }
  const hours = Math.floor(minutes / 60)
  const rem = minutes % 60
  return rem > 0 ? `${hours}h ${rem}m` : `${hours}h`
}

interface MetricCardProps {
  label: string
  value: string | number
  hint?: string
  icon?: string
  highlight?: boolean
}

function MetricCard({
  label,
  value,
  hint,
  icon,
  highlight = false,
}: MetricCardProps) {
  return (
    <div
      className={`kpi-card ${highlight ? 'border-primary/30 shadow-[0_0_20px_rgba(125,211,252,0.1)]' : ''}`}
    >
      {icon && (
        <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-primary">
          <MaterialIcon name={icon} size={20} />
        </div>
      )}
      <p className="text-sm text-on-surface-variant">{label}</p>
      <p
        className={`mt-1 font-headline text-3xl font-semibold tabular-nums ${highlight ? 'text-primary glacier-text-glow' : 'text-on-surface'}`}
      >
        {value}
      </p>
      {hint && (
        <p className="mt-1 text-[10px] text-on-surface-variant/70">{hint}</p>
      )}
    </div>
  )
}

function periodChipClass(active: boolean): string {
  return ['filter-chip', active ? 'filter-chip-active' : 'filter-chip-inactive'].join(
    ' ',
  )
}

export default function Reports() {
  const [period, setPeriod] = useState<ReportPeriod>('24h')
  const [summary, setSummary] = useState<ReportSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const fetchSummary = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getReportsSummary(period)
      setSummary(data)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to load report summary',
      )
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => {
    fetchSummary()
  }, [fetchSummary])

  const statusChartData = useMemo(() => {
    if (!summary) {
      return []
    }
    return STATUS_KEYS.map((key) => ({
      key,
      label: STATUS_LABELS[key],
      count: summary.by_status[key],
      fill: STATUS_CHART_COLORS[key],
    }))
  }, [summary])

  const confidenceChartData = useMemo(() => {
    if (!summary) {
      return []
    }
    const { by_confidence } = summary
    return (['high', 'medium', 'low'] as const).map((key) => ({
      key,
      label: key.charAt(0).toUpperCase() + key.slice(1),
      count: by_confidence[key],
      fill: CONFIDENCE_COLORS[key],
    }))
  }, [summary])

  const volumeChartData = useMemo(() => {
    if (!summary) {
      return []
    }
    return summary.daily_counts.map((row) => ({
      date: row.date,
      label: new Date(`${row.date}T12:00:00`).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      }),
      count: row.count,
    }))
  }, [summary])

  const precision = useMemo(() => {
    if (!summary) {
      return null
    }
    const confirmed = summary.by_status.confirmed
    const falsePositive = summary.by_status.false_positive
    const reviewedTotal = confirmed + falsePositive
    if (reviewedTotal === 0) {
      return {
        rate: null as number | null,
        reviewedTotal: 0,
        confirmed,
        falsePositive,
      }
    }
    return {
      rate: confirmed / reviewedTotal,
      reviewedTotal,
      confirmed,
      falsePositive,
    }
  }, [summary])

  const handleExport = async () => {
    setExporting(true)
    try {
      const blob = await exportReportsCsv(period)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `graphdrift-alerts-${period}.csv`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to export report CSV',
      )
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-headline text-2xl font-semibold tracking-tight text-on-background glacier-text-glow md:text-3xl">
            Performance Analytics
          </h1>
          <p className="page-subtitle mt-1">
            Alert volume and analyst outcomes for the selected period.
          </p>
          {summary && (
            <p className="mt-2 text-[11px] text-on-surface-variant">
              {formatDateTime(summary.period_start)} —{' '}
              {formatDateTime(summary.period_end)}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {PERIODS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setPeriod(opt.value)}
              className={periodChipClass(period === opt.value)}
            >
              {opt.label}
            </button>
          ))}
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting || loading || summary?.total_alerts === 0}
            className="ml-2 rounded-lg border border-primary/20 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {exporting ? 'Exporting…' : 'Export summary as CSV'}
          </button>
        </div>
      </div>

      {error && (
        <ErrorBanner message={error} onRetry={fetchSummary} />
      )}

      {loading ? (
        <LoadingSpinner label="Loading reports…" className="py-16" />
      ) : !summary || summary.total_alerts === 0 ? (
        <div className="glass-panel flex flex-col items-center justify-center rounded-2xl px-6 py-16 text-center">
          <p className="text-lg font-medium text-on-surface">No alerts in this period</p>
          <p className="mt-2 max-w-md text-sm text-on-surface-variant">
            Try a longer window, or let the simulator run after a demo reset
            before checking reports.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard
              label="Total alerts raised"
              value={summary.total_alerts.toLocaleString()}
              icon="notifications_active"
            />
            <MetricCard
              label="Avg time to review"
              value={formatDuration(summary.average_time_to_review_seconds)}
              hint="Analyst first action"
              icon="timer"
            />
            <MetricCard
              label="Confirmed"
              value={summary.by_status.confirmed}
              icon="gpp_maybe"
            />
            <MetricCard
              label="False positives"
              value={summary.by_status.false_positive}
              icon="filter_alt_off"
            />
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {STATUS_KEYS.map((key) => (
                <MetricCard
                  key={key}
                  label={STATUS_LABELS[key]}
                  value={summary.by_status[key]}
                />
              ))}
          </div>

          {precision && (
            <section className="glass-panel rounded-xl p-4">
              <h2 className="text-sm font-medium text-on-surface">
                Confirmed rate (analyst-reviewed only)
              </h2>
              <p className="mt-1 text-xs text-on-surface-variant">
                Of alerts where an analyst made a final call (
                {precision.confirmed} confirmed + {precision.falsePositive}{' '}
                false positive = {precision.reviewedTotal} judged), what share
                were confirmed fraud. New, reviewing, and auto-closed cases are
                excluded.
              </p>
              {precision.reviewedTotal === 0 ? (
                <p className="mt-3 text-sm text-on-surface-variant">
                  No analyst-reviewed alerts in this period yet — triage a few
                  cases in the Alert Queue to populate this metric.
                </p>
              ) : (
                <>
                  <p className="mt-3 text-3xl font-semibold tabular-nums text-on-surface">
                    {(precision.rate! * 100).toFixed(1)}%
                  </p>
                  {precision.reviewedTotal < MIN_REVIEWED_SAMPLE && (
                    <p className="mt-2 rounded-lg border border-tertiary/30 bg-tertiary/10 px-3 py-2 text-xs text-tertiary">
                      Small sample ({precision.reviewedTotal} reviewed alerts) —
                      this rate is indicative only and should not be treated as
                      a stable precision estimate until at least{' '}
                      {MIN_REVIEWED_SAMPLE} analyst-reviewed cases are available.
                    </p>
                  )}
                </>
              )}
            </section>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="glass-panel rounded-xl p-4">
              <h2 className="text-sm font-medium text-on-surface">Alerts by status</h2>
              <div className="mt-4 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={statusChartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#2a3238" strokeDasharray="3 3" />
                    <XAxis
                      dataKey="label"
                      stroke="#6b7280"
                      fontSize={10}
                      tickLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      stroke="#6b7280"
                      fontSize={10}
                      tickLine={false}
                      width={28}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#1a1f24',
                        border: '1px solid #2a3238',
                        borderRadius: 6,
                        fontSize: 12,
                      }}
                    />
                    <Bar dataKey="count" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="glass-panel rounded-xl p-4">
              <h2 className="text-sm font-medium text-on-surface">
                Alerts by confidence
              </h2>
              <div className="mt-4 h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={confidenceChartData}
                    margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid stroke="#2a3238" strokeDasharray="3 3" />
                    <XAxis
                      dataKey="label"
                      stroke="#6b7280"
                      fontSize={10}
                      tickLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      stroke="#6b7280"
                      fontSize={10}
                      tickLine={false}
                      width={28}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#1a1f24',
                        border: '1px solid #2a3238',
                        borderRadius: 6,
                        fontSize: 12,
                      }}
                    />
                    <Bar dataKey="count" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>

          <section className="glass-panel rounded-xl p-4">
            <h2 className="text-sm font-medium text-on-surface">Alert volume over time</h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              Daily alert count within the selected period.
            </p>
            <div className="mt-4 h-56">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={volumeChartData}
                  margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="volumeFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#7dd3fc" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#7dd3fc" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#2a3238" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="label"
                    stroke="#6b7280"
                    fontSize={10}
                    tickLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    allowDecimals={false}
                    stroke="#6b7280"
                    fontSize={10}
                    tickLine={false}
                    width={28}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#1a1f24',
                      border: '1px solid #2a3238',
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                    labelFormatter={(_, payload) =>
                      payload?.[0]?.payload?.date ?? ''
                    }
                  />
                  <Area
                    type="monotone"
                    dataKey="count"
                    stroke="#7dd3fc"
                    fill="url(#volumeFill)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
