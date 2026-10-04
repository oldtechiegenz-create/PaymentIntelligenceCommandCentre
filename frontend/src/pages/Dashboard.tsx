import { useSearchParams } from 'react-router-dom'
import { useDashboard } from '../lib/queries'
import type { OperatingMode } from '../lib/api'
import Donut, { fmtShort } from '../components/Donut'
import TrendChart from '../components/TrendChart'
import { useTooltip, TooltipTitle, TooltipRow } from '../components/Tooltip'

const fmtUSD = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const fmtN = (n: number) => n.toLocaleString('en-US')
const fmtPct = (n: number | null) => (n == null ? '\u2014' : `${n.toFixed(2)}%`)
const fmtMs = (ms: number | null) => {
  if (ms == null) return '\u2014'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)}s`
}

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: 'var(--green)',
  IN_PROGRESS: 'var(--cyan)',
  REJECTED: 'var(--red)',
  RETURNED: 'var(--violet)',
  INVESTIGATION: 'var(--amber)',
  FAILED: 'var(--red)',
  CANCELLED: 'var(--muted)',
}
const RAIL_COLOR: Record<string, string> = {
  'SWIFT CBPR+': 'var(--cyan)',
  Fedwire: 'var(--violet)',
  ACH: 'var(--blue)',
  RTP: 'var(--green)',
  FedNow: 'var(--amber)',
}

const PERSONAS: Array<{ mode: OperatingMode; dom: string; ttl: string; desc: string; tags: string[] }> = [
  {
    mode: 'CBCC',
    dom: 'CBCC',
    ttl: 'Cross-Border Correspondent Banking',
    desc: 'Correspondent chains, nostro funding, FX, sanctions screening and gpi-style tracking on SWIFT CBPR+ (ISO 20022 pacs.008 / pacs.009 / pacs.004).',
    tags: ['SWIFT CBPR+', 'Correspondent Banking', 'Cross-border', 'FX', 'Screening', 'Nostro'],
  },
  {
    mode: 'DOME',
    dom: 'DOME',
    ttl: 'Domestic Payments Operations',
    desc: 'US instant, batch and high-value rails \u2014 RTP and FedNow (24\u00d77, final), ACH batches and Fedwire Funds (ISO 20022 since Jul 2025) with intraday liquidity.',
    tags: ['RTP', 'ACH', 'FedNow', 'Fedwire', 'Domestic', 'Liquidity'],
  },
  {
    mode: 'ALL',
    dom: 'ENTERPRISE',
    ttl: 'Enterprise / All',
    desc: 'Unified command view over every rail, legal entity and corridor \u2014 exceptions, investigations, reconciliation and Kuber AI operations.',
    tags: ['All rails', 'ISO 20022', 'Exceptions', 'Investigations', 'Reconciliation', 'Kuber AI'],
  },
]

export default function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams()
  const mode = (searchParams.get('mode') as OperatingMode) ?? 'ALL'
  const date = searchParams.get('date') ?? undefined
  const { data, isLoading, isError, error } = useDashboard(mode, date)
  const { show, move, hide, node: tooltipNode } = useTooltip()

  function setMode(next: OperatingMode) {
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev)
      params.set('mode', next)
      return params
    })
  }

  if (isLoading) return <p className="muted">Loading dashboard…</p>
  if (isError) return <p style={{ color: 'var(--red)' }}>Failed to load: {(error as Error).message}</p>
  if (!data) return null

  const { portfolio, kpis, personas, charts } = data
  const personaCounts = Object.fromEntries(personas.map((p) => [p.mode, p.payments])) as Record<OperatingMode, number>

  const cards: Array<{ lbl: string; val: string; delta: string; acc: string }> = [
    {
      lbl: 'Payment Value',
      val: fmtUSD(kpis.payment_value_usd),
      delta:
        kpis.payment_value_excludes_non_usd_count > 0
          ? `USD-denominated only \u00b7 ${kpis.payment_value_excludes_non_usd_count} other-currency excluded`
          : 'USD-denominated payments',
      acc: 'var(--cyan)',
    },
    { lbl: 'Payments', val: fmtN(kpis.payments), delta: `as of ${data.date}`, acc: 'var(--blue)' },
    { lbl: 'STP Rate', val: fmtPct(kpis.stp_rate_pct), delta: 'never touched an exception state', acc: 'var(--green)' },
    {
      lbl: 'Success Rate',
      val: fmtPct(kpis.success_rate_pct),
      delta: `of ${fmtN(kpis.success_rate_terminal_total)} completed/terminal`,
      acc: 'var(--green)',
    },
    { lbl: 'Exceptions', val: fmtPct(kpis.exceptions_pct), delta: `${fmtN(kpis.exceptions_count)} cases`, acc: 'var(--amber)' },
    { lbl: 'Avg Latency', val: fmtMs(kpis.avg_latency_ms), delta: 'avg across instant & wire rails', acc: 'var(--violet)' },
    { lbl: 'Investigation Cases', val: fmtN(kpis.investigation_cases), delta: 'open or resolved cases', acc: 'var(--violet)' },
  ]

  const statusData = charts.status.map((r) => ({
    label: r.status!.replace('_', ' '),
    count: r.n,
    usd: r.usd,
    color: STATUS_COLOR[r.status!] ?? 'var(--muted)',
  }))
  const railData = charts.rail.map((r) => ({
    label: r.rail!,
    count: r.n,
    usd: r.usd,
    color: RAIL_COLOR[r.rail!] ?? 'var(--muted)',
  }))
  const typeTotalUsd = charts.type.reduce((sum, r) => sum + r.usd, 0)
  const typeTotalN = charts.type.reduce((sum, r) => sum + r.n, 0)
  const typeMax = Math.max(
    ...charts.type.map((r) => Math.max(typeTotalN ? (r.n / typeTotalN) * 100 : 0, typeTotalUsd ? (r.usd / typeTotalUsd) * 100 : 0)),
    1,
  )

  return (
    <>
      <section className="blk" style={{ marginTop: 6 }}>
        <div className="shead">
          <div>
            <h2>
              Executive Payment Radar <span className="tag">LIVE</span>
            </h2>
            <div className="ssub">As of {data.date} · live-computed from the current payment book</div>
          </div>
          <div className="stat-row">
            <span>Portfolio: <b>{portfolio.rails} rails</b></span>
            <span>Corridors: <b>{portfolio.corridors}</b></span>
            <span>Legal entities: <b>{portfolio.legal_entities}</b></span>
          </div>
        </div>
        <div className="kpis">
          {cards.map((c) => (
            <div className="card kpi" style={{ ['--acc' as string]: c.acc }} key={c.lbl}>
              <div className="lbl">{c.lbl}</div>
              <div className="val">{c.val}</div>
              <div className="delta">{c.delta}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="blk">
        <div className="shead">
          <div>
            <h2>
              Operating Mode <span className="tag">PERSONA</span>
            </h2>
            <div className="ssub">
              {mode === 'CBCC'
                ? 'CBCC \u2014 Cross-Border Correspondent Banking'
                : mode === 'DOME'
                  ? 'DOME \u2014 Domestic Payments Operations'
                  : 'Enterprise / All \u2014 Cross-Border and Domestic payment universe'}
            </div>
          </div>
        </div>
        <div className="grid g3">
          {PERSONAS.map((p) => (
            <div
              key={p.mode}
              className="card persona"
              onClick={() => setMode(p.mode)}
              style={{
                cursor: 'pointer',
                borderColor: mode === p.mode ? 'rgba(53,217,255,.6)' : undefined,
                boxShadow: mode === p.mode ? '0 0 0 1px rgba(53,217,255,.25), var(--shadow)' : undefined,
              }}
            >
              <div style={{ fontSize: 10.5, letterSpacing: 1, color: 'var(--cyan)', fontWeight: 700 }}>{p.dom}</div>
              <div style={{ fontSize: 15, fontWeight: 700, margin: '3px 0 4px' }}>{p.ttl}</div>
              <div className="muted">{p.desc}</div>
              <div className="tags" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                {p.tags.map((t) => (
                  <span key={t} className="t" style={{ fontSize: 11, padding: '3px 8px', borderRadius: 7, border: '1px solid var(--line)', background: '#0a1624', color: '#b8cce0' }}>
                    {t}
                  </span>
                ))}
              </div>
              <div className="muted" style={{ marginTop: 10, fontSize: 11.5 }}>
                Payment universe: <b style={{ color: 'var(--text)' }}>{fmtN(personaCounts[p.mode] ?? 0)}</b> payments
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="blk">
        <div className="shead">
          <div>
            <h2>
              Executive Payment Radar <span className="tag">ANALYTICS</span>
            </h2>
            <div className="ssub">Status, rail and type mix for the selected mode — live from the payment book</div>
          </div>
        </div>
        <div className="grid charts-grid" style={{ gap: 14 }}>
          <div className="card">
            <h3 style={{ margin: '0 0 10px', fontSize: 12, fontWeight: 700, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>
              Payment Status <span style={{ fontWeight: 500, textTransform: 'none', color: 'var(--dim)', fontSize: 11.5 }}>share · value</span>
            </h3>
            <Donut data={statusData} centerTop={`$${fmtShort(statusData.reduce((s, d) => s + d.usd, 0))}`} centerSub="by status" />
          </div>
          <div className="card">
            <h3 style={{ margin: '0 0 10px', fontSize: 12, fontWeight: 700, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>
              Payment Rail <span style={{ fontWeight: 500, textTransform: 'none', color: 'var(--dim)', fontSize: 11.5 }}>share · value</span>
            </h3>
            <Donut data={railData} centerTop={`${railData.length} rail${railData.length === 1 ? '' : 's'}`} centerSub="share" />
          </div>
          <div className="card">
            <h3 style={{ margin: '0 0 10px', fontSize: 12, fontWeight: 700, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>
              Payment Type <span style={{ fontWeight: 500, textTransform: 'none', color: 'var(--dim)', fontSize: 11.5 }}>volume vs value share</span>
            </h3>
            <div style={{ display: 'flex', gap: 14, fontSize: 11.5, color: 'var(--muted)', marginBottom: 10 }}>
              <span><i style={{ display: 'inline-block', width: 14, height: 3, borderRadius: 2, background: 'var(--cyan)', marginRight: 6 }} />Volume share</span>
              <span><i style={{ display: 'inline-block', width: 14, height: 3, borderRadius: 2, background: 'var(--violet)', marginRight: 6 }} />Value share</span>
            </div>
            {charts.type.map((r) => {
              const volPct = typeTotalN ? (r.n / typeTotalN) * 100 : 0
              const valPct = typeTotalUsd ? (r.usd / typeTotalUsd) * 100 : 0
              const avgTicket = r.n ? r.usd / r.n : null
              const tooltipContent = (color: string) => (
                <>
                  <TooltipTitle label={r.bucket!} color={color} />
                  <TooltipRow label="Volume share" value={`${volPct.toFixed(1)}%`} />
                  <TooltipRow label="Value" value={`$${fmtShort(r.usd)}`} />
                  <TooltipRow label="Value share" value={`${valPct.toFixed(1)}%`} />
                  <TooltipRow label="Avg ticket" value={avgTicket == null ? '\u2014' : `$${fmtShort(avgTicket)}`} />
                </>
              )
              return (
                <div key={r.bucket} style={{ display: 'grid', gridTemplateColumns: '104px 1fr', gap: 10, alignItems: 'center', marginBottom: 9 }}>
                  <div style={{ fontSize: 12, color: '#c9d9ea', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.bucket}</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    <div
                      style={{ height: 8, borderRadius: 5, width: `${(volPct / typeMax) * 100}%`, background: 'linear-gradient(90deg,#1a7fa0,var(--cyan))', cursor: 'default' }}
                      onMouseEnter={(e) => show(e, tooltipContent('var(--cyan)'))}
                      onMouseMove={move}
                      onMouseLeave={hide}
                    />
                    <div
                      style={{ height: 8, borderRadius: 5, width: `${(valPct / typeMax) * 100}%`, background: 'linear-gradient(90deg,#5a4fb0,var(--violet))', cursor: 'default' }}
                      onMouseEnter={(e) => show(e, tooltipContent('var(--violet)'))}
                      onMouseMove={move}
                      onMouseLeave={hide}
                    />
                  </div>
                </div>
              )
            })}
          </div>
          <div className="card">
            <h3 style={{ margin: '0 0 10px', fontSize: 12, fontWeight: 700, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>
              7-Day Trend <span style={{ fontWeight: 500, textTransform: 'none', color: 'var(--dim)', fontSize: 11.5 }}>volume & value</span>
            </h3>
            <div style={{ display: 'flex', gap: 14, fontSize: 11.5, color: 'var(--muted)', marginBottom: 10 }}>
              <span><i style={{ display: 'inline-block', width: 14, height: 3, borderRadius: 2, background: 'var(--cyan)', marginRight: 6 }} />Payments</span>
              <span><i style={{ display: 'inline-block', width: 14, height: 3, borderRadius: 2, background: 'var(--green)', marginRight: 6 }} />USD value</span>
            </div>
            <TrendChart data={charts.trend} windowDays={7} />
          </div>
        </div>
      </section>
      {tooltipNode}
    </>
  )
}
