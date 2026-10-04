export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8010'
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL ?? 'ws://localhost:8010'

export interface HealthResponse {
  status: string
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return response.json()
}

export interface IngestEventPayload {
  payment_id: string
  state: string
  msg_type: string
  tx_sts?: string
  reason_code?: string
  description?: string
}

export interface IngestEventResponse {
  event_id: number
  payment_id: string
  status: string
}

export async function postEvent(payload: IngestEventPayload): Promise<IngestEventResponse> {
  const response = await fetch(`${API_BASE_URL}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ? JSON.stringify(body.detail) : `POST /events failed: ${response.status}`)
  }
  return response.json()
}

export type OperatingMode = 'ALL' | 'CBCC' | 'DOME'

export interface BreakdownRow {
  n: number
  usd: number
  status?: string
  rail?: string
  bucket?: string
}

export interface TrendPoint {
  date: string
  payments: number
  value_usd: number
}

export interface DashboardResponse {
  date: string
  available_date_range: { min: string; max: string }
  mode: OperatingMode
  portfolio: {
    rails: number
    corridors: number
    legal_entities: number
  }
  kpis: {
    payments: number
    payment_value_usd: number
    payment_value_excludes_non_usd_count: number
    stp_rate_pct: number | null
    success_rate_pct: number | null
    success_rate_terminal_total: number
    exceptions_pct: number | null
    exceptions_count: number
    avg_latency_ms: number | null
    investigation_cases: number
  }
  personas: Array<{ mode: OperatingMode; payments: number }>
  charts: {
    status: BreakdownRow[]
    rail: BreakdownRow[]
    type: BreakdownRow[]
    trend: TrendPoint[]
  }
}

export async function getDashboard(mode: OperatingMode = 'ALL', date?: string): Promise<DashboardResponse> {
  const params = new URLSearchParams({ mode })
  if (date) params.set('date', date)
  const response = await fetch(`${API_BASE_URL}/dashboard?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Dashboard fetch failed: ${response.status}`)
  }
  return response.json()
}

export interface FieldDef {
  field_id: string
  display_name: string
  category_name: string
  domain_code: 'ALL' | 'CBCC' | 'DOME'
  message_types: string[]
  data_type: string
  filter_type: 'text' | 'enum' | 'range' | 'date'
  sortable: number
  groupable: number
  description: string
  example: string | null
  source_column: string
  iso_path_hint: string | null
}

export interface ColumnPreset {
  preset_code: string
  preset_label: string
  mode_code: OperatingMode | null
  fields: string[]
}

export interface FieldCatalogueResponse {
  categories: Array<{ category_name: string; sort_order: number }>
  fields: FieldDef[]
  presets: ColumnPreset[]
}

export async function getFields(): Promise<FieldCatalogueResponse> {
  const response = await fetch(`${API_BASE_URL}/fields`)
  if (!response.ok) {
    throw new Error(`Fields fetch failed: ${response.status}`)
  }
  return response.json()
}

export interface PaymentSearchParams {
  mode?: OperatingMode
  domain?: string
  rail?: string
  status?: string
  msg?: string
  ccy?: string
  min?: number
  q?: string
  sort?: string
  dir?: 1 | -1
  cols: string[]
  page?: number
  page_size?: number
  sim?: boolean
}

export interface PaymentSearchResponse {
  rows: Array<Record<string, unknown>>
  total: number
  page: number
  page_size: number
  columns: string[]
  stats: { notional_usd: number; exceptions_count: number }
}

function searchParamsFrom(params: PaymentSearchParams): URLSearchParams {
  const sp = new URLSearchParams()
  if (params.mode) sp.set('mode', params.mode)
  if (params.domain) sp.set('domain', params.domain)
  if (params.rail) sp.set('rail', params.rail)
  if (params.status) sp.set('status', params.status)
  if (params.msg) sp.set('msg', params.msg)
  if (params.ccy) sp.set('ccy', params.ccy)
  if (params.min) sp.set('min', String(params.min))
  if (params.q) sp.set('q', params.q)
  if (params.sort) sp.set('sort', params.sort)
  if (params.dir) sp.set('dir', String(params.dir))
  sp.set('cols', params.cols.join(','))
  sp.set('page', String(params.page ?? 1))
  sp.set('page_size', String(params.page_size ?? 25))
  if (params.sim) sp.set('sim', 'true')
  return sp
}

export async function searchPayments(params: PaymentSearchParams): Promise<PaymentSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/payments?${searchParamsFrom(params).toString()}`)
  if (!response.ok) {
    throw new Error(`Payment search failed: ${response.status}`)
  }
  return response.json()
}

export function exportPaymentsUrl(params: PaymentSearchParams): string {
  return `${API_BASE_URL}/payments/export?${searchParamsFrom(params).toString()}`
}

export interface PaymentRecord {
  paymentId: string
  uetr: string | null
  swiftTxnId: string | null
  businessMsgId: string
  instructionId: string
  endToEndId: string
  domain: OperatingMode
  rail: string
  paymentType: string
  scheme: string | null
  purpose: string | null
  messageType: string
  mtEquivalent: string
  isoVersion: string | null
  addressFormat: string | null
  debtor: string
  ultimateDebtor: string | null
  debtorAccount: string | null
  debtorCountry: string | null
  creditor: string
  ultimateCreditor: string | null
  creditorAccount: string | null
  creditorCountry: string | null
  debtorAgent: string | null
  correspondent: string | null
  intermediary: string | null
  creditorAgent: string | null
  bic: string | null
  hops: number
  amount: number
  debitCcy: string
  creditCcy: string
  creditAmount: number
  fxRate: number
  settlementDate: string
  initiatedAt: string
  completedAt: string | null
  duration: string | null
  status: string
  ultimateStatus: string
  intermediateStatus: string
  gpiStatus: string
  statusReason: string
  exceptionType: string
  settlementMethod: string
  clearingSystem: string | null
  priority: string
  screening: string
  riskScore: number | null
  segment: string | null
  paymentInitiationDept: string | null
  charges: string
  feeAmount: number
  nostro: string | null
  liquidityState: string
  mandateRef: string | null
  obligationDue: string | null
  investigationId: string | null
  reconState: string
  owner: string | null
  slaState: string
  aiRecommendation: string | null
  aiAgent: string | null
  aiConfidence: number | null
  country: string | null
  isSimulated: number
  sourcePaymentId: string | null
}

export interface PaymentHop {
  seq: number
  role: 'DEBTOR_AGENT' | 'CORRESPONDENT' | 'INTERMEDIARY' | 'CREDITOR_AGENT'
  bic: string
  name: string
  country: string | null
}

export interface PaymentEventRecord {
  seq: number
  state: string
  prev_state: string | null
  description: string
  iso_message: string
  actor: string
  reason_code: string | null
  reason_text: string | null
  event_ts: string
  processing_ms: number
}

export interface MtMxMappingRow {
  mt_type: string
  mx_type: string
  meaning: string
}

export interface PaymentDetailResponse {
  payment: PaymentRecord
  hops: PaymentHop[]
  events: PaymentEventRecord[]
  messageChain: string[]
  messages: Record<string, string>
  mtMxMapping: MtMxMappingRow[]
}

export async function getPaymentDetail(paymentId: string): Promise<PaymentDetailResponse> {
  const response = await fetch(`${API_BASE_URL}/payments/${encodeURIComponent(paymentId)}`)
  if (!response.ok) {
    throw new Error(`Payment detail fetch failed: ${response.status}`)
  }
  return response.json()
}

export interface ScenarioStep {
  stepNo: number
  state: string
  description: string
  isoMessage: string
}

export interface Scenario {
  scenarioCode: string
  scenarioName: string
  description: string
  expectedOutcome: string
  steps: ScenarioStep[]
}

export interface SpeedOption {
  speedCode: string
  stepDelayMs: number
}

export type SimMode = 'READY' | 'RUNNING' | 'PAUSED' | 'FINISHED' | 'RESET'

export interface SimRun {
  runId: string
  scenarioCode: string
  speedCode: string
  mode: SimMode
  outcome: string
  nextStepNo: number
  failureInjected: boolean
  eventCount: number
  isoMessageCount: number
  exceptionCount: number
  simulatedLatencyMs: number
  startedAt: string
  finishedAt: string | null
}

export interface SimEvent {
  seq: number
  state: string
  prev_state: string | null
  description: string
  iso_message: string
  actor: string
  reason_code: string | null
  reason_text: string | null
  event_ts: string
  processing_ms: number
}

export interface SimStatusResponse {
  sourcePaymentId: string
  shadowPaymentId: string | null
  shadowPayment: PaymentRecord | null
  hops: PaymentHop[]
  activeRun: SimRun | null
  scenarioCode: string
  steps: ScenarioStep[]
  events: SimEvent[]
}

export async function getScenarios(): Promise<Scenario[]> {
  const response = await fetch(`${API_BASE_URL}/sim/scenarios`)
  if (!response.ok) throw new Error(`Scenarios fetch failed: ${response.status}`)
  return response.json()
}

export async function getSpeeds(): Promise<SpeedOption[]> {
  const response = await fetch(`${API_BASE_URL}/sim/speeds`)
  if (!response.ok) throw new Error(`Speeds fetch failed: ${response.status}`)
  return response.json()
}

export async function getSimStatus(paymentId: string, scenarioCode?: string): Promise<SimStatusResponse> {
  const qs = scenarioCode ? `?scenario_code=${encodeURIComponent(scenarioCode)}` : ''
  const response = await fetch(`${API_BASE_URL}/sim/${encodeURIComponent(paymentId)}${qs}`)
  if (!response.ok) throw new Error(`Simulation status fetch failed: ${response.status}`)
  return response.json()
}

async function postJson<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail ? String(detail.detail) : `Request failed: ${response.status}`)
  }
  return response.json()
}

export function postSimStart(paymentId: string, scenarioCode: string, speedCode: string) {
  return postJson<{ run_id: string; mode: string }>(
    `${API_BASE_URL}/sim/${encodeURIComponent(paymentId)}/start`,
    { scenario_code: scenarioCode, speed_code: speedCode },
  )
}

export function postSimPause(paymentId: string) {
  return postJson<{ run_id: string; mode: string }>(`${API_BASE_URL}/sim/${encodeURIComponent(paymentId)}/pause`)
}

export function postSimReset(paymentId: string) {
  return postJson<{ payment_id: string; mode: string }>(`${API_BASE_URL}/sim/${encodeURIComponent(paymentId)}/reset`)
}

export function postSimInject(paymentId: string) {
  return postJson<{ run_id: string; failure_injected: boolean }>(`${API_BASE_URL}/sim/${encodeURIComponent(paymentId)}/inject`)
}

export interface DrilldownCustomer {
  customerId: string
  displayName: string
  valueUsd: number
  stpRatePct: number
}

export interface DrilldownRail {
  railLabel: string
  railCode: string | null
  valueUsd: number
  displayValue: string
  latencyDisplay: string
  colorToken: string | null
}

export interface Mandate {
  mandateId: string
  displayNo: number
  customerName: string | null
  description: string
  dueTime: string
  linkedPaymentId: string | null
  coveragePct: number
  readiness: string
  readinessNote: string | null
}

export async function getDrilldownCustomers(date?: string): Promise<DrilldownCustomer[]> {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const response = await fetch(`${API_BASE_URL}/drilldown/customers?${params.toString()}`)
  if (!response.ok) throw new Error(`Drilldown customers fetch failed: ${response.status}`)
  return response.json()
}

export async function getDrilldownRails(date?: string): Promise<DrilldownRail[]> {
  const params = new URLSearchParams()
  if (date) params.set('date', date)
  const response = await fetch(`${API_BASE_URL}/drilldown/rails?${params.toString()}`)
  if (!response.ok) throw new Error(`Drilldown rails fetch failed: ${response.status}`)
  return response.json()
}

export async function getMandates(): Promise<Mandate[]> {
  const response = await fetch(`${API_BASE_URL}/mandates`)
  if (!response.ok) throw new Error(`Mandates fetch failed: ${response.status}`)
  return response.json()
}

export interface KuberAgentInfo {
  agentKey: string
  label: string
  role: string
}

export interface KuberAskResponse {
  answer: string
  routed_to: string
  routing_reasoning: string
  evidence_used: string[]
  evidence: Record<string, boolean>
  confidence: number | null
  disclaimer: string
}

export async function getKuberAgents(): Promise<KuberAgentInfo[]> {
  const response = await fetch(`${API_BASE_URL}/kuber/agents`)
  if (!response.ok) throw new Error(`Kuber agents fetch failed: ${response.status}`)
  return response.json()
}

export async function postKuberAsk(paymentId: string, question: string, agentKey?: string): Promise<KuberAskResponse> {
  return postJson<KuberAskResponse>(`${API_BASE_URL}/kuber/ask`, { payment_id: paymentId, question, agent_key: agentKey ?? null })
}

