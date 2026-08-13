interface ErrorBannerProps {
  message: string
  onRetry?: () => void
  retryLabel?: string
  className?: string
}

export default function ErrorBanner({
  message,
  onRetry,
  retryLabel = 'Retry',
  className = '',
}: ErrorBannerProps) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border border-error/30 bg-error/10 px-3 py-2 text-xs text-error ${className}`}
      role="alert"
    >
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-lg border border-error/40 px-2.5 py-1 font-medium text-error hover:bg-error/10"
        >
          {retryLabel}
        </button>
      )}
    </div>
  )
}
