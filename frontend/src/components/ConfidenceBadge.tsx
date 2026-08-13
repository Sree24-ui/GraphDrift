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
    high: 'border-error/30 bg-error/10 text-error',
    medium: 'border-tertiary/30 bg-tertiary/10 text-tertiary',
    low: 'border-primary/30 bg-primary/10 text-primary',
  }

  return (
    <span
      className={[
        'inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
        styles[confidence],
        className,
      ].join(' ')}
    >
      {confidence}
    </span>
  )
}
