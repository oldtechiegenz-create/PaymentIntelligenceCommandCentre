import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { postEvent } from '../lib/api'
import { useWebSocketContext } from '../lib/WebSocketContext'

const STATES = [
  'INITIATED', 'ACCEPTED', 'SCREENING', 'SENT', 'IN_TRANSIT',
  'CORRESPONDENT_PROCESSING', 'SETTLEMENT_PENDING', 'COMPLETED',
  'REJECTED', 'RETURNED', 'INVESTIGATION', 'FAILED', 'CANCELLED',
]

const MSG_TYPES = [
  'pain.001', 'pacs.008', 'pacs.009', 'pacs.002', 'pacs.004',
  'camt.052', 'camt.053', 'camt.054', 'camt.056', 'camt.029',
]

/** Bare proof-of-life: push one ISO-shaped event, watch it arrive back over the
 * WebSocket without a manual refresh — proves push -> process -> broadcast -> UI. */
export default function LiveFeed() {
  const { connected, events } = useWebSocketContext()
  const [paymentId, setPaymentId] = useState('PAY-CB-000001')
  const [state, setState] = useState('SCREENING')
  const [msgType, setMsgType] = useState('pacs.008')
  const [txSts, setTxSts] = useState('')
  const [reasonCode, setReasonCode] = useState('')

  const mutation = useMutation({
    mutationFn: () =>
      postEvent({
        payment_id: paymentId,
        state,
        msg_type: msgType,
        tx_sts: txSts || undefined,
        reason_code: reasonCode || undefined,
      }),
  })

  return (
    <div style={{ padding: '1rem', fontFamily: 'sans-serif' }}>
      <h1>Payment Command Center — Live Feed (proof of life)</h1>
      <p>WebSocket: {connected ? '🟢 connected' : '🔴 disconnected'}</p>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          mutation.mutate()
        }}
        style={{ display: 'grid', gap: '0.5rem', maxWidth: 400, marginBottom: '1.5rem' }}
      >
        <label>
          Payment ID
          <input value={paymentId} onChange={(e) => setPaymentId(e.target.value)} required />
        </label>
        <label>
          State
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Message type
          <select value={msgType} onChange={(e) => setMsgType(e.target.value)}>
            {MSG_TYPES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        <label>
          Tx status (optional, e.g. ACSP / RJCT)
          <input value={txSts} onChange={(e) => setTxSts(e.target.value)} />
        </label>
        <label>
          Reason code (optional, e.g. AC03)
          <input value={reasonCode} onChange={(e) => setReasonCode(e.target.value)} />
        </label>
        <button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Pushing…' : 'Push event'}
        </button>
        {mutation.isError && <p style={{ color: 'red' }}>{(mutation.error as Error).message}</p>}
        {mutation.isSuccess && <p style={{ color: 'green' }}>Accepted: event #{mutation.data.event_id}, status now {mutation.data.status}</p>}
      </form>

      <h2>Live events</h2>
      <ul>
        {events.map((ev) => (
          <li key={ev.event_id}>
            <code>{ev.event_ts}</code> — {ev.payment_id} → <strong>{ev.state}</strong> ({ev.iso_message}) · status {ev.status}
          </li>
        ))}
        {events.length === 0 && <li>No events yet.</li>}
      </ul>
    </div>
  )
}
