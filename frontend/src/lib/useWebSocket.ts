import { useEffect, useRef, useState } from 'react'
import { WS_BASE_URL } from './api'

export interface PaymentEventNotification {
  type: string
  event_id: number
  payment_id: string
  sim_run_id: string | null
  seq: number
  state: string
  prev_state: string | null
  status: string
  iso_message: string
  event_ts: string
}

const MAX_EVENTS = 50
const INITIAL_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 10_000
const HEARTBEAT_INTERVAL_MS = 10_000
// If an abruptly-killed backend never sends a close frame, the browser's WebSocket can
// stay "open" indefinitely (no TCP FIN was ever received) — this is how long we wait
// without hearing anything (including our own pings being answered) before treating the
// connection as dead and forcing a reconnect, instead of trusting onclose to ever fire.
const STALE_TIMEOUT_MS = 25_000

/** Connects to the backend's broadcast-to-all WebSocket on mount, reconnecting with
 * exponential backoff on drop. Broadcast+client-filter design — every client receives
 * every event; filtering by relevance is the caller's job, not this hook's. */
export function useWebSocket(path = '/ws') {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<PaymentEventNotification[]>([])
  const backoffRef = useRef(INITIAL_BACKOFF_MS)
  const closedByUsRef = useRef(false)

  useEffect(() => {
    closedByUsRef.current = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null
    let lastActivity = Date.now()

    function connect() {
      socket = new WebSocket(`${WS_BASE_URL}${path}`)

      socket.onopen = () => {
        setConnected(true)
        backoffRef.current = INITIAL_BACKOFF_MS
        lastActivity = Date.now()
      }

      socket.onmessage = (raw) => {
        lastActivity = Date.now()
        try {
          const notification = JSON.parse(raw.data) as PaymentEventNotification
          setEvents((prev) => {
            // Dedupe by event_id: guards against a brief double-socket window
            // (e.g. React StrictMode's dev-only double-effect-invocation).
            if (prev.some((e) => e.event_id === notification.event_id)) return prev
            return [notification, ...prev].slice(0, MAX_EVENTS)
          })
        } catch {
          // Ignore malformed frames rather than crashing the whole feed.
        }
      }

      socket.onclose = () => {
        setConnected(false)
        if (heartbeatTimer) clearInterval(heartbeatTimer)
        if (closedByUsRef.current) return
        reconnectTimer = setTimeout(connect, backoffRef.current)
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS)
      }

      socket.onerror = () => {
        socket?.close()
      }

      heartbeatTimer = setInterval(() => {
        if (!socket || socket.readyState !== WebSocket.OPEN) return
        if (Date.now() - lastActivity > STALE_TIMEOUT_MS) {
          // No close frame ever arrived, but nothing's come through in a while either —
          // assume the server is gone and force a reconnect rather than keep showing LIVE.
          socket.close()
          return
        }
        socket.send('ping')
      }, HEARTBEAT_INTERVAL_MS)
    }

    connect()

    return () => {
      closedByUsRef.current = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (heartbeatTimer) clearInterval(heartbeatTimer)
      socket?.close()
    }
  }, [path])

  return { connected, events }
}
