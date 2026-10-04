import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { usePaymentDetail, useSimStatus } from '../lib/queries'
import type { MtMxMappingRow, PaymentEventRecord, PaymentHop, PaymentRecord } from '../lib/api'
import { Badge, badgeCls } from '../components/Badge'
import IsoXmlView from '../components/IsoXmlView'

type Tab = 'LINEAGE' | 'EVENTS' | 'MESSAGES' | 'PARTIES'
const TABS: Tab[] = ['LINEAGE', 'EVENTS', 'MESSAGES', 'PARTIES']

const ISO_TYPES = ['pain.001', 'pacs.008', 'pacs.009', 'pacs.002', 'pacs.004', 'camt.052', 'camt.053', 'camt.054', 'camt.056', 'camt.029']

const KIND_COLOR: Record<string, string> = {
  ok: 'var(--green)', prog: 'var(--cyan)', err: 'var(--red)', warn: 'var(--amber)', ret: 'var(--violet)', canc: 'var(--muted)',
}
const BADGE_COLOR: Record<string, string> = {
  g: 'var(--green)', c: 'var(--cyan)', r: 'var(--red)', a: 'var(--amber)', v: 'var(--violet)', n: 'var(--muted)', bl: 'var(--blue)',
}

function reasonCode(reason: string | null | undefined): string {
  if (!reason) return ''
  const m = String(reason).match(/^([A-Z0-9_]{3,})/)
  return m ? m[1] : ''
}

function stateLabel(p: PaymentRecord): string {
  const labels: Record<string, string> = {
    COMPLETED: 'Completed', REJECTED: 'Rejected', RETURNED: 'Returned',
    INVESTIGATION: 'Investigation', FAILED: 'Failed', CANCELLED: 'Cancelled',
  }
  return labels[p.status] ?? `Current State - ${p.intermediateStatus}`
}

function statusIndicator(p: PaymentRecord): string {
  const c = reasonCode(p.statusReason)
  switch (p.status) {
    case 'COMPLETED': return 'ACCC - Settled'
    case 'REJECTED': return `RJCT - ${c || 'NARR'}`
    case 'RETURNED': return `RTND - pacs.004${c ? ' - ' + c : ''}`
    case 'FAILED': return `RJCT - ${c || 'NACK_TIMEOUT'}`
    case 'INVESTIGATION': return `PDNG - ${p.investigationId || 'case open'}`
    case 'CANCELLED': return 'CANC - camt.029'
    default: return `ACSP - ${p.intermediateStatus}`
  }
}

function journeyKind(status: string): keyof typeof KIND_COLOR {
  if (status === 'COMPLETED') return 'ok'
  if (status === 'REJECTED' || status === 'FAILED') return 'err'
  if (status === 'RETURNED') return 'ret'
  if (status === 'INVESTIGATION') return 'warn'
  if (status === 'CANCELLED') return 'canc'
  return 'prog'
}

function fmtAmt(n: number, ccy: string): string {
  return `${ccy} ${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
  return `${(ms / 3600000).toFixed(1)}h`
}

interface LineageNode {
  role: string
  name: string
  sub: string
}

function lineageNodes(p: PaymentRecord, hops: PaymentHop[]): LineageNode[] {
  const nodes: LineageNode[] = []
  nodes.push({
    role: 'Debtor', name: p.debtor,
    sub: p.ultimateDebtor && p.ultimateDebtor !== p.debtor ? `UD - ${p.ultimateDebtor}` : (p.debtorCountry || ''),
  })
  const roleLabels: Record<string, string> = {
    DEBTOR_AGENT: 'Debtor Agent', CORRESPONDENT: 'Correspondent', INTERMEDIARY: 'Intermediary', CREDITOR_AGENT: 'Creditor Agent',
  }
  hops.forEach((h) => nodes.push({ role: roleLabels[h.role] ?? h.role, name: h.name, sub: h.bic }))
  nodes.push({
    role: p.ultimateCreditor ? 'Ultimate Creditor' : 'Creditor', name: p.ultimateCreditor || p.creditor,
    sub: p.ultimateCreditor && p.ultimateCreditor !== p.creditor ? `Cdtr - ${p.creditor}` : (p.creditorCountry || ''),
  })
  return nodes
}

type JourneyKind = 'ok' | 'prog' | 'err' | 'warn' | 'ret' | 'canc'
interface Journey { kind: JourneyKind; target: number; iDA: number; iCA: number }

const HAPPY_PATH = ['INITIATED', 'ACCEPTED', 'SCREENING', 'SENT', 'IN_TRANSIT', 'CORRESPONDENT_PROCESSING', 'SETTLEMENT_PENDING', 'COMPLETED']

function computeJourney(p: PaymentRecord, nodes: LineageNode[], events: PaymentEventRecord[]): Journey {
  const n = nodes.length
  const iDA = 1
  const iCA = n - 2
  const iEnd = n - 1
  const iCorr = nodes.findIndex((nd) => nd.role === 'Correspondent')
  const iInt = nodes.findIndex((nd) => nd.role === 'Intermediary')
  const corr = iCorr >= 0 ? iCorr : null
  const inter = iInt >= 0 ? iInt : null

  const posOf = (state: string): number | undefined => {
    const table: Record<string, number> = {
      INITIATED: 0, ACCEPTED: iDA, SCREENING: iDA, SENT: iDA + 0.5,
      IN_TRANSIT: corr ?? inter ?? iCA - 0.5,
      CORRESPONDENT_PROCESSING: inter ?? corr ?? iCA - 0.5,
      SETTLEMENT_PENDING: iCA, COMPLETED: iEnd,
    }
    return table[state]
  }

  const status = p.status
  if (status === 'COMPLETED') return { kind: 'ok', target: iEnd, iDA, iCA }
  if (status === 'RETURNED') return { kind: 'ret', target: iCA, iDA, iCA }

  // Last event whose state is on the happy path (skips terminal exception states like
  // REJECTED/INVESTIGATION/CANCELLED, which have no lineage position of their own —
  // mirrors the POC's lastProgPos tracking).
  let lastProgPos: number | undefined
  for (const e of events) {
    const q = posOf(e.state)
    if (q !== undefined) lastProgPos = q
  }

  const kind: JourneyKind = status === 'REJECTED' || status === 'FAILED' ? 'err'
    : status === 'INVESTIGATION' ? 'warn'
      : status === 'CANCELLED' ? 'canc' : 'prog'
  return { kind, target: lastProgPos ?? iDA, iDA, iCA }
}

function hopMsg(p: PaymentRecord, i: number, nodes: LineageNode[]): string {
  const b = nodes[i + 1]
  if (!b) return ''
  if (i === 0) return p.rail === 'ACH' ? 'pain.001 -> ACH file' : 'pain.001'
  if (b.role === 'Ultimate Creditor') return 'credit - camt.054'
  if (p.rail === 'ACH') return 'ACH batch'
  return p.messageType === 'pacs.009' ? 'pacs.009' : 'pacs.008'
}

function IdentityCard({ p }: { p: PaymentRecord }) {
  const kind = journeyKind(p.status)
  const col = KIND_COLOR[kind]
  const fx = p.debitCcy !== p.creditCcy
  return (
    <div className="card c1">
      <div className="idhead">
        <div>
          <div className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.8 }}>PAYMENT IDENTITY</div>
          <div className="pid">{p.paymentId}</div>
        </div>
        <Badge value={p.status} />
      </div>
      <div className="tags">
        <span className="t">{p.rail}</span>
        <span className="t">{p.domain}</span>
        <span className="t">{p.messageType}</span>
        <span className="t">{p.paymentType}</span>
      </div>
      <div className="amt">{fmtAmt(p.amount, p.debitCcy)}</div>
      {fx && <div className="muted">-&gt; {fmtAmt(p.creditAmount, p.creditCcy)} @ {p.fxRate}</div>}
      <div className="statusbox" style={{ borderColor: `${col}55`, background: `${col}0f` }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: col, boxShadow: `0 0 12px ${col}` }} />
        <div>
          <div className="big" style={{ color: col }}>{stateLabel(p)}</div>
          <div className="mono" style={{ color: col }}>{statusIndicator(p)}</div>
        </div>
      </div>
      <dl className="kv">
        <dt>UETR</dt><dd className="mono">{p.uetr || '(not applicable)'}</dd>
        <dt>Message</dt>
        <dd>{p.isoVersion} {p.mtEquivalent !== '\u2014' && <span className="dim">~ {p.mtEquivalent}</span>}</dd>
        <dt>Business Msg ID</dt><dd className="mono">{p.businessMsgId}</dd>
        <dt>Instruction ID</dt><dd className="mono">{p.instructionId}</dd>
        <dt>End-to-End ID</dt><dd className="mono">{p.endToEndId}</dd>
        {p.swiftTxnId && (<><dt>Swift Txn ID</dt><dd className="mono">{p.swiftTxnId}</dd></>)}
        <dt>Ultimate Status</dt><dd><Badge value={p.ultimateStatus} /></dd>
        <dt>Intermediate</dt><dd><Badge value={p.intermediateStatus} /></dd>
        <dt>Status Reason</dt><dd style={p.statusReason !== '\u2014' ? { color: 'var(--red)' } : undefined}>{p.statusReason}</dd>
        <dt>Corridor</dt><dd>{p.country} - {p.settlementMethod}</dd>
        <dt>Settlement Date</dt><dd>{p.settlementDate}</dd>
        <dt>Initiated</dt><dd>{p.initiatedAt}</dd>
        {p.status === 'COMPLETED' && p.completedAt
          ? (<><dt>Completed At</dt><dd>{p.completedAt}</dd></>)
          : (<><dt>Final Credit</dt><dd><span className="dim">none ({stateLabel(p)})</span></dd></>)}
        <dt>Duration</dt><dd>{p.duration}</dd>
        <dt>Charges</dt><dd>{p.charges} - fees {p.feeAmount.toFixed(2)} USD</dd>
      </dl>
    </div>
  )
}

function LineageTab({ p, hops, events }: { p: PaymentRecord; hops: PaymentHop[]; events: PaymentEventRecord[] }) {
  const nodes = useMemo(() => lineageNodes(p, hops), [p, hops])
  const journey = useMemo(() => computeJourney(p, nodes, events), [p, nodes, events])
  const col = KIND_COLOR[journey.kind]

  function nodeState(i: number): 'done' | 'cur' | 'pend' | JourneyKind {
    if (journey.kind === 'ok') return 'done'
    if (journey.kind === 'ret') {
      if (i === journey.iCA) return 'err'
      if (i >= journey.iDA && i < journey.iCA) return 'ret'
      return i < journey.iDA ? 'done' : 'pend'
    }
    const t = journey.target
    if (i < Math.floor(t) || (i === Math.floor(t) && t % 1 !== 0)) return 'done'
    if (i === Math.round(t)) return journey.kind
    return 'pend'
  }

  const STATE_COLOR: Record<string, string> = { done: 'var(--green)', cur: 'var(--cyan)', pend: 'var(--dim)', ...KIND_COLOR }

  // Current node = where the packet actually is right now; only 'prog' (genuinely IN_PROGRESS)
  // gets the flowing/moving animation on the connector leading into it \u2014 exception states
  // (err/warn/ret/canc) are at rest, so only the node itself pulses to mark "stopped here".
  const currentNodeIndex = journey.kind === 'ok' ? null : journey.kind === 'ret' ? journey.iCA : Math.round(journey.target)
  const flowingConnector = journey.kind === 'prog' && currentNodeIndex !== null ? currentNodeIndex - 1 : null

  // Auto-scroll the lineage strip into view on load, since a long hop chain can overflow the
  // visible width: unfinished payments scroll to the current/stopped-at node, completed
  // payments scroll to the final (delivered) node.
  const scrollTargetIndex = journey.kind === 'ok' ? nodes.length - 1 : currentNodeIndex
  const wrapRef = useRef<HTMLDivElement>(null)
  const scrollTargetRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const wrap = wrapRef.current
    const node = scrollTargetRef.current
    if (!wrap || !node) return
    const target = node.offsetLeft - wrap.clientWidth / 2 + node.clientWidth / 2
    wrap.scrollTo({ left: Math.max(0, target), behavior: 'smooth' })
  }, [p.paymentId, journey.kind, scrollTargetIndex])


  const where = journey.kind === 'ok'
    ? `Packet delivered to ${nodes[nodes.length - 1].name}`
    : journey.kind === 'ret'
      ? `Rejected at ${nodes[journey.iCA].name}; return flowing back to ${nodes[journey.iDA].name}`
      : journey.target % 1 !== 0
        ? `In flight between ${nodes[Math.floor(journey.target)].name} and ${nodes[Math.ceil(journey.target)].name}`
        : `Packet ${journey.kind === 'prog' ? 'at' : 'stopped at'} ${nodes[Math.round(journey.target)]?.name ?? nodes[journey.iDA].name}`

  return (
    <div>
      <div className="lineage-wrap" ref={wrapRef}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 0, minWidth: 720 }}>
          {nodes.map((nd, i) => {
            const st = nodeState(i)
            const color = STATE_COLOR[st] ?? 'var(--dim)'
            const isCurrent = i === currentNodeIndex
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', flex: i < nodes.length - 1 ? 1 : 'none' }}>
                <div
                  ref={i === scrollTargetIndex ? scrollTargetRef : undefined}
                  className={`lng-node${isCurrent ? ' on' : ''}`}
                  style={{
                    minWidth: 130, border: `1.6px solid ${color}`, borderRadius: 12, padding: '8px 10px',
                    background: st === 'pend' ? '#08131f' : `${color}18`, textAlign: 'center', color,
                  }}
                >
                  <div className="dim" style={{ fontSize: 9, letterSpacing: 0.8, fontWeight: 700, color: st === 'pend' ? 'var(--dim)' : color }}>
                    {nd.role.toUpperCase()}
                  </div>
                  <div style={{ fontSize: 11.5, fontWeight: 650, color: st === 'pend' ? 'var(--muted)' : 'var(--text)' }}>{nd.name || '-'}</div>
                  <div className="dim mono" style={{ fontSize: 9.5 }}>{nd.sub || ''}</div>
                </div>
                {i < nodes.length - 1 && (
                  <div style={{ flex: 1, textAlign: 'center', padding: '0 4px' }}>
                    <div className={`lng-track${i === flowingConnector ? ' flow' : ''}`} style={{ background: i < journey.target ? '#2f6f63' : '#24405c' }} />
                    <div className="dim mono" style={{ fontSize: 9.5, marginTop: 4 }}>{hopMsg(p, i, nodes)}</div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
      <div className="term">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.8 }}>JOURNEY STATE</span>
          <Badge value={p.status} />
          <span className="mono" style={{ color: col, fontWeight: 800 }}>{statusIndicator(p)}</span>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>{where}</span>
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
        Nodes are populated from this payment's real party and hop records; correspondent / intermediary hops appear only when present.
      </div>
      <h3 style={{ marginTop: 16 }}>Latest Events <span className="dim" style={{ fontSize: 11 }}>see EVENTS tab for full timeline</span></h3>
      <div className="evlog" style={{ maxHeight: 'none' }}>
        <table>
          <tbody>
            {events.slice(-5).reverse().map((e) => (
              <tr key={e.seq}>
                <td className="mono dim" style={{ width: 70 }}>{e.event_ts.slice(11)}</td>
                <td style={{ width: 180 }}><Badge value={e.state} /></td>
                <td>{e.description} <span className="dim">- {e.actor}</span>{e.iso_message !== '\u2014' && <> - <span className="mono" style={{ color: 'var(--cyan)' }}>{e.iso_message}</span></>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function EventsTab({ events }: { events: PaymentEventRecord[] }) {
  return (
    <div>
      <div className="muted" style={{ marginBottom: 10 }}>{events.length} lifecycle events - source: <b style={{ color: 'var(--text)' }}>payment record / derived lifecycle</b></div>
      <div className="timeline">
        {events.map((e) => {
          const color = BADGE_COLOR[badgeCls(e.state)] ?? 'var(--muted)'
          return (
            <div className="tev" key={e.seq}>
              <span className="dot" style={{ background: color }} />
              <div className="h"><b>{e.description}</b><Badge value={e.state} /></div>
              <div className="meta">
                <span>Time: {e.event_ts}</span>
                <span>Actor: {e.actor}</span>
                <span>Message: {e.iso_message}</span>
                <span>Duration: +{fmtMs(e.processing_ms)}</span>
              </div>
              <div className="tr">{e.prev_state ?? '(start)'} -&gt; {e.state}{e.reason_text ? ` - reason: ${e.reason_text}` : ''}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function MessagesTab({
  paymentId, messageChain, messages, mtMxMapping, selectedMsg, onSelectMsg,
}: {
  paymentId: string; messageChain: string[]; messages: Record<string, string>; mtMxMapping: MtMxMappingRow[]
  selectedMsg: string; onSelectMsg: (m: string) => void
}) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.8, marginBottom: 6 }}>
        MESSAGE CHAIN FOR {paymentId}
      </div>
      <div className="path" style={{ marginBottom: 10 }}>
        {messageChain.map((m, i) => {
          const cls = m.includes('RJCT') ? 'r' : m.includes('ACCC') ? 'g' : m.includes('pacs.004') ? 'v' : 'bl'
          return (
            <span key={i}>
              <button type="button" className={`b nodot ${cls}`} style={{ cursor: 'pointer' }} onClick={() => onSelectMsg(m.split(' ')[0])}>{m}</button>
              {i < messageChain.length - 1 && <span className="ar">-&gt;</span>}
            </span>
          )
        })}
      </div>
      <div className="msglist">
        {ISO_TYPES.map((t) => (
          <button key={t} className={`btn sm ${t === selectedMsg ? 'on' : ''}`} onClick={() => onSelectMsg(t)}>{t}</button>
        ))}
      </div>
      <IsoXmlView xml={messages[selectedMsg] ?? ''} />
      <table className="maptbl">
        <thead><tr><th>Legacy MT</th><th>ISO 20022</th><th>Meaning</th></tr></thead>
        <tbody>
          {mtMxMapping.map((r) => (
            <tr key={r.mt_type}>
              <td className="mono">{r.mt_type}</td>
              <td className="mono" style={{ color: 'var(--cyan)' }}>{r.mx_type}</td>
              <td className="muted">{r.meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        Illustrative, semantically aligned samples generated from this payment's real fields. MT to MX is a semantic mapping only; element-level XPaths and full usage-guideline validation are not modelled.
      </div>
    </div>
  )
}

function PartiesTab({ p, hops }: { p: PaymentRecord; hops: PaymentHop[] }) {
  const roleLabels: Record<string, string> = {
    DEBTOR_AGENT: 'Debtor Agent', CORRESPONDENT: 'Correspondent', INTERMEDIARY: 'Intermediary', CREDITOR_AGENT: 'Creditor Agent',
  }
  const customers = [
    p.ultimateDebtor && p.ultimateDebtor !== p.debtor ? { role: 'Ultimate Debtor', name: p.ultimateDebtor } : null,
    { role: 'Debtor', name: p.debtor },
    { role: 'Creditor', name: p.creditor },
    p.ultimateCreditor && p.ultimateCreditor !== p.creditor ? { role: 'Ultimate Creditor', name: p.ultimateCreditor } : null,
  ].filter((c): c is { role: string; name: string } => c !== null)

  const rows: Array<[string, string | null, string | null, string | null, string | null]> = [
    ['Ultimate Debtor', p.ultimateDebtor, null, p.debtorCountry, null],
    ['Debtor', p.debtor, null, p.debtorCountry, p.debtorAccount],
    ...hops.map((h): [string, string, string, string | null, string | null] =>
      [roleLabels[h.role] ?? h.role, h.name, h.bic, h.country, h.role === 'CORRESPONDENT' ? p.nostro : null]),
    ['Creditor', p.creditor, null, p.creditorCountry, p.creditorAccount],
    ['Ultimate Creditor', p.ultimateCreditor, null, p.creditorCountry, null],
  ].filter((r) => r[1]) as Array<[string, string, string | null, string | null, string | null]>

  return (
    <div>
      <div className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.8, marginBottom: 6 }}>
        PARTY &amp; BANK GRAPH - customer tier (top) - bank tier (bottom)
      </div>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${customers.length}, minmax(0,1fr))` }}>
        {customers.map((c) => (
          <div key={c.role} style={{ border: '1.4px solid var(--green)', borderRadius: 11, padding: '8px 10px', background: '#08131f', textAlign: 'center' }}>
            <div className="dim" style={{ fontSize: 9, letterSpacing: 0.8, fontWeight: 700, color: 'var(--green)' }}>{c.role.toUpperCase()}</div>
            <div style={{ fontSize: 11, fontWeight: 600 }}>{c.name}</div>
          </div>
        ))}
      </div>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${hops.length}, minmax(0,1fr))`, marginTop: 30 }}>
        {hops.map((h) => (
          <div key={h.role} style={{ border: '1.4px solid var(--cyan)', borderRadius: 11, padding: '8px 10px', background: '#08131f', textAlign: 'center' }}>
            <div className="dim" style={{ fontSize: 9, letterSpacing: 0.8, fontWeight: 700, color: 'var(--cyan)' }}>{(roleLabels[h.role] ?? h.role).toUpperCase()}</div>
            <div style={{ fontSize: 11, fontWeight: 600 }}>{h.name}</div>
            <div className="dim mono" style={{ fontSize: 9.5 }}>{h.bic} - {h.country}</div>
          </div>
        ))}
      </div>
      <table className="maptbl">
        <thead><tr><th>Role</th><th>Name</th><th>BIC</th><th>Country</th><th>Account / Nostro</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="muted">{r[0]}</td>
              <td><b>{r[1]}</b></td>
              <td className="mono">{r[2] || '-'}</td>
              <td>{r[3] || '-'}</td>
              <td className="mono">{r[4] || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function chk(ok: boolean | null, title: string, desc: string, key: string) {
  const color = ok === true ? 'var(--green)' : ok === false ? 'var(--red)' : 'var(--amber)'
  const bg = ok === true ? 'rgba(70,230,165,.15)' : ok === false ? 'rgba(255,102,133,.15)' : 'rgba(255,200,87,.15)'
  const icon = ok === true ? 'OK' : ok === false ? 'X' : '!'
  return (
    <div className="chk" key={key}>
      <span className="ic" style={{ background: bg, color }}>{icon}</span>
      <div><b>{title}</b><div className="muted" style={{ fontSize: 11.5 }}>{desc}</div></div>
    </div>
  )
}

function LifecyclePanel({ p, events, hops }: { p: PaymentRecord; events: PaymentEventRecord[]; hops: PaymentHop[] }) {
  const last = events.length - 1
  const rows = events.map((e, i) => {
    let cls = i < last ? 'done' : ({ COMPLETED: 'done', REJECTED: 'err', FAILED: 'err', RETURNED: 'ret', INVESTIGATION: 'warn', CANCELLED: 'err' }[p.status] ?? 'cur')
    if (i < last && (e.state === 'REJECTED' || e.state === 'FAILED')) cls = 'err'
    if (i < last && e.state === 'INVESTIGATION') cls = 'warn'
    return { cls, e }
  })

  let pendingStates: string[] = []
  if (p.status === 'IN_PROGRESS') {
    const cur = events.length ? events[events.length - 1].state : 'INITIATED'
    const ci = HAPPY_PATH.lastIndexOf(cur)
    pendingStates = HAPPY_PATH.slice(ci + 1).filter((s) => s !== 'COMPLETED')
  }

  const roleLabels: Record<string, string> = {
    DEBTOR_AGENT: 'Debtor Agent', CORRESPONDENT: 'Correspondent', INTERMEDIARY: 'Intermediary', CREDITOR_AGENT: 'Creditor Agent',
  }
  const ac03 = reasonCode(p.statusReason) === 'AC03'
  const screening = String(p.screening || '')

  return (
    <div className="card c3">
      <h3>Lifecycle <span className="dim" style={{ fontSize: 11 }}>{events.length} events</span></h3>
      <div className="stepper">
        {rows.map(({ cls, e }) => (
          <div className={`stp ${cls}`} key={e.seq}>
            <span className="d" />
            <span>{e.state}</span>
            <span className="dim mono" style={{ fontSize: 10.5 }}>{e.event_ts.slice(11)}</span>
          </div>
        ))}
        {pendingStates.map((s) => (
          <div className="stp pend" key={s}>
            <span className="d" />
            <span>{s}</span>
            <span className="dim" style={{ fontSize: 10.5 }}>expected</span>
          </div>
        ))}
        {p.status === 'IN_PROGRESS' && (
          <div className="stp pend">
            <span className="d" />
            <span>Awaiting final confirmation (ACCC)</span>
            <span className="dim" style={{ fontSize: 10.5 }}>pending</span>
          </div>
        )}
        {p.status !== 'COMPLETED' && p.status !== 'IN_PROGRESS' && (
          <div className="muted" style={{ fontSize: 11.5, padding: '4px 8px' }}>
            Path terminates here - {stateLabel(p)}. No completion event exists for this payment.
          </div>
        )}
      </div>

      <h3 style={{ marginTop: 14 }}>Hop Tracker <span className="dim" style={{ fontSize: 11 }}>{p.domain === 'CBCC' ? `gpi-style - ${p.gpiStatus}` : 'domestic rail'}</span></h3>
      <div className="gpi">
        {hops.map((h) => {
          const reached = events.some((e) => e.actor.includes(h.name))
          return (
            <div className="hopcard" key={h.role}>
              <div className="dim" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.6 }}>{(roleLabels[h.role] ?? h.role).toUpperCase()}</div>
              <div className="bn" title={h.name}>{h.name}</div>
              <div style={{ color: reached ? 'var(--green)' : 'var(--dim)', fontWeight: 700, fontSize: 10.5 }}>{reached ? 'reached' : 'not yet reached'}</div>
            </div>
          )
        })}
      </div>

      <h3 style={{ marginTop: 14 }}>Operations</h3>
      <dl className="kv" style={{ marginTop: 0 }}>
        <dt>Owner</dt><dd>{p.owner} - {p.paymentInitiationDept}</dd>
        <dt>SLA</dt><dd><Badge value={p.slaState} /></dd>
        <dt>Screening</dt><dd><Badge value={p.screening} /> <span className="dim">risk {p.riskScore}</span></dd>
        <dt>Liquidity</dt><dd><Badge value={p.liquidityState} /> <span className="dim">{p.nostro}</span></dd>
        <dt>Investigation</dt><dd className="mono">{p.investigationId || '-'}</dd>
        <dt>Reconciliation</dt><dd><Badge value={p.reconState} /></dd>
        <dt>Kuber</dt><dd style={{ color: 'var(--violet)' }}>{p.aiRecommendation}</dd>
      </dl>

      <h3 style={{ marginTop: 14 }}>Readiness Checks</h3>
      {chk(
        p.domain !== 'CBCC' ? true : p.addressFormat === 'STRUCTURED' ? true : p.addressFormat === 'HYBRID' ? null : false,
        'CBPR+ SR2026 postal address',
        p.domain !== 'CBCC' ? 'Not applicable (domestic)' : p.addressFormat === 'STRUCTURED' ? 'Structured address - compliant' : p.addressFormat === 'HYBRID' ? 'Hybrid - accepted; town + country must be structured' : 'Unstructured - will be rejected after Nov 2026 cut-over',
        'address',
      )}
      {chk(
        ac03 ? false : p.domain === 'CBCC' ? null : true,
        'Beneficiary account pre-validation',
        ac03 ? 'AC03 - account invalid; pre-validation would have caught this' : p.domain === 'CBCC' ? 'Recommended before release for high-value cross-border' : 'Domestic routing/account verified',
        'prevalidate',
      )}
      {chk(
        screening.includes('MATCH') ? false : screening.includes('RELEASED') ? null : true,
        'Sanctions / AML screening',
        p.screening,
        'screening',
      )}
      {chk(
        p.liquidityState === 'AVAILABLE' ? true : p.liquidityState === 'TIGHT' ? null : false,
        'Intraday liquidity',
        `${p.liquidityState} - ${p.nostro}`,
        'liquidity',
      )}
    </div>
  )
}

export default function Payment360() {
  const [searchParams] = useSearchParams()
  const paymentId = searchParams.get('paymentId')
  const { data, isLoading, isError, error } = usePaymentDetail(paymentId)
  const { data: simStatus } = useSimStatus(paymentId)
  const [tab, setTab] = useState<Tab>('LINEAGE')
  const [selectedMsg, setSelectedMsg] = useState<string | null>(null)

  if (!paymentId) {
    return (
      <section className="blk" style={{ marginTop: 6 }}>
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <h2>Payment 360</h2>
          <p className="muted" style={{ marginTop: 8 }}>
            No payment selected. Pick a row in{' '}
            <Link to={{ pathname: '/discovery', search: searchParams.toString() }}>Payment Discovery</Link> to open its 360 view here.
          </p>
        </div>
      </section>
    )
  }

  if (isLoading) {
    return (
      <section className="blk" style={{ marginTop: 6 }}>
        <p className="muted">Loading payment 360...</p>
      </section>
    )
  }

  if (isError || !data) {
    return (
      <section className="blk" style={{ marginTop: 6 }}>
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <h2>Payment 360</h2>
          <p className="muted" style={{ marginTop: 8 }}>{(error as Error)?.message ?? `Payment '${paymentId}' not found.`}</p>
        </div>
      </section>
    )
  }

  const { payment: p, hops, events, messageChain, messages, mtMxMapping } = data
  const activeMsg = selectedMsg ?? p.messageType

  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="shead">
        <div>
          <h2>Payment 360 <span className="tag">{p.paymentId}</span></h2>
          <div className="ssub">Identity, lineage, lifecycle, ISO messages, parties</div>
        </div>
        <div className="toolbar">
          {p.isSimulated ? (
            <Link className="btn" to={{ pathname: '/payment-360', search: new URLSearchParams({ ...Object.fromEntries(searchParams), paymentId: p.sourcePaymentId ?? '' }).toString() }}>
              View real payment ({p.sourcePaymentId})
            </Link>
          ) : (
            <>
              <Link className="btn primary" to={{ pathname: '/simulation', search: new URLSearchParams({ ...Object.fromEntries(searchParams), paymentId: p.paymentId }).toString() }}>
                Simulate this payment
              </Link>
              {simStatus?.shadowPaymentId && (
                <Link className="btn" to={{ pathname: '/payment-360', search: new URLSearchParams({ ...Object.fromEntries(searchParams), paymentId: simStatus.shadowPaymentId }).toString() }}>
                  View simulated version
                </Link>
              )}
            </>
          )}
        </div>
      </div>
      {!!p.isSimulated && (
        <div className="banner warn" style={{ marginBottom: 12 }}>
          Simulated payment, cloned from {p.sourcePaymentId} - never affects the real payment or portfolio totals
        </div>
      )}
      <div className="p360">
        <IdentityCard p={p} />
        <div className="card c2">
          <div className="p360tabs">
            {TABS.map((t) => (
              <button key={t} className={t === tab ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>
          {tab === 'LINEAGE' && <LineageTab p={p} hops={hops} events={events} />}
          {tab === 'EVENTS' && <EventsTab events={events} />}
          {tab === 'MESSAGES' && (
            <MessagesTab
              paymentId={p.paymentId} messageChain={messageChain} messages={messages} mtMxMapping={mtMxMapping}
              selectedMsg={activeMsg} onSelectMsg={setSelectedMsg}
            />
          )}
          {tab === 'PARTIES' && <PartiesTab p={p} hops={hops} />}
        </div>
        <LifecyclePanel p={p} events={events} hops={hops} />
      </div>
    </section>
  )
}

