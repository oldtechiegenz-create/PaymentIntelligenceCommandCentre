import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { OperatingMode } from '../lib/api'

interface PaletteItem {
  label: string
  kind: string
  run: () => void
}

interface Props {
  open: boolean
  onClose: () => void
}

const SECTIONS: Array<[string, string]> = [
  ['Dashboard', '/'],
  ['Payment Discovery', '/discovery'],
  ['Payment 360', '/payment-360'],
  ['Simulation Engine', '/simulation'],
  ['Drilldown & Mandates', '/drilldown'],
  ['Kuber Agents', '/kuber-agents'],
]

const MODE_ACTIONS: Array<[string, OperatingMode]> = [
  ['Switch to CBCC', 'CBCC'],
  ['Switch to DOME', 'DOME'],
  ['Switch to Enterprise / All', 'ALL'],
]

/** Ctrl/Cmd+K command palette \u2014 scoped for now to section navigation and mode
 * switching (the screens/actions that actually exist). Extend with payment
 * search-and-jump once Payment 360 exists, and sim actions once Simulation Engine does. */
export default function CommandPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setIndex(0)
    const t = setTimeout(() => inputRef.current?.focus(), 10)
    return () => clearTimeout(t)
  }, [open])

  const items: PaletteItem[] = useMemo(() => {
    const modes = MODE_ACTIONS.map(([label, mode]) => ({
      label: `\u21bb ${label}`,
      kind: 'mode',
      run: () => navigate(`/?mode=${mode}`),
    }))
    const secs = SECTIONS.map(([label, path]) => ({
      label: `\u00a7 ${label}`,
      kind: 'section',
      run: () => navigate(path),
    }))
    const all = [...modes, ...secs]
    const q = query.toLowerCase().trim()
    return q ? all.filter((i) => i.label.toLowerCase().includes(q)) : all
  }, [query, navigate])

  function run(i: number) {
    const item = items[i]
    onClose()
    item?.run()
  }

  if (!open) return null

  return (
    <div className="pal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pal-box">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIndex(0)
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              setIndex((i) => Math.min(items.length - 1, i + 1))
              e.preventDefault()
            } else if (e.key === 'ArrowUp') {
              setIndex((i) => Math.max(0, i - 1))
              e.preventDefault()
            } else if (e.key === 'Enter') {
              run(index)
            } else if (e.key === 'Escape') {
              onClose()
            }
          }}
          placeholder={'Jump to a section or switch mode\u2026'}
          autoComplete="off"
        />
        <div className="pal-res">
          {items.map((it, i) => (
            <div key={it.label} className={`pal-it${i === index ? ' on' : ''}`} onClick={() => run(i)}>
              <span>{it.label}</span>
              <span className="dim" style={{ fontSize: 11 }}>{it.kind}</span>
            </div>
          ))}
          {items.length === 0 && <div className="muted" style={{ padding: 12 }}>No matches</div>}
        </div>
      </div>
    </div>
  )
}
