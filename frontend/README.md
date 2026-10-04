# Payment Command Center — frontend

React + TypeScript + Vite, TanStack Query for data fetching, React Router for navigation. Talks to
the FastAPI backend in `../backend/` — see that project's `README.md` for the API surface and the
Kuber agent layer it serves.

## Setup

```bash
npm install
cp .env.example .env
```

`VITE_API_BASE_URL` (default `http://localhost:8010`) must point at a running backend.

## Run

```bash
npm run dev
```

Dev server runs on port 5180 (the backend's CORS config is pinned to this origin — see
`../backend/app/main.py`).

## Pages (`src/pages/`)

- `Dashboard.tsx` (`/`) — KPIs and charts for the selected business date/mode. The only page with
  the Business Date picker (in `AppShell.tsx`'s header) — every other page shows the current full
  payment book, not a date-scoped slice.
- `PaymentDiscovery.tsx` (`/discovery`) — metadata-driven search/column picker across all payment
  fields, with CSV export.
- `Payment360.tsx` (`/payment-360`) — single-payment deep dive (identity, lineage, ISO messages,
  risk, liquidity, mandates).
- `Drilldown.tsx` (`/drilldown`) — customer/rail/mandate aggregates.
- `Simulation.tsx` (`/simulation`) — drives the backend's guided-simulation ticker
  (start/pause/inject/reset) for a chosen payment and scenario.
- `Kuber.tsx` (`/kuber-agents`) — the AI agent screen: pick an agent (or let the Orchestrator
  route for you), ask a question about a payment, see the evidence panel, confidence score, and
  any proposed actions (Approve/Decline — human-in-the-loop, no action is ever auto-executed).
- `LiveFeed.tsx` (`/live`) — WebSocket proof-of-life page for the real-time event feed.
- `HealthCheck.tsx` (`/health`) — backend `/health` ping.

## Real-time updates

`lib/useWebSocket.ts` connects to the backend's `ws://.../ws` endpoint and includes a
heartbeat/staleness check (10s ping, 25s timeout) so the "LIVE" indicator in the header correctly
flips to "OFFLINE" if the backend goes away without sending a close frame.

## Test / lint / build

```bash
npx tsc --noEmit   # typecheck
npm run lint       # oxlint
npm run build      # production build
```

There is no frontend unit test suite yet — correctness is currently verified via typechecking plus
manual/browser verification against the real backend (see `../backend/README.md`'s Kuber section
for how that was validated end-to-end against a real LLM provider).

