# Payment Command Center — Implementation Plan

**Status:** Draft for review
**Companion documents:** `PaymentIntelligenceCommandCentre-POC/Kuber_V4_Complete_Rebuild_Prompt.txt` (full POC spec — the functional source of truth for what the app does), `PaymentIntelligenceCommandCentre-POC/schema_package/` (entity/field reference, not literal DDL), `PayTrace360_Architecture_and_Design.md` + `PayTrace360_Implementation_Plan.md` + its actual code in `paytrace360/backend/` (a prior app in this same workspace — its generic infrastructure is reused directly, not just referenced for ideas)

This plan assumes the POC's full V4 spec as the functional baseline: same field model, same canonical payments, same simulation scenarios, same Kuber agent design (deferred). Where PayTrace already solved a generic, domain-independent problem well — SQLite connection/WAL setup, the migration runner, the sync-publisher/async-WebSocket-registry bridge, module layout, test structure — we port that code directly rather than reinventing it. What we don't carry over is domain-*mismatched* logic: PayTrace's correlation/anomaly engines solve a problem (matching independently-arriving messages with no shared ID) that doesn't exist here, since every event in this app is generated for an already-known `payment_id`. The rule is "reuse whatever fits, skip only what solves a different problem" — not a blanket avoidance of PayTrace's code. Read the rebuild prompt first if a phase's detail seems thin here; it is the authority on exact field names, values and behavior.

---

## 0. Confirmed tech stack

| Layer | Confirmed choice |
|---|---|
| Backend | Python + FastAPI, dependency management via `uv` |
| Database | SQLite, raw `sqlite3` (no ORM), WAL mode, `foreign_keys=ON`, one connection per request via `Depends(get_db_connection)`. Sync (`def`, not `async def`) route handlers — FastAPI runs them in a threadpool |
| Business logic location | Python application layer (services), not DB functions/triggers — SQLite can't run the reference Postgres schema's PL/pgSQL |
| Real-time transport | Native WebSocket (FastAPI), broadcast-to-all + client-side relevance filtering. Sync in-process `EventPublisher` → async `ConnectionRegistry` bridged via `asyncio.run_coroutine_threadsafe` |
| Frontend | React + TypeScript + Vite |
| Frontend routing | React Router — multi-page app, not the POC's single-scroll anchor nav |
| Frontend data layer | TanStack Query (react-query) + a WebSocket hook that calls `invalidateQueries` on incoming events |
| Repos | Two separate repos: `backend/`, `frontend/` at the root of this workspace |
| Scope (message types) | ISO 20022 only — `pain.001/008`, `pacs.008/009/002/004`, `camt.052/053/054/056/029` (the 10 types in the POC) |
| Simulation authority | Server-side (not client-side like the POC) — `sim_run`/`payment_event` persist, survive reload, driven by a backend background task |
| Correlation/anomaly engines | **Not built.** Every event is generated for an already-known `payment_id`; there is no independent-message-matching problem to solve here (see architecture decisions memory for full rationale) |
| Innovation Lab (POC §15) | **Out of scope for now.** Not part of this plan. Revisit only if explicitly requested |
| AI agent (POC §14) | **Deferred.** Placeholder phase only, no design work until we reach it |

---

## 1. Build order and dependency rationale

```
Phase 0: Repo scaffolding         (nothing below can start without this)
Phase 1: Schema & DB               (needs Phase 0)
Phase 2: Core engine (shared lib)  (needs schema; this is the single state-machine/projection
                                     implementation everything else calls — built once, up front,
                                     not discovered mid-build)
Phase 3: Seed generator            (needs Phase 2 — must call the same engine, never a parallel path)
Phase 4: Ingestion + Simulation    (needs Phase 2/3; this is the showcase feature — REST push,
        ticker + WebSocket +        WebSocket broadcast, and a bare proof-of-life frontend page —
        proof-of-life UI            brought forward deliberately, ahead of Discovery/Payment 360)
Phase 5: Payment Discovery Fabric  (needs Phase 4's data + WebSocket for live grid updates)
Phase 6: Payment 360               (needs Discovery to link from; needs Phase 4's engine for lineage/events)
Phase 7: Guided Simulation Engine  (needs Phase 4's engine; adds the full POC-parity UI:
        UI (POC parity)             Start/Pause/Inject/Reset, state machine, sequence diagram)
Phase 8: Executive Radar/Dashboard (needs a populated payment universe from Phase 3)
Phase 9: Drilldown & Mandates      (needs Payment 360 + Discovery to link from)
Phase 10: AI agent                 (deferred — placeholder only)
Phase 11: Testing & demo readiness (needs everything above)
```

Every phase closes with at least a thin, working UI slice against real backend data — never a phase that is "backend only, no visible progress." Polished, POC-matching layout/navigation for a screen is finished once that screen's API contract is stable, not deferred to one giant final UI phase.

---

## 2. Phase 0 — Repo scaffolding

- [x] Create `backend/` — `uv init --no-workspace` (own git repo, Python 3.14.2 pinned), FastAPI + uvicorn + pydantic + python-dotenv deps, `app/main.py` with `/health`, CORS middleware, dev deps `pytest`/`httpx`. — `backend/pyproject.toml`, `backend/app/main.py`
- [x] Create `frontend/` — Vite + React + TypeScript scaffold (own git repo), `react-router-dom` + `@tanstack/react-query` installed, `App.tsx` with a placeholder route (`/`) rendering `HealthCheck.tsx`, which calls the backend `/health` endpoint via `useQuery`. Scaffold's default hero/CSS/assets removed. — `frontend/src/{main.tsx,App.tsx,pages/HealthCheck.tsx,lib/api.ts}`
- [x] Backend module skeleton (empty `__init__.py` files, no logic yet): `db/`, `engine/`, `simulation/`, `payments/`, `realtime/`, `seed/`, `tests/`.
- [x] Verify both servers boot locally and the frontend's placeholder route successfully calls `/health`. — confirmed in a real browser: page renders "Backend says: ok"; backend test suite passes (`uv run pytest`, 1/1).

**Port note (resolved):** fixed dev ports for this app: backend **8010**, frontend **5180** (`vite.config.ts`: `server.port = 5180, strictPort: true`; backend CORS `allow_origins` matches).

**Phase 0 status: COMPLETE.** Run with: `uv run --directory backend uvicorn app.main:app --port 8010 --reload` and `npm --prefix frontend run dev`.

---

## 3. Phase 1 — Schema & DB ✅ COMPLETE

SQLite port of the entities in `schema_package/kuber_v4_postgres_schema.sql`, scoped to what the POC actually needs (drop anything Postgres-specific like RLS/roles; keep the shape of the data).

**Deliberate simplifications made during implementation** (see header comment in `backend/app/db/schema.sql` for full rationale): dropped `ref_region`/`ref_country`/`ref_currency`/`payment_business`/`department`/`app_user`/`settlement_account` — the POC treats country/currency/owner/department/nostro as plain display strings on `payment`, not normalized lookups, so these add no real value here. Also dropped `payment_party` as a separate table — the POC's `addressFormat` is one flat field per payment (not per party role), so debtor/ultimateDebtor/creditor/ultimateCreditor/addressFormat live directly on `payment`. `payment_agent_hop` **is** kept as a real table (genuinely useful: populated-hops-only lineage queries, "find payments touching bank X"). `iso_message`/`payment_exception`/`investigation`/`screening_result`/`liquidity_position`/`reconciliation_item`/`sla_policy` dropped — represented as plain columns on `payment` instead (`investigation_id`, `screening`, `liquidity_state`, `recon_state`); ISO XML is generated on demand in Python (Phase 6), never stored.

**`legal_entity` — added back post-Phase-4, built for real (not dropped after all).** Originally dropped alongside the group above, since the POC's own reference schema only ever seeds 2 rows and only 1 is ever used by any customer — the POC's "Legal entities: 18" header stat isn't backed by real data at all. Reinstated once the user clarified the actual deployment model: one Payment Command Center instance can span multiple legal entities of the same banking group (e.g. a bank's US subsidiary + UK branch sharing one instance), making it a genuine multi-tenancy dimension, not a display stat. Real `legal_entity` table + `payment.legal_entity_id` FK, seeded with 2 generic entities (`ENT-US`/`ENT-UK`) a deployer renames/extends for their own group. Assignment rule (`app/seed/generator.py::legal_entity_for()`, reused by live ingestion): DOME is always `ENT-US` (its rails are US-only); CBCC follows the debtor's country. Verified honest rather than fabricated: the real 68-payment dataset is 67 US / 1 non-US, matching actual debtor-country distribution — not force-balanced.

Two distinct kinds of data live outside the DDL, and only one of them belongs in this phase:
- **Reference/config data** (static lookups the app cannot function without — banks, state machine, scenario definitions, field catalogue) — **seeded in this phase**, since Phase 3's payment seeder depends on these rows already existing (e.g. a `payment_agent_hop` row needs its BIC to already be in `bank`).
- **Synthetic business data** (the 8 canonical + 60 generated payments and their full event histories) — **NOT this phase.** That's Phase 3, and it also needs Phase 2's engine to exist first.

**Primary data source for this phase — ported, not hand-derived:** `schema_package/kuber_v4_seed.sql` already contains every reference/config row, extracted from the HTML and parity-verified (README claims 47/47 parity). Translated its `INSERT` blocks to SQLite dialect (Postgres `uuid`/`json`/array types → `TEXT`/JSON-string; drop `ON CONFLICT`/`DEFERRABLE`; timestamps → literal ISO strings; `INSERT OR IGNORE` for idempotency). Blocks used: `bank` L33, `ref_domain` L48, `ref_rail` L54, `ref_scheme` L60, `ref_payment_type` L68, `ref_iso_message_type` L75, `ref_mt_mx_mapping` L87, `ref_iso_tx_status` L99, `ref_reason_code` L107, `ref_payment_status` L120, `ref_payment_state` L128, `ref_state_transition` L142, `customer` L201, `sim_scenario` L246, `sim_scenario_step` L251, `sim_speed` L282, `sim_latency_profile` L287, `mandate_obligation` L999, `field_category` L1015, `field_catalogue` L1036, `persona`+`persona_tag` L1109/L1113, `column_preset`+`column_preset_field` L1132/L1140, `nl_query_vocabulary` L1205, `customer_metric_daily`/`rail_metric_daily` L1513/L1519. Skipped `ai_*`/`exec_kpi_*`/`rail_route_rule`/`regulatory_item`/`knowledge_document` blocks — deferred agent or out-of-scope Innovation Lab.

- [x] `app/db/schema.sql` — all core payment tables (`payment` — flat model per the simplification above, `payment_agent_hop`, `payment_event`), simulation tables (`sim_scenario`, `sim_scenario_step`, `sim_run`, `sim_speed`, `sim_latency_profile`), reference/config tables (`ref_payment_state`, `ref_state_transition`, `ref_reason_code`, `ref_iso_message_type`, `ref_mt_mx_mapping`, `ref_iso_tx_status`, `ref_payment_status`, `bank`, `ref_domain`, `ref_rail`, `ref_scheme`, `ref_payment_type`, `customer`, `persona`, `persona_tag`, `nl_query_vocabulary`), discovery/UI-support tables (`field_category`, `field_catalogue`, `column_preset`, `column_preset_field`), drilldown/mandate tables (`customer_metric_daily`, `rail_metric_daily`, `mandate_obligation`). 32 tables total. `mandate_obligation.linked_payment_id` deliberately has no FK constraint (seeded here in Phase 1, before Phase 3's payments exist).
- [x] `app/db/connection.py` — WAL-mode connection helper + `get_db_connection` FastAPI dependency (ported from PayTrace's `connection.py` pattern as-is).
- [x] `app/db/migrate.py` — `executescript` runner, idempotent (`CREATE TABLE IF NOT EXISTS`).
- [x] `app/db/seed_reference_data.py` — populates ONLY reference/config tables, idempotent (`INSERT OR IGNORE`), runnable standalone (`uv run python -m app.db.seed_reference_data`), ported from the `kuber_v4_seed.sql` blocks listed above.
- [x] `app/db/reset.py` — wipes the DB file (+ `-wal`/`-shm`) and re-runs migrate + reference-data seed, for **explicit, deliberate use only**. Has a `TODO(Phase 3)` marker to also call the payment seeder once it exists.

**Locked decision (confirmed against PayTrace's actual code, not just its plan doc — `paytrace360/backend/main.py`'s `lifespan` handler never calls `run_migration()`/`seed_database()`; both are standalone `if __name__ == "__main__"` scripts):** schema creation and all seeding (reference data here, payments in Phase 3, and any live-generated batches in Phase 5) happen exactly once, via an explicit, deliberate action — never automatically on app/backend startup. `app/main.py`'s lifespan handler only wires up the WebSocket publisher/registry (Phase 4), same as PayTrace's. The only way to get a fresh dataset is running `app/db/reset.py` on purpose. Verified: `app/main.py` has no migration/seed calls.

- [x] `tests/test_schema_integrity.py` — 3 tests: all 32 expected tables exist, migration is idempotent, `payment` has its key columns.
- [x] `tests/test_reference_data.py` — 4 tests: row counts (14 banks, 4 scenarios, 64 field_catalogue rows, 20 categories, 7 column presets, 4 mandates, 17 customers, 6/5 rail/customer metric rows), scenario step counts match POC §11 exactly (HAPPY 8, TIMEOUT 7, AC03 7, SCREEN 8), spot-check exact values (AC03 reason name, JPMorgan BIC, XB/INVESTIGATION latency row present), reseeding is idempotent.

**Verification:** `uv run pytest` → 8/8 passing (1 health + 3 schema + 4 reference data). Also ran `app/db/reset.py` against the real dev DB file (not just test fixtures) and spot-checked with the `sqlite3` CLI: all 32 tables present, `sim_scenario_step` counts exactly match AC03=7/HAPPY=8/SCREEN=8/TIMEOUT=7.

**Phase 1 status: COMPLETE.**

---

## 4. Phase 2 — Core engine (shared library) ✅ COMPLETE

The single implementation of "what happens when a payment event occurs" — ported from the POC's `applySimState()`/`derive()` JS functions (exact source lines: `derive` L768, `applySimState` L1655, `stepLatency`/`actorFor` L1211-1223, `reasonCode` L644, `fmtMs` L618, latency table `LAT` L1204). Used by the seed generator (Phase 3), the REST ingestion endpoint (Phase 4), and the simulation ticker (Phase 4/7). No second implementation anywhere.

- [x] `app/engine/derive.py` — `derive(p)`: computes `mt_equivalent`, `hops`, `credit_amount`, `gpi_status`, `exception_type`, `sla_state`; `reason_code(reason)` helper.
- [x] `app/engine/latency.py` — `step_latency()`: per-rail-class base latency **loaded from the `sim_latency_profile` table** (not a hardcoded Python copy) + deterministic FNV-1a hash jitter (`fnv1a_hash`), matching the POC's `hash()` exactly; `format_ms()` (port of `fmtMs`).
- [x] `app/engine/apply_event.py` — `apply_event(payment, event, ...)`: the single write path — updates `sim_state`, `status`, `intermediate_status`, `ultimate_status`, `status_reason`, `screening`, `completed_at`, `message_type` (pacs.004 on RETURNED, restored on INITIATED via an optional `original_message_type` param), `investigation_id`, `recon_state`, `ai_recommendation`, `ai_agent`, then calls `derive()`. Also `actor_for()` and `pay_status_from_state()`.
- [x] `app/engine/state_machine.py` — `load_scenario_steps()` reads the 4 scenarios' steps from `sim_scenario_step` (DB, not hardcoded — one source of truth with Phase 1's seed data); `adapt_steps()` drops CORRESPONDENT_PROCESSING/IN_TRANSIT for domestic HAPPY-path payments with no correspondent/intermediary; `build_event()`; `run_scenario()` runs an entire scenario against a payment in one call (used by this phase's tests; the live ticker will call the same per-step primitives one tick at a time instead).
- [x] Unit tests (`tests/test_engine.py`, 18 tests): exact event/ISO-message/exception counts for all 4 scenarios matching POC §11's table exactly (Happy 8/7/0, Timeout 7/5/2, AC03 7/6/2, Screen 8/6/1); the mandatory state consistency rule (status never reports COMPLETED at any intermediate step of a non-happy-ending run) checked step-by-step across TIMEOUT/AC03/SCREEN; AC03 return reason propagation; domestic HAPPY-path hop-skipping.

**Verification:** `uv run pytest` → 18/18 passing.

**Phase 2 status: COMPLETE.**

---

## 5. Phase 3 — Seed generator ✅ COMPLETE

**Correction made during implementation (important, differs from what was originally planned above):** the literal `payment_event` rows are NOT replayed through `engine/apply_event.py`. Investigation showed the payment's authoritative fields (`status`, `ultimate_status`, `intermediate_status`) are already given directly in the source data — they are not derivable by replaying the *display* event history (which is truncated/adapted for UI purposes, e.g. `WAITING_CORRESPONDENT` becomes a synthetic `IN_TRANSIT` display event that would incorrectly overwrite `intermediate_status` if replayed). Verified against the actual `genPayment()` JS source: `status_from_intermediate()` (special-cases `SETTLED`/`ACCC` → `COMPLETED`, otherwise reuses `pay_status_from_state()`) and `ultimate_status = IN_PROGRESS if status == INVESTIGATION else status` exactly reproduce genPayment()'s own rules, confirmed against all 8 canonical payments. `engine/apply_event.py` remains reserved for live simulation ticks (Phase 4/7) only — seeding just inserts the given fields and the literal event rows as historical/display data.

**Data pipeline actually used:** a one-time extraction script (`backend/scripts/extract_poc_seed.py`, kept for provenance, never invoked by the running app) parsed `schema_package/kuber_v4_seed.sql`'s `payment`/`payment_party`/`payment_agent_hop`/`investigation`/`payment_event` `INSERT` blocks into `backend/app/seed/data/*.json` — our own self-contained assets (68 payments, 271 party rows, 173 hops, 5 investigations, 450 events — counts spot-checked against the source). `app/seed/generator.py` loads only these JSON files at runtime, folding `payment_party` (dropped as a table, see Phase 1) back into flat `payment` columns and `payment_agent_hop` bics into bank display names (via the already-seeded `bank` table).

- [x] `app/seed/generator.py::build_payments()` — loads `payment`/`payment_party`/`payment_agent_hop`/`investigation` JSON, folds party rows into flat debtor/creditor/ultimate*/address_format columns, hop rows into debtor_agent/correspondent/intermediary/creditor_agent (BIC → bank name via the `bank` table), looks up `segment` (via `customer`) and `obligation_due` (via `mandate_obligation`) from Phase 1's seeded tables, derives `status`/`ultimate_status` per the rules above, and calls `engine/derive.py::derive()` for the computed fields (`hops`, `credit_amount`, `gpi_status`, etc.).
- [x] `app/seed/generator.py::build_events()` — loads `payment_event` JSON, computes `seq`/`prev_state` from row order per payment (not present in the source columns).
- [x] `app/seed/generator.py::ai_fields()` — the 4 canonical payments with explicit AI overrides in the POC prompt get exact hardcoded values; the other 4 canonical payments get `mk()`'s defaults (`"No action required"`/`"Orchestrator"`/`0.9`); the 60 generated payments get `genPayment()`'s actual status-based mapping, with a deterministic (not random) confidence derived from a hash of the payment ID.
- [x] `app/seed/generator.py::seed_payments()` — single idempotent entrypoint (`INSERT OR IGNORE`), runnable via CLI (`uv run python -m app.seed.generator`), wired into `app/db/reset.py`.
- [x] Tests (`tests/test_seed.py`, 6 tests): exact row counts (68/450/173); PAY-CB-000001 matches the POC's canonical values field-for-field (lineage, hops, investigation ID, owner, nostro, AI agent/confidence, exact 5-event truncated history ending at `IN_TRANSIT`); PAY-RETURN-000001 is REJECTED (not RETURNED) matching the canonical record exactly; PAY-TIMEOUT-000001's `ultimate_status` is `IN_PROGRESS` despite `status=INVESTIGATION`; no payment shows a `completed_at` unless `status=COMPLETED`; reseeding is idempotent.

**Verification:** `uv run pytest` → 24/24 passing. Also ran `app/db/reset.py` against the real dev DB and spot-checked: 62 COMPLETED / 3 IN_PROGRESS / 1 REJECTED / 1 RETURNED / 1 INVESTIGATION across 68 payments; PAY-FEDWIRE-000001 shows `hops=2` (no correspondent/intermediary) as expected.

**Phase 3 status: COMPLETE.**

---

## 6. Phase 4 — Ingestion + Simulation ticker + WebSocket (the showcase phase) ✅ COMPLETE

**Design note (differs slightly from the plan above):** `process_event()` drives the engine in terms of `state` (one of the 13 `ref_payment_state` values), not by reverse-inferring a state purely from `msg_type`/`tx_sts` — several states share the same `iso_tx_status` (e.g. `ACSP` is shared by `SENT`/`IN_TRANSIT`/`CORRESPONDENT_PROCESSING`/`SETTLEMENT_PENDING`), so that mapping isn't reversible without a fuller ISO-inference engine, which is out of scope. `msg_type`/`tx_sts`/`reason_code`/`orgnl_*` are still accepted and carried through as descriptive ISO metadata into the event's `iso_message` display string and a `iso_payload` JSON audit blob (no new columns needed).

- [x] `app/realtime/publisher.py` + `app/realtime/registry.py` — ported PayTrace's `EventPublisher`/`ConnectionRegistry` pattern as-is (sync pub/sub → async WS bridge via `run_coroutine_threadsafe`). `EventPublisher.subscribe()` is idempotent (skips a callback already subscribed) — found necessary because repeated app-lifespan startups (e.g. once per `TestClient` context in tests) would otherwise register `registry.broadcast_sync` multiple times and double-broadcast every event.
- [x] `app/simulation/schemas.py` — `IngestEventRequest` (`payment_id` optional, `state` whitelisted against the 13 state-machine states, `msg_type` whitelisted against the 10 ISO 20022 types, `tx_sts`/`reason_code`/`orgnl_end_to_end_id`/`orgnl_uetr` optional, `new_payment` required exactly when `payment_id` is omitted) + `NewPaymentFields` (identity fields to create a payment on first push) + `SimStartRequest` (`scenario_code`, `speed_code` whitelisted against `1x/2x/5x/10x`).
- [x] `app/simulation/pipeline.py::process_event()` — the shared entrypoint: resolves an existing payment or creates one via `insert_new_payment()`, calls `engine/apply_event.py`, writes every `PAYMENT_COLUMNS` field back to the `payment` row, inserts the `payment_event` row (computing `actor` via `engine/apply_event.py::actor_for()` and `reason_code` via `engine/derive.py::reason_code()`), and returns a `notification` dict. Does **not** call `publisher.notify()` itself — the caller commits the transaction first, then publishes, so a later rollback can never cause a false broadcast.
- [x] `app/simulation/api.py` — `POST /events` (commits, then publishes) and `POST /sim/{payment_id}/start|pause|reset|inject`, all thin wrappers around `app/simulation/ticker.py`.
- [x] `app/simulation/ticker.py` — one background loop per running `sim_run`, driven by `asyncio.run_coroutine_threadsafe()` onto the loop captured by `registry.set_loop()` in `main.py`'s lifespan (**not** `asyncio.create_task()` — `sim_start` is a sync route handler running in FastAPI's threadpool, which has no event loop of its own to attach a task to). Reuses `engine/state_machine.py::load_scenario_steps()`/`adapt_steps()` and `engine/latency.py::step_latency()` for per-step timing, then calls the same `process_event()` as the REST endpoint. `reset_run()` restores the payment from `sim_run.snapshot_json` (taken at start) regardless of whether the run is still active or already finished, and clears its `payment_event` rows.
- [x] `app/main.py` — lifespan calls `registry.set_loop()` + `publisher.subscribe(registry.broadcast_sync)`; `@app.websocket("/ws")` broadcasts every notification to all connected clients (broadcast-to-all + client-side filtering, no server-side rooms).
- [x] Frontend: `src/lib/useWebSocket.ts` (connect on mount, exponential backoff up to 10s on drop, dedupes incoming events by `event_id` as a safety net against a brief double-socket window) + `src/pages/LiveFeed.tsx` (`/live` route): a form to POST an ISO-shaped event and a live-updating list of events arriving over the socket.
- [x] Tests (`tests/test_simulation.py`, 7 tests): `POST /events` creates a new payment on first push with full identity fields (including a real DB `CHECK` rejection surfaced as 400 when a CBCC payment is pushed without a `uetr`); a follow-up push against an existing `payment_id` appends correctly; unknown `msg_type`/`state` → 422; a full `HAPPY` scenario run via the ticker on a payment with both a correspondent and an intermediary produces the exact 8 events / 7 ISO messages / 0 exceptions from POC §11; `reset` restores the payment to its pre-run snapshot and clears that run's events; WebSocket delivers exactly one notification per event.

**Bug caught by the WebSocket test (real, not hypothetical):** the first version of the test suite double-delivered every event over the WebSocket, because each test's `with TestClient(app) as c:` re-triggered the app's lifespan startup, re-subscribing `registry.broadcast_sync` onto the same module-level `publisher` singleton every time. Fixed by making `EventPublisher.subscribe()` idempotent — this is exactly the class of bug the plan's WebSocket test was written to guard against.

**Verification:** `uv run pytest` → 31/31 passing (24 prior + 7 new). Manual smoke test: booted both dev servers, `curl -X POST /events` with a `pacs.002 RJCT` payload against `PAY-CB-000001` — confirmed in the real dev DB (`status` → `REJECTED`, `status_reason` → `AC03 — Invalid Creditor Account Number`) and, separately, over a raw WebSocket client (received the broadcast within the same request). Also verified in an actual browser at `http://localhost:5180/live`: pushed an event through the form, watched it arrive in the live list with no manual refresh, WebSocket indicator showed connected. Dev DB reset back to a clean seeded state afterward (`uv run python -m app.db.reset`).

**Phase 4 status: COMPLETE.**

---

## 7. Phase 5 — Payment Discovery Fabric

- [ ] `payments/queries.py::search_payments()` — port of POC's `fn_search_payments`/`applyFilters`: mode (ALL/CBCC/DOME), status[], rail[], message type, currency, min/max amount, free-text terms against a per-payment search index, BIC match across debtor/creditor/correspondent/intermediary.
- [ ] `payments/api.py` — `GET /payments` with all Discovery filters as query params, sort, pagination (10/25/50/100).
- [ ] `GET /fields` — serve `field_catalogue` (64 fields / 20 categories) + `column_preset` rows for the frontend's field catalogue and preset picker.
- [ ] `GET /payments/export` — same filters, no pagination, returns CSV (RFC-quoting, UTF-8 BOM, `\r\n`) matching POC's `exportCurrentView()`.
- [ ] Frontend: app shell (header, nav, ticker — the POC's global chrome from §1, adapted to React Router multi-page nav instead of anchor scroll), field catalogue sidebar, search bar with the POC's example-query chips, filter selects, results grid (sortable, sticky header, badges per POC's `badgeCls()` rules), footer stats, CSV export button.
- [ ] Frontend: results grid subscribes to the WebSocket hook with a predicate matching the active filters — new/changed payments animate in live, matching Phase 4's ingestion demo.

**"＋ Generate +100/+1000" — persisted background task, not a blocking request, not JS.** Redesigned from the POC's client-side, session-only behavior for three reasons the user flagged: (1) generation must be a genuine backend/Python process, never client-side JS; (2) once generated, rows are permanent in the DB — there is no re-generation on reload, only this explicit action adds rows, and only `db/reset.py` (Phase 1) removes them; (3) for `+1000`, computing and inserting synchronously inside the HTTP request is the wrong shape. The right analog **is** PayTrace's Scenario Player — but only for one principle, not its schema: a Scenario Player run is a persisted `asyncio` background task using the shared `process_event()` pipeline, decoupled from the request/response and from any single open browser tab (architecture doc §7.1). Borrow exactly that execution model here. Do NOT borrow its `scenario_runs` step-by-step/timed-delay design — that fits a scripted multi-step lifecycle (which the guided Simulation Engine, Phase 7, already covers), not a one-shot bulk insert of N independent random payments.

- [ ] `POST /payments/generate` — accepts `{n}`, validates against a sane cap, immediately returns `202 Accepted` with a generation-run id (no `scenario_runs`-style table needed — a lightweight in-memory/`payment_generation_run` status row is enough: id, requested_n, inserted_so_far, status).
- [ ] `payments/generator.py` — port of the POC's `genPayment(r)`/`mulberry32` PRNG (HTML L620/L831): rail mix (SWIFT 34%/RTP 18%/FedNow 14%/ACH 22%/Fedwire 12%), status mix, corridor/bank selection, address format distribution. This is the ONE place that PRNG logic needs porting to Python — Phase 3's seed data was loaded pre-generated from `kuber_v4_seed.sql`, but this button must keep generating genuinely new payments on demand, matching POC's "＋ Generate +100/+1000" toolbar buttons and `generateMorePayments(n)` behavior.
- [ ] Backend background task: an `asyncio` task (started from the endpoint above, same pattern as Phase 4/7's simulation ticker) that generates and inserts the `n` payments through `engine/apply_event.py` (same shared pipeline as everywhere else — no direct raw INSERTs bypassing it), updating the run's status as it goes.
- [ ] `@app.websocket("/ws")` reuse: broadcast a `generation_progress`/`generation_complete` notification via Phase 4's existing publisher, so the frontend updates live without polling — no new WebSocket endpoint needed.
- [ ] Tests: natural-language-ish query parsing (`"rejected swift over 1m"` → correct filters), UETR/BIC/name substring search, CSV row count matches filtered count, `+100`/`+1000` generation produces exactly that many new valid payments with unique IDs, and the generated rows are still present after an app restart (persistence, no auto-regeneration on boot).

---

## 8. Phase 6 — Payment 360

- [ ] `payments/queries.py::payment_360()` — port of POC's `fn_payment_360`/lineage/journey computation: identity, `stateLabel()`/`statusIndicator()`, lineage nodes (debtor → debtor agent → correspondent → intermediary → creditor agent → creditor, only populated hops), `journey()` kind/target/back logic, hop tracker, readiness checks, operations block.
- [ ] `payments/queries.py::iso_xml()` — port of POC's `isoXml(type, p)` for all 10 message types, using the payment's real field values (`OrgnlInstrId`/`OrgnlEndToEndId`/`OrgnlUETR` blocks included, per the correlation domain note in the architecture decisions).
- [ ] `GET /payments/{id}/360` — full Payment 360 payload (identity, lineage, events, messages, parties, lifecycle, evidence-adjacent operations block — NOT the AI evidence/confidence scoring, that's Phase 10).
- [ ] Frontend: Payment 360 page — identity card, LINEAGE/EVENTS/MESSAGES/PARTIES tabs (SVG lineage animation, event timeline, ISO XML viewer with the MT↔MX mapping table, party/bank graph), lifecycle stepper, hop tracker, readiness checks.
- [ ] Test: looping over every non-COMPLETED seeded/canonical payment across all four tabs never renders the string "Completed"/"COMPLETED" (POC acceptance test §19.12) — same as the mandatory state consistency rule, verified at this layer.
- [ ] Test: PAY-RETURN-000001 shows `RJCT · AC03`, no Intermediary node (JPMorgan → Barclays only), and its `pacs.004` XML contains `<RtrRsnInf><Rsn><Cd>AC03`.

---

## 9. Phase 7 — Guided Simulation Engine UI (POC parity)

- [ ] Frontend: Control Plane card — scenario select, payment select (canonical + synthetic optgroups), speed control, Start/Pause/Reset/Inject Failure buttons wired to Phase 4's `POST /sim/...` endpoints, suggestion hint (`scenarioForPayment`).
- [ ] Frontend: State Machine grid — nodes per scenario step, pending/active/completed/error/investigation/returned styling, strikethrough after injection, progress bar.
- [ ] Frontend: Simulated Network Journey swimlane SVG (message-sequence diagram) driven by the live WebSocket event stream for the active `sim_run`.
- [ ] Frontend: Event Stream table (newest first), Simulation Metrics card, ISO Event Payload JSON viewer (updates on every event), Run History table.
- [ ] Test: Happy Path at 10x → COMPLETED, 8 events, 7 ISO messages, 0 exceptions, all POC §19 acceptance criteria for tests 17–24 (Timeout, AC03, Screening Hold, Pause/Resume, Inject Failure, Reset).

---

## 10. Phase 8 — Executive Radar / Dashboard

**Open decision to make when this phase starts (flagging now, not resolving yet):** `kuber_v4_seed.sql` has both a live-computable path and a static-snapshot path for this data. `exec_kpi_snapshot`/`exec_kpi_spark`/`exec_breakdown`/`exec_trend_daily` (L1430-1512) store the POC's exact hardcoded headline figures ($1.84T, 99.42%, etc. — POC §3 explicitly hardcodes these regardless of what the generator produces). Computing these live via `GROUP BY`/aggregate queries over the seeded `payment` table (mirroring PayTrace's dashboard pattern) is architecturally cleaner and should reproduce the same figures since the snapshot rows were themselves derived from this same payment set — but verify that equivalence before relying on it, and use the stored snapshot values as the acceptance-test oracle either way.

- [ ] `payments/queries.py` — KPI/aggregate queries: payment value, count, STP rate, success rate, exceptions, avg latency, AI cases (POC §3's 7 KPIs), status/rail donut breakdowns, payment-type bars, 7-day trend.
- [ ] `GET /radar` — combined payload for the KPI row + 4 charts.
- [ ] Frontend: KPI cards with sparklines and hover tooltips, 2 donuts + type bars + dual-axis trend chart (hand-built inline SVG, matching POC §5's exact tooltip/legend behavior), persona segmented control (POC §4) wired to filter every downstream screen.
- [ ] Test: KPI values match POC §19.2 acceptance values ($1.84T, 99.42%, 237 AI cases) against the seeded dataset; donut segment/legend hover shows correct share/value.

---

## 11. Phase 9 — Drilldown & Mandates

`customer_metric_daily`/`rail_metric_daily` are seeded as static display data in Phase 1 (like `mandate_obligation`) rather than computed live — POC §13's figures (e.g. Global Trading AG $210B/99.1%) are fixed demo constants, not derived from the separately-generated payment universe.

- [ ] `GET /drilldown/customers`, `GET /drilldown/rails`, `GET /mandates` — port of POC §13's top customers, rail volumes, and the 4 mandate obligations with coverage meters.
- [ ] Frontend: drilldown cards with click-to-filter behavior into Discovery (customer/rail chips), mandate cards linking to their payment via Payment 360.

---

## 12. Phase 10 — AI agent (deferred)

Placeholder phase only. No design work here — revisit when we reach this screen, per the explicit deferral decision. The POC's mocked deterministic agent (§14) remains the functional target; how it's served (mock-in-Python vs. a real LLM/Vertex AI integration per `schema_package/README.md`) is an open decision to make at that time.

---

## 13. Phase 11 — Testing & demo readiness

- [ ] Full regression against the POC's acceptance test list (`Kuber_V4_Complete_Rebuild_Prompt.txt` §19), adapted to the new architecture: KPIs, search/filter, Payment 360 state consistency, all 4 simulation scenarios, CSV export, responsive layout at 1600/1200/820/390px equivalents in the new nav.
- [ ] End-to-end WebSocket resilience test: kill the connection mid-session, verify silent polling fallback and silent reconnect.
- [ ] Demo script walkthrough: seed → dashboard → push a live ISO event via REST → Discovery search → Payment 360 → guided simulation run → drilldown/mandates.
- [ ] Only after sign-off here: begin the cloud migration phase (AlloyDB/BigQuery/Vertex AI per `schema_package/README.md`) — separate plan, not part of this document.
