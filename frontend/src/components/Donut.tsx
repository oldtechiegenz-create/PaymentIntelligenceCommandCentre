import { useState } from 'react'
import { useTooltip, TooltipTitle, TooltipRow } from './Tooltip'

interface DonutDatum {
  label: string
  count: number
  usd: number
  color: string
}

interface DonutProps {
  data: DonutDatum[]
  centerTop: string
  centerSub: string
}

function arcPath(cx: number, cy: number, r: number, ri: number, a0: number, a1: number): string {
  const p = (a: number, rr: number): [number, number] => [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]
  const largeArc = a1 - a0 > Math.PI ? 1 : 0
  const [x0, y0] = p(a0, r)
  const [x1, y1] = p(a1, r)
  const [x2, y2] = p(a1, ri)
  const [x3, y3] = p(a0, ri)
  return `M${x0} ${y0}A${r} ${r} 0 ${largeArc} 1 ${x1} ${y1}L${x2} ${y2}A${ri} ${ri} 0 ${largeArc} 0 ${x3} ${y3}Z`
}

export function fmtShort(n: number): string {
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(Math.round(n))
}

/** Live-data donut chart \u2014 React port of the POC's arcPath()/drawDonut() SVG math,
 * including its hover tooltips on both segments and legend rows, and the
 * legend-hover-dims-other-segments interaction. */
export default function Donut({ data, centerTop, centerSub }: DonutProps) {
  const [hovered, setHovered] = useState<number | null>(null)
  const { show, move, hide, node } = useTooltip()

  const total = data.reduce((sum, d) => sum + d.count, 0)
  let angle = -Math.PI / 2
  const gap = 0.025
  const segments = data.map((d) => {
    const span = total ? (d.count / total) * Math.PI * 2 : 0
    const path = arcPath(75, 75, 68, 46, angle + gap / 2, angle + span - gap / 2)
    angle += span
    return { ...d, path, pct: total ? Math.round((d.count / total) * 1000) / 10 : 0 }
  })

  function tooltipContent(s: (typeof segments)[number]) {
    return (
      <>
        <TooltipTitle label={s.label} color={s.color} />
        <TooltipRow label="Share" value={`${s.pct}%`} />
        <TooltipRow label="Value" value={`$${fmtShort(s.usd)}`} />
      </>
    )
  }

  return (
    <div className="donutwrap" style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
      <svg width={128} height={128} viewBox="0 0 150 150" style={{ flex: 'none' }}>
        {segments.map((s, i) => (
          <path
            key={s.label}
            d={s.path}
            fill={s.color}
            style={{ opacity: hovered === null || hovered === i ? 1 : 0.3, transition: 'opacity .15s', cursor: 'default' }}
            onMouseEnter={(e) => show(e, tooltipContent(s))}
            onMouseMove={move}
            onMouseLeave={hide}
          />
        ))}
        <text x="75" y="72" textAnchor="middle" fill="#e8f3ff" fontSize="17" fontWeight="750">{centerTop}</text>
        <text x="75" y="90" textAnchor="middle" fill="var(--muted)" fontSize="10">{centerSub}</text>
      </svg>
      <div className="legend" style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12, flex: 1, minWidth: 0 }}>
        {segments.map((s, i) => (
          <div
            className="li"
            key={s.label}
            style={{ display: 'grid', gridTemplateColumns: '10px 1fr auto', gap: 8, alignItems: 'center', padding: '3px 6px', borderRadius: 7, cursor: 'default' }}
            onMouseEnter={(e) => {
              setHovered(i)
              show(e, tooltipContent(s))
            }}
            onMouseMove={move}
            onMouseLeave={() => {
              setHovered(null)
              hide()
            }}
          >
            <span className="sw" style={{ width: 10, height: 10, borderRadius: 3, background: s.color }} />
            <span className="ln" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.label}</span>
            <span className="v" style={{ color: 'var(--muted)', fontVariantNumeric: 'tabular-nums', fontSize: 11, textAlign: 'right', lineHeight: 1.25 }}>
              {s.pct}%<br /><span className="dim">${fmtShort(s.usd)}</span>
            </span>
          </div>
        ))}
      </div>
      {node}
    </div>
  )
}
