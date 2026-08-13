import type { AlertStatus } from '../api/types'

interface StatusBadgeProps {
  status: AlertStatus
  className?: string
}

const styles: Record<AlertStatus, string> = {
  new: 'border-teal-muted/60 bg-teal-muted/10 text-teal-accent',
  reviewing: 'border-amber-soft/60 bg-amber-soft/10 text-amber-soft',
  confirmed: 'border-red-400/50 bg-red-400/10 text-red-300',
  false_positive: 'border-gray-600 bg-gray-700/30 text-gray-400',
  auto_closed:
    'border-slate-500/50 bg-slate-600/15 text-slate-300 italic',
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
        'inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
        styles[status],
        className,
      ].join(' ')}
    >
      {labels[status]}
    </span>
  )
}
