import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useDrilldownCustomers, useDrilldownRails, useMandates } from '../lib/queries'
import { Badge } from '../components/Badge'

const RAIL_COLOR: Record<string, string> = {
  'SWIFT CBPR+': 'var(--cyan)',
  Fedwire: 'var(--violet)',
  ACH: 'var(--blue)',
  RTP: 'var(--green)',
  FedNow: 'var(--amber)',
}

const fmtUSD = (n: number) =>
  n >= 1e12 ? `$${(n / 1e12).toFixed(2)}T` : n >= 1e9 ? `$${(n / 1e9).toFixed(0)}B` : `$${(n / 1e6).toFixed(0)}M`

const TOP_N = 10

export default function Drilldown() {
  const [searchParams] = useSearchParams()
  const date = searchParams.get('date') ?? undefined

  const [search, setSearch] = useState('')

  const { data: customers } = useDrilldownCustomers(date)
  const { data: rails } = useDrilldownRails(date)
  const { data: mandates } = useMandates()

  const term = search.trim().toLowerCase()
  const matchedCustomers = useMemo(
    () => (customers ?? []).filter((c) => !term || c.displayName.toLowerCase().includes(term)),
    [customers, term],
  )
  // Backend never truncates (so search can reach any customer) — the top-10 cap is a
  // display-only default, lifted the moment the user searches.
  const filteredCustomers = term ? matchedCustomers : matchedCustomers.slice(0, TOP_N)
  const filteredRails = useMemo(
    () => (rails ?? []).filter((r) => !term || r.railLabel.toLowerCase().includes(term)),
    [rails, term],
  )
  const filteredMandates = useMemo(
    () => (mandates ?? []).filter((m) => !term || `${m.customerName} ${m.description}`.toLowerCase().includes(term)),
    [mandates, term],
  )

  const maxCustomerValue = Math.max(1, ...(customers ?? []).map((c) => c.valueUsd))
  const maxRailValue = Math.max(1, ...(rails ?? []).map((r) => r.valueUsd))

  function discoveryLink(extra: Record<string, string>) {
    return { pathname: '/discovery', search: new URLSearchParams({ ...Object.fromEntries(searchParams), ...extra }).toString() }
  }

  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="shead">
        <div>
          <h2>Customer, Rail &amp; Obligation Drilldown <span className="tag">DRILL</span></h2>
          <div className="ssub">Click a customer or rail to filter the discovery grid</div>
        </div>
      </div>

      <div className="drill">
        <div className="card">
          <h3>Search</h3>
          <input
            className="inp"
            style={{ width: '100%' }}
            placeholder="Filter customers, rails, mandates..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <div className="dim" style={{ fontSize: 11.5, marginTop: 8 }}>
              {matchedCustomers.length} customers - {filteredRails.length} rails - {filteredMandates.length} mandates
            </div>
          )}
        </div>

        <div className="grid">
          <div className="card">
            <h3>
              Top Customers <span className="dim" style={{ fontSize: 11 }}>value - STP</span>
              {!term && (customers?.length ?? 0) > TOP_N && (
                <span className="dim" style={{ fontSize: 11, float: 'right' }}>top {TOP_N} of {customers!.length} - search for more</span>
              )}
            </h3>
            <div style={{ maxHeight: 420, overflow: 'auto' }}>
              {filteredCustomers.length === 0 ? (
                <div className="muted">No customers match "{search}"</div>
              ) : (
                filteredCustomers.map((c) => {
                  const cls = c.stpRatePct >= 99 ? 'g' : c.stpRatePct >= 98.5 ? 'c' : 'a'
                  return (
                    <Link key={c.customerId} className="dl" to={discoveryLink({ q: c.displayName })}>
                      <span className="nm">{c.displayName}</span>
                      <span className="num">{fmtUSD(c.valueUsd)}</span>
                      <span className={`b ${cls} nodot`}>{c.stpRatePct}% STP</span>
                      <div className="bar"><i style={{ width: `${(c.valueUsd / maxCustomerValue) * 100}%`, background: 'linear-gradient(90deg,#1a7fa0,var(--cyan))' }} /></div>
                    </Link>
                  )
                })
              )}
            </div>
          </div>

          <div className="card">
            <h3>Rails <span className="dim" style={{ fontSize: 11 }}>value - latency</span></h3>
            {filteredRails.length === 0 ? (
              <div className="muted">No rails match "{search}"</div>
            ) : (
              filteredRails.map((r) => {
                const color = RAIL_COLOR[r.railLabel] ?? 'var(--muted)'
                const clickable = r.railCode != null
                return (
                  <Link
                    key={r.railLabel}
                    className="dl"
                    to={clickable ? discoveryLink({ rail: r.railLabel }) : '#'}
                    title={clickable ? undefined : 'Not modelled as a distinct rail in the synthetic grid'}
                    style={clickable ? undefined : { cursor: 'default', opacity: 0.6 }}
                    onClick={clickable ? undefined : (e) => e.preventDefault()}
                  >
                    <span className="nm"><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: color, marginRight: 7 }} />{r.railLabel}</span>
                    <span className="num">{r.displayValue}</span>
                    <span className="dim mono">{r.latencyDisplay}</span>
                    <div className="bar"><i style={{ width: `${Math.max(1, (r.valueUsd / maxRailValue) * 100)}%`, background: color }} /></div>
                  </Link>
                )
              })
            )}
          </div>

          <div className="card">
            <h3>Mandates / Obligations <span className="dim" style={{ fontSize: 11 }}>Kuber readiness</span></h3>
            {filteredMandates.length === 0 ? (
              <div className="muted">No mandates match "{search}"</div>
            ) : (
              filteredMandates.map((m) => (
                <Link
                  key={m.mandateId}
                  className="mand"
                  to={m.linkedPaymentId ? { pathname: '/payment-360', search: new URLSearchParams({ ...Object.fromEntries(searchParams), paymentId: m.linkedPaymentId }).toString() } : '#'}
                >
                  <span className="n">{m.displayNo}</span>
                  <div style={{ minWidth: 0 }}>
                    <div><b>{m.customerName}</b> - {m.description}</div>
                    <div className="due">due {m.dueTime} ET - linked {m.linkedPaymentId ?? '-'}</div>
                    <div className="meter"><i style={{ width: `${m.coveragePct}%`, background: m.readiness === 'Ready' ? 'var(--green)' : 'var(--amber)' }} /></div>
                  </div>
                  <Badge value={m.readiness} />
                </Link>
              ))
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
