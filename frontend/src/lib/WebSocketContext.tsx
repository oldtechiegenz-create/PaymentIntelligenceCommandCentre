import { createContext, useContext, type ReactNode } from 'react'
import { useWebSocket, type PaymentEventNotification } from './useWebSocket'

interface WebSocketContextValue {
  connected: boolean
  events: PaymentEventNotification[]
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null)

/** Owns the single app-wide WebSocket connection so the header (connection pill)
 * and any page (e.g. the live feed) share one socket instead of opening their own. */
export function WebSocketProvider({ children }: { children: ReactNode }) {
  const value = useWebSocket()
  return <WebSocketContext.Provider value={value}>{children}</WebSocketContext.Provider>
}

export function useWebSocketContext(): WebSocketContextValue {
  const ctx = useContext(WebSocketContext)
  if (!ctx) throw new Error('useWebSocketContext must be used within a WebSocketProvider')
  return ctx
}
