interface PeripheralStructuralBadgeProps {
  className?: string
  compact?: boolean
}

export default function PeripheralStructuralBadge({
  className = '',
  compact = false,
}: PeripheralStructuralBadgeProps) {
  return (
    <span
      className={[
        'inline-flex items-center rounded-full border border-tertiary/40 bg-tertiary/10 font-semibold uppercase tracking-wide text-tertiary',
        compact ? 'px-1.5 py-0.5 text-[9px]' : 'px-2 py-0.5 text-[10px]',
        className,
      ].join(' ')}
      title="Flagged via 1-hop structural association with a fusion hub — thinner evidence than full Mahalanobis + ring scoring"
    >
      {compact ? 'structural' : 'structural association'}
    </span>
  )
}
