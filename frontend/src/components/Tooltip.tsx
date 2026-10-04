import { useState, type ReactNode, type MouseEvent } from 'react'

interface TooltipState {
  x: number
  y: number
  content: ReactNode
}

/** Floating cursor-follow tooltip \u2014 React port of the POC's #tip/showTip/moveTip. */
export function useTooltip() {
  const [tip, setTip] = useState<TooltipState | null>(null)

  function show(e: MouseEvent, content: ReactNode) {
    setTip({ x: e.clientX, y: e.clientY, content })
  }
  function move(e: MouseEvent) {
    setTip((t) => (t ? { ...t, x: e.clientX, y: e.clientY } : t))
  }
  function hide() {
    setTip(null)
  }

  const node = tip ? (
    <div
      style={{
        position: 'fixed',
        left: Math.max(8, Math.min(tip.x + 14, window.innerWidth - 220)),
        top: Math.max(8, Math.min(tip.y + 14, window.innerHeight - 90)),
        zIndex: 200,
        pointerEvents: 'none',
        background: 'rgba(9,20,33,.97)',
        border: '1px solid var(--line2)',
        borderRadius: 10,
        padding: '8px 11px',
        fontSize: 12,
        boxShadow: '0 12px 30px -8px rgba(0,0,0,.8)',
        maxWidth: 280,
      }}
    >
      {tip.content}
    </div>
  ) : null

  return { show, move, hide, node }
}

export function TooltipTitle({ label, color }: { label: string; color: string }) {
  return (
    <div className="tt">
      <span style={{ width: 9, height: 9, borderRadius: 3, background: color, display: 'inline-block' }} />
      {label}
    </div>
  )
}

export function TooltipRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="tr">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  )
}
