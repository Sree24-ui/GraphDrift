import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  getAccountDetail,
  getAccountScoreHistory,
  getAlerts,
  getSettings,
} from '../api/client'
import type {
  AccountDetail,
  Alert,
  ScoreHistoryPoint,
  TransactionItem,
} from '../api/types'
import AlertExplainability from '../components/AlertExplainability'
import ConfidenceBadge from '../components/ConfidenceBadge'
import ErrorBanner from '../components/ErrorBanner'
import LoadingSpinner from '../components/LoadingSpinner'
import StatusBadge from '../components/StatusBadge'
import {
  formatCurrency,
  formatDateTime,
  formatPatternType,
  formatRelativeTime,
  isPeripheralStructural,
} from '../utils/format'
import MaterialIcon from '../components/MaterialIcon'
import PeripheralStructuralBadge from '../components/PeripheralStructuralBadge'
import { GDI_MAX } from '../knobs'

const TX_PAGE_SIZE = 15
const FUSED_SCORE_SCALE = GDI_MAX

type TxSortKey = 'timestamp' | 'amount'
type TxSortDir = 'asc' | 'desc'

function ScoreTrendChart({
  points,
  thresholdScore,
}: {
  points: ScoreHistoryPoint[]
  thresholdScore: number | null
}) {
  const chartData = useMemo(
    () =>
      points.map((point) => ({
        time: new Date(point.recorded_at).getTime(),
        label: formatDateTime(point.recorded_at),
        score: point.score,
      })),
    [points],
  )

  if (chartData.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-on-surface-variant">
        No score history yet — scores are recorded each detection cycle after the
        account appears in the graph.
      </p>
    )
  }

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#2a3a48" strokeDasharray="3 3" />
          <XAxis
            dataKey="time"
            type="number"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(value) =>
              new Date(value).toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
              })
            }
            stroke="#a0b4c4"
            fontSize={10}
            tickLine={false}
          />
          <YAxis
            domain={[0, 5]}
            stroke="#a0b4c4"
            fontSize={10}
            tickLine={false}
            width={28}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#0f1524',
              border: '1px solid #2a3a48',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(_, payload) =>
              payload?.[0]?.payload?.label ?? ''
            }
            formatter={(value) => [
              typeof value === 'number' ? value.toFixed(2) : value,
              'Risk score',
            ]}
          />
          {thresholdScore != null && (
            <ReferenceLine
              y={thresholdScore}
              stroke="#c8a0f0"
              strokeDasharray="6 4"
              label={{
                value: `Alert threshold (${thresholdScore.toFixed(2)})`,
                position: 'insideTopRight',
                fill: '#c8a0f0',
                fontSize: 10,
              }}
            />
          )}
          <Line
            type="monotone"
            dataKey="score"
            stroke="#7dd3fc"
            strokeWidth={2}
            dot={chartData.length <= 24}
            activeDot={{ r: 4, fill: '#7dd3fc' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

interface AlertHistoryItemProps {
  alert: Alert
  expanded: boolean
  onToggle: () => void
}

function AlertHistoryItem({ alert, expanded, onToggle }: AlertHistoryItemProps) {
  return (
    <div className="glass-panel rounded-xl">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-primary/5"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-on-surface">
              Alert #{alert.id}
            </span>
            <StatusBadge status={alert.status} />
            <ConfidenceBadge confidence={alert.confidence} />
            {isPeripheralStructural(alert.pattern_type) && (
              <PeripheralStructuralBadge compact />
            )}
            {alert.is_escalated && (
              <span className="rounded border border-tertiary/40 bg-tertiary/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-tertiary">
                ↑ escalated
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-xs text-on-surface-variant">
            {formatPatternType(alert.pattern_type)} · risk{' '}
            {alert.risk_score.toFixed(2)} · {formatRelativeTime(alert.detected_at)}
          </p>
        </div>
        <span className="text-xs text-on-surface-variant">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="border-t border-primary/10 px-4 py-3">
          <AlertExplainability alertId={alert.id} />
        </div>
      )}
    </div>
  )
}

export default function AccountDetail() {
  const { accountId: rawAccountId } = useParams<{ accountId: string }>()
  const accountId = rawAccountId ? decodeURIComponent(rawAccountId) : ''

  const [account, setAccount] = useState<AccountDetail | null>(null)
  const [scoreHistory, setScoreHistory] = useState<ScoreHistoryPoint[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [activeAlertAccountIds, setActiveAlertAccountIds] = useState<Set<string>>(
    new Set(),
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [txPage, setTxPage] = useState(1)
  const [txSortKey, setTxSortKey] = useState<TxSortKey>('timestamp')
  const [txSortDir, setTxSortDir] = useState<TxSortDir>('desc')
  const [expandedAlertId, setExpandedAlertId] = useState<number | null>(null)
  const [alertThresholdScore, setAlertThresholdScore] = useState<number | null>(
    null,
  )

  const fetchAccountPage = useCallback(async () => {
    if (!accountId) {
      return
    }

    setLoading(true)
    setError(null)

    try {
      const [detail, history, alertResponse, openNew, openReviewing, settings] =
        await Promise.all([
          getAccountDetail(accountId, { page: txPage, page_size: TX_PAGE_SIZE }),
          getAccountScoreHistory(accountId),
          getAlerts({
            account_id: accountId,
            page_size: 100,
            sort_by: 'detected_at',
            sort_dir: 'desc',
          }),
          getAlerts({ status: 'new', page_size: 100 }),
          getAlerts({ status: 'reviewing', page_size: 100 }),
          getSettings(),
        ])

      setAccount(detail)
      setScoreHistory(history.points)
      setAlerts(alertResponse.items)
      setAlertThresholdScore(settings.alert_top_percentile * FUSED_SCORE_SCALE)

      const activeIds = new Set<string>()
      for (const item of [...openNew.items, ...openReviewing.items]) {
        activeIds.add(item.account.account_id)
      }
      setActiveAlertAccountIds(activeIds)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to load account detail',
      )
      setAccount(null)
    } finally {
      setLoading(false)
    }
  }, [accountId, txPage])

  useEffect(() => {
    fetchAccountPage()
  }, [fetchAccountPage])

  const sortedTransactions = useMemo(() => {
    if (!account) {
      return []
    }

    const rows = [...account.transactions]
    rows.sort((a: TransactionItem, b: TransactionItem) => {
      let cmp = 0
      if (txSortKey === 'timestamp') {
        cmp =
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      } else {
        cmp = a.amount - b.amount
      }
      return txSortDir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [account, txSortKey, txSortDir])

  const toggleTxSort = (key: TxSortKey) => {
    if (txSortKey === key) {
      setTxSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setTxSortKey(key)
      setTxSortDir('desc')
    }
  }

  const sortIndicator = (key: TxSortKey) => {
    if (txSortKey !== key) {
      return ''
    }
    return txSortDir === 'desc' ? ' ↓' : ' ↑'
  }

  if (!accountId) {
    return (
      <p className="text-sm text-on-surface-variant">No account ID provided in the URL.</p>
    )
  }

  if (loading && !account) {
    return <LoadingSpinner label="Loading account…" className="py-16" />
  }

  if (error || !account) {
    return (
      <div className="max-w-lg">
        <ErrorBanner
          message={error ?? 'Account not found'}
          onRetry={fetchAccountPage}
        />
      </div>
    )
  }

  const { pagination } = account
  const hasActiveScore =
    account.fused_score != null && account.confidence != null

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto">
      <header className="glass-panel sticky top-0 z-20 flex flex-wrap items-center justify-between gap-4 rounded-xl px-4 py-4 shadow-sm md:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-4">
          <Link
            to="/alerts"
            className="group flex items-center gap-2 text-on-surface-variant transition-colors hover:text-primary"
          >
            <MaterialIcon
              name="arrow_back"
              size={18}
              className="transition-transform group-hover:-translate-x-1"
            />
            <span className="text-sm font-medium tracking-wide">
              Back to Alert Queue
            </span>
          </Link>
          <div className="hidden h-6 w-px bg-primary/20 sm:block" />
          <div className="min-w-0">
            <h1 className="truncate font-headline text-lg font-bold tracking-tight text-primary">
              {account.account_id}
            </h1>
            <p className="text-xs text-on-surface-variant">
              Created {formatDateTime(account.created_at)}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {hasActiveScore && (
            <span className="flex items-center gap-1 rounded-full border border-error/20 bg-error/10 px-3 py-1 text-xs font-semibold text-error shadow-[0_0_15px_rgba(255,107,107,0.1)]">
              <MaterialIcon name="warning" size={14} />
              {account.confidence === 'high' ? 'High' : account.confidence} Risk
            </span>
          )}
          <Link
            to={`/?focus=${encodeURIComponent(account.account_id)}`}
            className="rounded-lg border border-primary/20 bg-primary/10 px-4 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/20 hover:shadow-primary-glow"
          >
            Open in Live Monitor
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
        <div className="flex flex-col gap-6 lg:col-span-8">
      <section className="glass-panel-elevated rounded-xl p-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="font-headline text-lg font-semibold text-primary">
              Risk Trend Analysis
            </h2>
            <p className="text-sm text-on-surface-variant">
              Detection-cycle risk scoring (0–5 scale)
            </p>
          </div>
          {hasActiveScore && (
            <div className="text-right">
              <p className="text-xs text-on-surface-variant">Current score</p>
              <p className="text-xl font-semibold tabular-nums text-on-surface">
                {account.fused_score!.toFixed(2)}
              </p>
            </div>
          )}
        </div>
        <ScoreTrendChart
          points={scoreHistory}
          thresholdScore={alertThresholdScore}
        />
      </section>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <section className="glass-panel rounded-xl p-6">
          <h3 className="mb-4 flex items-center gap-2 font-headline font-semibold text-on-surface">
            <MaterialIcon name="account_circle" className="text-tertiary" size={20} />
            Account Profile
          </h3>
          <ul className="space-y-3 text-sm">
            <li>
              <p className="text-xs uppercase tracking-wider text-on-surface-variant">
                Last active
              </p>
              <p className="font-medium text-on-surface">
                {formatRelativeTime(account.last_active_at)}
              </p>
            </li>
            <li>
              <p className="text-xs uppercase tracking-wider text-on-surface-variant">
                Open alerts
              </p>
              <p className="font-medium text-on-surface">{alerts.length}</p>
            </li>
          </ul>
        </section>
        <section className="glass-panel rounded-xl p-6">
          <h3 className="mb-4 flex items-center gap-2 font-headline font-semibold text-on-surface">
            <MaterialIcon name="policy" className="text-tertiary" size={20} />
            Risk Status
          </h3>
          <div className="space-y-2">
            {hasActiveScore ? (
              <div className="flex items-center justify-between rounded border border-error/20 bg-error/5 p-2">
                <span className="text-sm text-on-surface-variant">Flagged</span>
                <ConfidenceBadge confidence={account.confidence!} />
              </div>
            ) : (
              <div className="flex items-center justify-between rounded border border-primary/10 bg-surface-container/50 p-2">
                <span className="text-sm text-on-surface-variant">Current status</span>
                <span className="text-xs font-semibold text-primary">CLEAR</span>
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-medium text-on-surface">Alert history</h2>
          <p className="text-xs text-on-surface-variant">
            All cases tied to this account, most recent first.
          </p>
        </div>
        {alerts.length === 0 ? (
          <p className="glass-panel rounded-xl px-4 py-6 text-center text-sm text-on-surface-variant">
            No alerts on record for this account.
          </p>
        ) : (
          <div className="space-y-2">
            {alerts.map((alert) => (
              <AlertHistoryItem
                key={alert.id}
                alert={alert}
                expanded={expandedAlertId === alert.id}
                onToggle={() =>
                  setExpandedAlertId((id) =>
                    id === alert.id ? null : alert.id,
                  )
                }
              />
            ))}
          </div>
        )}
      </section>

      <section className="glass-panel rounded-xl">
        <div className="border-b border-primary/10 px-4 py-3">
          <h2 className="text-sm font-medium text-on-surface">
            Transaction history
          </h2>
          <p className="text-xs text-on-surface-variant">
            {pagination.total.toLocaleString()} total transaction
            {pagination.total === 1 ? '' : 's'}
          </p>
        </div>

        {sortedTransactions.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-on-surface-variant">
            No transactions recorded for this account yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-primary/10 text-[10px] uppercase tracking-wide text-on-surface-variant">
                  <th className="px-4 py-2 font-medium">Direction</th>
                  <th className="px-4 py-2 font-medium">Counterparty</th>
                  <th className="px-4 py-2 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleTxSort('amount')}
                      className="hover:text-on-surface"
                    >
                      Amount{sortIndicator('amount')}
                    </button>
                  </th>
                  <th className="px-4 py-2 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleTxSort('timestamp')}
                      className="hover:text-on-surface"
                    >
                      Time{sortIndicator('timestamp')}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedTransactions.map((tx) => (
                  <tr
                    key={tx.id}
                    className="border-b border-primary/10/60 text-on-surface"
                  >
                    <td className="px-4 py-2 capitalize text-on-surface-variant">
                      {tx.direction}
                    </td>
                    <td className="px-4 py-2">
                      <Link
                        to={`/accounts/${encodeURIComponent(tx.counterparty_id)}`}
                        className="text-primary hover:underline"
                      >
                        {tx.counterparty_id}
                      </Link>
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {formatCurrency(tx.amount)}
                    </td>
                    <td className="px-4 py-2 text-on-surface-variant">
                      {formatDateTime(tx.timestamp)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pagination.total_pages > 1 && (
          <div className="flex items-center justify-between border-t border-primary/10 px-4 py-3 text-xs text-on-surface-variant">
            <span>
              Page {pagination.page} of {pagination.total_pages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={txPage <= 1}
                onClick={() => setTxPage((p) => Math.max(1, p - 1))}
                className="rounded border border-primary/10 px-3 py-1 hover:bg-primary/5 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={txPage >= pagination.total_pages}
                onClick={() =>
                  setTxPage((p) => Math.min(pagination.total_pages, p + 1))
                }
                className="rounded border border-primary/10 px-3 py-1 hover:bg-primary/5 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </section>
        </div>

        <aside className="lg:col-span-4">
          <section className="glass-panel-elevated flex h-full flex-col rounded-xl">
            <div className="border-b border-primary/10 p-6">
              <h2 className="flex items-center gap-2 font-headline font-semibold text-primary">
                <MaterialIcon name="hub" className="text-tertiary" size={20} />
                Connected Entities
              </h2>
              <p className="mt-1 text-xs text-on-surface-variant">
                Counterparties in the recent activity window
              </p>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {account.connected_accounts.length === 0 ? (
                <p className="text-sm text-on-surface-variant">
                  No connected accounts in the current window.
                </p>
              ) : (
                account.connected_accounts.map((peerId) => {
                  const hasActiveAlert = activeAlertAccountIds.has(peerId)
                  return (
                    <Link
                      key={peerId}
                      to={`/accounts/${encodeURIComponent(peerId)}`}
                      className="block rounded-lg border border-white/5 bg-surface-container/40 p-3 transition-colors hover:border-primary/30"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10 text-primary">
                            <MaterialIcon name="account_circle" size={16} />
                          </div>
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-on-surface hover:text-primary">
                              {peerId}
                            </p>
                            <p className="text-[10px] text-on-surface-variant">
                              1st degree · UPI account
                            </p>
                          </div>
                        </div>
                        {hasActiveAlert && (
                          <span className="shrink-0 rounded bg-error/10 px-1.5 py-0.5 text-[10px] font-bold text-error">
                            Alert
                          </span>
                        )}
                      </div>
                    </Link>
                  )
                })
              )}
            </div>
            <div className="border-t border-primary/10 bg-surface/40 p-4">
              <Link
                to={`/?focus=${encodeURIComponent(account.account_id)}`}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-primary/20 bg-primary/10 py-2 text-sm font-medium text-primary transition-colors hover:bg-primary/20"
              >
                <MaterialIcon name="bubble_chart" size={18} />
                Expand Graph View
              </Link>
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
