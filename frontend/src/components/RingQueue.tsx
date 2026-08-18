import { Fragment, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { getRingDetail, getRings, patchAlert, patchRing } from '../api/client'
import type {
  AlertStatus,
  GetRingsParams,
  RingDetail,
  RingListItem,
  RingMember,
} from '../api/types'
import { canTransitionTo, getQuickActions } from '../utils/alertActions'
import { formatRelativeTime, isPeripheralStructural } from '../utils/format'
import ConfidenceBadge from './ConfidenceBadge'
import ErrorBanner from './ErrorBanner'
import LoadingSpinner from './LoadingSpinner'
import PeripheralStructuralBadge from './PeripheralStructuralBadge'
import RiskScorePill from './RiskScorePill'
import StatusBadge from './StatusBadge'
import TableSkeleton from './TableSkeleton'

const PAGE_SIZE = 25

function actionButtonClass(variant: 'primary' | 'danger' | 'neutral'): string {
  const base =
    'rounded-lg border px-3 py-1.5 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40'
  switch (variant) {
    case 'primary':
      return `${base} border-primary/20 bg-primary/10 text-primary hover:bg-primary/20`
    case 'danger':
      return `${base} border-error/40 bg-error/10 text-error hover:bg-error/20`
    default:
      return `${base} border-primary/10 text-on-surface hover:border-primary/30 hover:text-primary`
  }
}

function roleLabel(role: string): string {
  if (role === 'hub') return 'Hub'
  if (role === 'fan-in') return 'Fan-in'
  if (role === 'fan-out') return 'Fan-out'
  if (role === 'peripheral') return 'Peripheral'
  return 'Core'
}

interface RingQueueProps {
  queryParams: GetRingsParams
  onTotalChange?: (total: number) => void
}

export default function RingQueue({ queryParams, onTotalChange }: RingQueueProps) {
  const [items, setItems] = useState<RingListItem[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyRingId, setBusyRingId] = useState<string | null>(null)

  const page = queryParams.page ?? 1

  const fetchRings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await getRings({ ...queryParams, page_size: PAGE_SIZE })
      setItems(response.items)
      setTotal(response.pagination.total)
      setTotalPages(response.pagination.total_pages)
      onTotalChange?.(response.pagination.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load rings')
      setItems([])
      setTotal(0)
      setTotalPages(0)
      onTotalChange?.(0)
    } finally {
      setLoading(false)
    }
  }, [queryParams, onTotalChange])

  useEffect(() => {
    fetchRings()
  }, [fetchRings])

  const handleBulk = async (
    ringId: string,
    status: 'confirmed' | 'false_positive' | 'reviewing',
  ) => {
    setBusyRingId(ringId)
    setActionError(null)
    try {
      const result = await patchRing(ringId, { status })
      setItems((prev) =>
        prev.map((item) =>
          item.ring_id === ringId
            ? {
                ...item,
                status: result.ring.status,
                open_alert_count: result.ring.open_alert_count,
                aggregate_risk_score: result.ring.aggregate_risk_score,
                last_updated_at: result.ring.last_updated_at,
              }
            : item,
        ),
      )
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update ring')
    } finally {
      setBusyRingId(null)
    }
  }

  if (loading) {
    return <TableSkeleton rows={8} columns={7} />
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={fetchRings} />
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
        <p className="text-lg font-medium text-on-surface">No rings yet</p>
        <p className="mt-2 max-w-sm text-sm text-on-surface-variant">
          Rings appear when Layer 2 community detection (or a peripheral linked
          to a hub) groups alerts onto a shared ring ID.
        </p>
      </div>
    )
  }

  return (
    <div>
      {actionError && (
        <p className="border-b border-red-400/30 bg-red-400/10 px-4 py-2 text-xs text-red-300">
          {actionError}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead>
            <tr className="data-table-head">
              <th className="px-6 py-4 font-medium">Hub</th>
              <th className="px-6 py-4 font-medium">Members</th>
              <th className="px-6 py-4 font-medium">Risk</th>
              <th className="px-6 py-4 font-medium">Confidence</th>
              <th className="px-6 py-4 font-medium">Detected</th>
              <th className="px-6 py-4 font-medium">Status</th>
              <th className="px-6 py-4 text-right font-medium">Ring actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-primary/5">
            {items.map((ring) => {
              const isExpanded = expandedId === ring.ring_id
              const busy = busyRingId === ring.ring_id
              return (
                <Fragment key={ring.ring_id}>
                  <tr
                    role="button"
                    tabIndex={0}
                    onClick={() => setExpandedId(isExpanded ? null : ring.ring_id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setExpandedId(isExpanded ? null : ring.ring_id)
                      }
                    }}
                    className={[
                      'data-table-row cursor-pointer',
                      isExpanded ? 'bg-primary/5' : '',
                    ].join(' ')}
                  >
                    <td className="px-6 py-4">
                      <div className="flex flex-col">
                        <span className="font-medium text-primary">
                          {ring.hub_account_id ?? 'Unknown hub'}
                        </span>
                        <span className="font-mono text-[10px] text-on-surface-variant">
                          {ring.ring_id}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-on-surface-variant">
                      {ring.member_count} · {ring.alert_count} alerts
                    </td>
                    <td className="px-6 py-4">
                      <RiskScorePill
                        score={ring.aggregate_risk_score}
                        confidence={ring.confidence}
                        compact
                      />
                    </td>
                    <td className="px-6 py-4">
                      <ConfidenceBadge confidence={ring.confidence} />
                    </td>
                    <td className="px-6 py-4 text-on-surface-variant">
                      {formatRelativeTime(ring.first_detected_at)}
                    </td>
                    <td className="px-6 py-4">
                      <StatusBadge status={ring.status} />
                    </td>
                    <td
                      className="px-6 py-4"
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {canTransitionTo(ring.status, 'confirmed') && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleBulk(ring.ring_id, 'confirmed')}
                            className={actionButtonClass('danger')}
                          >
                            Confirm ring
                          </button>
                        )}
                        {canTransitionTo(ring.status, 'false_positive') && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleBulk(ring.ring_id, 'false_positive')}
                            className={actionButtonClass('neutral')}
                          >
                            Ring false positive
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="border-b border-primary/5">
                      <td colSpan={7} className="p-0">
                        <RingExpanded
                          ringId={ring.ring_id}
                          onMemberStatusChange={fetchRings}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-primary/10 bg-surface/20 px-6 py-4 text-xs text-on-surface-variant">
          <span>
            Showing {(page - 1) * PAGE_SIZE + 1} to{' '}
            {Math.min(page * PAGE_SIZE, total)} of {total} rings
          </span>
        </div>
      )}
    </div>
  )
}

function RingExpanded({
  ringId,
  onMemberStatusChange,
}: {
  ringId: string
  onMemberStatusChange: () => void
}) {
  const [detail, setDetail] = useState<RingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    let cancelled = false
    getRingDetail(ringId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load ring')
        }
      })
    return () => {
      cancelled = true
    }
  }, [ringId])

  useEffect(() => load(), [load])

  const handleMember = async (member: RingMember, status: AlertStatus) => {
    if (member.alert_id == null || member.status == null) return
    if (!canTransitionTo(member.status, status)) return
    setBusyId(member.alert_id)
    try {
      await patchAlert(member.alert_id, { status })
      onMemberStatusChange()
      const fresh = await getRingDetail(ringId)
      setDetail(fresh)
    } finally {
      setBusyId(null)
    }
  }

  if (error) {
    return (
      <div className="px-4 py-3">
        <ErrorBanner message={error} onRetry={load} />
      </div>
    )
  }

  if (!detail) {
    return (
      <LoadingSpinner size="sm" label="Loading ring members…" className="py-6" />
    )
  }

  const explanation = detail.explanation

  return (
    <div className="space-y-4 border-t border-primary/10 bg-surface/40 px-4 py-4">
      <div className="grid gap-3 text-xs text-on-surface-variant lg:grid-cols-2">
        <div>
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide">
            Merged explanation
          </p>
          <p className="text-on-surface">
            {explanation.reason ?? 'No Layer 2 reason stored.'}
          </p>
          <p className="mt-2">
            Hub concentration{' '}
            {explanation.hub_concentration != null
              ? `${(explanation.hub_concentration * 100).toFixed(0)}%`
              : '—'}{' '}
            · external flow{' '}
            {explanation.external_edge_ratio != null
              ? explanation.external_edge_ratio.toFixed(2)
              : '—'}
            {explanation.detection_window != null
              ? ` · window ${explanation.detection_window}m`
              : ''}
          </p>
          <p className="mt-1">
            Core {explanation.core_account_ids.length} · peripheral{' '}
            {explanation.peripheral_account_ids.length}
          </p>
        </div>
        <p className="text-[11px] text-on-surface-variant">
          Confirm or mark the whole ring above, or review individual members
          below.
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-primary/10">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-on-surface-variant">
              <th className="px-3 py-2">Account</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Confidence</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">Individual</th>
            </tr>
          </thead>
          <tbody>
            {detail.members.map((member) => {
              const actions = member.status ? getQuickActions(member.status) : []
              return (
                <tr key={member.account_id} className="border-t border-primary/5">
                  <td className="px-3 py-2">
                    <Link
                      to={`/accounts/${encodeURIComponent(member.account_id)}`}
                      className="text-primary hover:underline"
                    >
                      {member.account_id}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <span className="rounded border border-primary/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-on-surface">
                      {roleLabel(member.role)}
                    </span>
                    {member.pattern_type &&
                      isPeripheralStructural(member.pattern_type) && (
                        <PeripheralStructuralBadge compact className="ml-1" />
                      )}
                  </td>
                  <td className="px-3 py-2">
                    {member.confidence ? (
                      <ConfidenceBadge confidence={member.confidence} />
                    ) : (
                      <span className="text-on-surface-variant">no alert</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {member.status ? (
                      <StatusBadge status={member.status} />
                    ) : (
                      <span className="text-on-surface-variant">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex flex-wrap justify-end gap-1">
                      {actions.map((action) => (
                        <button
                          key={action.target}
                          type="button"
                          disabled={busyId === member.alert_id}
                          onClick={() => handleMember(member, action.target)}
                          className={actionButtonClass(action.variant)}
                        >
                          {action.label}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
