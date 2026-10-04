const PROGRESS_STATES = new Set([
  'INITIATED', 'ACCEPTED', 'SCREENING', 'SENT', 'IN_TRANSIT',
  'CORRESPONDENT_PROCESSING', 'SETTLEMENT_PENDING', 'COMPLETED',
])

/** Status/badge color-class mapping \u2014 React port of the POC's badgeCls(). */
export function badgeCls(raw: unknown): string {
  const s = String(raw ?? '').toUpperCase()
  if (['COMPLETED', 'ACCC', 'SETTLED', 'MATCHED', 'CLEARED', 'AVAILABLE', 'READY'].includes(s)) return 'g'
  if (['REJECTED', 'FAILED', 'RJCT', 'EXCEPTION', 'BLOCKED', 'SHORTFALL'].includes(s)) return 'r'
  if (['RETURNED', 'RTND'].includes(s)) return 'v'
  if (s === 'INVESTIGATION' || s === 'PDNG' || s.includes('HOLD') || s.includes('MATCH') || s === 'TIGHT' || s === 'OPEN' || s === 'NEEDS REVIEW' || s === 'WAITING_CORRESPONDENT' || s === 'SCREENING') return 'a'
  if (s === 'CANCELLED' || s === 'CANC' || s === '\u2014' || !s) return 'n'
  if (s === 'IN_PROGRESS' || PROGRESS_STATES.has(s) || s === 'ACSP' || s === 'RCVD') return 'c'
  return 'bl'
}

export function Badge({ value }: { value: unknown }) {
  const text = String(value ?? '')
  if (!text || text === '\u2014') return <span className="dim">{'\u2014'}</span>
  return <span className={`b ${badgeCls(text)}`}>{text}</span>
}
