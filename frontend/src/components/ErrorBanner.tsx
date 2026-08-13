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
      className={`flex flex-wrap items-center justify-between gap-3 rounded border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300 ${className}`}
      role="alert"
    >
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded border border-red-400/40 px-2.5 py-1 font-medium text-red-200 hover:bg-red-400/10"
        >
          {retryLabel}
        </button>
      )}
    </div>
  )
}
