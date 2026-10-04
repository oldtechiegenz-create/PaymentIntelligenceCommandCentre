import type { TrendPoint } from '../lib/api'
import { fmtShort } from './Donut'

interface Props {
  data: TrendPoint[]
  windowDays: number
}

/** Real daily volume/value trend. Degrades honestly when there isn't enough history
 * to show an actual trend \u2014 a line needs >=2 points, so with 0\u20131 real days we show
 * a plain summary instead of a misleading/empty chart. */
export default function TrendChart({ data, windowDays }: Props) {
  if (data.length === 0) {
    return <p className="muted">No payment data in this window.</p>
  }

  if (data.length === 1) {
    const [only] = data
    return (
      <div>
        <p className="muted" style={{ marginBottom: 8 }}>
          Only 1 day of history available — a trend needs at least 2. This will fill in as more days of data accumulate.
        </p>
        <div style={{ display: 'flex', gap: 24 }}>
          <div>
            <div className="dim" style={{ fontSize: 10.5 }}>{only.date}</div>
            <div style={{ fontSize: 20, fontWeight: 750 }}>{only.payments.toLocaleString('en-US')}</div>
            <div className="dim" style={{ fontSize: 11 }}>payments</div>
          </div>
          <div>
            <div className="dim" style={{ fontSize: 10.5 }}>{only.date}</div>
            <div style={{ fontSize: 20, fontWeight: 750 }}>${fmtShort(only.value_usd)}</div>
            <div className="dim" style={{ fontSize: 11 }}>USD value</div>
          </div>
        </div>
      </div>
    )
  }

  const W = 520, H = 210, L = 40, R = 44, T = 14, B = 30
  const iw = W - L - R, ih = H - T - B
  const n = data.length

  const vols = data.map((d) => d.payments)
  const vals = data.map((d) => d.value_usd)
  const vMin = Math.min(...vols), vMax = Math.max(...vols)
  const vRange = vMax - vMin || 1
  const $Min = Math.min(...vals), $Max = Math.max(...vals)
  const $Range = $Max - $Min || 1

  const X = (i: number) => L + i * (iw / (n - 1))
  const Yv = (v: number) => T + ih - ((v - vMin) / vRange) * ih
  const Y$ = (v: number) => T + ih - ((v - $Min) / $Range) * ih

  const pv = data.map((d, i) => `${X(i)},${Yv(d.payments)}`)
  const p$ = data.map((d, i) => `${X(i)},${Y$(d.value_usd)}`)

  return (
    <div>
      {data.length < windowDays && (
        <p className="dim" style={{ fontSize: 11, marginBottom: 8 }}>
          Showing {data.length} of {windowDays} requested days — earlier days have no data yet.
        </p>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {[0, 1, 2, 3, 4, 5].map((k) => {
          const y = T + (ih * k) / 5
          return (
            <line key={k} x1={L} x2={W - R} y1={y} y2={y} stroke="#15283d" />
          )
        })}
        <polyline points={pv.join(' ')} fill="none" stroke="var(--cyan)" strokeWidth={2.4} strokeLinejoin="round" />
        <polyline points={p$.join(' ')} fill="none" stroke="var(--green)" strokeWidth={2.2} strokeDasharray="6 4" strokeLinejoin="round" />
        {data.map((d, i) => (
          <circle key={d.date} cx={X(i)} cy={Yv(d.payments)} r={3.6} fill="var(--cyan)" stroke="#06101c" strokeWidth={1.5} />
        ))}
        {data.map((d, i) => (
          <text key={d.date} x={X(i)} y={H - 10} textAnchor="middle" fontSize={10} fill="var(--muted)">{d.date.slice(5)}</text>
        ))}
      </svg>
    </div>
  )
}
