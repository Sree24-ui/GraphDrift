export function formatRelativeTime(isoTimestamp: string): string {
  const then = new Date(isoTimestamp).getTime()
  const now = Date.now()
  const seconds = Math.floor((now - then) / 1000)

  if (seconds < 10) return 'just now'
  if (seconds < 60) return `${seconds}s ago`

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`

  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatPatternType(pattern: string): string {
  if (pattern === 'peripheral_structural') {
    return 'Structural association'
  }
  if (pattern === 'fan_in_fan_out') {
    return 'Fan-in / fan-out'
  }
  if (pattern === 'slow_drip') {
    return 'Slow drip'
  }
  return pattern.replace(/_/g, ' ')
}

export function isPeripheralStructural(patternType: string): boolean {
  return patternType === 'peripheral_structural'
}

export function formatDateTime(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatCurrency(amount: number): string {
  return `₹${amount.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}
