import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, {
  type ForceGraphMethods,
} from 'react-force-graph-2d'

import { getAlerts, getGraphCurrent } from '../api/client'
import type {
  ConfidenceLevel,
  GraphSnapshot,
  LiveAlertMessage,
  LiveTransactionMessage,
} from '../api/types'
import { isReplayFlagged } from '../utils/replayFlagged'

const RESYNC_INTERVAL_MS = 60_000
const PRIMARY = '#7dd3fc'
const TERTIARY = '#c8a0f0'
const ERROR = '#ff6b6b'
const PULSE_DURATION_MS = 900

export interface GraphNodeObject {
  id: string
  fused_score: number | null
  confidence: ConfidenceLevel | null
  alertConfidence?: ConfidenceLevel
  x?: number
  y?: number
}

export interface GraphLinkObject {
  source: string
  target: string
  weight: number
  total_amount: number
}

export type GraphViewMode = 'live' | 'replay'

interface GraphViewProps {
  mode?: GraphViewMode
  staticSnapshot?: GraphSnapshot | null
  pulsingNodeIds?: Set<string>
  replayLoading?: boolean
  transactions: LiveTransactionMessage[]
  alerts: LiveAlertMessage[]
  onNodeClick: (accountId: string) => void
  onInitialLoad?: (snapshot: GraphSnapshot) => void
  focusNodeId?: string | null
}

function ensureNode(
  nodesMap: Map<string, GraphNodeObject>,
  accountId: string,
): GraphNodeObject {
  let node = nodesMap.get(accountId)
  if (!node) {
    node = {
      id: accountId,
      fused_score: null,
      confidence: null,
    }
    nodesMap.set(accountId, node)
  }
  return node
}

function linkKey(source: string, target: string): string {
  return `${source}|${target}`
}

function mergeSnapshot(
  snapshot: GraphSnapshot,
  nodesMap: Map<string, GraphNodeObject>,
  linksMap: Map<string, GraphLinkObject>,
) {
  for (const node of snapshot.nodes) {
    const existing = ensureNode(nodesMap, node.account_id)
    if (node.fused_score !== null) {
      existing.fused_score = node.fused_score
    }
    if (node.confidence !== null) {
      existing.confidence = node.confidence
    }
  }

  for (const edge of snapshot.edges) {
    const key = linkKey(edge.sender, edge.receiver)
    const existing = linksMap.get(key)
    if (existing) {
      existing.weight = edge.weight
      existing.total_amount = edge.total_amount
    } else {
      linksMap.set(key, {
        source: edge.sender,
        target: edge.receiver,
        weight: edge.weight,
        total_amount: edge.total_amount,
      })
    }
  }
}

function snapshotToGraphData(snapshot: GraphSnapshot): {
  nodes: GraphNodeObject[]
  links: GraphLinkObject[]
} {
  const nodes: GraphNodeObject[] = snapshot.nodes.map((node) => ({
    id: node.account_id,
    fused_score: node.fused_score,
    confidence: node.confidence,
    alertConfidence: isReplayFlagged(node) ? node.confidence ?? 'low' : undefined,
  }))

  const links: GraphLinkObject[] = snapshot.edges.map((edge) => ({
    source: edge.sender,
    target: edge.receiver,
    weight: edge.weight,
    total_amount: edge.total_amount,
  }))

  return { nodes, links }
}

function nodeRadius(node: GraphNodeObject): number {
  if (node.fused_score != null && node.fused_score > 0) {
    return 3 + Math.min(node.fused_score, 5) * 1.4
  }
  return 4
}

function linkEndpointId(endpoint: string | GraphNodeObject): string {
  return typeof endpoint === 'string' ? endpoint : endpoint.id
}

function computeFlaggedNetwork(
  nodes: GraphNodeObject[],
  links: GraphLinkObject[],
  flaggedIds: Set<string>,
): { nodes: GraphNodeObject[]; links: GraphLinkObject[] } {
  if (flaggedIds.size === 0) {
    return { nodes: [], links: [] }
  }

  const visibleIds = new Set(flaggedIds)

  for (const link of links) {
    const sourceId = linkEndpointId(link.source)
    const targetId = linkEndpointId(link.target)

    if (flaggedIds.has(sourceId)) {
      visibleIds.add(targetId)
    }
    if (flaggedIds.has(targetId)) {
      visibleIds.add(sourceId)
    }
  }

  const visibleNodes = nodes.filter((node) => visibleIds.has(node.id))
  const visibleLinks = links.filter((link) => {
    const sourceId = linkEndpointId(link.source)
    const targetId = linkEndpointId(link.target)
    return visibleIds.has(sourceId) && visibleIds.has(targetId)
  })

  return { nodes: visibleNodes, links: visibleLinks }
}

export default function GraphView({
  mode = 'live',
  staticSnapshot = null,
  pulsingNodeIds = new Set(),
  replayLoading = false,
  transactions,
  alerts,
  onNodeClick,
  onInitialLoad,
  focusNodeId = null,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<ForceGraphMethods<GraphNodeObject, GraphLinkObject> | undefined>(
    undefined,
  )
  const nodesMapRef = useRef(new Map<string, GraphNodeObject>())
  const linksMapRef = useRef(new Map<string, GraphLinkObject>())
  const lastTxIndexRef = useRef(0)
  const initialLoadDoneRef = useRef(false)
  const pulseStartedAtRef = useRef<Map<string, number>>(new Map())

  const [dimensions, setDimensions] = useState({ width: 800, height: 520 })
  const [graphVersion, setGraphVersion] = useState(0)
  const [showFlaggedOnly, setShowFlaggedOnly] = useState(true)
  const [restFlaggedIds, setRestFlaggedIds] = useState<Set<string>>(new Set())
  const [pulseFrame, setPulseFrame] = useState(0)

  useEffect(() => {
    if (focusNodeId) {
      setShowFlaggedOnly(false)
    }
  }, [focusNodeId])

  const isReplay = mode === 'replay'

  const bumpGraph = useCallback(() => {
    setGraphVersion((v) => v + 1)
  }, [])

  const liveGraphData = useMemo(() => {
    void graphVersion
    return {
      nodes: Array.from(nodesMapRef.current.values()),
      links: Array.from(linksMapRef.current.values()),
    }
  }, [graphVersion])

  const replayGraphData = useMemo(() => {
    if (!staticSnapshot) {
      return { nodes: [], links: [] }
    }
    return snapshotToGraphData(staticSnapshot)
  }, [staticSnapshot])

  const graphData = isReplay ? replayGraphData : liveGraphData

  const flaggedIds = useMemo(() => {
    if (isReplay && staticSnapshot) {
      return new Set(
        staticSnapshot.nodes
          .filter(isReplayFlagged)
          .map((node) => node.account_id),
      )
    }

    const ids = new Set(restFlaggedIds)
    for (const alert of alerts) {
      ids.add(alert.data.account_id)
    }
    for (const node of liveGraphData.nodes) {
      if (node.alertConfidence !== undefined) {
        ids.add(node.id)
      }
    }
    return ids
  }, [alerts, isReplay, liveGraphData.nodes, restFlaggedIds, staticSnapshot])

  const displayGraphData = useMemo(() => {
    if (!showFlaggedOnly) {
      return graphData
    }
    return computeFlaggedNetwork(graphData.nodes, graphData.links, flaggedIds)
  }, [graphData, flaggedIds, showFlaggedOnly])

  const totalNodeCount = graphData.nodes.length
  const visibleNodeCount = displayGraphData.nodes.length

  const refreshActiveAlerts = useCallback(async () => {
    try {
      const [openAlerts, reviewingAlerts] = await Promise.all([
        getAlerts({ status: 'new', page_size: 100 }),
        getAlerts({ status: 'reviewing', page_size: 100 }),
      ])

      const ids = new Set<string>()
      for (const item of [...openAlerts.items, ...reviewingAlerts.items]) {
        ids.add(item.account.account_id)
      }
      setRestFlaggedIds(ids)

      let changed = false
      for (const accountId of ids) {
        const node = nodesMapRef.current.get(accountId)
        if (node) {
          const alertItem =
            openAlerts.items.find((a) => a.account.account_id === accountId) ??
            reviewingAlerts.items.find((a) => a.account.account_id === accountId)
          if (alertItem && node.alertConfidence !== alertItem.confidence) {
            node.alertConfidence = alertItem.confidence
            changed = true
          }
        }
      }
      if (changed) {
        bumpGraph()
      }
    } catch {
      // Non-fatal: WS alerts still drive live highlighting.
    }
  }, [bumpGraph])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setDimensions({
        width: Math.max(width, 320),
        height: Math.max(height, 400),
      })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (isReplay) return

    let cancelled = false

    async function loadInitial() {
      try {
        const snapshot = await getGraphCurrent()
        if (cancelled) return
        mergeSnapshot(snapshot, nodesMapRef.current, linksMapRef.current)
        bumpGraph()
        if (!initialLoadDoneRef.current) {
          initialLoadDoneRef.current = true
          onInitialLoad?.(snapshot)
          void refreshActiveAlerts()
        }
      } catch {
        if (!cancelled && !initialLoadDoneRef.current) {
          initialLoadDoneRef.current = true
          onInitialLoad?.({
            window_start: '',
            window_end: '',
            nodes: [],
            edges: [],
          })
        }
      }
    }

    loadInitial()
    const interval = window.setInterval(() => {
      void loadInitial()
    }, RESYNC_INTERVAL_MS)

    const alertInterval = window.setInterval(() => {
      void refreshActiveAlerts()
    }, RESYNC_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(interval)
      window.clearInterval(alertInterval)
    }
  }, [bumpGraph, isReplay, onInitialLoad, refreshActiveAlerts])

  useEffect(() => {
    if (isReplay) return

    for (let i = lastTxIndexRef.current; i < transactions.length; i++) {
      const tx = transactions[i].data
      ensureNode(nodesMapRef.current, tx.sender)
      ensureNode(nodesMapRef.current, tx.receiver)

      const key = linkKey(tx.sender, tx.receiver)
      const existing = linksMapRef.current.get(key)
      if (existing) {
        existing.weight += 1
        existing.total_amount += tx.amount
      } else {
        linksMapRef.current.set(key, {
          source: tx.sender,
          target: tx.receiver,
          weight: 1,
          total_amount: tx.amount,
        })
      }
    }
    lastTxIndexRef.current = transactions.length
    if (transactions.length > 0) {
      bumpGraph()
    }
  }, [transactions, bumpGraph, isReplay])

  useEffect(() => {
    if (isReplay) return

    const latestByAccount = new Map<string, ConfidenceLevel>()
    for (const alert of alerts) {
      latestByAccount.set(alert.data.account_id, alert.data.confidence)
    }

    let changed = false
    for (const node of nodesMapRef.current.values()) {
      const next = latestByAccount.get(node.id)
      if (node.alertConfidence !== next) {
        node.alertConfidence = next
        changed = true
      }
    }

    if (changed) {
      bumpGraph()
    }
  }, [alerts, bumpGraph, isReplay])

  useEffect(() => {
    const now = Date.now()
    for (const nodeId of pulsingNodeIds) {
      if (!pulseStartedAtRef.current.has(nodeId)) {
        pulseStartedAtRef.current.set(nodeId, now)
      }
    }
  }, [pulsingNodeIds])

  useEffect(() => {
    if (pulsingNodeIds.size === 0) return

    let frame = 0
    let raf = 0
    const tick = () => {
      frame += 1
      setPulseFrame(frame)
      const now = Date.now()
      for (const [nodeId, startedAt] of pulseStartedAtRef.current) {
        if (now - startedAt > PULSE_DURATION_MS) {
          pulseStartedAtRef.current.delete(nodeId)
        }
      }
      if (pulseStartedAtRef.current.size > 0) {
        raf = requestAnimationFrame(tick)
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [pulsingNodeIds, pulseFrame])

  useEffect(() => {
    if (!focusNodeId || isReplay) {
      return
    }

    const centerOnFocus = () => {
      const node =
        nodesMapRef.current.get(focusNodeId) ??
        displayGraphData.nodes.find((n) => n.id === focusNodeId)
      if (
        node?.x != null &&
        node?.y != null &&
        graphRef.current
      ) {
        graphRef.current.centerAt(node.x, node.y, 800)
        graphRef.current.zoom(2.4, 800)
      }
    }

    const timer = window.setTimeout(centerOnFocus, 600)
    const retry = window.setTimeout(centerOnFocus, 1500)
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(retry)
    }
  }, [focusNodeId, displayGraphData, graphVersion, isReplay])

  const drawNode = useCallback(
    (node: GraphNodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const radius = nodeRadius(node)
      const alertConf = node.alertConfidence
      const isFocused = focusNodeId === node.id

      if (isFocused) {
        ctx.beginPath()
        ctx.arc(x, y, radius + 6, 0, 2 * Math.PI)
        ctx.strokeStyle = 'rgba(74, 143, 143, 0.9)'
        ctx.lineWidth = 3 / globalScale
        ctx.stroke()
      }

      const pulseStart = pulseStartedAtRef.current.get(node.id)
      if (pulseStart !== undefined) {
        const t = (Date.now() - pulseStart) / PULSE_DURATION_MS
        if (t < 1) {
          const pulseRadius = radius + 4 + t * 14
          ctx.beginPath()
          ctx.arc(x, y, pulseRadius, 0, 2 * Math.PI)
          ctx.strokeStyle = `rgba(212, 165, 116, ${0.85 * (1 - t)})`
          ctx.lineWidth = (2.5 + (1 - t) * 2) / globalScale
          ctx.stroke()
        }
      }

      ctx.beginPath()
      ctx.arc(x, y, radius, 0, 2 * Math.PI)

      if (alertConf === 'high') {
        ctx.fillStyle = ERROR
        ctx.fill()
      } else {
        ctx.fillStyle = PRIMARY
        ctx.fill()
        if (alertConf === 'medium' || alertConf === 'low') {
          ctx.strokeStyle = TERTIARY
          ctx.lineWidth = 1.5 / globalScale
          ctx.stroke()
        }
      }
    },
    [focusNodeId],
  )

  return (
    <div
      ref={containerRef}
      className="relative h-full min-h-[360px] w-full overflow-hidden bg-[radial-gradient(circle_at_center,rgba(125,211,252,0.05)_0%,transparent_70%)]"
    >
      <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-3 rounded-lg glass-panel px-3 py-2">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-on-surface">
          <input
            type="checkbox"
            checked={showFlaggedOnly}
            onChange={(e) => setShowFlaggedOnly(e.target.checked)}
            className="accent-primary"
          />
          Show flagged network only
        </label>
        <span className="text-[11px] tabular-nums text-on-surface-variant">
          Showing {visibleNodeCount.toLocaleString()} of{' '}
          {totalNodeCount.toLocaleString()} nodes
        </span>
        {isReplay && (
          <span className="rounded border border-tertiary/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-tertiary">
            Replay
          </span>
        )}
      </div>

      {isReplay && replayLoading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface/50">
          <div className="h-7 w-7 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
        </div>
      )}

      <ForceGraph2D
        ref={graphRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={displayGraphData}
        backgroundColor="#1a1f24"
        nodeRelSize={1}
        nodeLabel={(node) =>
          `${node.id}\nscore: ${node.fused_score?.toFixed(2) ?? 'n/a'}`
        }
        nodeCanvasObject={drawNode}
        nodeCanvasObjectMode={() => 'replace'}
        onNodeClick={(node) => onNodeClick(node.id)}
        linkDirectionalArrowLength={3.5}
        linkDirectionalArrowRelPos={1}
        linkColor={() => 'rgba(95, 168, 168, 0.28)'}
        linkWidth={(link) => Math.max(0.5, Math.log2((link.weight ?? 1) + 1))}
        linkLabel={(link) =>
          `₹${link.total_amount?.toLocaleString('en-IN') ?? 0} (${link.weight} tx)`
        }
        cooldownTicks={isReplay ? 40 : 80}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
    </div>
  )
}
