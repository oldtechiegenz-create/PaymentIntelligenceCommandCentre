import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { usePayments, usePaymentDetail } from '../lib/queries'
import { postKuberAsk, type KuberAskResponse, type PaymentDetailResponse } from '../lib/api'

interface ChatMessage {
  role: 'user' | 'agent' | 'system'
  text: string
  agentKey?: string
  confidence?: number | null
}

interface AgentDisplay {
  key: string
  icon: string
  color: string
  label: string
  role: string
  status: string
  chips: string[]
}

// Full POC roster (\u00a714) \u2014 only orchestrator/investigator/risk are backed by a real
// LangGraph specialist today; the rest render for visual parity and route through the
// Orchestrator until their own specialist is built.
const AGENTS: AgentDisplay[] = [
  { key: 'orchestrator', icon: '\u25ce', color: 'var(--cyan)', label: 'Orchestrator', role: 'Routes intents - composes evidence',
    status: 'coordinating 7 agents', chips: ['Explain this payment', 'Why is it not complete?', 'What should I do next?'] },
  { key: 'investigator', icon: '\u2315', color: 'var(--amber)', label: 'Payment Investigator', role: 'Exceptions - returns - root cause',
    status: '14 open cases', chips: ['Root cause?', 'Why rejected?', 'Draft investigation'] },
  { key: 'correspondent', icon: '\u21c4', color: 'var(--blue)', label: 'Correspondent Agent', role: 'Correspondent chain - gpi tracking',
    status: 'watching 31 chains', chips: ['Where is the payment?', 'Show correspondent chain', 'gpi status?'] },
  { key: 'risk', icon: '\u26e8', color: 'var(--red)', label: 'Risk & Screening Agent', role: 'Sanctions - AML - fraud signals',
    status: '3 alerts in review', chips: ['Screening & risk?', 'Any sanctions exposure?', 'Fraud signals?'] },
  { key: 'liquidity', icon: '\u2248', color: 'var(--green)', label: 'Liquidity Agent', role: 'Nostro - intraday liquidity',
    status: '12 accounts', chips: ['Liquidity impact?', 'Nostro position?', 'Mandate readiness?'] },
  { key: 'reconciliation', icon: '\u2696', color: 'var(--violet)', label: 'Reconciliation Agent', role: 'camt.053 / camt.054 matching',
    status: '98.9% auto-matched', chips: ['Reconciliation state?', 'Which camt matches?', 'Open breaks?'] },
  { key: 'iso', icon: '</>', color: 'var(--cyan)', label: 'ISO Interpreter', role: 'ISO 20022 semantics - MT mapping',
    status: '10 message families', chips: ['Show ISO chain', 'MT103 mapping?', 'SR2026 address readiness'] },
]

const EVIDENCE_ROWS: Array<{ key: string; label: string }> = [
  { key: 'IDENTITY', label: 'IDENTITY' }, { key: 'ISO', label: 'ISO' }, { key: 'LINEAGE', label: 'LINEAGE' },
  { key: 'STATE', label: 'STATE' }, { key: 'RISK', label: 'RISK' }, { key: 'LIQUIDITY', label: 'LIQUIDITY' }, { key: 'OWNER', label: 'OWNER' },
]

// Mirrors backend/app/ai_agents/graph/confidence.py exactly \u2014 the evidence panel is
// scoped to the selected PAYMENT, not to a chat answer, so it must populate as soon as a
// payment is chosen, the same way the POC's evidence panel always did.
function computeEvidence(detail: PaymentDetailResponse): Record<string, boolean> {
  const p = detail.payment
  return {
    IDENTITY: !!p.paymentId && !!(p.uetr || p.domain === 'DOME'),
    ISO: !!(p.messageType && p.instructionId && p.endToEndId),
    LINEAGE: detail.hops.length >= 2,
    STATE: !!(p.status && (p.intermediateStatus || p.slaState)),
    RISK: !!p.screening,
    LIQUIDITY: !!p.liquidityState,
    OWNER: !!(p.owner && p.paymentInitiationDept),
  }
}

function evidenceValue(key: string, detail: PaymentDetailResponse | undefined): string {
  if (!detail) return '\u2014'
  const p = detail.payment
  switch (key) {
    case 'IDENTITY': return `${p.paymentId} \u00b7 ${p.uetr ?? 'domestic (no UETR)'}`
    case 'ISO': return `${p.messageType} \u00b7 InstrId ${p.instructionId} \u00b7 E2E ${p.endToEndId}`
    case 'LINEAGE': return detail.hops.map((h) => h.name).join(' \u2192 ') || '\u2014'
    case 'STATE': return `${p.status} \u00b7 ${p.intermediateStatus} \u00b7 ${p.statusReason ?? '\u2014'}`
    case 'RISK': return `Screening ${p.screening}${p.riskScore != null ? ` \u00b7 score ${p.riskScore}` : ''}`
    case 'LIQUIDITY': return `${p.liquidityState}${p.nostro ? ` \u00b7 ${p.nostro}` : ''}`
    case 'OWNER': return `${p.owner ?? '\u2014'} \u00b7 ${p.paymentInitiationDept ?? '\u2014'}`
    default: return '\u2014'
  }
}

interface ProposedAction { title: string; description: string }

function proposedActionsFor(detail: PaymentDetailResponse | undefined): ProposedAction[] {
  if (!detail) return []
  const p = detail.payment
  switch (p.status) {
    case 'COMPLETED':
      return [
        { title: 'Confirm camt.054 match and close', description: 'Reconciliation matched - safe to close the case.' },
        { title: 'Send completion advice', description: 'Notify the client the payment has settled.' },
      ]
    case 'REJECTED':
      return [
        { title: 'Validate the beneficiary account (pre-validation)', description: p.statusReason ?? 'Rejected by the network.' },
        { title: 'Prepare the pacs.004 return / repair', description: 'Repair the instruction or initiate a return.' },
        { title: 'Notify the relationship manager', description: 'Keep the client informed ahead of any repair.' },
      ]
    case 'RETURNED':
      return [
        { title: 'Credit the return to the debtor', description: 'Funds must be credited back per the return reason.' },
        { title: 'Close the investigation with camt.029', description: 'Send resolution of investigation advice.' },
      ]
    case 'INVESTIGATION':
      return p.screening !== 'CLEARED'
        ? [{ title: 'Assign to an L2 analyst (four-eyes)', description: 'Screening alert requires dual control review.' },
           { title: 'Keep funds on hold', description: 'No release until screening is resolved.' }]
        : [{ title: 'Draft an investigation request to the correspondent', description: p.statusReason ?? 'Awaiting correspondent response.' },
           { title: 'Block duplicate re-sends', description: 'Prevent a second instruction while under investigation.' }]
    case 'FAILED':
      return [
        { title: 'Open a network incident', description: p.statusReason ?? 'Network-level failure.' },
        { title: 'Verify no duplicate before re-sending', description: 'Confirm the original never settled.' },
      ]
    default:
      return p.slaState === 'AT_RISK' || p.intermediateStatus === 'WAITING_CORRESPONDENT'
        ? [{ title: `Escalate acknowledgement with ${detail.hops.find((h) => h.role === 'CORRESPONDENT')?.name ?? 'the next hop'}`,
             description: `Current ${p.intermediateStatus} - SLA ${p.slaState}` }]
        : [{ title: 'Continue monitoring', description: 'On track, no action required yet.' },
           { title: 'Reserve liquidity on the nostro', description: p.nostro ?? 'Protect settlement capacity.' }]
  }
}

// Only these are backed by a real LangGraph specialist today (see AGENTS comment above);
// selecting any other agent card still routes through the Orchestrator until built.
const BACKED_AGENT_KEYS = new Set(['orchestrator', 'investigator', 'risk', 'liquidity'])

export default function Kuber() {
  const [searchParams] = useSearchParams()
  const [paymentId, setPaymentId] = useState(searchParams.get('paymentId') ?? '')
  const [activeAgentKey, setActiveAgentKey] = useState('orchestrator')
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [latest, setLatest] = useState<KuberAskResponse | null>(null)
  const [approved, setApproved] = useState<Set<string>>(new Set())

  const { data: paymentList } = usePayments({ cols: ['paymentId'], page_size: 500, sort: 'paymentId' })
  const { data: detail } = usePaymentDetail(paymentId || null)
  const activeAgent = AGENTS.find((a) => a.key === activeAgentKey) ?? AGENTS[0]
  const actions = useMemo(() => proposedActionsFor(detail), [detail])
  const evidence = useMemo(() => (detail ? computeEvidence(detail) : null), [detail])
  // Confidence is reported by the LLM per answer (not derivable from the payment alone), so the
  // gauge shows the latest answer's value and stays empty until a question has been asked.
  const confidence = latest?.confidence ?? null

  const askMutation = useMutation({
    mutationFn: (q: string) => postKuberAsk(paymentId, q, BACKED_AGENT_KEYS.has(activeAgentKey) ? activeAgentKey : undefined),
    onSuccess: (res) => {
      setMessages((m) => [...m, { role: 'agent', text: res.answer, agentKey: res.routed_to, confidence: res.confidence }])
      setLatest(res)
    },
    onError: (err: Error) => {
      setMessages((m) => [...m, { role: 'system', text: err.message }])
    },
  })

  function ask(text?: string) {
    const q = (text ?? question).trim()
    if (!paymentId || !q || askMutation.isPending) return
    setMessages((m) => [...m, { role: 'user', text: q }])
    setQuestion('')
    askMutation.mutate(q)
  }

  function approve(action: ProposedAction) {
    setApproved((s) => new Set(s).add(action.title))
    setMessages((m) => [...m, {
      role: 'system',
      text: `Human approval recorded for "${action.title}" on ${paymentId}. No external action was executed — this is a simulation.`,
    }])
  }

  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="shead">
        <div>
          <h2>Kuber Agentic Operations <span className="tag">AI · SIMULATED</span></h2>
          <div className="ssub">Deterministic multi-agent observation over the active payment — evidence-bound, human-approved</div>
        </div>
        <div className="stat-row">
          <span>Active payment <b>{paymentId || '\u2014'}</b></span>
          <span>Agents <b>{AGENTS.length}</b></span>
        </div>
      </div>

      <div className="kuber">
        <div className="card">
          <h3>Agents</h3>
          {AGENTS.map((a) => (
            <div
              key={a.key}
              className={`kagent ${a.key === activeAgentKey ? 'active' : ''}`}
              onClick={() => setActiveAgentKey(a.key)}
            >
              <span className="kicon" style={{ color: a.color, borderColor: a.color }}>{a.icon}</span>
              <div style={{ minWidth: 0 }}>
                <div className="nm">{a.label}</div>
                <div className="dim" style={{ fontSize: 11 }}>{'\u2022'} {a.status}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="card">
          <h3>{activeAgent.label} <span className="r">{activeAgent.role}</span></h3>

          <select className="inp" value={paymentId} onChange={(e) => setPaymentId(e.target.value)} style={{ width: '100%', marginBottom: 10 }}>
            <option value="">Select a payment...</option>
            {(paymentList?.rows ?? []).map((p) => (
              <option key={String(p.paymentId)} value={String(p.paymentId)}>{String(p.paymentId)}</option>
            ))}
          </select>

          <div className="evlog" style={{ minHeight: 260, maxHeight: 380 }}>
            {messages.length === 0 && <div className="muted" style={{ padding: 10 }}>Select a payment and ask a question.</div>}
            {messages.map((m, i) => (
              <div key={i} style={{ margin: '8px 10px', textAlign: m.role === 'user' ? 'right' : 'left' }}>
                {m.role === 'agent' && (
                  <div style={{ fontSize: 11, fontWeight: 700, color: AGENTS.find((a) => a.key === m.agentKey)?.color ?? 'var(--cyan)' }}>
                    {AGENTS.find((a) => a.key === m.agentKey)?.label ?? m.agentKey}
                  </div>
                )}
                <span
                  style={{
                    display: 'inline-block', maxWidth: '85%', textAlign: 'left', whiteSpace: 'pre-wrap', fontSize: m.role === 'system' ? 11.5 : 13,
                    background: m.role === 'user' ? 'linear-gradient(135deg,#1aa7d6,#3d6fe0)' : m.role === 'system' ? 'transparent' : '#0f2033',
                    border: m.role === 'system' ? '1px dashed rgba(255,200,87,.4)' : '1px solid var(--line2)',
                    color: m.role === 'system' ? 'var(--amber)' : m.role === 'user' ? '#fff' : 'inherit',
                    borderRadius: 10, padding: '8px 10px',
                  }}
                >
                  {m.text}
                  {m.role === 'agent' && m.confidence != null && (
                    <div className="dim" style={{ marginTop: 6, fontSize: 11, borderTop: '1px dashed var(--line2)', paddingTop: 6 }}>
                      Confidence: <b style={{ color: 'var(--cyan)' }}>{m.confidence.toFixed(2)}</b> · Action: human approval required.
                    </div>
                  )}
                </span>
              </div>
            ))}
            {latest && (
              <div className="banner warn" style={{ margin: '8px 10px' }}>{latest.disclaimer}</div>
            )}
          </div>

          <div className="msglist" style={{ marginTop: 10 }}>
            {activeAgent.chips.map((c) => (
              <button key={c} className="btn sm" onClick={() => ask(c)} disabled={!paymentId || askMutation.isPending}>{c}</button>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="inp"
              style={{ flex: 1 }}
              placeholder="Ask Kuber... e.g. why is this payment not complete?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && ask()}
            />
            <button className="btn primary" disabled={!paymentId || !question.trim() || askMutation.isPending} onClick={() => ask()}>
              {askMutation.isPending ? 'Asking...' : 'Ask Kuber'}
            </button>
          </div>
        </div>

        <div className="card">
          <h3>Evidence <span className="r">{paymentId || '\u2014'} {paymentId ? '\u00b7' : ''} {detail?.payment.status ?? ''}</span></h3>
          <div className="gauge">
            <div className="g1">
              <div className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.8px' }}>CONFIDENCE</div>
              <div className="v c-cyan">{confidence != null ? confidence.toFixed(2) : '\u2014'}</div>
            </div>
            <div className="g1">
              <div className="muted" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.8px' }}>EVIDENCE</div>
              <div className="v c-green">
                {evidence ? `${Math.round((Object.values(evidence).filter(Boolean).length / EVIDENCE_ROWS.length) * 100)}%` : '0%'}
              </div>
            </div>
          </div>

          {EVIDENCE_ROWS.map((row) => (
            <div key={row.key} className="evi">
              <span className="k">{row.label}</span>
              <span style={{ overflowWrap: 'anywhere' }}>{evidenceValue(row.key, detail)}</span>
              <span className={evidence?.[row.key] ? 'c-green' : 'c-red'}>{evidence ? (evidence[row.key] ? '\u2713' : '\u2717') : '\u2014'}</span>
            </div>
          ))}

          <h3 style={{ marginTop: 14 }}>Proposed Actions <span className="r">human approval boundary</span></h3>
          {actions.length === 0 && <div className="muted">Select a payment to see proposed actions.</div>}
          {actions.map((a) => (
            <div key={a.title} className="card" style={{ padding: 10, marginBottom: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 12.5 }}>{a.title}</div>
              <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>{a.description}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                {approved.has(a.title) ? (
                  <span className="b g nodot">Approved (simulated) - not executed</span>
                ) : (
                  <>
                    <button className="btn sm" onClick={() => approve(a)}>{'\u2713'} Approve (simulated)</button>
                    <button className="btn sm">{'\u2717'} Decline</button>
                  </>
                )}
              </div>
            </div>
          ))}

          <div className="banner warn" style={{ marginTop: 12 }}>
            No external action was executed. Human approval remains required.
          </div>
        </div>
      </div>
    </section>
  )
}
