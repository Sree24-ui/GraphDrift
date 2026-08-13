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
import PeripheralStructuralBadge from '../components/PeripheralStructuralBadge'

// Top 5% of fused scale (0–5) — mirrors ALERT_TOP_PERCENTILE in fusion.py
const ALERT_THRESHOLD_SCORE = 4.75

const TX_PAGE_SIZE = 15

type TxSortKey = 'timestamp' | 'amount'
type TxSortDir = 'asc' | 'desc'

function ScoreTrendChart({ points }: { points: ScoreHistoryPoint[] }) {
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
      <p className="py-8 text-center text-sm text-gray-500">
        No score history yet — scores are recorded each detection cycle after the
        account appears in the graph.
      </p>
    )
  }

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#2a3238" strokeDasharray="3 3" />
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
            stroke="#6b7280"
            fontSize={10}
            tickLine={false}
          />
          <YAxis
            domain={[0, 5]}
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
              payload?.[0]?.payload?.label ?? ''
            }
            formatter={(value) => [
              typeof value === 'number' ? value.toFixed(2) : value,
              'Risk score',
            ]}
          />
          <ReferenceLine
            y={ALERT_THRESHOLD_SCORE}
            stroke="#d4a574"
            strokeDasharray="6 4"
            label={{
              value: `Alert threshold (${ALERT_THRESHOLD_SCORE})`,
              position: 'insideTopRight',
              fill: '#d4a574',
              fontSize: 10,
            }}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#4a8f8f"
            strokeWidth={2}
            dot={chartData.length <= 24}
            activeDot={{ r: 4, fill: '#4a8f8f' }}
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
    <div className="rounded-lg border border-charcoal-lighter bg-charcoal-light">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-charcoal-lighter/40"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-gray-200">
              Alert #{alert.id}
            </span>
            <StatusBadge status={alert.status} />
            <ConfidenceBadge confidence={alert.confidence} />
            {isPeripheralStructural(alert.pattern_type) && (
              <PeripheralStructuralBadge compact />
            )}
            {alert.is_escalated && (
              <span className="rounded border border-amber-soft/40 bg-amber-soft/10 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-amber-soft">
                ↑ escalated
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-xs text-gray-500">
            {formatPatternType(alert.pattern_type)} · risk{' '}
            {alert.risk_score.toFixed(2)} · {formatRelativeTime(alert.detected_at)}
          </p>
        </div>
        <span className="text-xs text-gray-600">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="border-t border-charcoal-lighter px-4 py-3">
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

  const fetchAccountPage = useCallback(async () => {
    if (!accountId) {
      return
    }

    setLoading(true)
    setError(null)

    try {
      const [detail, history, alertResponse, openNew, openReviewing] =
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
        ])

      setAccount(detail)
      setScoreHistory(history.points)
      setAlerts(alertResponse.items)

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
      <p className="text-sm text-gray-500">No account ID provided in the URL.</p>
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
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-medium uppercase tracking-wide text-gray-500">
            Account
          </p>
          <h1 className="mt-1 break-all text-xl font-semibold text-gray-100">
            {account.account_id}
          </h1>
          <dl className="mt-3 grid gap-2 text-xs text-gray-400 sm:grid-cols-2">
            <div>
              <dt className="text-gray-600">Created</dt>
              <dd className="text-gray-300">
                {formatDateTime(account.created_at)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-600">Last active</dt>
              <dd className="text-gray-300">
                {formatDateTime(account.last_active_at)}{' '}
                <span className="text-gray-600">
                  ({formatRelativeTime(account.last_active_at)})
                </span>
              </dd>
            </div>
          </dl>
          {hasActiveScore && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-500">Current risk</span>
              <span className="text-sm font-medium tabular-nums text-gray-200">
                {account.fused_score!.toFixed(2)}
              </span>
              <ConfidenceBadge confidence={account.confidence!} />
            </div>
          )}
        </div>

        <Link
          to={`/?focus=${encodeURIComponent(account.account_id)}`}
          className="rounded border border-teal-muted/50 bg-teal-muted/10 px-4 py-2 text-sm font-medium text-teal-accent hover:bg-teal-muted/20"
        >
          Open in Live Monitor →
        </Link>
      </div>

      <section className="rounded-lg border border-charcoal-lighter bg-charcoal-light p-4">
        <h2 className="text-sm font-medium text-gray-200">Risk score trend</h2>
        <p className="mt-1 text-xs text-gray-500">
          Combined risk score over detection cycles (0–5). Dashed line = top-5%
          alert threshold.
        </p>
        <div className="mt-4">
          <ScoreTrendChart points={scoreHistory} />
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-medium text-gray-200">Alert history</h2>
          <p className="text-xs text-gray-500">
            All cases tied to this account, most recent first.
          </p>
        </div>
        {alerts.length === 0 ? (
          <p className="rounded-lg border border-charcoal-lighter bg-charcoal-light px-4 py-6 text-center text-sm text-gray-500">
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

      <section className="rounded-lg border border-charcoal-lighter bg-charcoal-light">
        <div className="border-b border-charcoal-lighter px-4 py-3">
          <h2 className="text-sm font-medium text-gray-200">
            Transaction history
          </h2>
          <p className="text-xs text-gray-500">
            {pagination.total.toLocaleString()} total transaction
            {pagination.total === 1 ? '' : 's'}
          </p>
        </div>

        {sortedTransactions.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-gray-500">
            No transactions recorded for this account yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-charcoal-lighter text-[10px] uppercase tracking-wide text-gray-500">
                  <th className="px-4 py-2 font-medium">Direction</th>
                  <th className="px-4 py-2 font-medium">Counterparty</th>
                  <th className="px-4 py-2 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleTxSort('amount')}
                      className="hover:text-gray-300"
                    >
                      Amount{sortIndicator('amount')}
                    </button>
                  </th>
                  <th className="px-4 py-2 font-medium">
                    <button
                      type="button"
                      onClick={() => toggleTxSort('timestamp')}
                      className="hover:text-gray-300"
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
                    className="border-b border-charcoal-lighter/60 text-gray-300"
                  >
                    <td className="px-4 py-2 capitalize text-gray-400">
                      {tx.direction}
                    </td>
                    <td className="px-4 py-2">
                      <Link
                        to={`/accounts/${encodeURIComponent(tx.counterparty_id)}`}
                        className="text-teal-accent hover:underline"
                      >
                        {tx.counterparty_id}
                      </Link>
                    </td>
                    <td className="px-4 py-2 tabular-nums">
                      {formatCurrency(tx.amount)}
                    </td>
                    <td className="px-4 py-2 text-gray-500">
                      {formatDateTime(tx.timestamp)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pagination.total_pages > 1 && (
          <div className="flex items-center justify-between border-t border-charcoal-lighter px-4 py-3 text-xs text-gray-500">
            <span>
              Page {pagination.page} of {pagination.total_pages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={txPage <= 1}
                onClick={() => setTxPage((p) => Math.max(1, p - 1))}
                className="rounded border border-charcoal-lighter px-3 py-1 hover:bg-charcoal-lighter disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={txPage >= pagination.total_pages}
                onClick={() =>
                  setTxPage((p) => Math.min(pagination.total_pages, p + 1))
                }
                className="rounded border border-charcoal-lighter px-3 py-1 hover:bg-charcoal-lighter disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-charcoal-lighter bg-charcoal-light p-4">
        <h2 className="text-sm font-medium text-gray-200">Connected accounts</h2>
        <p className="mt-1 text-xs text-gray-500">
          Distinct counterparties in the recent activity window.
        </p>
        {account.connected_accounts.length === 0 ? (
          <p className="mt-4 text-sm text-gray-500">
            No connected accounts in the current window.
          </p>
        ) : (
          <ul className="mt-3 space-y-1">
            {account.connected_accounts.map((peerId) => {
              const hasActiveAlert = activeAlertAccountIds.has(peerId)
              return (
                <li key={peerId}>
                  <Link
                    to={`/accounts/${encodeURIComponent(peerId)}`}
                    className="flex items-center justify-between rounded px-2 py-1.5 text-sm text-teal-accent hover:bg-charcoal-lighter/60 hover:underline"
                  >
                    <span className="truncate">{peerId}</span>
                    {hasActiveAlert && (
                      <span className="ml-2 shrink-0 rounded border border-amber-soft/40 bg-amber-soft/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-amber-soft">
                        active alert
                      </span>
                    )}
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
