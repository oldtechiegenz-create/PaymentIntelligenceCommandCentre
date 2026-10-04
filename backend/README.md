# Payment Command Center — backend

FastAPI + SQLite backend. See `../docs/PaymentCommandCenter_Implementation_Plan.md` for the full
phase-by-phase build plan, and `../docs/Kuber_Agentic_Implementation_Plan.md` for the AI agent
layer's design and build order.

## Setup

```bash
uv sync
cp .env.example .env
```

## First-time data load

```bash
uv run python -m app.db.reset
```

Wipes any existing DB file and rebuilds it: creates all tables (`app/db/schema.sql`), loads
reference/config data (banks, state machine, scenarios, field catalogue, personas, mandates) via
`app/db/seed_reference_data.py`, then loads the 68 canonical + generated payments and their ~450
events via `app/seed/generator.py`. Idempotent — safe to re-run at any time to get back to a clean
state. Not run automatically on server startup; always a deliberate, explicit step.

Step-by-step equivalent:

```bash
uv run python -m app.db.migrate
uv run python -m app.db.seed_reference_data
uv run python -m app.seed.generator
```

## Run

```bash
uv run --env-file .env uvicorn app.main:app --port 8010 --reload
```

The app does not load `.env` itself, so pass `--env-file .env` (as above) for `LLM_PROVIDER`, `ANTHROPIC_API_KEY` etc. to take effect.

Defaults to the `ollama` LLM provider (see below) if no `LLM_PROVIDER` is set — the Kuber agent
endpoints will error without a reachable Ollama instance unless you pick a different provider.

## Kuber agent layer (`app/ai_agents/`)

The AI agent screen (`/kuber-agents` in the frontend) is a real multi-agent system, not mocked:
a pluggable LLM factory, a read-only MCP tool server, and a LangGraph pipeline that routes a
question to a specialist, lets it call tools, and summarizes the answer. See
`../docs/Kuber_Agentic_Implementation_Plan.md` for the full design and build history.

- `llm/factory.py::get_llm(provider=None, ...)` — resolves a provider via explicit arg →
  `LLM_PROVIDER` env var → `"ollama"` default. One module per provider under `llm/providers/`,
  each lazy-imported so an unused provider's SDK never needs to be configured:

  | Provider | Env vars |
  |---|---|
  | `ollama` (default) | `OLLAMA_BASE_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` |
  | `vertex_proxy` | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` required; `VERTEX_PROXY_URL` optional (local dev reverse-proxy), `VERTEX_MODEL` |
  | `db_ai_access_layer` | `AAL_GATEWAY_URL` required; `AAL_QUOTA_PROJECT_ID`, `AAL_ROOT_CA_PATH`, `AAL_MODEL` optional. Authenticates via Application Default Credentials — run `gcloud auth application-default login --account you@example.com` first |
  | `anthropic` | `ANTHROPIC_API_KEY` required; `ANTHROPIC_API_URL`, `ANTHROPIC_MODEL` optional |
  | `openai_proxy` | `OPENAI_PROXY_BASE_URL`, `OPENAI_PROXY_API_KEY`, `OPENAI_PROXY_MODEL` optional |

  Example run against an AAL gateway:

  ```bash
  LLM_PROVIDER=db_ai_access_layer AAL_GATEWAY_URL="https://aal-gateway.example.com" AAL_QUOTA_PROJECT_ID="your-gcp-project-id" uv run uvicorn app.main:app --port 8010 --reload
  ```

- `mcp_servers/kuber_mcp.py` — FastMCP server exposing 10 read-only tools (payment identity,
  state, lineage, events, risk screening, liquidity, mandates, ISO message, reconciliation state,
  MT/MX mapping). No write tool exists anywhere in the file — a structural guarantee, not just a
  convention, and it's enforced by `tests/test_kuber_mcp.py::test_no_write_tools_exposed`.
- `mcp_client.py::load_mcp_tools` — hand-rolled bridge from MCP tools to LangChain
  `StructuredTool`s (`langchain-mcp-adapters` is incompatible with our installed `fastmcp`/`mcp`
  versions). A transport-level tool-call failure degrades to a `{"error": ...}` result rather than
  raising into the graph runtime.
- `graph/graph.py` — `router → specialist ⇄ tool_call → summarizer` LangGraph.
  - Routing is LLM-based (`router_node`), but a user's explicit agent selection in the UI is
    authoritative and bypasses the router entirely (`run_kuber_agent(..., agent_key=...)`) —
    except selecting **Orchestrator**, whose role *is* routing.
  - A configurable max-tool-loop guard (`max_tool_loops`, default 3, env `KUBER_MAX_TOOL_LOOPS`)
    caps how many tool-call round-trips a specialist can make before being forced to summarize.
  - Evidence-completeness is computed in pure deterministic Python (`graph/confidence.py`, a port
    of the POC's checks). **Confidence is reported by the LLM** itself in the summarizer's structured
    output (`SummarizerOutput.confidence`, validated to 0.0–1.0), and `evidence_used` is restricted to
    the 7 known evidence categories.
  - A specialist can only execute the tools in its own `tool_names`: the graph's tool node refuses any
    other call (even to a real tool) with an `{"error": ...}` result.
  - Current specialist roster (`graph/agents.py`): `orchestrator`, `investigator`, `risk`,
    `liquidity`. The POC's remaining agents (`correspondent`, `reconciliation`, `iso`) are designed
    for but not yet built — see the implementation plan's step 6. The router's allowed keys are
    built from this roster (`graph/schemas.py`), so a new entry is routable without a second edit.
  - Each specialist's behavioral guidance lives in its own file under `skills/` (e.g.
    `skills/risk.md`), loaded by `graph/skills.py::load_skill()` and appended to its system
    prompt — editable/auditable without a code deploy.
- `api.py` — `POST /kuber/ask` (`{payment_id, question, provider?, agent_key?}`), `GET /kuber/agents`.

```bash
curl -s -X POST http://localhost:8010/kuber/ask -H 'Content-Type: application/json' -d '{
  "payment_id": "PAY-CB-000001", "question": "Why is this payment not complete?"
}' | python3 -m json.tool
```

## Live ingestion + simulation (Phase 4)

Push an ISO-shaped event onto an existing payment:

```bash
curl -X POST http://localhost:8010/events -H 'Content-Type: application/json' -d '{
  "payment_id": "PAY-CB-000001", "state": "REJECTED",
  "msg_type": "pacs.002", "tx_sts": "RJCT", "reason_code": "AC03"
}'
```

Omit `payment_id` and include a `new_payment` object (see `app/simulation/schemas.py`) to create a
brand-new payment on first push instead. Every accepted event is broadcast over `ws://localhost:8010/ws`
to all connected clients (broadcast-to-all + client-side filtering — no server-side rooms).

Drive the guided-simulation ticker for a payment (runs as a background `asyncio` task, independent
of any open connection):

```bash
curl -X POST http://localhost:8010/sim/PAY-CB-000001/start -H 'Content-Type: application/json' -d '{"scenario_code": "HAPPY", "speed_code": "10x"}'
curl -X POST http://localhost:8010/sim/PAY-CB-000001/pause
curl -X POST http://localhost:8010/sim/PAY-CB-000001/inject
curl -X POST http://localhost:8010/sim/PAY-CB-000001/reset
```

`reset` restores the payment to its pre-run snapshot (taken automatically at `start`) and clears
that run's `payment_event` rows, whether the run finished or was still in progress.

## Test

```bash
uv run pytest
```

## Build status

- **Not yet built**: the remaining 4 Kuber specialists (`correspondent`/`liquidity`/
  `reconciliation`/`iso`) and the `ai_agent_interaction` audit table — see
  `../docs/Kuber_Agentic_Implementation_Plan.md` steps 6 and 8.
- **108/108 backend tests passing** (`uv run pytest -q`).


