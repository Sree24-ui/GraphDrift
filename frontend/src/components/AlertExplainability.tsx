import { useCallback, useEffect, useState } from 'react'

import { getAlertDetail } from '../api/client'
import type { AlertDetail } from '../api/types'
import ErrorBanner from './ErrorBanner'

interface Layer1Factor {
  feature: string
  contribution_pct: number
  z_score: number
}

interface Layer2Detail {
  community_id: number
  hub_account_id?: string | null
  hub_concentration: number
  member_count: number
  ring_risk_score: number
  reason: string
  is_hub_account?: boolean
}

interface PeripheralDetail {
  linked_hub_account_id: string
  leg_pattern: string
  tx_count: number
  in_degree: number
  out_degree: number
}

interface AlertExplainabilityProps {
  alertId: number | null
  emptyMessage?: string
  className?: string
}

export default function AlertExplainability({
  alertId,
  emptyMessage = 'Select an alert to view contributing factors.',
  className = '',
}: AlertExplainabilityProps) {
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadDetail = useCallback(() => {
    if (alertId === null) {
      return () => {}
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    getAlertDetail(alertId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load alert detail',
          )
          setDetail(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [alertId])

  useEffect(() => {
    if (alertId === null) {
      setDetail(null)
      setError(null)
      return
    }
    return loadDetail()
  }, [alertId, loadDetail])

  if (alertId === null) {
    return <p className={`text-xs text-on-surface-variant ${className}`}>{emptyMessage}</p>
  }

  if (loading) {
    return <p className={`text-xs text-on-surface-variant ${className}`}>Loading explanation...</p>
  }

  if (error) {
    return (
      <ErrorBanner
        message={error}
        onRetry={loadDetail}
        className={className}
      />
    )
  }

  if (!detail?.feature_breakdown) {
    return (
      <p className={`text-xs text-on-surface-variant ${className}`}>No explanation available.</p>
    )
  }

  const breakdown = detail.feature_breakdown
  const primaryReason =
    typeof breakdown.primary_reason === 'string' ? breakdown.primary_reason : null
  const layer1 = Array.isArray(breakdown.layer1_breakdown)
    ? (breakdown.layer1_breakdown as Layer1Factor[])
    : []
  const layer2 =
    breakdown.layer2_detail && typeof breakdown.layer2_detail === 'object'
      ? (breakdown.layer2_detail as Layer2Detail)
      : null
  const peripheral =
    breakdown.peripheral_detail && typeof breakdown.peripheral_detail === 'object'
      ? (breakdown.peripheral_detail as PeripheralDetail)
      : null
  const isStructural =
    breakdown.detection_method === 'peripheral_structural' || peripheral !== null

  return (
    <div className={`space-y-3 ${className}`}>
      {isStructural && (
        <p className="rounded-lg border border-tertiary/30 bg-tertiary/10 px-2 py-1.5 text-[11px] text-tertiary">
          Low-confidence structural association — flagged for 1-hop leg pattern
          near a fusion hub, not full Mahalanobis + ring scoring.
        </p>
      )}

      {primaryReason && (
        <p className="text-xs leading-relaxed text-on-surface">{primaryReason}</p>
      )}

      {layer1.length > 0 && (
        <div className="space-y-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
            Layer 1 factors
          </p>
          {layer1.map((factor) => (
            <div key={factor.feature} className="space-y-1">
              <div className="flex justify-between text-[11px] text-on-surface-variant">
                <span>{factor.feature.replace(/_/g, ' ')}</span>
                <span>{factor.contribution_pct.toFixed(1)}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-bright">
                <div
                  className="h-full rounded-full bg-primary transition-all duration-300"
                  style={{
                    width: `${Math.min(100, Math.max(0, factor.contribution_pct))}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {peripheral && (
        <div className="space-y-1 border-t border-primary/10 pt-3 text-[11px] text-on-surface-variant">
          <p className="text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
            Structural association
          </p>
          <p>
            Linked hub: {peripheral.linked_hub_account_id} · pattern{' '}
            {peripheral.leg_pattern.replace(/_/g, ' ')}
          </p>
          <p>
            {peripheral.tx_count} tx in window · in_deg={peripheral.in_degree}{' '}
            out_deg={peripheral.out_degree}
          </p>
        </div>
      )}

      {layer2 && (
        <div className="space-y-1 border-t border-primary/10 pt-3 text-[11px] text-on-surface-variant">
          <p className="text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
            Layer 2 — community ring
          </p>
          <p>{layer2.reason}</p>
          <p>
            Hub: {layer2.hub_account_id ?? 'unknown'} · concentration{' '}
            {(layer2.hub_concentration * 100).toFixed(0)}% · {layer2.member_count}{' '}
            members
          </p>
        </div>
      )}
    </div>
  )
}
