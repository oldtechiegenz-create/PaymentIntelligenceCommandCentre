import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useSearchParams } from 'react-router-dom'
import { useDashboard } from '../lib/queries'
import { useWebSocketContext } from '../lib/WebSocketContext'
import type { OperatingMode } from '../lib/api'
import CommandPalette from './CommandPalette'

const TABS: Array<[string, string]> = [
  ['Dashboard', '/'],
  ['Payment Discovery', '/discovery'],
  ['Payment 360', '/payment-360'],
  ['Simulation Engine', '/simulation'],
  ['Drilldown & Mandates', '/drilldown'],
  ['Kuber Agents', '/kuber-agents'],
]

export default function AppShell() {
  const [searchParams, setSearchParams] = useSearchParams()
  const mode = (searchParams.get('mode') as OperatingMode) ?? 'ALL'
  const date = searchParams.get('date') ?? undefined
  const { connected } = useWebSocketContext()
  const isDashboard = useLocation().pathname === '/'
  const { data } = useDashboard(mode, date, { enabled: isDashboard })
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Once the effective date resolves (defaults to the latest date with data),
  // reflect it explicitly in the URL so it's shareable/bookmarkable. Dashboard-only,
  // since the Business Date filter only applies there.
  useEffect(() => {
    if (isDashboard && data?.date && !searchParams.get('date')) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('date', data.date)
        return next
      }, { replace: true })
    }
  }, [isDashboard, data?.date, searchParams, setSearchParams])

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  function setMode(next: OperatingMode) {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev)
      params.set('mode', next)
      return params
    })
  }

  function setDate(next: string) {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev)
      params.set('date', next)
      return params
    })
  }

  return (
    <>
      <header className="top">
        <div className="hbar">
          <div className="logo" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path d="M4 18V6m0 6 7-6m-7 6 7 6" stroke="#35d9ff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              <circle cx="17.5" cy="12" r="3.2" stroke="#46e6a5" strokeWidth="2" />
              <path d="M17.5 5v2M17.5 17v2" stroke="#9b8cff" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </div>
          <div className="brand">
            <h1>Payment Command Center</h1>
            <div className="sub">Domestic · Cross-Border · ISO 20022 · AI Operations</div>
          </div>
          <div className="hspacer" />
          <div className="hctl">
            <span className={`pill ${connected ? 'live' : ''}`} title={connected ? 'Connected — receiving live updates' : 'Disconnected — reconnecting…'}>
              <span className="dot">●</span> {connected ? 'LIVE' : 'OFFLINE'}
            </span>
            {isDashboard && (
              <span className="pill" title={data ? `Data available: ${data.available_date_range.min} to ${data.available_date_range.max} (any date is selectable)` : undefined}>
                Business Date:{' '}
                <input
                  type="date"
                  value={date ?? ''}
                  onChange={(e) => e.target.value && setDate(e.target.value)}
                />
              </span>
            )}
            <div className="seg">
              {(['CBCC', 'DOME', 'ALL'] as OperatingMode[]).map((m) => (
                <button key={m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)}>
                  {m === 'ALL' ? 'Enterprise / All' : m}
                </button>
              ))}
            </div>
            <button className="pill" style={{ cursor: 'pointer' }} title="Command palette" onClick={() => setPaletteOpen(true)}>
              ⌘ <span className="kbd">Ctrl K</span>
            </button>
          </div>
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map(([label, path]) => (
            <NavLink key={path} to={{ pathname: path, search: searchParams.toString() }} end={path === '/'} className={({ isActive }) => (isActive ? 'active' : '')}>
              {label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  )
}
