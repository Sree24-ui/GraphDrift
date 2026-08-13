import type { ConfidenceLevel } from '../api/types'

interface ConfidenceBadgeProps {
  confidence: ConfidenceLevel
  className?: string
}

export default function ConfidenceBadge({
  confidence,
  className = '',
}: ConfidenceBadgeProps) {
  const styles: Record<ConfidenceLevel, string> = {
    high: 'border-amber-soft bg-amber-soft/20 text-amber-soft',
    medium: 'border-amber-soft/70 bg-transparent text-amber-soft',
    low: 'border-gray-600 bg-transparent text-gray-400',
  }

  return (
    <span
      className={[
        'inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
        styles[confidence],
        className,
      ].join(' ')}
    >
      {confidence}
    </span>
  )
}
