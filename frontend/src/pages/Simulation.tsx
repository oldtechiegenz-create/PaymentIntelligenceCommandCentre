import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { usePayments, usePaymentDetail, useScenarios, useSpeeds, useSimStatus } from '../lib/queries'
import { postSimStart, postSimPause, postSimReset, postSimInject, type PaymentHop, type ScenarioStep, type SimRun } from '../lib/api'
import { useWebSocketContext } from '../lib/WebSocketContext'
import { Badge } from '../components/Badge'
import JsonView from '../components/JsonView'

const ERR_STATES = new Set(['REJECTED', 'FAILED'])
const ROLE_LABELS: Record<string, string> = {
  DEBTOR_AGENT: 'Debtor Agent', CORRESPONDENT: 'Correspondent', INTERMEDIARY: 'Intermediary', CREDITOR_AGENT: 'Creditor Agent',
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

function fmtElapsed(startedAt: string | undefined, running: boolean, finishedAt: string | null | undefined): string {
  if (!startedAt) return '0 ms'
  const start = new Date(`${startedAt.replace(' ', 'T')}Z`).getTime()
  const end = running ? Date.now() : finishedAt ? new Date(`${finishedAt.replace(' ', 'T')}Z`).getTime() : Date.now()
  return fmtMs(Math.max(0, end - start))
}

function stepNodeClass(i: number, state: string, eventsCount: number, run: SimRun | null): string {
  if (!run) return ''
  if (run.failureInjected) {
    return i < eventsCount ? (ERR_STATES.has(state) ? 'error' : state === 'INVESTIGATION' ? 'warnn' : 'done') : 'skip'
  }
  const cur = eventsCount - 1
  if (i < cur) return ERR_STATES.has(state) ? 'error' : state === 'INVESTIGATION' ? 'warnn' : 'done'
  if (i === cur) {
    if (state === 'COMPLETED') return 'done'
    if (ERR_STATES.has(state)) return 'error'
    if (state === 'INVESTIGATION') return 'warnn'
    if (state === 'RETURNED') return 'retn'
    return run.mode === 'RUNNING' ? 'active' : 'done'
  }
  return ''
}

function outcomeColor(outcome: string | undefined): string {
  if (outcome === 'COMPLETED') return 'var(--green)'
  if (outcome?.includes('FAIL')) return 'var(--red)'
  if (outcome === 'RETURNED') return 'var(--violet)'
  if (outcome === 'RUNNING') return 'var(--cyan)'
  if (outcome === 'PAUSED') return 'var(--amber)'
  return 'var(--text)'
}

interface JourneyNode { role: string; name: string; sub: string }

function journeyNodes(p: { debtor: string; creditor: string } | null, hops: PaymentHop[]): JourneyNode[] {
  if (!p) return []
  const nodes: JourneyNode[] = [{ role: 'Debtor', name: p.debtor, sub: '' }]
  hops.forEach((h) => nodes.push({ role: ROLE_LABELS[h.role] ?? h.role, name: h.name, sub: h.bic }))
  nodes.push({ role: 'Creditor', name: p.creditor, sub: '' })
  return nodes
}

function journeyPosition(state: string, nodeCount: number): number {
  const iDA = 1
  const iCA = nodeCount - 2
  const table: Record<string, number> = {
    INITIATED: 0, ACCEPTED: iDA, SCREENING: iDA, SENT: iDA,
    IN_TRANSIT: Math.max(iDA, Math.round((iDA + iCA) / 2)), CORRESPONDENT_PROCESSING: Math.max(iDA, Math.round((iDA + iCA) / 2)),
    SETTLEMENT_PENDING: iCA, COMPLETED: nodeCount - 1,
    REJECTED: iCA, RETURNED: iDA, INVESTIGATION: iDA, FAILED: iDA,
  }
  return table[state] ?? 0
}

export default function Simulation() {
  const [searchParams] = useSearchParams()
  const urlPaymentId = searchParams.get('paymentId')
  const [selectedPaymentId, setSelectedPaymentId] = useState<string | null>(urlPaymentId)
  const [selectedScenario, setSelectedScenario] = useState<string>('HAPPY')
  const [selectedSpeed, setSelectedSpeed] = useState<string>('1x')
  const [, forceTick] = useState(0)

  const queryClient = useQueryClient()
  const { events: wsEvents } = useWebSocketContext()

  const { data: scenarios } = useScenarios()
  const { data: speeds } = useSpeeds()
  const { data: paymentList } = usePayments({ cols: ['paymentId', 'rail', 'status'], page_size: 500, sort: 'paymentId' })
  const { data: status } = useSimStatus(selectedPaymentId, selectedScenario)
  const { data: sourceDetail } = usePaymentDetail(selectedPaymentId)

  const run = status?.activeRun ?? null
  const running = run?.mode === 'RUNNING'

  // WebSocket-driven refresh: as soon as an event lands for this source or its shadow, refetch.
  useEffect(() => {
    const latest = wsEvents[0]
    if (!latest || !selectedPaymentId) return
    if (latest.payment_id === selectedPaymentId || latest.payment_id === status?.shadowPaymentId) {
      queryClient.invalidateQueries({ queryKey: ['sim-status', selectedPaymentId] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsEvents])

  // Keep the scenario dropdown synced to whatever scenario the active run is actually using.
  useEffect(() => {
    if (run) setSelectedScenario(run.scenarioCode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.runId])

  // Elapsed-time ticker while a run is actively running (real wall-clock, from real startedAt).
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => forceTick((n) => n + 1), 200)
    return () => clearInterval(id)
  }, [running])

  const startMutation = useMutation({
    mutationFn: () => postSimStart(selectedPaymentId as string, selectedScenario, selectedSpeed),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sim-status', selectedPaymentId] }),
  })
  const pauseMutation = useMutation({
    mutationFn: () => postSimPause(selectedPaymentId as string),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sim-status', selectedPaymentId] }),
  })
  const resetMutation = useMutation({
    mutationFn: () => postSimReset(selectedPaymentId as string),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sim-status', selectedPaymentId] }),
  })
  const injectMutation = useMutation({
    mutationFn: () => postSimInject(selectedPaymentId as string),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sim-status', selectedPaymentId] }),
  })

  const steps: ScenarioStep[] = status?.steps ?? []
  const displaySteps = run?.failureInjected
    ? [...steps, { stepNo: steps.length + 1, state: 'FAILED', description: 'Failure injected by operator', isoMessage: 'pacs.002 RJCT' }]
    : steps
  const eventsCount = status?.events.length ?? 0

  const nodes = useMemo(
    () => journeyNodes(sourceDetail ? { debtor: sourceDetail.payment.debtor, creditor: sourceDetail.payment.creditor } : null, sourceDetail?.hops ?? []),
    [sourceDetail],
  )
  const lastEvent = status?.events[status.events.length - 1]
  const currentNodeIndex = lastEvent ? journeyPosition(lastEvent.state, nodes.length) : 0

  const payloadPreview = useMemo(() => {
    const p = status?.shadowPayment
    if (!p || !lastEvent) {
      return { status: 'READY', paymentId: selectedPaymentId, scenario: selectedScenario, note: 'Start the simulation to generate ISO 20022 events' }
    }
    return {
      businessMessageId: p.businessMsgId,
      uetr: p.uetr,
      paymentId: status?.sourcePaymentId,
      messageType: lastEvent.iso_message === '\u2014' ? '(internal event, no ISO message)' : lastEvent.iso_message.split(' ')[0],
      event: lastEvent.state,
      description: lastEvent.description,
      debtorAgent: p.debtorAgent,
      correspondent: p.correspondent,
      intermediary: p.intermediary,
      creditorAgent: p.creditorAgent,
      amount: p.amount,
      currency: p.debitCcy,
      runId: run?.runId,
      sequence: lastEvent.seq,
      scenario: status?.scenarioCode,
      timestamp: lastEvent.event_ts,
      previousState: lastEvent.prev_state,
      actor: lastEvent.actor,
      simulatedLatencyMs: lastEvent.processing_ms,
      failureInjected: !!run?.failureInjected,
    }
  }, [status, lastEvent, run, selectedPaymentId, selectedScenario])

  const scenarioMeta = scenarios?.find((s) => s.scenarioCode === selectedScenario)
  const progressPct = displaySteps.length ? Math.min(100, (eventsCount / displaySteps.length) * 100) : 0
  const progClass = run?.failureInjected || run?.outcome?.includes('FAIL') || run?.outcome === 'REJECTED'
    ? 'bad' : run?.outcome === 'RETURNED' ? 'ret' : ''

  const banner = run?.mode === 'FINISHED'
    ? run.failureInjected
      ? { cls: 'bad', text: 'FAILURE INJECTED - payment FAILED - pacs.002 RJCT emitted' }
      : run.outcome === 'COMPLETED'
        ? { cls: 'ok', text: 'COMPLETED - pacs.002 ACCC - settlement confirmed' }
        : run.outcome === 'FAILED'
          ? { cls: 'bad', text: 'FAILED - network SLA breached - payment unresolved (not completed)' }
          : run.outcome === 'RETURNED'
            ? { cls: 'ret', text: 'RETURNED - REJECTED (AC03) -> pacs.004 return generated (not completed)' }
            : { cls: 'warn', text: `Simulation finished - ${run.outcome}` }
    : null

  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="shead">
        <div>
          <h2>Payment Simulation Engine <span className="tag">EVENT-DRIVEN</span></h2>
          <div className="ssub">Deterministic state machine - real ISO 20022 event generation - runs against a shadow payment, never the source</div>
        </div>
      </div>

      <div className="sim">
        <div className="card">
          <h3>Control Plane <span className="dim" style={{ fontSize: 11 }}>{scenarioMeta?.scenarioName ?? ''}</span></h3>
          <div className="simctl">
            <label className="f">
              Scenario
              <select className="inp" value={selectedScenario} disabled={running} onChange={(e) => setSelectedScenario(e.target.value)}>
                {scenarios?.map((s) => <option key={s.scenarioCode} value={s.scenarioCode}>{s.scenarioName}</option>)}
              </select>
            </label>
            <label className="f">
              Payment
              <select
                className="inp" value={selectedPaymentId ?? ''} disabled={running}
                onChange={(e) => setSelectedPaymentId(e.target.value || null)}
              >
                <option value="">Select a payment...</option>
                {paymentList?.rows.map((p) => (
                  <option key={String(p.paymentId)} value={String(p.paymentId)}>{p.paymentId} - {p.rail} - {p.status}</option>
                ))}
              </select>
            </label>
            <label className="f">
              Speed
              <div className="speed">
                {(speeds ?? []).map((s) => (
                  <button key={s.speedCode} className={selectedSpeed === s.speedCode ? 'on' : ''} disabled={running} onClick={() => setSelectedSpeed(s.speedCode)}>
                    {s.speedCode}
                  </button>
                ))}
              </div>
            </label>
          </div>
          <div className="simbtns">
            <button className="btn primary" disabled={!selectedPaymentId || running} onClick={() => startMutation.mutate()}>Start</button>
            <button className="btn" disabled={!running} onClick={() => pauseMutation.mutate()}>Pause</button>
            <button className="btn" disabled={!status?.shadowPaymentId} onClick={() => resetMutation.mutate()}>Reset</button>
            <button className="btn danger" disabled={!running} onClick={() => injectMutation.mutate()}>Inject Failure</button>
            {!selectedPaymentId && <span className="muted" style={{ fontSize: 11.5 }}>Select a payment to begin</span>}
          </div>
          {banner && <div className={`banner ${banner.cls}`}>{banner.text}</div>}

          <div className="simstats">
            <div className="ss"><div className="l">Mode</div><div className="v" style={{ color: outcomeColor(run?.mode) }}>{run?.mode ?? 'READY'}</div></div>
            <div className="ss"><div className="l">Outcome</div><div className="v" style={{ color: outcomeColor(run?.outcome) }}>{run?.outcome ?? 'NO RUN'}</div></div>
            <div className="ss"><div className="l">Current State</div><div className="v">{lastEvent?.state ?? 'READY'}</div></div>
            <div className="ss"><div className="l">Elapsed</div><div className="v">{fmtElapsed(run?.startedAt, running, run?.finishedAt)}</div></div>
            <div className="ss"><div className="l">Events</div><div className="v">{run?.eventCount ?? 0}</div></div>
            <div className="ss"><div className="l">ISO Msgs</div><div className="v">{run?.isoMessageCount ?? 0} ISO messages</div></div>
            <div className="ss"><div className="l">Exceptions</div><div className="v">{run?.exceptionCount ?? 0} exceptions</div></div>
          </div>

          <h3 style={{ marginTop: 16 }}>State Machine <span className="dim" style={{ fontSize: 11 }}>step {Math.min(eventsCount, displaySteps.length)} / {displaySteps.length}</span></h3>
          <div className="smach" style={{ ['--n' as string]: displaySteps.length }}>
            {displaySteps.map((s, i) => (
              <div key={i} className={`smn ${stepNodeClass(i, s.state, eventsCount, run)}`}>
                <span className="ix">{i + 1}</span>
                <div className="st">{s.state.replace(/_/g, ' ')}</div>
                <div className="ms">{s.isoMessage}</div>
              </div>
            ))}
          </div>
          <div className={`prog ${progClass}`}><i style={{ width: `${progressPct}%` }} /></div>

          <h3 style={{ marginTop: 16 }}>Simulated Network Journey <span className="dim" style={{ fontSize: 11 }}>real hop chain - built from events</span></h3>
          <div className="seqwrap" style={{ padding: 12 }}>
            {nodes.length === 0 ? (
              <p className="muted" style={{ padding: 8 }}>Select a payment to preview its network journey.</p>
            ) : (
              <>
                <div style={{ display: 'flex', gap: 0, minWidth: 640 }}>
                  {nodes.map((nd, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', flex: i < nodes.length - 1 ? 1 : 'none' }}>
                      <div
                        style={{
                          minWidth: 110, border: `1.4px solid ${i === currentNodeIndex && running ? 'var(--cyan)' : i <= currentNodeIndex ? 'var(--green)' : 'var(--line)'}`,
                          borderRadius: 10, padding: '6px 8px', textAlign: 'center', background: '#08131f',
                        }}
                      >
                        <div className="dim" style={{ fontSize: 9, fontWeight: 700 }}>{nd.role.toUpperCase()}</div>
                        <div style={{ fontSize: 11, fontWeight: 600 }}>{nd.name}</div>
                      </div>
                      {i < nodes.length - 1 && <div style={{ flex: 1, height: 2, background: i < currentNodeIndex ? '#2f6f63' : '#24405c' }} />}
                    </div>
                  ))}
                </div>
                {eventsCount === 0 ? (
                  <p className="muted" style={{ marginTop: 10 }}>Message sequence appears here as the simulation emits events.</p>
                ) : (
                  <div className="path" style={{ marginTop: 10 }}>
                    {status?.events.map((e, i) => (
                      <span key={e.seq}>
                        {e.state}{e.iso_message !== '\u2014' ? ` (${e.iso_message})` : ''}
                        {i < (status.events.length - 1) && <span className="ar">-&gt;</span>}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="grid" style={{ alignContent: 'start' }}>
          <div className="card">
            <h3>Event Stream <span className="dim" style={{ fontSize: 11 }}>newest first</span></h3>
            <div className="evlog">
              <table>
                <thead><tr><th style={{ width: 62 }}>Time</th><th style={{ width: 170 }}>State / Event</th><th>Detail</th></tr></thead>
                <tbody>
                  {!status?.events.length ? (
                    <tr><td colSpan={3} className="muted" style={{ padding: 14 }}>No events. Choose a scenario and press Start.</td></tr>
                  ) : (
                    status.events.slice().reverse().map((e) => (
                      <tr key={e.seq}>
                        <td className="mono">{e.processing_ms ? fmtMs(e.processing_ms) : '-'}</td>
                        <td><Badge value={e.state} /></td>
                        <td>
                          {e.description}
                          {e.iso_message !== '\u2014' && <> - <span className="mono" style={{ color: 'var(--cyan)' }}>{e.iso_message}</span></>}
                          {e.reason_text && <div style={{ color: 'var(--red)', fontSize: 11 }}>{e.reason_text}</div>}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <h3>Simulation Metrics</h3>
            <div className="metric3">
              <div className="ss"><div className="l">Latency</div><div className="v">{fmtMs(run?.simulatedLatencyMs ?? 0)}</div></div>
              <div className="ss"><div className="l">Hops</div><div className="v">{sourceDetail?.hops.length ?? 0}</div></div>
              <div className="ss"><div className="l">ISO Messages</div><div className="v">{run?.isoMessageCount ?? 0}</div></div>
            </div>
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 10.5, letterSpacing: 0.8, fontWeight: 700, textTransform: 'uppercase' }}>Scenario</div>
              <div style={{ marginTop: 4 }}>{scenarioMeta?.description}</div>
              <div className="muted" style={{ fontSize: 10.5, letterSpacing: 0.8, fontWeight: 700, textTransform: 'uppercase', marginTop: 10 }}>Simulation path</div>
              <div className="path">
                {steps.map((s, i) => (
                  <span key={i}>
                    <span style={{ color: s.state === 'COMPLETED' ? 'var(--green)' : ERR_STATES.has(s.state) ? 'var(--red)' : s.state === 'RETURNED' ? 'var(--violet)' : s.state === 'INVESTIGATION' ? 'var(--amber)' : '#b9d3ea' }}>{s.state}</span>
                    {i < steps.length - 1 && <span className="ar">-&gt;</span>}
                  </span>
                ))}
                {run?.failureInjected && <><span className="ar">~&gt;</span><span style={{ color: 'var(--red)' }}>FAILED (injected)</span></>}
              </div>
            </div>
          </div>

          <div className="card">
            <h3>ISO Event Payload <span className="dim" style={{ fontSize: 11 }}>{lastEvent ? `seq ${lastEvent.seq} - ${lastEvent.state}` : 'awaiting first event'}</span></h3>
            <JsonView data={payloadPreview} />
          </div>
        </div>
      </div>
    </section>
  )
}
