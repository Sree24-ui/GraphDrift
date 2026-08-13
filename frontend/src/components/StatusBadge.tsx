import type { AlertStatus } from '../api/types'

interface StatusBadgeProps {
  status: AlertStatus
  className?: string
}

const styles: Record<AlertStatus, string> = {
  new: 'border-primary/40 bg-primary/10 text-primary',
  reviewing: 'border-tertiary/40 bg-tertiary/10 text-tertiary',
  confirmed: 'border-error/40 bg-error/10 text-error',
  false_positive: 'border-outline/40 bg-surface-bright/50 text-on-surface-variant',
  auto_closed:
    'border-outline-variant/50 bg-surface-bright/30 text-on-surface-variant italic',
}

const labels: Record<AlertStatus, string> = {
  new: 'New',
  reviewing: 'Reviewing',
  confirmed: 'Confirmed',
  false_positive: 'False Positive',
  auto_closed: 'Auto-closed',
}

const titles: Partial<Record<AlertStatus, string>> = {
  auto_closed:
    'Aged out of the active queue without analyst action — not cleared as benign',
  false_positive: 'Analyst marked as not fraudulent',
}

export default function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  return (
    <span
      title={titles[status]}
      className={[
        'inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
        styles[status],
        className,
      ].join(' ')}
    >
      {labels[status]}
    </span>
  )
}
