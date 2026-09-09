import { useCallback, useEffect, useRef, useState } from 'react'

import { getStoredToken, WS_BASE_URL } from '../api/client'
import type {
  LiveAlertMessage,
  LiveTransactionMessage,
  MetricsUpdate,
} from '../api/types'

// The handshake cannot carry an Authorization header, so the session token
// travels as a query parameter and is validated like the REST bearer token.
function liveFeedUrl(): string {
  const token = getStoredToken()
  const base = `${WS_BASE_URL}/ws/live-feed`
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}
const MAX_BUFFER = 50
const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

function appendRolling<T>(prev: T[], item: T, max: number): T[] {
  const next = [...prev, item]
  return next.length > max ? next.slice(next.length - max) : next
}

export function useLiveFeed() {
  const [transactions, setTransactions] = useState<LiveTransactionMessage[]>([])
  const [alerts, setAlerts] = useState<LiveAlertMessage[]>([])
  const [metrics, setMetrics] = useState<MetricsUpdate | null>(null)
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>('connecting')

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const backoffRef = useRef(INITIAL_BACKOFF_MS)
  const mountedRef = useRef(true)

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    clearReconnectTimer()

    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    setConnectionStatus('connecting')
    const ws = new WebSocket(liveFeedUrl())
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      backoffRef.current = INITIAL_BACKOFF_MS
      setConnectionStatus('connected')
    }

    ws.onmessage = (event) => {
      if (!mountedRef.current) return

      try {
        const message = JSON.parse(event.data as string) as {
          type: string
          timestamp: string
          data: unknown
        }

        switch (message.type) {
          case 'transaction':
            setTransactions((prev) =>
              appendRolling(
                prev,
                message as LiveTransactionMessage,
                MAX_BUFFER,
              ),
            )
            break
          case 'alert':
            setAlerts((prev) =>
              appendRolling(prev, message as LiveAlertMessage, MAX_BUFFER),
            )
            break
          case 'metrics_update':
            setMetrics((message as { data: MetricsUpdate }).data)
            break
          default:
            break
        }
      } catch {
        // Ignore malformed messages.
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setConnectionStatus('disconnected')

      const delay = backoffRef.current
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS)

      reconnectTimerRef.current = window.setTimeout(() => {
        if (mountedRef.current) {
          connect()
        }
      }, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [clearReconnectTimer])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      clearReconnectTimer()
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect, clearReconnectTimer])

  return {
    transactions,
    alerts,
    metrics,
    connectionStatus,
  }
}
