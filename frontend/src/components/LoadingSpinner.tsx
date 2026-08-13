interface LoadingSpinnerProps {
  label?: string
  className?: string
  size?: 'sm' | 'md'
}

export default function LoadingSpinner({
  label,
  className = '',
  size = 'md',
}: LoadingSpinnerProps) {
  const dim = size === 'sm' ? 'h-5 w-5' : 'h-8 w-8'

  return (
    <div className={`flex flex-col items-center justify-center gap-3 ${className}`}>
      <div
        className={`${dim} animate-spin rounded-full border-2 border-charcoal-lighter border-t-teal-accent`}
        role="status"
        aria-label={label ?? 'Loading'}
      />
      {label && <p className="text-sm text-gray-500">{label}</p>}
    </div>
  )
}
