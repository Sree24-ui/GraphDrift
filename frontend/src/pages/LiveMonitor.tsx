import { useCallback, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import type { GraphSnapshot, MetricsUpdate } from '../api/types'
import { useLiveFeed } from '../hooks/useLiveFeed'
import AlertFeedPanel from '../components/AlertFeedPanel'
import ErrorBanner from '../components/ErrorBanner'
import GraphView from '../components/GraphView'
import MaterialIcon from '../components/MaterialIcon'
import TimelineScrubber, {
  type TimelineMode,
} from '../components/TimelineScrubber'

interface MetricCardProps {
  label: string
  value: string | number
  icon: string
  accent?: 'primary' | 'tertiary' | 'error'
  trend?: string
  trendUp?: boolean
}

function MetricCard({
  label,
  value,
  icon,
  accent = 'primary',
  trend,
  trendUp = true,
}: MetricCardProps) {
  const iconColor =
    accent === 'error'
      ? 'text-error/80'
      : accent === 'tertiary'
        ? 'text-tertiary/70'
        : 'text-primary/70'

  const gradientClass =
    accent === 'error'
      ? 'from-error/10'
      : accent === 'tertiary'
        ? 'from-tertiary/10'
        : 'from-primary/10'

  const cardBorder =
    accent === 'error'
      ? 'border-error/20 shadow-[0_0_15px_rgba(255,107,107,0.08)]'
      : ''

  const trendColor = trendUp ? 'text-emerald-400 bg-emerald-400/10' : 'text-error bg-error/10'

  return (
    <div className={`kpi-card ${cardBorder}`}>
      <div
        className={`kpi-card-gradient absolute inset-0 bg-gradient-to-br ${gradientClass} to-transparent`}
      />
      <div className="relative z-10 flex items-start justify-between">
        <h3 className="text-sm font-medium text-on-surface-variant">{label}</h3>
        <MaterialIcon name={icon} className={iconColor} size={20} />
      </div>
      <div className="relative z-10 mt-2 flex items-baseline gap-2">
        <span className="font-display text-3xl font-bold tabular-nums tracking-tight text-on-surface">
          {value}
        </span>
        {trend && (
          <span
            className={`flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${trendColor}`}
          >
            <MaterialIcon
              name={trendUp ? 'arrow_upward' : 'arrow_downward'}
              size={14}
            />
            {trend}
          </span>
        )}
      </div>
    </div>
  )
}

function fallbackMetricsFromGraph(graph: GraphSnapshot | null) {
  return {
    active_node_count: graph?.nodes.length ?? 0,
    live_edge_count: graph?.edges.length ?? 0,
    peak_fused_score: graph
      ? Math.max(
          0,
          ...graph.nodes
            .map((n) => n.fused_score ?? 0)
            .filter((s) => s > 0),
        )
      : 0,
    active_alert_count: 0,
  }
}

export default function LiveMonitor() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const focusAccountId = searchParams.get('focus')
  const { transactions, alerts, metrics, connectionStatus } = useLiveFeed()
  const [graphLoaded, setGraphLoaded] = useState(false)
  const [initialGraph, setInitialGraph] = useState<GraphSnapshot | null>(null)

  const [timelineMode, setTimelineMode] = useState<TimelineMode>('live')
  const [replaySnapshot, setReplaySnapshot] = useState<GraphSnapshot | null>(
    null,
  )
  const [replayLoading, setReplayLoading] = useState(false)
  const [pulsingNodeIds, setPulsingNodeIds] = useState<Set<string>>(new Set())
  const pulseClearTimerRef = useRef<number | null>(null)

  const displayMetrics: MetricsUpdate = useMemo(() => {
    if (timelineMode === 'replay' && replaySnapshot) {
      return {
        active_node_count: replaySnapshot.nodes.length,
        live_edge_count: replaySnapshot.edges.length,
        peak_fused_score: Math.max(
          0,
          ...replaySnapshot.nodes
            .map((n) => n.fused_score ?? 0)
            .filter((s) => s > 0),
        ),
        active_alert_count: replaySnapshot.nodes.filter(
          (n) => n.confidence !== null,
        ).length,
      }
    }

    if (metrics) {
      return metrics
    }
    return fallbackMetricsFromGraph(initialGraph)
  }, [timelineMode, replaySnapshot, metrics, initialGraph])

  const newAlertCount = useMemo(
    () => alerts.filter((a) => a.data.action === 'CREATE').length,
    [alerts],
  )

  const handleNodeClick = useCallback(
    (accountId: string) => {
      navigate(`/accounts/${encodeURIComponent(accountId)}`)
    },
    [navigate],
  )

  const handleInitialLoad = useCallback((snapshot: GraphSnapshot) => {
    setInitialGraph(snapshot)
    setGraphLoaded(true)
  }, [])

  const handleReplaySnapshot = useCallback(
    (
      snapshot: GraphSnapshot | null,
      meta: { endMs: number; newlyFlagged: string[] },
    ) => {
      if (snapshot === null) {
        setReplayLoading(false)
        return
      }

      setReplaySnapshot(snapshot)
      setReplayLoading(false)

      if (meta.newlyFlagged.length > 0) {
        setPulsingNodeIds(new Set(meta.newlyFlagged))
        if (pulseClearTimerRef.current !== null) {
          window.clearTimeout(pulseClearTimerRef.current)
        }
        pulseClearTimerRef.current = window.setTimeout(() => {
          setPulsingNodeIds(new Set())
        }, 1200)
      }
    },
    [],
  )

  const handleTimelineModeChange = useCallback((mode: TimelineMode) => {
    setTimelineMode(mode)
    if (mode === 'live') {
      setReplaySnapshot(null)
      setReplayLoading(false)
      setPulsingNodeIds(new Set())
    } else {
      setReplayLoading(true)
    }
  }, [])

  const statusLabel =
    timelineMode === 'replay' ? 'replay' : connectionStatus

  return (
    <div className="flex h-[calc(100vh-5rem)] flex-col gap-5 overflow-hidden md:h-[calc(100vh-3rem)]">
      <div className="flex shrink-0 items-center justify-between gap-4">
        <div>
          <h1 className="page-title glacier-text-glow">Live Monitor</h1>
          <p className="page-subtitle mt-1">
            Real-time UPI transaction graph and fraud alerts
          </p>
        </div>
        <span
          className={[
            'flex items-center gap-1.5 rounded-full border px-3 py-1 text-[10px] font-semibold uppercase tracking-wide',
            timelineMode === 'replay'
              ? 'border-tertiary/40 bg-tertiary/10 text-tertiary'
              : connectionStatus === 'connected'
                ? 'border-primary/40 bg-primary/10 text-primary'
                : connectionStatus === 'connecting'
                  ? 'border-tertiary/40 bg-tertiary/10 text-tertiary'
                  : 'border-error/40 bg-error/10 text-error',
          ].join(' ')}
        >
          {connectionStatus === 'connected' && timelineMode === 'live' && (
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
          )}
          {statusLabel}
        </span>
      </div>

      <div className="grid shrink-0 grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard
          label="Active Nodes"
          value={displayMetrics.active_node_count.toLocaleString()}
          icon="grain"
        />
        <MetricCard
          label="Live Edges"
          value={displayMetrics.live_edge_count.toLocaleString()}
          icon="timeline"
          accent="tertiary"
        />
        <MetricCard
          label="Anomalies Detected"
          value={displayMetrics.active_alert_count.toLocaleString()}
          icon="gpp_maybe"
          accent="error"
          trend={newAlertCount > 0 ? `${newAlertCount} new` : undefined}
          trendUp
        />
      </div>

      {timelineMode === 'live' && connectionStatus === 'disconnected' && (
        <ErrorBanner message="Live feed disconnected — graph may be stale. Reconnecting automatically…" />
      )}

      <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(280px,320px)]">
        {!graphLoaded && timelineMode === 'live' && (
          <div className="absolute inset-0 z-20 flex items-center justify-center rounded-2xl glass-panel-elevated xl:right-[calc(320px+1.25rem)]">
            <div className="text-center">
              <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
              <p className="text-sm text-on-surface-variant">
                Loading graph snapshot…
              </p>
            </div>
          </div>
        )}

        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          <div className="glass-panel-elevated flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl">
            <div className="z-20 flex items-center justify-between border-b border-primary/10 bg-surface/40 px-4 py-3">
              <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
                <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
                Network Topology
              </h2>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded-md border border-transparent p-1.5 text-on-surface-variant transition-colors hover:border-primary/20 hover:bg-surface-bright/50"
                  aria-label="Zoom in"
                >
                  <MaterialIcon name="zoom_in" size={20} />
                </button>
                <button
                  type="button"
                  className="rounded-md border border-transparent p-1.5 text-on-surface-variant transition-colors hover:border-primary/20 hover:bg-surface-bright/50"
                  aria-label="Zoom out"
                >
                  <MaterialIcon name="zoom_out" size={20} />
                </button>
                <button
                  type="button"
                  className="ml-2 rounded-md border border-transparent p-1.5 text-on-surface-variant transition-colors hover:border-primary/20 hover:bg-surface-bright/50"
                  aria-label="Filter graph"
                >
                  <MaterialIcon name="filter_list" size={20} />
                </button>
              </div>
            </div>
            <div className="relative min-h-[360px] flex-1 bg-[radial-gradient(circle_at_center,rgba(125,211,252,0.05)_0%,transparent_70%)]">
              <GraphView
                mode={timelineMode}
                staticSnapshot={
                  timelineMode === 'replay' ? replaySnapshot : null
                }
                pulsingNodeIds={pulsingNodeIds}
                replayLoading={replayLoading}
                transactions={transactions}
                alerts={alerts}
                focusNodeId={focusAccountId}
                onNodeClick={handleNodeClick}
                onInitialLoad={handleInitialLoad}
              />
            </div>
          </div>
          <TimelineScrubber
            mode={timelineMode}
            onModeChange={handleTimelineModeChange}
            onSnapshotChange={handleReplaySnapshot}
            onLoadingChange={setReplayLoading}
          />
        </div>

        <div className="min-h-[320px] min-w-0 xl:min-h-0">
          <AlertFeedPanel alerts={alerts} />
        </div>
      </div>
    </div>
  )
}
