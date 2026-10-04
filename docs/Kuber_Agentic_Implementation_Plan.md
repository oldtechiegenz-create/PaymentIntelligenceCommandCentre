# Kuber Agentic Operations — Agentic Implementation Plan

**Status:** Draft, accepted — ready to build
**Scope:** The AI agent layer behind the Kuber Agentic Operations screen (`POC §14`, deferred in the main implementation plan, now underway). Companion documents: `PaymentCommandCenter_Implementation_Plan.md` (Phase 10 placeholder this supersedes), `Kuber_V4_Complete_Rebuild_Prompt.txt §14` (functional spec for the 7-agent roster, evidence panel, proposed actions).

This plan covers everything needed to take the Kuber agent layer from "3 working specialists, hardcoded routing decisions, no loop guard" to a complete, production-shaped 7-agent system with configurable safety limits, auditable policy text, and a deliberate (not accidental) routing design.

---

## 0. What already exists (built and tested, not part of this plan)

- `backend/app/ai_agents/llm/` — multi-provider LLM factory (`ollama`/`vertex_proxy`/`db_ai_access_layer`/`anthropic`/`openai_proxy`), config-driven, 11 tests passing.
- `backend/app/ai_agents/mcp_servers/kuber_mcp.py` — FastMCP read-only tool server, 10 tools, zero write tools (structurally — no create/update/post/delete tool exists in the file at all).
- `backend/app/ai_agents/mcp_client.py` — hand-rolled MCP→LangChain tool bridge (`langchain-mcp-adapters` was incompatible with our installed `fastmcp`/`mcp` versions).
- `backend/app/ai_agents/graph/` — `router → specialist ⇄ tool_call → summarizer` LangGraph, 3 specialists (`orchestrator`, `investigator`, `risk`), deterministic evidence scoring (`confidence.py`, ported from the POC's checks), LLM-reported confidence (`SummarizerOutput.confidence`), LLM-based routing, and a custom tool node that enforces each specialist's tool scope at execution time.
- `backend/app/ai_agents/api.py` — `POST /kuber/ask`, `GET /kuber/agents`.
- `frontend/src/pages/Kuber.tsx` — full POC-parity UI (agent roster, conversation pane, evidence panel with real computed values, proposed actions with Approve/Decline), verified end-to-end against the real AI Access Layer gateway.

---

## 1. Build the remaining 4 agents

Full roster target: `orchestrator`, `investigator`, `risk`, `liquidity` (done) + `correspondent`, `reconciliation`, `iso` (this phase).

### 1.1 New read-only MCP tools needed first

| Tool | Wraps | Why it's missing today |
|---|---|---|
| `get_reconciliation_state(payment_id)` | `payment.recon_state` | No existing tool exposes this column |
| `get_mt_mx_mapping(mt_type)` | `ref_mt_mx_mapping` table | Table exists in schema, no tool reads it |

Both follow the exact pattern of the first 8 tools in `kuber_mcp.py` (thin wrapper over an existing query function, `{"error": ...}` on bad input, no write path). Add alongside tests in `test_kuber_mcp.py` mirroring the existing per-tool assertions (output matches the same underlying query function Payment 360/Drilldown use).

### 1.2 New specialists (`graph/agents.py`)

| Specialist | Tools |
|---|---|
| Correspondent Agent | `get_payment_lineage`, `get_payment_events` |
| Liquidity Agent | `get_liquidity_position`, `get_mandate_for_payment` |
| Reconciliation Agent | `get_reconciliation_state` (new) |
| ISO Interpreter | `get_iso_message`, `get_mt_mx_mapping` (new) |

### 1.3 Widen the router's type

`RouterOutput.routing_key` (`graph/schemas.py`) goes from `Literal["orchestrator", "investigator", "risk"]` to all 7 keys. `_router_prompt()` in `graph.py` already builds its specialist list from `SPECIALISTS.items()`, so no change needed there beyond the roster dict itself.

### 1.4 Order

Build and test one agent at a time (tool → specialist entry → skill file [§5] → a graph-level test asking a question that should route to it), not all 4 at once — matches how the first 3 were built and keeps each PR-sized change independently verifiable.

---

## 2. Hybrid agent-selection routing (accepted design)

**Decision**: when a user explicitly selects an agent card in the UI, that selection is **authoritative** — the request bypasses `router_node` entirely and goes straight to that specialist. Selecting **Orchestrator** is the one exception: Orchestrator's defined role *is* routing, so the LLM-based router still runs in that case.

**Why this over "preference + orchestrator can override"**: matches the POC's own spec (§14 — routing only happens "when the Orchestrator is active"); silently overriding a human's explicit choice is a trust problem in a financial-ops tool — if an analyst deliberately picks Risk, quietly rerouting them elsewhere without telling them erodes confidence in the tool rather than building it.

### 2.1 Backend changes

- `KuberAskRequest` (`api.py`) gains `agent_key: Optional[str] = None`.
- `run_kuber_agent()`/`build_kuber_graph()` (`graph.py`) accept an optional `forced_agent_key`. When set and not `"orchestrator"`, the compiled graph's entry edge goes `START → specialist` directly (skipping `router_node`), with `routing_key` pre-populated in the initial state instead of produced by `router_node`.
- `router_node` itself is unchanged — still used whenever `forced_agent_key` is `None` or `"orchestrator"`.

### 2.2 Frontend changes

- `Kuber.tsx` already tracks `activeAgentKey` — thread it through `postKuberAsk(paymentId, question, activeAgentKey)` → request body's `agent_key`.
- No change to the evidence panel, proposed actions, or chat rendering — only the routing decision changes.

### 2.3 Tests

- A forced non-orchestrator `agent_key` never invokes the router LLM at all (assert via the fake-chat-model test harness that the router's `with_structured_output` mock is never called).
- A forced `"orchestrator"` (or omitted `agent_key`) still runs the router as today.

---

## 3. Max ReAct-loop guard (configurable)

**Problem**: `specialist ⇄ tool_call` has no iteration cap today — a confused model could loop indefinitely issuing tool calls. LangGraph's own default recursion limit (25 steps) is a blunt backstop, not a deliberate guard.

### 3.1 State change

`KuberAgentState` (`graph.py`) gains `tool_call_count: int`, initialized to `0`, incremented by 1 each time `specialist_node` is entered with a non-empty `state["messages"]` (i.e., each loop iteration after the first).

### 3.2 Conditional edge change

`after_specialist()` forces `"summarize"` once `tool_call_count >= max_tool_loops`, regardless of whether the latest `AIMessage` still has `tool_calls`.

### 3.3 Configuration

- `build_kuber_graph(mcp_client, provider=None, max_tool_loops: int = 3)` — new parameter.
- Default sourced from `KUBER_MAX_TOOL_LOOPS` env var (fallback `3`) at the `run_kuber_agent()` call site, so it's tunable per-deployment without a code change.

### 3.4 Tests

- A fake chat model scripted to *always* request a tool call (never stops) must still terminate at exactly `max_tool_loops` iterations and reach the summarizer — proves the cap is real, not just theoretical.
- A `max_tool_loops=1` override terminates after the first tool call even though more might otherwise be requested.

---

## 4. Tool-assignment invariant (enforced, not just convention)

**Rule** (already true in practice for the first 3 agents, now made explicit and tested): every entry in `SPECIALISTS` must have a non-empty `tool_names` list. No agent ever gets the full unscoped toolset, and no agent ever gets zero tools.

- Add one test in `test_ai_agents_graph.py` (or a new `test_ai_agents_agents.py`): `assert all(len(cfg["tool_names"]) > 0 for cfg in SPECIALISTS.values())`, plus `assert all(name in ALL_KNOWN_TOOL_NAMES for name in cfg["tool_names"])` so a typo'd tool name fails loudly at test time instead of silently resolving to an empty bound-tools list at runtime.

---

## 5. Skill files (accepted design)

**Problem**: each specialist's entire behavioral guidance today is one throwaway sentence (`"You are the {label} ({role}). Answer using only your tools."`). Real domain judgment — escalation policy, known edge cases, what *not* to suggest — isn't encoded anywhere.

**Distinction**: *tool* = a callable function with a schema (what `kuber_mcp` provides). *Skill* = the procedure/judgment for using those tools well — richer instructions, do's/don'ts, example Q&A, specific to one agent's domain.

### 5.1 Why a file, not just a longer string in `agents.py`

Compliance/ops policy (how to phrase a sanctions answer, what counts as "needs escalation") is exactly the kind of thing a bank's compliance team wants to **edit and audit without a code deploy**. A separate file gives: git-history-as-policy-audit-log, non-engineer editability, and reuse if an agent ever needs more than one "mode."

### 5.2 Layout

```
backend/app/ai_agents/skills/
├── orchestrator.md
├── investigator.md
├── risk.md
├── liquidity.md
├── reconciliation.md
├── correspondent.md
└── iso.md
```

Each file, short (role restated, a numbered procedure, explicit do's/don'ts, 1-2 example Q&A pairs) — not a prompt-engineering essay. Loaded once at process start (simple `pathlib.Path.read_text()`, no hot-reload needed for v1) and appended to the existing system prompt built in `specialist_node`.

### 5.3 Implementation

- `graph/skills.py` — `load_skill(agent_key: str) -> str`, raises clearly if a roster entry has no matching file (fails fast at startup, not mid-conversation).
- `specialist_node`'s `SystemMessage` becomes: role line (existing) + `\n\n` + `load_skill(state["routing_key"])`.
- Write the first 3 skill files (`orchestrator.md`, `investigator.md`, `risk.md`) as part of this phase to prove the pattern before the 4 new agents get theirs in §1.

### 5.4 Tests

- Every key in `SPECIALISTS` has a corresponding skill file (fails loudly if one's missing, same spirit as §4's tool-name check).
- `load_skill()` raises a clear error for an unknown key rather than silently returning empty text.

---

## 6. Additional items surfaced during review (not in the original 5 points)

### 6.1 Tool-call failure handling

Today, if an MCP tool call errors mid-conversation (bad `payment_id`, transient network issue), the exception propagates up through LangGraph's `ToolNode` uncaught. `kuber_mcp`'s tools already self-report unknown-payment errors as `{"error": ...}` dicts for known failure modes — extend that same pattern to the MCP *client* bridge (`mcp_client.py::load_mcp_tools`'s wrapped `_call`) so a transport-level failure (not just an application-level "unknown payment") also degrades to a `{"error": ...}` tool result instead of a raised exception reaching the graph runtime.

### 6.2 Audit table (carried over from the original 5-phase plan, still pending)

`ai_agent_interaction` table: `payment_id`, `agent_key`, `question`, `tools_used`, `confidence`, `timestamp`, plus the Approve/Decline actions already surfaced in the UI. Slot this in after the 4 new agents are live, since it should capture the full roster from day one rather than needing a second pass.

---

## 7. Build order

_Status: steps 1–5 and 7 are done; 6 (four new specialists) and 8 (audit table) are open. Confidence is now LLM-reported rather than computed (see §9)._

```
1. Two new kuber_mcp tools (reconciliation, MT/MX mapping) + tests        — unblocks §1.2/§1.4
2. Max-tool-loop guard (§3)                                                — isolated, no dependency on anything else
3. Tool-assignment invariant test (§4)                                     — isolated, quick
4. Skill files for the existing 3 agents + load_skill() (§5)              — proves the pattern
5. Hybrid routing: agent_key param, bypass logic, frontend wiring (§2)    — depends on nothing above, but logically pairs with skill files since both touch specialist_node's prompt construction
6. Four new specialists, one at a time: tool(s) → agents.py entry →
   skill file → graph test → (repeat)                                     — depends on 1, 3, 4
7. Tool-call failure handling in mcp_client.py (§6.1)
8. Audit table + logging (§6.2)
```

Steps 2–5 are independent of each other and of step 1/6 — could be parallelized if useful, but recommended sequentially given the small size of this codebase's contributor pool (keeps review simple, one concern at a time).

---

## 8. Open items for a future pass (explicitly out of scope here)

- Hot-reloading skill files without a process restart (not needed until skill-editing cadence is high enough to matter).
- Per-agent LLM provider override (today all agents share whatever `LLM_PROVIDER` is configured process-wide).
- A real evidence-completeness gauge distinct from "confidence" (flagged in an earlier review — the POC shows these as two separate numbers; we currently only expose one cleanly to the frontend, which computes evidence % itself client-side as a stopgap).

---

## 9. Changes after review

- **Confidence is LLM-reported.** Supersedes the original "never by the LLM" rule: `SummarizerOutput` now carries `confidence` (float, validated 0.0–1.0), which `run_kuber_agent` returns as-is. `confidence.py` keeps only the deterministic evidence-completeness check, so the evidence gauge stays reproducible while confidence is now a model self-assessment (not repeatable between runs, and not a calibrated probability). The frontend gauge shows the latest answer's value instead of a client-side copy of the old formula.
- **Tool scope enforced at execution.** `ToolNode(all_tools)` is replaced by `tool_node` in `graph.py`, which refuses any call outside the active specialist's `tool_names`.
- **`evidence_used` validated.** Restricted to the 7 evidence categories (`EvidenceCategory` in `graph/schemas.py`); case/whitespace/duplicates are normalized and unknown values dropped.
- **Skill-file tests strengthened.** Each skill needs its title (containing the agent's label) and the Procedure/Do/Don't/Example sections, and no skill file may exist without a roster entry.
