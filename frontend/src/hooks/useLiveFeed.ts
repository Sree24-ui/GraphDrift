import { useCallback, useEffect, useRef, useState } from 'react'

import { liveFeedUrl, UNAUTHORIZED_EVENT } from '../api/client'
import type {
  LiveAlertMessage,
  LiveTransactionMessage,
  MetricsUpdate,
} from '../api/types'

const MAX_BUFFER = 50
const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000
const WS_POLICY_VIOLATION = 1008

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

// Sockets we closed on purpose (replacing one, or unmounting). Their onclose is
// not a dropped connection, so it must not schedule a reconnect. Without this,
// React StrictMode's dev double-mount reconnects forever: the first socket is
// closed during cleanup, but its onclose fires after the remount has already
// set mountedRef back to true, so the guard below lets it through and each new
// socket is torn down by the previous one's reconnect.
const deliberatelyClosed = new WeakSet<WebSocket>()

function closeDeliberately(ws: WebSocket | null): void {
  if (!ws) return
  deliberatelyClosed.add(ws)
  ws.close()
}

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

    closeDeliberately(wsRef.current)
    wsRef.current = null

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

    ws.onclose = (event) => {
      if (!mountedRef.current || deliberatelyClosed.has(ws)) return
      setConnectionStatus('disconnected')
      // 1008: the server ended the session (e.g. the token expired). Retrying
      // with the same token cannot succeed, so sign out like a REST 401 does.
      if (event.code === WS_POLICY_VIOLATION) {
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
        return
      }

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
      closeDeliberately(wsRef.current)
      wsRef.current = null
    }
  }, [connect, clearReconnectTimer])

  return {
    transactions,
    alerts,
    metrics,
    connectionStatus,
  }
}
