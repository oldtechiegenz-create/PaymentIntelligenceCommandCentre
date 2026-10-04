import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useFields, usePayments } from '../lib/queries'
import { exportPaymentsUrl, type FieldDef, type OperatingMode } from '../lib/api'
import { Badge } from '../components/Badge'

const EXAMPLE_HINTS = [
  'rejected swift over 1m', 'PAY-RETURN-000001', 'a9f4c1e8', 'status:investigation',
  'rtp completed under 50k', 'bic:CITIUS33', 'HSBC Treasury', 'pacs.009 fedwire',
  'returned gbp', 'debtor:northstar',
]

const MIN_AMOUNT_OPTIONS = [
  { value: 0, label: 'Any' },
  { value: 10000, label: '≥ 10K' },
  { value: 100000, label: '≥ 100K' },
  { value: 1000000, label: '≥ 1M' },
  { value: 10000000, label: '≥ 10M' },
]

const RAILS = ['SWIFT CBPR+', 'Fedwire', 'ACH', 'RTP', 'FedNow']
const STATUSES = ['COMPLETED', 'IN_PROGRESS', 'REJECTED', 'RETURNED', 'INVESTIGATION', 'FAILED', 'CANCELLED']
const MSG_TYPES = ['pacs.008', 'pacs.009', 'pacs.004', 'pain.001']
const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'INR', 'SGD', 'CHF', 'CAD']
const PAGE_SIZES = [10, 25, 50, 100]

export default function PaymentDiscovery() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const mode = (searchParams.get('mode') as OperatingMode) ?? 'ALL'

  const { data: fieldData } = useFields()

  const [cols, setCols] = useState<string[] | null>(null)
  const [domain, setDomain] = useState('')
  const [rail, setRail] = useState(() => searchParams.get('rail') ?? '')
  const [status, setStatus] = useState('')
  const [msg, setMsg] = useState('')
  const [ccy, setCcy] = useState('')
  const [min, setMin] = useState(0)
  const [queryInput, setQueryInput] = useState(() => searchParams.get('q') ?? '')
  const [q, setQ] = useState(() => searchParams.get('q') ?? '')
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [fieldSearch, setFieldSearch] = useState('')
  const [colMenuOpen, setColMenuOpen] = useState(false)
  const [includeSimulated, setIncludeSimulated] = useState(false)

  const defaultPresetCode = mode === 'ALL' ? 'DEFAULT_ALL' : mode === 'CBCC' ? 'DEFAULT_CBCC' : 'DEFAULT_DOME'
  const effectiveCols = cols ?? fieldData?.presets.find((p) => p.preset_code === defaultPresetCode)?.fields ?? []
  // paymentId is always fetched (even if not a displayed column) so rows can link to Payment 360
  const requestCols = effectiveCols.includes('paymentId') ? effectiveCols : ['paymentId', ...effectiveCols]

  const { data, isLoading, isError, error } = usePayments({
    mode,
    domain: domain || undefined,
    rail: rail || undefined,
    status: status || undefined,
    msg: msg || undefined,
    ccy: ccy || undefined,
    min: min || undefined,
    q: q || undefined,
    sort: sort?.key,
    dir: sort?.dir,
    cols: requestCols,
    page,
    page_size: pageSize,
    sim: includeSimulated,
  })

  const fieldById = useMemo(() => {
    const map = new Map<string, FieldDef>()
    fieldData?.fields.forEach((f) => map.set(f.field_id, f))
    return map
  }, [fieldData])

  function fieldVisible(f: FieldDef) {
    return mode === 'ALL' || f.domain_code === 'ALL' || f.domain_code === mode
  }

  function toggleCol(fieldId: string) {
    setCols((prev) => {
      const base = prev ?? effectiveCols
      return base.includes(fieldId) ? base.filter((c) => c !== fieldId) : [...base, fieldId]
    })
  }

  function applyPreset(presetCode: string) {
    const preset = fieldData?.presets.find((p) => p.preset_code === presetCode)
    if (preset) setCols(preset.fields)
    setColMenuOpen(false)
  }

  function runSearch() {
    setQ(queryInput)
    setPage(1)
  }

  function useHint(hint: string) {
    setQueryInput(hint)
    setQ(hint)
    setPage(1)
  }

  function clearSearch() {
    setQueryInput('')
    setQ('')
    setPage(1)
  }

  function toggleSort(key: string) {
    setSort((prev) => (prev?.key === key ? { key, dir: prev.dir === 1 ? -1 : 1 } : { key, dir: 1 }))
  }

  function openPayment360(paymentId: unknown) {
    const params = new URLSearchParams(searchParams)
    params.set('paymentId', String(paymentId))
    navigate({ pathname: '/payment-360', search: params.toString() })
  }

  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / pageSize))

  const activeFilters: Array<[string, string, () => void]> = []
  if (domain) activeFilters.push(['Domain', domain, () => setDomain('')])
  if (rail) activeFilters.push(['Rail', rail, () => setRail('')])
  if (status) activeFilters.push(['Status', status, () => setStatus('')])
  if (msg) activeFilters.push(['Message', msg, () => setMsg('')])
  if (ccy) activeFilters.push(['Currency', ccy, () => setCcy('')])
  if (min) activeFilters.push(['Min amount', `≥ ${min.toLocaleString()}`, () => setMin(0)])
  if (q) activeFilters.push(['Search', q, clearSearch])

  const categories = fieldData?.categories ?? []
  const fieldsByCategory = useMemo(() => {
    const map = new Map<string, FieldDef[]>()
    fieldData?.fields.forEach((f) => {
      if (!fieldVisible(f)) return
      const list = map.get(f.category_name) ?? []
      list.push(f)
      map.set(f.category_name, list)
    })
    return map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldData, mode])

  const visibleFieldCount = fieldData?.fields.filter(fieldVisible).length ?? 0

  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="shead">
        <div>
          <h2>
            Payment Discovery Fabric <span className="tag">SEARCH</span>
          </h2>
          <div className="ssub">Metadata-driven search across identity, ISO 20022, parties, network, state, risk, liquidity and AI activity</div>
        </div>
        <div className="toolbar">
          <div className="colpop">
            <button className="btn" onClick={() => setColMenuOpen((o) => !o)}>
              Columns <b>{effectiveCols.length}</b>
            </button>
            {colMenuOpen && (
              <div className="colmenu">
                <div className="preset">
                  {fieldData?.presets.map((p) => (
                    <button key={p.preset_code} className="btn sm" onClick={() => applyPreset(p.preset_code)}>
                      {p.preset_label}
                    </button>
                  ))}
                </div>
                {effectiveCols.map((c, i) => (
                  <div className="fld on" key={c} style={{ paddingLeft: 6 }}>
                    <span style={{ color: 'var(--dim)', width: 16 }}>{i + 1}</span>
                    {fieldById.get(c)?.display_name ?? c}
                    <button className="btn sm" style={{ marginLeft: 'auto' }} onClick={() => toggleCol(c)}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <label className="f" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox" checked={includeSimulated}
              onChange={(e) => { setIncludeSimulated(e.target.checked); setPage(1) }}
            />
            <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 600 }}>Show simulated payments</span>
          </label>
          <a
            className="btn"
            href={exportPaymentsUrl({
              mode, domain: domain || undefined, rail: rail || undefined, status: status || undefined,
              msg: msg || undefined, ccy: ccy || undefined, min: min || undefined, q: q || undefined, cols: effectiveCols,
              sim: includeSimulated,
            })}
          >
            Export CSV
          </a>
        </div>
      </div>

      <div className="disc">
        <div className="card fcat">
          <div style={{ fontWeight: 700, fontSize: 13 }}>Field Catalogue</div>
          <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>
            {visibleFieldCount} fields, {categories.length} categories
          </div>
          <input
            className="inp"
            style={{ width: '100%', marginBottom: 10, boxSizing: 'border-box' }}
            placeholder="Find a field"
            value={fieldSearch}
            onChange={(e) => setFieldSearch(e.target.value)}
          />
          {categories.map((cat) => {
            const fields = (fieldsByCategory.get(cat.category_name) ?? []).filter(
              (f) => !fieldSearch || f.display_name.toLowerCase().includes(fieldSearch.toLowerCase()) || f.description.toLowerCase().includes(fieldSearch.toLowerCase()),
            )
            if (!fields.length) return null
            const onCount = fields.filter((f) => effectiveCols.includes(f.field_id)).length
            return (
              <details key={cat.category_name}>
                <summary>
                  {cat.category_name}
                  <span className={`cnt${onCount ? ' on' : ''}`}>
                    {onCount}/{fields.length}
                  </span>
                </summary>
                {fields.map((f) => (
                  <label className={`fld${effectiveCols.includes(f.field_id) ? ' on' : ''}`} key={f.field_id}>
                    <input type="checkbox" checked={effectiveCols.includes(f.field_id)} onChange={() => toggleCol(f.field_id)} />
                    {f.display_name}
                    <span className="dt">{f.data_type}</span>
                  </label>
                ))}
              </details>
            )
          })}
        </div>

        <div className="disc-main">
          <div className="searchbar">
            <span className="ico">Q</span>
            <input
              className="inp"
              placeholder="Search payment ID, UETR, beneficiary, debtor, BIC, rail, status... try: rejected swift over 1m"
              value={queryInput}
              onChange={(e) => setQueryInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
            />
            <button className="btn primary" onClick={runSearch}>Search</button>
            <button className="btn" onClick={clearSearch}>Clear</button>
          </div>
          <div className="hints">
            {EXAMPLE_HINTS.map((h) => (
              <button className="hint" key={h} onClick={() => useHint(h)}>{h}</button>
            ))}
          </div>

          <div className="filters">
            <label className="f">
              Domain
              <select className="inp" value={domain} onChange={(e) => { setDomain(e.target.value); setPage(1) }}>
                <option value="">All domains</option>
                <option>CBCC</option>
                <option>DOME</option>
              </select>
            </label>
            <label className="f">
              Rail
              <select className="inp" value={rail} onChange={(e) => { setRail(e.target.value); setPage(1) }}>
                <option value="">All rails</option>
                {RAILS.map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
            <label className="f">
              Status
              <select className="inp" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
                <option value="">All statuses</option>
                {STATUSES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="f">
              Message Type
              <select className="inp" value={msg} onChange={(e) => { setMsg(e.target.value); setPage(1) }}>
                <option value="">All messages</option>
                {MSG_TYPES.map((m) => <option key={m}>{m}</option>)}
              </select>
            </label>
            <label className="f">
              Currency
              <select className="inp" value={ccy} onChange={(e) => { setCcy(e.target.value); setPage(1) }}>
                <option value="">All currencies</option>
                {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label className="f">
              Min Amount
              <select className="inp" value={min} onChange={(e) => { setMin(Number(e.target.value)); setPage(1) }}>
                {MIN_AMOUNT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          </div>

          <div className="chips">
            {activeFilters.length === 0 && (
              <span className="dim" style={{ fontSize: 11.5 }}>No active filters, showing the full payment universe</span>
            )}
            {activeFilters.map(([label, value, clear]) => (
              <span className="chip" key={label}>
                <span>{label}:</span>
                <b>{value}</b>
                <button onClick={() => { clear(); setPage(1) }} title="Remove filter">X</button>
              </span>
            ))}
          </div>

          {isLoading && <p className="muted" style={{ marginTop: 12 }}>Loading...</p>}
          {isError && <p style={{ color: 'var(--red)', marginTop: 12 }}>{(error as Error).message}</p>}

          {data && (
            <>
              <div className="tbl-wrap">
                <table className="grid-t">
                  <thead>
                    <tr>
                      {effectiveCols.map((c) => (
                        <th key={c} onClick={() => toggleSort(c)} title={fieldById.get(c)?.description}>
                          {fieldById.get(c)?.display_name ?? c}
                          {sort?.key === c && <span className="ar">{sort.dir === 1 ? '^' : 'v'}</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.length === 0 && (
                      <tr>
                        <td colSpan={effectiveCols.length} style={{ padding: 22, textAlign: 'center' }} className="muted">
                          No payments match. Try removing a filter.
                        </td>
                      </tr>
                    )}
                    {data.rows.map((row) => {
                      const isSim = String(row.paymentId).startsWith('SIM-')
                      return (
                        <tr key={String(row.paymentId)} className={isSim ? 'sim-row' : ''} onClick={() => openPayment360(row.paymentId)} title="Open Payment 360">
                          {effectiveCols.map((c) => {
                            const field = fieldById.get(c)
                            const value = row[c]
                            const isNum = field?.data_type === 'amount' || field?.data_type === 'number'
                            const isStatusLike = field?.data_type === 'status' || c === 'gpiStatus'
                            return (
                              <td key={c} className={isNum ? 'num' : ''}>
                                {c === 'paymentId' && isSim && <span className="b v nodot" style={{ marginRight: 6 }}>SIM</span>}
                                {isStatusLike ? <Badge value={value} /> : value == null || value === '' ? <span className="dim">-</span> : String(value)}
                              </td>
                            )
                          })}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="pager">
                <span className="stat-row">
                  <span>Rows <b>{data.rows.length}</b> of {total}</span>
                  <span>Notional <b>${(data.stats.notional_usd / 1e6).toFixed(2)}M</b></span>
                  <span>Exceptions <b style={data.stats.exceptions_count ? { color: 'var(--amber)' } : undefined}>{data.stats.exceptions_count}</b></span>
                  <span>Columns <b>{effectiveCols.length}</b></span>
                </span>
                <div className="pg">
                  <select className="inp sm" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}>
                    {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <span className="dim" style={{ fontSize: 11.5 }}>Page {page} / {pages}</span>
                  <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
                  <button className="btn sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}
