import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { LiveAlertMessage } from '../api/types'
import { formatPatternType, formatRelativeTime, isPeripheralStructural } from '../utils/format'
import AlertExplainability from './AlertExplainability'
import ConfidenceBadge from './ConfidenceBadge'
import MaterialIcon from './MaterialIcon'
import PeripheralStructuralBadge from './PeripheralStructuralBadge'

import RiskScorePill from './RiskScorePill'

interface AlertFeedPanelProps {
  alerts: LiveAlertMessage[]
}

function riskBorderClass(score: number): string {
  if (score >= 4) return 'border-l-error'
  if (score >= 2.5) return 'border-l-tertiary'
  return 'border-l-primary'
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
    <div className="glass-panel flex h-full flex-col overflow-hidden rounded-2xl">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-primary/10 bg-surface/60 px-4 py-3 backdrop-blur-md">
        <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
          <MaterialIcon name="list_alt" className="text-tertiary" size={18} />
          Live Feed
        </h2>
        <span className="rounded-full bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
          Real-time
        </span>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {sortedAlerts.length === 0 ? (
          <p className="px-2 py-8 text-center text-xs text-on-surface-variant">
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
                  'glass-panel w-full cursor-pointer rounded-lg border-l-2 p-3 text-left transition-all duration-300',
                  riskBorderClass(data.risk_score),
                  isSelected
                    ? 'border-primary/30 bg-primary/5 ring-1 ring-primary/20'
                    : 'hover:bg-surface-bright/40',
                ].join(' ')}
                style={{ animation: 'fadeSlideIn 0.35s ease-out' }}
              >
                <div className="mb-1 flex items-start justify-between gap-2">
                  <RiskScorePill
                    score={data.risk_score}
                    confidence={data.confidence}
                    compact
                  />
                  <span className="font-mono text-[10px] text-on-surface-variant">
                    {formatRelativeTime(alert.timestamp)}
                  </span>
                </div>
                <div className="group flex items-start justify-between gap-2">
                  <Link
                    to={`/accounts/${encodeURIComponent(data.account_id)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="truncate text-sm font-medium text-on-surface transition-colors hover:text-primary"
                  >
                    {data.account_id}
                  </Link>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                    {isStructural && <PeripheralStructuralBadge compact />}
                    <ConfidenceBadge confidence={data.confidence} />
                  </div>
                </div>
                <p className="mt-1 truncate text-xs text-on-surface-variant">
                  {formatPatternType(data.pattern_type)}
                </p>
                <p className="mt-0.5 text-[10px] uppercase tracking-wide text-on-surface-variant/70">
                  {data.action}
                </p>
              </div>
            )
          })
        )}
      </div>

      <div className="border-t border-primary/10 p-4">
        <p className="mb-2 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
          <MaterialIcon name="psychology" size={14} />
          Explainability
        </p>
        <AlertExplainability alertId={selectedAlertId} />
      </div>

      <div className="border-t border-primary/10 px-4 py-3">
        <Link
          to="/alerts"
          className="text-xs font-medium text-primary transition-colors hover:text-primary/80"
        >
          View all alerts →
        </Link>
      </div>
    </div>
  )
}
