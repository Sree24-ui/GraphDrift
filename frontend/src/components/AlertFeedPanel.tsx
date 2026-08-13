import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { LiveAlertMessage } from '../api/types'
import { formatPatternType, formatRelativeTime, isPeripheralStructural } from '../utils/format'
import AlertExplainability from './AlertExplainability'
import ConfidenceBadge from './ConfidenceBadge'
import PeripheralStructuralBadge from './PeripheralStructuralBadge'

interface AlertFeedPanelProps {
  alerts: LiveAlertMessage[]
}

export default function AlertFeedPanel({ alerts }: AlertFeedPanelProps) {
  const [selectedAlertId, setSelectedAlertId] = useState<number | null>(null)

  const sortedAlerts = useMemo(
    () =>
      [...alerts].sort(
        (a, b) =>
          new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
      ),
    [alerts],
  )

  useEffect(() => {
    if (sortedAlerts.length > 0 && selectedAlertId === null) {
      setSelectedAlertId(sortedAlerts[0].data.id)
    }
  }, [sortedAlerts, selectedAlertId])

  return (
    <div className="flex h-full flex-col rounded-lg border border-charcoal-lighter bg-charcoal-light">
      <div className="border-b border-charcoal-lighter px-4 py-3">
        <h2 className="text-sm font-medium text-gray-200">Live Alerts</h2>
        <p className="text-xs text-gray-500">{sortedAlerts.length} in buffer</p>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto p-2">
        {sortedAlerts.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-gray-500">
            No alerts yet — detection runs every few minutes. New cases will
            appear here as they are raised.
          </p>
        ) : (
          sortedAlerts.map((alert) => {
            const { data } = alert
            const isSelected = selectedAlertId === data.id
            const isStructural = isPeripheralStructural(data.pattern_type)

            return (
              <div
                key={`${data.id}-${alert.timestamp}`}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedAlertId(data.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setSelectedAlertId(data.id)
                  }
                }}
                className={[
                  'w-full cursor-pointer rounded-md border px-3 py-2 text-left transition-all duration-300',
                  isSelected
                    ? 'border-teal-muted/50 bg-charcoal-lighter'
                    : 'border-transparent bg-charcoal hover:border-charcoal-lighter hover:bg-charcoal-lighter/60',
                ].join(' ')}
                style={{ animation: 'fadeSlideIn 0.35s ease-out' }}
              >
                <div className="flex items-start justify-between gap-2">
                  <Link
                    to={`/accounts/${encodeURIComponent(data.account_id)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="truncate text-sm font-medium text-teal-accent hover:underline"
                  >
                    {data.account_id}
                  </Link>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                    {isStructural && (
                      <PeripheralStructuralBadge compact />
                    )}
                    <ConfidenceBadge confidence={data.confidence} />
                  </div>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="truncate text-[11px] text-gray-500">
                    {formatPatternType(data.pattern_type)}
                  </span>
                  <span className="shrink-0 text-[10px] text-gray-600">
                    {formatRelativeTime(alert.timestamp)}
                  </span>
                </div>
                <p className="mt-0.5 text-[10px] uppercase tracking-wide text-gray-600">
                  {data.action} · risk {data.risk_score.toFixed(2)}
                </p>
              </div>
            )
          })
        )}
      </div>

      <div className="border-t border-charcoal-lighter p-4">
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-gray-500">
          Explainability
        </p>
        <AlertExplainability alertId={selectedAlertId} />
      </div>

      <div className="border-t border-charcoal-lighter px-4 py-3">
        <Link
          to="/alerts"
          className="text-xs font-medium text-teal-accent hover:text-teal-muted"
        >
          View all alerts →
        </Link>
      </div>
    </div>
  )
}
