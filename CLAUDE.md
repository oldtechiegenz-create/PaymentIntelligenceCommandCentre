# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Payment Command Center: a rebuild of the single-file POC (`payment_intelligence_command_center.html`) as a FastAPI + SQLite backend (`backend/`) and a React + TypeScript + Vite frontend (`frontend/`). It simulates ISO 20022 payment lifecycles, with dashboards, search, single-payment deep dives, live simulation and an AI agent layer ("Kuber").

Reference material at the repo root:
- `Kuber_V4_Complete_Rebuild_Prompt.txt`: the functional source of truth (exact field names, values, behavior, scenarios). Check it whenever a plan's detail seems thin.
- `docs/PaymentCommandCenter_Implementation_Plan.md`: phase-by-phase build plan, tech-stack decisions and checkboxes for build status.
- `docs/Kuber_Agentic_Implementation_Plan.md`: design and build order for the AI agent layer.
- `schema_package/`: the reference Postgres/BigQuery schema, seed data and agent tool contract. It describes entities and fields only; it is **not** the DDL this app runs. The real schema is `backend/app/db/schema.sql`.

`backend/` is its own nested git repository.

## Commands

Backend (run from `backend/`, Python 3.14, managed with `uv`):

```bash
uv sync && cp .env.example .env
uv run python -m app.db.reset                         # wipe + rebuild DB: schema, reference data, 68 payments/~450 events
uv run uvicorn app.main:app --port 8010 --reload      # dev server
uv run pytest                                         # all tests
uv run pytest tests/test_engine.py                    # one file
uv run pytest tests/test_kuber_mcp.py::test_no_write_tools_exposed   # one test
```

Frontend (run from `frontend/`):

```bash
npm install && cp .env.example .env   # VITE_API_BASE_URL defaults to http://localhost:8010
npm run dev                           # port 5180, strictPort
npx tsc --noEmit                      # typecheck
npm run lint                          # oxlint
npm run build
```

There is no frontend test suite. Check frontend changes with typecheck, lint and a manual check against a running backend.

Ports are fixed: the backend runs on 8010, and the frontend on 5180. Backend CORS in `app/main.py` allows only `http://localhost:5180`.

## Architecture

### Backend (`backend/app/`)

- **DB**: raw `sqlite3` with no ORM. It runs in WAL mode with `foreign_keys=ON` and opens one connection per request via `Depends(get_db_connection)` (`db/connection.py`). Route handlers are sync `def`, so FastAPI runs them in a threadpool. The DB path is hardcoded in `connection.py` (`app/db/payment_command_center.db`), not read from `.env`.
- **Schema/seed run only when asked.** Neither runs automatically on startup. `app.db.reset` is the sanctioned way to get a fresh dataset. Its step-by-step equivalent is `app.db.migrate` → `app.db.seed_reference_data` → `app.seed.generator`. Seed source data lives in `app/seed/data/*.json`.
- **Business logic lives in Python, not in the DB**: SQLite can't run the reference schema's PL/pgSQL functions or triggers.
- **Engine (`engine/`) is the single implementation of payment state logic.** `apply_event.py` is the only write path onto a payment's projected state. `derive.py` is the only place derived fields (mt_equivalent, hops, credit_amount, gpi_status, exception_type, sla_state) are computed. `state_machine.py` loads scenario steps from the `sim_scenario`/`sim_scenario_step` tables, never from a hardcoded Python copy. Don't add a parallel code path.
- **Live events go through `simulation/pipeline.py::process_event`.** Both `POST /events` (REST ingestion) and the guided-simulation ticker (`simulation/ticker.py`, a background asyncio task) call it, so both update a payment the same way. The seed generator deliberately does **not** call it.
- **Real-time**: `realtime/publisher.py` is a sync in-process pub/sub singleton. The `lifespan` in `main.py` subscribes `realtime/registry.py`, which bridges sync to async via `asyncio.run_coroutine_threadsafe` and broadcasts every event to all `/ws` clients. Clients filter for relevance themselves; there are no server-side rooms.
- **Simulation runs** persist in the DB (`sim_run`/`payment_event` with `sim_run_id`). `/sim/{id}/start` snapshots the payment, and `/reset` restores that snapshot and deletes that run's events.
- Feature modules (`dashboard/`, `drilldown/`, `payments/`, `simulation/`) follow an `api.py` (router) + `queries.py`/helpers split, and every router is registered in `main.py`.
- Correlation/anomaly engines are intentionally **not** built: every event is generated for a known `payment_id`.

### Kuber AI agent layer (`backend/app/ai_agents/`)

- `llm/factory.py::get_llm(provider=None)` resolves the provider from the explicit argument, then the `LLM_PROVIDER` env var, then the default `ollama`. There is one lazily imported module per provider under `llm/providers/` (`ollama`, `vertex_proxy`, `db_ai_access_layer`, `anthropic`, `openai_proxy`). `backend/README.md` lists each provider's env vars. Without a reachable provider, `/kuber/*` endpoints error.
- `mcp_servers/kuber_mcp.py` is a FastMCP server with **read-only tools only**. `tests/test_kuber_mcp.py::test_no_write_tools_exposed` enforces this, so never add a write tool. Each tool wraps the same query function that Payment 360/Drilldown use and returns `{"error": ...}` on bad input.
- `mcp_client.py` is a hand-rolled bridge from MCP tools to LangChain `StructuredTool`. `langchain-mcp-adapters` is incompatible with the installed `fastmcp`/`mcp` versions, so don't swap it in.
- `graph/graph.py` is a LangGraph pipeline: `router → specialist ⇄ tool_call → summarizer`. An explicit `agent_key` from the UI bypasses the router (except `orchestrator`). The tool loop is capped by `KUBER_MAX_TOOL_LOOPS` (default 3). Evidence completeness is computed deterministically in `graph/confidence.py` (a port of the POC checks), but **confidence is reported by the LLM** as part of the summarizer's structured output (`SummarizerOutput.confidence`, 0–1). The tool node enforces each specialist's `tool_names` at execution time, so an out-of-scope tool call is refused rather than run.
- Specialists are defined in `graph/agents.py`; currently built: `orchestrator`, `investigator`, `risk`, `liquidity` (the router's allowed keys are derived from this dict). Each specialist's behavioral prompt is a markdown file in `ai_agents/skills/<agent>.md`, loaded by `graph/skills.py`. Remaining planned agents (`correspondent`, `reconciliation`, `iso`) are in the Kuber plan.
- Proposed actions are human-in-the-loop (Approve/Decline in the UI) and are never executed automatically.

### Frontend (`frontend/src/`)

- React Router multi-page app; routes are defined in `App.tsx` and the layout/header (including the Business Date picker, used only by Dashboard) is in `components/AppShell.tsx`.
- Data fetching uses TanStack Query. API calls are in `lib/api.ts` and the query hooks in `lib/queries.ts`. `lib/useWebSocket.ts` + `WebSocketContext.tsx` hold the single `/ws` connection, which invalidates queries when events arrive and runs a heartbeat (10s ping, 25s timeout) that drives the LIVE/OFFLINE indicator.

## Testing conventions

Backend tests build a fresh temporary DB per test (`run_migration` → `seed_reference_data` → `seed_payments` against `tmp_path`). They then point the app at it with `app.dependency_overrides[get_db_connection]` and use `TestClient`. Follow this pattern rather than touching the real DB file.
