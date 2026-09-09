import MaterialIcon from './MaterialIcon'
import type { ConfidenceLevel } from '../api/types'
import { GDI_MAX } from '../knobs'

// Fallback bands for when the backend sent no confidence label. Expressed as a
// share of the GDI display scale so they follow gdi_max instead of silently
// going stale if that knob changes.
const HIGH_SCORE = GDI_MAX * 0.9
const MEDIUM_SCORE = GDI_MAX * 0.6

interface RiskScorePillProps {
  score: number
  confidence?: ConfidenceLevel
  compact?: boolean
}

function pillStyle(score: number, confidence?: ConfidenceLevel) {
  if (confidence === 'high' || score >= HIGH_SCORE) {
    return {
      wrap: 'bg-error/10 border-error/20 text-error shadow-[0_0_10px_rgba(255,107,107,0.1)]',
      icon: 'warning' as const,
    }
  }
  if (confidence === 'medium' || score >= MEDIUM_SCORE) {
    return {
      wrap: 'bg-secondary/10 border-secondary/20 text-secondary',
      icon: 'trending_up' as const,
    }
  }
  return {
    wrap: 'bg-primary/10 border-primary/20 text-primary',
    icon: 'info' as const,
  }
}

export default function RiskScorePill({
  score,
  confidence,
  compact = false,
}: RiskScorePillProps) {
  const style = pillStyle(score, confidence)
  const display = score >= 10 ? Math.round(score) : score.toFixed(2)

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${style.wrap} ${compact ? 'px-2 py-0.5 text-[10px]' : ''}`}
    >
      <MaterialIcon name={style.icon} size={compact ? 12 : 14} />
      {display}
    </span>
  )
}
