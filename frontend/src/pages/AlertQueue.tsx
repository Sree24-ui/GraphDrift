import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { getAlertDetail, getAlerts, patchAlert } from '../api/client'
import type {
  Alert,
  AlertDetail,
  AlertStatus,
  ConfidenceLevel,
  GetAlertsParams,
} from '../api/types'
import AlertExplainability from '../components/AlertExplainability'
import ConfidenceBadge from '../components/ConfidenceBadge'
import ErrorBanner from '../components/ErrorBanner'
import LoadingSpinner from '../components/LoadingSpinner'
import PeripheralStructuralBadge from '../components/PeripheralStructuralBadge'
import StatusBadge from '../components/StatusBadge'
import TableSkeleton from '../components/TableSkeleton'
import {
  canTransitionTo,
  datePresetToIso,
  getQuickActions,
  type DatePreset,
} from '../utils/alertActions'
import { formatPatternType, formatRelativeTime, isPeripheralStructural } from '../utils/format'
import {
  DEFAULT_STATUS_FILTER,
  loadDefaultConfidenceFilter,
  loadDefaultStatusFilter,
} from '../utils/settingsPreferences'

const ALL_STATUSES: AlertStatus[] = [
  'new',
  'reviewing',
  'confirmed',
  'false_positive',
  'auto_closed',
]

const STATUS_OPTIONS: { value: AlertStatus | 'all'; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'reviewing', label: 'Reviewing' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'false_positive', label: 'False Positive' },
  { value: 'auto_closed', label: 'Auto-closed' },
  { value: 'all', label: 'All' },
]

const CONFIDENCE_OPTIONS: { value: ConfidenceLevel | 'all'; label: string }[] = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
  { value: 'all', label: 'All' },
]

const DATE_OPTIONS: { value: DatePreset; label: string }[] = [
  { value: '1h', label: 'Last hour' },
  { value: '24h', label: 'Last 24h' },
  { value: '7d', label: 'Last 7 days' },
  { value: 'all', label: 'All' },
]

const PAGE_SIZE = 25

function actionButtonClass(variant: 'primary' | 'danger' | 'neutral'): string {
  const base =
    'rounded border px-2.5 py-1 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40'
  switch (variant) {
    case 'primary':
      return `${base} border-teal-muted/50 text-teal-accent hover:bg-teal-muted/10`
    case 'danger':
      return `${base} border-red-400/40 text-red-300 hover:bg-red-400/10`
    default:
      return `${base} border-gray-600 text-gray-400 hover:bg-charcoal-lighter`
  }
}

function filterChipClass(active: boolean): string {
  return [
    'rounded border px-2.5 py-1 text-xs font-medium transition-colors',
    active
      ? 'border-teal-muted/60 bg-teal-muted/15 text-teal-accent'
      : 'border-charcoal-lighter text-gray-500 hover:border-gray-600 hover:text-gray-300',
  ].join(' ')
}

interface AlertRowDetailProps {
  alert: Alert
  onStatusChange: (
    alertId: number,
    newStatus: AlertStatus,
    notes?: string,
  ) => Promise<void>
  onNotesSaved: (alertId: number, notes: string) => void
  busy: boolean
}

function AlertRowDetail({
  alert,
  onStatusChange,
  onNotesSaved,
  busy,
}: AlertRowDetailProps) {
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [notes, setNotes] = useState('')
  const [notesDirty, setNotesDirty] = useState(false)
  const [savingNotes, setSavingNotes] = useState(false)
  const [notesError, setNotesError] = useState<string | null>(null)

  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const loadDetail = useCallback(() => {
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    getAlertDetail(alert.id)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
          setNotes(data.analyst_notes ?? '')
          setNotesDirty(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null)
          setDetailError(
            err instanceof Error ? err.message : 'Failed to load alert detail',
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDetailLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [alert.id])

  useEffect(() => {
    return loadDetail()
  }, [loadDetail])

  const quickActions = getQuickActions(alert.status)

  const handleSaveNotes = async () => {
    setSavingNotes(true)
    setNotesError(null)
    try {
      const updated = await patchAlert(alert.id, {
        status: alert.status,
        analyst_notes: notes,
      })
      setDetail(updated)
      setNotesDirty(false)
      onNotesSaved(alert.id, notes)
    } catch (err) {
      setNotesError(
        err instanceof Error ? err.message : 'Failed to save notes',
      )
    } finally {
      setSavingNotes(false)
    }
  }

  return (
    <div className="space-y-4 border-t border-charcoal-lighter bg-charcoal/60 px-4 py-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">
            Explainability
          </p>
          <AlertExplainability
            alertId={alert.id}
            emptyMessage="Loading explanation..."
          />
          {detailError && (
            <ErrorBanner
              message={detailError}
              onRetry={loadDetail}
              className="mt-2"
            />
          )}
          {detailLoading && !detail && (
            <LoadingSpinner size="sm" label="Loading alert detail…" className="py-4" />
          )}
        </div>

        <div className="space-y-3">
          {detail?.escalation_history && detail.escalation_history.length > 0 && (
            <div className="rounded border border-amber-soft/30 bg-amber-soft/5 px-3 py-2 text-xs text-amber-soft">
              <p className="font-medium">Escalation history</p>
              <ul className="mt-1 space-y-1 text-[11px] text-amber-soft/80">
                {detail.escalation_history.map((entry, index) => (
                  <li key={`${entry.escalated_at}-${index}`}>
                    {formatRelativeTime(entry.escalated_at)} — score{' '}
                    {entry.previous_risk_score?.toFixed(2) ?? '?'} →{' '}
                    {entry.new_risk_score.toFixed(2)} · confidence{' '}
                    {entry.previous_confidence ?? '?'} → {entry.new_confidence}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <label
              htmlFor={`notes-${alert.id}`}
              className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-gray-500"
            >
              Analyst notes
            </label>
            <textarea
              id={`notes-${alert.id}`}
              value={notes}
              onChange={(e) => {
                setNotes(e.target.value)
                setNotesDirty(true)
              }}
              rows={3}
              placeholder="Add investigation notes..."
              className="w-full resize-y rounded border border-charcoal-lighter bg-charcoal px-3 py-2 text-xs text-gray-200 placeholder:text-gray-600 focus:border-teal-muted/50 focus:outline-none"
            />
            {notesError && (
              <p className="mt-1 text-[11px] text-red-300">{notesError}</p>
            )}
            <button
              type="button"
              onClick={handleSaveNotes}
              disabled={!notesDirty || savingNotes}
              className="mt-2 rounded border border-teal-muted/50 px-3 py-1 text-xs font-medium text-teal-accent hover:bg-teal-muted/10 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {savingNotes ? 'Saving...' : 'Save notes'}
            </button>
          </div>

          {quickActions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {quickActions.map((action) => (
                <button
                  key={action.target}
                  type="button"
                  disabled={busy}
                  onClick={() => onStatusChange(alert.id, action.target, notes)}
                  className={actionButtonClass(action.variant)}
                >
                  {action.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function AlertQueue() {
  const [statusFilter, setStatusFilter] = useState<Set<AlertStatus | 'all'>>(
    () => loadDefaultStatusFilter(),
  )
  const [confidenceFilter, setConfidenceFilter] = useState<
    ConfidenceLevel | 'all'
  >(() => loadDefaultConfidenceFilter())
  const [datePreset, setDatePreset] = useState<DatePreset>('all')
  const [sortBy, setSortBy] = useState<'detected_at' | 'risk_score'>(
    'detected_at',
  )
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)

  const [items, setItems] = useState<Alert[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [actionBusyIds, setActionBusyIds] = useState<Set<number>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const queryParams = useMemo((): GetAlertsParams => {
    const params: GetAlertsParams = {
      page,
      page_size: PAGE_SIZE,
      sort_by: sortBy,
      sort_dir: sortDir,
    }

    const hasAll = statusFilter.has('all')
    if (!hasAll && statusFilter.size > 0) {
      params.statuses = ALL_STATUSES.filter((s) => statusFilter.has(s))
    }

    if (confidenceFilter !== 'all') {
      params.confidence = confidenceFilter
    }

    const detectedAfter = datePresetToIso(datePreset)
    if (detectedAfter) {
      params.detected_after = detectedAfter
    }

    return params
  }, [statusFilter, confidenceFilter, datePreset, page, sortBy, sortDir])

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await getAlerts(queryParams)
      setItems(response.items)
      setTotal(response.pagination.total)
      setTotalPages(response.pagination.total_pages)
      setSelectedIds(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load alerts')
      setItems([])
      setTotal(0)
      setTotalPages(0)
    } finally {
      setLoading(false)
    }
  }, [queryParams])

  useEffect(() => {
    fetchAlerts()
  }, [fetchAlerts])

  const toggleStatus = (value: AlertStatus | 'all') => {
    setPage(1)
    setExpandedId(null)

    if (value === 'all') {
      setStatusFilter(new Set(['all']))
      return
    }

    setStatusFilter((prev) => {
      const next = new Set(prev)
      next.delete('all')

      if (next.has(value)) {
        next.delete(value)
      } else {
        next.add(value)
      }

      if (next.size === 0) {
        return new Set(DEFAULT_STATUS_FILTER)
      }

      return next
    })
  }

  const clearFilters = () => {
    setStatusFilter(loadDefaultStatusFilter())
    setConfidenceFilter(loadDefaultConfidenceFilter())
    setDatePreset('all')
    setSortBy('detected_at')
    setSortDir('desc')
    setPage(1)
    setExpandedId(null)
  }

  const toggleSort = (column: 'detected_at' | 'risk_score') => {
    setPage(1)
    if (sortBy === column) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortBy(column)
      setSortDir('desc')
    }
  }

  const updateAlertInList = (alertId: number, patch: Partial<Alert>) => {
    setItems((prev) =>
      prev.map((item) => (item.id === alertId ? { ...item, ...patch } : item)),
    )
  }

  const handleStatusChange = async (
    alertId: number,
    newStatus: AlertStatus,
    notes?: string,
  ) => {
    const current = items.find((a) => a.id === alertId)
    if (!current || !canTransitionTo(current.status, newStatus)) {
      return
    }

    const previousStatus = current.status
    setActionError(null)
    setActionBusyIds((prev) => new Set(prev).add(alertId))
    updateAlertInList(alertId, { status: newStatus })

    try {
      await patchAlert(alertId, {
        status: newStatus,
        ...(notes !== undefined ? { analyst_notes: notes } : {}),
      })

      if (
        !statusFilter.has('all') &&
        !statusFilter.has(newStatus)
      ) {
        setItems((prev) => prev.filter((a) => a.id !== alertId))
        setTotal((t) => Math.max(0, t - 1))
        if (expandedId === alertId) {
          setExpandedId(null)
        }
      }
    } catch (err) {
      updateAlertInList(alertId, { status: previousStatus })
      setActionError(
        err instanceof Error ? err.message : 'Failed to update alert status',
      )
    } finally {
      setActionBusyIds((prev) => {
        const next = new Set(prev)
        next.delete(alertId)
        return next
      })
    }
  }

  const handleBulkFalsePositive = async () => {
    const eligible = items.filter(
      (a) => selectedIds.has(a.id) && canTransitionTo(a.status, 'false_positive'),
    )
    if (eligible.length === 0) {
      return
    }

    setBulkBusy(true)
    setActionError(null)

    const previousStatuses = new Map(
      eligible.map((a) => [a.id, a.status] as const),
    )

    for (const alert of eligible) {
      updateAlertInList(alert.id, { status: 'false_positive' })
    }

    const results = await Promise.allSettled(
      eligible.map((a) =>
        patchAlert(a.id, { status: 'false_positive' }),
      ),
    )

    const failedIds: number[] = []
    results.forEach((result, index) => {
      const alert = eligible[index]
      if (result.status === 'rejected') {
        failedIds.push(alert.id)
        updateAlertInList(alert.id, {
          status: previousStatuses.get(alert.id)!,
        })
      }
    })

    if (failedIds.length > 0) {
      setActionError(
        `Failed to update ${failedIds.length} alert(s). Changes were rolled back.`,
      )
    }

    const succeededIds = eligible
      .filter((a) => !failedIds.includes(a.id))
      .map((a) => a.id)

    if (
      succeededIds.length > 0 &&
      !statusFilter.has('all') &&
      !statusFilter.has('false_positive')
    ) {
      setItems((prev) => prev.filter((a) => !succeededIds.includes(a.id)))
      setTotal((t) => Math.max(0, t - succeededIds.length))
    }

    setSelectedIds(new Set())
    setBulkBusy(false)
  }

  const selectableOnPage = items.filter((a) =>
    canTransitionTo(a.status, 'false_positive'),
  )
  const allOnPageSelected =
    selectableOnPage.length > 0 &&
    selectableOnPage.every((a) => selectedIds.has(a.id))

  const toggleSelectAll = () => {
    if (allOnPageSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(selectableOnPage.map((a) => a.id)))
    }
  }

  const toggleSelect = (alertId: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(alertId)) {
        next.delete(alertId)
      } else {
        next.add(alertId)
      }
      return next
    })
  }

  const sortIndicator = (column: 'detected_at' | 'risk_score') => {
    if (sortBy !== column) {
      return ''
    }
    return sortDir === 'desc' ? ' ↓' : ' ↑'
  }

  const showingNeedsAttention =
    !statusFilter.has('all') &&
    statusFilter.has('new') &&
    statusFilter.has('reviewing') &&
    statusFilter.size === 2

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-100">Alert Queue</h1>
          <p className="mt-1 text-sm text-gray-500">
            {loading
              ? 'Loading alerts...'
              : `${total.toLocaleString()} alert${total === 1 ? '' : 's'} matching filters`}
            {showingNeedsAttention && !loading && (
              <span className="text-teal-accent">
                {' '}
                · needs-attention view (new + reviewing)
              </span>
            )}
          </p>
        </div>

        {selectedIds.size > 0 && (
          <button
            type="button"
            onClick={handleBulkFalsePositive}
            disabled={bulkBusy}
            className="shrink-0 rounded border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 hover:bg-charcoal-lighter disabled:opacity-40"
          >
            {bulkBusy
              ? 'Updating...'
              : `Mark ${selectedIds.size} False Positive`}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-charcoal-lighter bg-charcoal-light p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[10px] font-medium uppercase tracking-wide text-gray-500">
            Status
          </span>
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => toggleStatus(opt.value)}
              className={filterChipClass(statusFilter.has(opt.value))}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="h-4 w-px bg-charcoal-lighter" />

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[10px] font-medium uppercase tracking-wide text-gray-500">
            Confidence
          </span>
          {CONFIDENCE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                setConfidenceFilter(opt.value)
                setPage(1)
              }}
              className={filterChipClass(confidenceFilter === opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="h-4 w-px bg-charcoal-lighter" />

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[10px] font-medium uppercase tracking-wide text-gray-500">
            Detected
          </span>
          {DATE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                setDatePreset(opt.value)
                setPage(1)
              }}
              className={filterChipClass(datePreset === opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={clearFilters}
          className="ml-auto text-xs text-gray-500 hover:text-gray-300"
        >
          Clear filters
        </button>
      </div>

      {actionError && (
        <p className="rounded border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </p>
      )}

      {error && (
        <ErrorBanner message={error} onRetry={fetchAlerts} />
      )}

      <div className="min-h-[320px] flex-1 overflow-hidden rounded-lg border border-charcoal-lighter bg-charcoal-light">
        {loading ? (
          <TableSkeleton rows={8} columns={7} />
        ) : items.length === 0 && !error ? (
          <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
            <p className="text-lg font-medium text-gray-300">All caught up</p>
            <p className="mt-2 max-w-sm text-sm text-gray-500">
              No alerts match your current filters. Try widening the status or
              date range, or check back after the next detection cycle.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-charcoal-lighter text-[10px] uppercase tracking-wide text-gray-500">
                  <th className="w-10 px-3 py-3">
                    <input
                      type="checkbox"
                      checked={allOnPageSelected}
                      onChange={toggleSelectAll}
                      aria-label="Select all on page"
                      className="rounded border-gray-600 bg-charcoal"
                    />
                  </th>
                  <th className="px-3 py-3 font-medium">Account</th>
                  <th className="px-3 py-3 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleSort('risk_score')}
                      className="hover:text-gray-300"
                    >
                      Risk{sortIndicator('risk_score')}
                    </button>
                  </th>
                  <th className="px-3 py-3 font-medium">Confidence</th>
                  <th className="px-3 py-3 font-medium">Pattern</th>
                  <th className="px-3 py-3 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleSort('detected_at')}
                      className="hover:text-gray-300"
                    >
                      Detected{sortIndicator('detected_at')}
                    </button>
                  </th>
                  <th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((alert) => {
                  const isExpanded = expandedId === alert.id
                  const quickActions = getQuickActions(alert.status)
                  const isBusy = actionBusyIds.has(alert.id)
                  const canSelect = canTransitionTo(
                    alert.status,
                    'false_positive',
                  )

                  return (
                    <Fragment key={alert.id}>
                      <tr
                        role="button"
                        tabIndex={0}
                        onClick={() =>
                          setExpandedId(isExpanded ? null : alert.id)
                        }
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            setExpandedId(isExpanded ? null : alert.id)
                          }
                        }}
                        className={[
                          'cursor-pointer border-b border-charcoal-lighter/60 transition-colors hover:bg-charcoal-lighter/40',
                          isExpanded ? 'bg-charcoal-lighter/30' : '',
                        ].join(' ')}
                      >
                        <td
                          className="px-3 py-3"
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={selectedIds.has(alert.id)}
                            disabled={!canSelect}
                            onChange={() => toggleSelect(alert.id)}
                            aria-label={`Select alert ${alert.id}`}
                            className="rounded border-gray-600 bg-charcoal disabled:opacity-30"
                          />
                        </td>

                        <td className="px-3 py-3">
                          <div className="flex items-center gap-2">
                            <Link
                              to={`/accounts/${encodeURIComponent(alert.account.account_id)}`}
                              onClick={(e) => e.stopPropagation()}
                              className="truncate font-medium text-teal-accent hover:underline"
                            >
                              {alert.account.account_id}
                            </Link>
                            {alert.is_escalated && (
                              <span className="shrink-0 rounded border border-amber-soft/40 bg-amber-soft/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-amber-soft">
                                ↑ escalated
                              </span>
                            )}
                          </div>
                        </td>

                        <td className="px-3 py-3 text-gray-300">
                          {alert.risk_score.toFixed(2)}
                        </td>

                        <td className="px-3 py-3">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <ConfidenceBadge confidence={alert.confidence} />
                            {isPeripheralStructural(alert.pattern_type) && (
                              <PeripheralStructuralBadge compact />
                            )}
                          </div>
                        </td>

                        <td className="max-w-[160px] truncate px-3 py-3 text-gray-400">
                          {formatPatternType(alert.pattern_type)}
                        </td>

                        <td className="px-3 py-3 text-gray-500">
                          {formatRelativeTime(alert.detected_at)}
                        </td>

                        <td className="px-3 py-3">
                          <StatusBadge status={alert.status} />
                        </td>

                        <td
                          className="px-3 py-3"
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => e.stopPropagation()}
                        >
                          <div className="flex flex-wrap gap-1.5">
                            {quickActions.map((action) => (
                              <button
                                key={action.target}
                                type="button"
                                disabled={isBusy}
                                onClick={() =>
                                  handleStatusChange(alert.id, action.target)
                                }
                                className={actionButtonClass(action.variant)}
                              >
                                {action.label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr className="border-b border-charcoal-lighter/60">
                          <td colSpan={8} className="p-0">
                            <AlertRowDetail
                              alert={alert}
                              onStatusChange={handleStatusChange}
                              onNotesSaved={() => {}}
                              busy={isBusy}
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
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            Page {page} of {totalPages} · {PAGE_SIZE} per page
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="rounded border border-charcoal-lighter px-3 py-1 hover:bg-charcoal-lighter disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="rounded border border-charcoal-lighter px-3 py-1 hover:bg-charcoal-lighter disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
