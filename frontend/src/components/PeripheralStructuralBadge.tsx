interface PeripheralStructuralBadgeProps {
  className?: string
  compact?: boolean
}

/** Low-confidence alerts from the hub-association structural pass (not full fusion). */
export default function PeripheralStructuralBadge({
  className = '',
  compact = false,
}: PeripheralStructuralBadgeProps) {
  return (
    <span
      className={[
        'inline-flex items-center rounded border border-violet-400/40 bg-violet-400/10 font-medium uppercase tracking-wide text-violet-300',
        compact ? 'px-1 py-0.5 text-[9px]' : 'px-1.5 py-0.5 text-[10px]',
        className,
      ].join(' ')}
      title="Flagged via 1-hop structural association with a fusion hub — thinner evidence than full Mahalanobis + ring scoring"
    >
      {compact ? 'structural' : 'structural association'}
    </span>
  )
}
