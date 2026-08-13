import { useCallback, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import type { GraphSnapshot, MetricsUpdate } from '../api/types'
import { useLiveFeed } from '../hooks/useLiveFeed'
import AlertFeedPanel from '../components/AlertFeedPanel'
import ErrorBanner from '../components/ErrorBanner'
import GraphView from '../components/GraphView'
import TimelineScrubber, {
  type TimelineMode,
} from '../components/TimelineScrubber'

interface MetricCardProps {
  label: string
  value: string | number
}

function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div className="rounded-lg border border-charcoal-lighter bg-charcoal-light px-4 py-3">
      <p className="text-2xl font-semibold tabular-nums text-gray-100">{value}</p>
      <p className="mt-1 text-xs text-gray-500">{label}</p>
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

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-100">Live Monitor</h1>
          <p className="text-xs text-gray-500">
            Real-time UPI transaction graph and fraud alerts
          </p>
        </div>
        <span
          className={[
            'rounded border px-2 py-1 text-[10px] font-medium uppercase tracking-wide',
            timelineMode === 'replay'
              ? 'border-amber-soft/40 text-amber-soft'
              : connectionStatus === 'connected'
                ? 'border-teal-muted/40 text-teal-accent'
                : connectionStatus === 'connecting'
                  ? 'border-amber-soft/40 text-amber-soft'
                  : 'border-red-800/50 text-red-300',
          ].join(' ')}
        >
          {timelineMode === 'replay' ? 'replay' : connectionStatus}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="Active Nodes"
          value={displayMetrics.active_node_count.toLocaleString()}
        />
        <MetricCard
          label="Live Edges"
          value={displayMetrics.live_edge_count.toLocaleString()}
        />
        <MetricCard
          label="Peak Risk Score"
          value={displayMetrics.peak_fused_score.toFixed(2)}
        />
        <MetricCard
          label="Active Alerts"
          value={displayMetrics.active_alert_count.toLocaleString()}
        />
      </div>

      {timelineMode === 'live' && connectionStatus === 'disconnected' && (
        <ErrorBanner
          message="Live feed disconnected — graph may be stale. Reconnecting automatically…"
        />
      )}

      <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(220px,280px)]">
        {!graphLoaded && timelineMode === 'live' && (
          <div className="absolute inset-0 z-20 flex items-center justify-center rounded-lg border border-charcoal-lighter bg-charcoal/90 lg:right-[calc(280px+1rem)]">
            <div className="text-center">
              <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-2 border-charcoal-lighter border-t-teal-accent" />
              <p className="text-sm text-gray-400">Loading graph snapshot...</p>
            </div>
          </div>
        )}

        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          <GraphView
            mode={timelineMode}
            staticSnapshot={timelineMode === 'replay' ? replaySnapshot : null}
            pulsingNodeIds={pulsingNodeIds}
            replayLoading={replayLoading}
            transactions={transactions}
            alerts={alerts}
            focusNodeId={focusAccountId}
            onNodeClick={handleNodeClick}
            onInitialLoad={handleInitialLoad}
          />
          <TimelineScrubber
            mode={timelineMode}
            onModeChange={handleTimelineModeChange}
            onSnapshotChange={handleReplaySnapshot}
            onLoadingChange={setReplayLoading}
          />
        </div>

        <div className="min-h-[280px] min-w-0 lg:min-h-0">
          <AlertFeedPanel alerts={alerts} />
        </div>
      </div>
    </div>
  )
}
