-- =====================================================================
-- Payment Command Center — SQLite schema (Phase 1)
-- Deliberate simplifications vs a fully-normalized payment schema:
--   - No ref_region/ref_country/ref_currency/payment_business/department/app_user/
--     settlement_account tables — country/currency/owner/department/nostro are plain display
--     strings on `payment`, not normalized lookups.
--   - No payment_party table — debtor/ultimateDebtor/creditor/ultimateCreditor/addressFormat are
--     flat top-level payment fields (one addressFormat per payment, not per party role), so they
--     live directly on `payment`. payment_agent_hop IS kept as a real table (genuinely useful for
--     lineage queries: "populated hops only", "find payments touching bank X").
--   - No ai_*/exec_kpi_*/rail_route_rule/regulatory_item/knowledge_document/mc_run/mc_outcome/
--     audit_log/saved_view/iso_message/payment_charge/payment_exception/investigation/
--     screening_result/liquidity_position/reconciliation_item/sla_policy tables — deferred agent,
--     out-of-scope Innovation Lab, or represented as plain columns on `payment` instead
--     (investigation_id, screening, liquidity_state, recon_state). ISO XML is generated on demand
--     in Python from a payment's fields (Phase 6), never stored.
--   - `legal_entity` IS a real table (added post-Phase-4, unlike the POC/reference schema where
--     it's vestigial — only 2 rows ever seeded, only 1 ever used by any customer, and the POC's
--     own "Legal entities: 18" header stat isn't backed by it at all). We model it for real
--     because this app deploys per-bank and one deployment can span multiple legal entities of
--     the same banking group (e.g. a US subsidiary + a UK branch sharing one instance).
--   - Guided simulation runs never mutate their source payment. Pressing Start clones the
--     source into a shadow row `SIM-{payment_id}` (`is_simulated=1`); the ticker only ever
--     writes to the shadow. This keeps Dashboard/Discovery aggregates honest while a
--     simulation is running or left un-reset, and makes Reset a plain DELETE of the shadow
--     (no snapshot/restore needed) instead of an in-place undo.
-- All business logic (state machine enforcement, projections) lives in the Python app layer,
-- not in DB triggers/functions.
-- =====================================================================

PRAGMA foreign_keys = ON;

/* =====================================================================
   A. REFERENCE / CONFIG DATA (seeded once by seed_reference_data.py)
   ===================================================================== */
CREATE TABLE IF NOT EXISTS bank (
  bic           TEXT PRIMARY KEY,
  bank_name     TEXT NOT NULL UNIQUE,
  country_code  TEXT
);

CREATE TABLE IF NOT EXISTS ref_domain (
  domain_code   TEXT PRIMARY KEY,           -- CBCC | DOME
  domain_name   TEXT NOT NULL,
  description   TEXT
);

CREATE TABLE IF NOT EXISTS ref_rail (
  rail_code          TEXT PRIMARY KEY,      -- 'SWIFT CBPR+','RTP','FedNow','ACH','Fedwire'
  rail_name          TEXT NOT NULL,
  domain_code        TEXT NOT NULL REFERENCES ref_domain(domain_code),
  latency_class      TEXT NOT NULL CHECK (latency_class IN ('XB','INSTANT','WIRE','BATCH')),
  network_limit_usd  REAL,                  -- NULL = no network cap
  operates_24x7      INTEGER NOT NULL CHECK (operates_24x7 IN (0,1)),
  settlement_model   TEXT,
  iso20022_since     TEXT,
  color_token        TEXT,
  description        TEXT
);

CREATE TABLE IF NOT EXISTS ref_scheme (
  scheme_code   TEXT PRIMARY KEY,           -- 'CBPR+','TCH RTP','FedNow','Nacha CCD',…
  rail_code     TEXT REFERENCES ref_rail(rail_code),
  description   TEXT
);

CREATE TABLE IF NOT EXISTS ref_payment_type (
  payment_type  TEXT PRIMARY KEY,
  radar_bucket  TEXT,                       -- Customer Credit / Instant / ACH Batch / FI Transfer / Return / Other
  description   TEXT
);

CREATE TABLE IF NOT EXISTS ref_iso_message_type (
  msg_type        TEXT PRIMARY KEY,         -- pacs.008, camt.056 …
  msg_family      TEXT NOT NULL,            -- pain | pacs | camt | head
  msg_name        TEXT NOT NULL,
  default_version TEXT,
  description     TEXT
);

CREATE TABLE IF NOT EXISTS ref_mt_mx_mapping (
  mt_type       TEXT PRIMARY KEY,
  mx_type       TEXT NOT NULL,
  meaning       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_iso_tx_status (
  tx_status     TEXT PRIMARY KEY,           -- ACCC ACSP RJCT RTND PDNG RCVD CANC
  meaning       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_reason_code (
  reason_code   TEXT PRIMARY KEY,           -- AC03, AM04, NACK_TIMEOUT …
  reason_name   TEXT NOT NULL,
  category      TEXT,                       -- ACCOUNT | AMOUNT | REGULATORY | NETWORK | SCREENING
  is_iso        INTEGER NOT NULL DEFAULT 1 CHECK (is_iso IN (0,1))
);

CREATE TABLE IF NOT EXISTS ref_payment_status (
  status        TEXT PRIMARY KEY,           -- COMPLETED IN_PROGRESS REJECTED RETURNED INVESTIGATION FAILED CANCELLED
  state_label   TEXT NOT NULL,
  badge_color   TEXT NOT NULL,              -- g c a r v n bl
  is_exception  INTEGER NOT NULL CHECK (is_exception IN (0,1)),
  is_final      INTEGER NOT NULL CHECK (is_final IN (0,1))
);

CREATE TABLE IF NOT EXISTS ref_payment_state (   -- authoritative state-machine states
  state          TEXT PRIMARY KEY,
  sort_order     INTEGER NOT NULL,
  iso_tx_status  TEXT NOT NULL REFERENCES ref_iso_tx_status(tx_status),
  payment_status TEXT NOT NULL REFERENCES ref_payment_status(status),
  is_terminal    INTEGER NOT NULL CHECK (is_terminal IN (0,1)),
  is_exception   INTEGER NOT NULL CHECK (is_exception IN (0,1)),
  is_happy_path  INTEGER NOT NULL CHECK (is_happy_path IN (0,1)),
  badge_color    TEXT NOT NULL,
  description    TEXT
);

CREATE TABLE IF NOT EXISTS ref_state_transition (
  from_state      TEXT REFERENCES ref_payment_state(state),  -- NULL = start of stream
  to_state        TEXT NOT NULL REFERENCES ref_payment_state(state),
  transition_type TEXT NOT NULL DEFAULT 'NORMAL'
                  CHECK (transition_type IN ('NORMAL','EXCEPTION','FAILURE','CANCEL','RECOVERY')),
  note            TEXT,
  UNIQUE (from_state, to_state)
);

CREATE TABLE IF NOT EXISTS customer (
  customer_id    TEXT PRIMARY KEY,
  customer_name  TEXT NOT NULL UNIQUE,
  segment        TEXT,                      -- Corporate, Global Corporate, Financial Institutions …
  country_code   TEXT
);

-- Added post-Phase-4: which of the deploying bank's own regulated subsidiaries/branches
-- legally booked a payment (not a counterparty bank — see `bank` table for those). Real
-- multi-tenant dimension since one deployment can span multiple legal entities of one group.
CREATE TABLE IF NOT EXISTS legal_entity (
  legal_entity_id  TEXT PRIMARY KEY,        -- e.g. 'ENT-US' — deployer renames/adds rows freely
  name             TEXT NOT NULL UNIQUE,
  country_code     TEXT,
  lei              TEXT                      -- ISO 17442 Legal Entity Identifier, optional
);

CREATE TABLE IF NOT EXISTS persona (
  mode_code        TEXT PRIMARY KEY,        -- CBCC | DOME | ALL
  domain_label     TEXT NOT NULL,
  title            TEXT NOT NULL,
  description      TEXT NOT NULL,
  mode_label       TEXT NOT NULL,
  default_agent_id TEXT                     -- referenced loosely; ai_agent table not built yet (deferred)
);

CREATE TABLE IF NOT EXISTS persona_tag (
  mode_code      TEXT NOT NULL REFERENCES persona(mode_code),
  tag            TEXT NOT NULL,
  sort_order     INTEGER NOT NULL,
  PRIMARY KEY (mode_code, tag)
);

CREATE TABLE IF NOT EXISTS field_category (
  category_name  TEXT PRIMARY KEY,
  sort_order     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS field_catalogue (
  field_id       TEXT PRIMARY KEY,          -- camelCase id used by UI / API
  display_name   TEXT NOT NULL,
  category_name  TEXT NOT NULL REFERENCES field_category(category_name),
  domain_code    TEXT NOT NULL DEFAULT 'ALL', -- ALL | CBCC | DOME
  message_types  TEXT NOT NULL DEFAULT '[]', -- JSON array string
  data_type      TEXT NOT NULL,              -- id uuid enum status text amount number date datetime
  searchable     INTEGER NOT NULL DEFAULT 1 CHECK (searchable IN (0,1)),
  displayable    INTEGER NOT NULL DEFAULT 1 CHECK (displayable IN (0,1)),
  filter_type    TEXT NOT NULL CHECK (filter_type IN ('text','enum','range','date')),
  sortable       INTEGER NOT NULL DEFAULT 1 CHECK (sortable IN (0,1)),
  groupable      INTEGER NOT NULL DEFAULT 0 CHECK (groupable IN (0,1)),
  description    TEXT NOT NULL,
  example        TEXT,
  source_column  TEXT NOT NULL,             -- snake_case column on `payment` this field maps to
  iso_path_hint  TEXT,                      -- semantic hint only (e.g. 'CdtTrfTxInf/PmtId/UETR')
  synonyms       TEXT NOT NULL DEFAULT '[]' -- JSON array string, NL aliases
);

CREATE TABLE IF NOT EXISTS column_preset (
  preset_code    TEXT PRIMARY KEY,          -- DEFAULT_ALL, DEFAULT_CBCC, DEFAULT_DOME, Operations, ISO 20022, Network, Risk & Liquidity
  preset_label   TEXT NOT NULL,
  mode_code      TEXT REFERENCES persona(mode_code)
);

CREATE TABLE IF NOT EXISTS column_preset_field (
  preset_code    TEXT NOT NULL REFERENCES column_preset(preset_code) ON DELETE CASCADE,
  position       INTEGER NOT NULL,
  field_id       TEXT NOT NULL REFERENCES field_catalogue(field_id),
  PRIMARY KEY (preset_code, position)
);

CREATE TABLE IF NOT EXISTS nl_query_vocabulary (   -- Discovery Fabric's query-parser dictionary
  token          TEXT PRIMARY KEY,
  token_kind     TEXT NOT NULL CHECK (token_kind IN ('STATUS','RAIL','DOMAIN','CURRENCY','KEY_ALIAS','STOPWORD','AMOUNT_OP')),
  maps_to        TEXT NOT NULL
);

/* =====================================================================
   B. SIMULATION DEFINITIONS
   ===================================================================== */
CREATE TABLE IF NOT EXISTS sim_scenario (
  scenario_code    TEXT PRIMARY KEY,        -- HAPPY TIMEOUT AC03 SCREEN
  scenario_name    TEXT NOT NULL,
  description      TEXT NOT NULL,
  expected_outcome TEXT NOT NULL REFERENCES ref_payment_state(state)
);

CREATE TABLE IF NOT EXISTS sim_scenario_step (
  scenario_code  TEXT NOT NULL REFERENCES sim_scenario(scenario_code),
  step_no        INTEGER NOT NULL,
  state          TEXT NOT NULL REFERENCES ref_payment_state(state),
  description    TEXT NOT NULL,
  iso_message    TEXT NOT NULL,             -- 'pacs.002 ACSP' or '—'
  PRIMARY KEY (scenario_code, step_no)
);

CREATE TABLE IF NOT EXISTS sim_speed (
  speed_code     TEXT PRIMARY KEY,          -- 1x 2x 5x 10x
  step_delay_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_latency_profile (
  latency_class  TEXT NOT NULL,             -- XB | INSTANT | WIRE | BATCH
  state          TEXT NOT NULL REFERENCES ref_payment_state(state),
  base_ms        INTEGER NOT NULL,
  PRIMARY KEY (latency_class, state)
);

/* =====================================================================
   C. CORE PAYMENT (flat, matching the POC's own JS payment object — see header note)
   ===================================================================== */
CREATE TABLE IF NOT EXISTS payment (
  -- identity
  payment_id          TEXT PRIMARY KEY,
  uetr                TEXT UNIQUE,          -- UUIDv4; NULL for rails without UETR
  swift_txn_id        TEXT,
  message_type        TEXT NOT NULL REFERENCES ref_iso_message_type(msg_type),
  business_msg_id     TEXT NOT NULL,
  instruction_id      TEXT NOT NULL,
  end_to_end_id       TEXT NOT NULL,
  -- classification
  legal_entity_id     TEXT REFERENCES legal_entity(legal_entity_id),  -- which of our entities booked this
  domain              TEXT NOT NULL REFERENCES ref_domain(domain_code),
  rail                TEXT NOT NULL REFERENCES ref_rail(rail_code),
  payment_type        TEXT NOT NULL REFERENCES ref_payment_type(payment_type),
  scheme              TEXT REFERENCES ref_scheme(scheme_code),
  country             TEXT,                 -- corridor display, e.g. 'US → GB'
  purpose             TEXT,                 -- CtgyPurp: SUPP TREA INTC SALA GDDS CASH
  iso_version         TEXT,
  address_format      TEXT CHECK (address_format IN ('STRUCTURED','HYBRID','UNSTRUCTURED')),
  priority            TEXT NOT NULL DEFAULT 'NORM' CHECK (priority IN ('NORM','HIGH','URGP')),
  settlement_method   TEXT NOT NULL CHECK (settlement_method IN ('INDA','INGA','COVE','CLRG')),
  clearing_system     TEXT,
  -- parties
  customer_id         TEXT REFERENCES customer(customer_id),  -- the debtor's customer master record, when known
  debtor              TEXT NOT NULL,
  ultimate_debtor      TEXT,
  debtor_account      TEXT,
  debtor_country      TEXT,
  creditor            TEXT NOT NULL,
  ultimate_creditor    TEXT,
  creditor_account    TEXT,
  creditor_country    TEXT,
  -- network / lineage summary (full hop chain lives in payment_agent_hop)
  debtor_agent        TEXT,
  correspondent       TEXT,
  intermediary        TEXT,
  creditor_agent      TEXT,
  bic                 TEXT,                 -- debtor agent BIC (display convenience)
  hops                INTEGER,              -- derived: populated agents in the chain
  -- amounts
  amount              REAL NOT NULL CHECK (amount > 0),
  debit_ccy           TEXT NOT NULL,
  credit_ccy          TEXT NOT NULL,
  credit_amount       REAL,                 -- derived: amount * fx_rate
  fx_rate             REAL NOT NULL DEFAULT 1 CHECK (fx_rate > 0),
  charges             TEXT NOT NULL DEFAULT 'SHAR' CHECK (charges IN ('SHAR','DEBT','CRED','SLEV','OUR')),
  fee_amount          REAL NOT NULL DEFAULT 0,
  -- dates
  settlement_date     TEXT NOT NULL,
  initiated_at        TEXT NOT NULL,
  completed_at        TEXT,                 -- ONLY when status = COMPLETED (see CHECK)
  duration            TEXT,                 -- display text, e.g. '17m 42s'
  -- authoritative state (written ONLY by engine/apply_event.py)
  sim_state           TEXT REFERENCES ref_payment_state(state),  -- NULL = no simulator override yet
  status              TEXT NOT NULL DEFAULT 'IN_PROGRESS' REFERENCES ref_payment_status(status),
  ultimate_status     TEXT NOT NULL DEFAULT 'IN_PROGRESS' REFERENCES ref_payment_status(status),
  intermediate_status TEXT NOT NULL DEFAULT 'INITIATED',
  status_reason       TEXT,
  exception_type      TEXT,                 -- derived
  screening           TEXT NOT NULL DEFAULT 'CLEARED',
  gpi_status          TEXT,                 -- derived, CBCC only
  mt_equivalent       TEXT,                 -- derived, CBCC only
  sla_state           TEXT,                 -- derived
  -- ownership & operations
  segment             TEXT,
  payment_initiation_dept TEXT,
  owner               TEXT,
  nostro              TEXT,
  liquidity_state     TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (liquidity_state IN ('AVAILABLE','TIGHT','SHORTFALL')),
  risk_score          INTEGER CHECK (risk_score BETWEEN 0 AND 100),
  investigation_id    TEXT,
  recon_state         TEXT NOT NULL DEFAULT 'OPEN' CHECK (recon_state IN ('OPEN','MATCHED','EXCEPTION')),
  mandate_ref         TEXT,                 -- FK added logically once mandate_obligation exists; not enforced (optional)
  obligation_due      TEXT,
  -- AI fields (flat, per POC data model; no separate agent tables yet)
  ai_recommendation   TEXT,
  ai_agent            TEXT,
  ai_confidence       REAL,
  -- guided simulation (Simulation Engine) — a simulated row is a full clone of its source
  -- payment under a derived id (SIM-{source_payment_id}); the source is never mutated by a
  -- simulation run, so Dashboard/Discovery/Payment 360 never see live-in-flight sim state
  -- unless explicitly viewing the simulated row itself.
  is_simulated        INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0,1)),
  source_payment_id   TEXT REFERENCES payment(payment_id),  -- set only when is_simulated=1
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
  -- ===== consistency rules (the "never show Completed" rule, enforced at the DB layer too) =====
  CHECK (completed_at IS NULL OR status = 'COMPLETED'),
  CHECK (domain <> 'CBCC' OR uetr IS NOT NULL),
  CHECK (NOT (status = 'COMPLETED' AND ultimate_status <> 'COMPLETED'))
);
CREATE INDEX IF NOT EXISTS ix_payment_status ON payment(status);
CREATE INDEX IF NOT EXISTS ix_payment_rail   ON payment(rail, status);
CREATE INDEX IF NOT EXISTS ix_payment_domain ON payment(domain);
CREATE INDEX IF NOT EXISTS ix_payment_sdate  ON payment(settlement_date);
CREATE INDEX IF NOT EXISTS ix_payment_customer ON payment(customer_id);
CREATE INDEX IF NOT EXISTS ix_payment_e2e    ON payment(end_to_end_id);
CREATE INDEX IF NOT EXISTS ix_payment_amount ON payment(amount);
CREATE INDEX IF NOT EXISTS ix_payment_simulated ON payment(is_simulated);

CREATE TABLE IF NOT EXISTS payment_agent_hop (      -- the bank chain (lineage), populated hops only
  payment_id     TEXT NOT NULL REFERENCES payment(payment_id) ON DELETE CASCADE,
  hop_seq        INTEGER NOT NULL CHECK (hop_seq BETWEEN 1 AND 9),
  agent_role     TEXT NOT NULL CHECK (agent_role IN ('DEBTOR_AGENT','CORRESPONDENT','INTERMEDIARY','CREDITOR_AGENT')),
  bic            TEXT NOT NULL REFERENCES bank(bic),
  PRIMARY KEY (payment_id, hop_seq),
  UNIQUE (payment_id, agent_role)
);
CREATE INDEX IF NOT EXISTS ix_hop_bic ON payment_agent_hop(bic);

/* =====================================================================
   D. SIMULATION RUNS & EVENTS (append-only, single source of truth)
   ===================================================================== */
CREATE TABLE IF NOT EXISTS sim_run (
  run_id                TEXT PRIMARY KEY,       -- SIM-20260925-0001
  scenario_code         TEXT NOT NULL REFERENCES sim_scenario(scenario_code),
  payment_id            TEXT NOT NULL REFERENCES payment(payment_id),  -- the source (real, never-deleted) payment
  shadow_payment_id     TEXT,                   -- the SIM-{payment_id} row this run actually operated on (no FK: outlives shadow deletion on reset)
  speed_code            TEXT NOT NULL DEFAULT '1x' REFERENCES sim_speed(speed_code),
  mode                  TEXT NOT NULL DEFAULT 'READY' CHECK (mode IN ('READY','RUNNING','PAUSED','FINISHED','RESET')),
  outcome               TEXT NOT NULL DEFAULT 'NO RUN',
  next_step_no          INTEGER NOT NULL DEFAULT 1,
  failure_injected      INTEGER NOT NULL DEFAULT 0 CHECK (failure_injected IN (0,1)),
  event_count           INTEGER NOT NULL DEFAULT 0,
  iso_message_count     INTEGER NOT NULL DEFAULT 0,
  exception_count       INTEGER NOT NULL DEFAULT 0,
  simulated_latency_ms  INTEGER NOT NULL DEFAULT 0,
  wall_elapsed_ms       INTEGER NOT NULL DEFAULT 0,
  hops                  INTEGER,
  started_at            TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at           TEXT
);
CREATE INDEX IF NOT EXISTS ix_simrun_payment ON sim_run(payment_id);

CREATE TABLE IF NOT EXISTS payment_event (
  event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_id      TEXT NOT NULL REFERENCES payment(payment_id) ON DELETE CASCADE,
  sim_run_id      TEXT REFERENCES sim_run(run_id),  -- NULL = baseline / directly-pushed stream
  seq             INTEGER NOT NULL,             -- 1..n within (payment_id, sim_run_id)
  state           TEXT NOT NULL REFERENCES ref_payment_state(state),
  prev_state      TEXT REFERENCES ref_payment_state(state),
  description     TEXT NOT NULL,
  iso_message     TEXT NOT NULL DEFAULT '—',    -- as displayed: 'pacs.002 ACSP'
  actor           TEXT NOT NULL,
  reason_code     TEXT REFERENCES ref_reason_code(reason_code),
  reason_text     TEXT,
  event_ts        TEXT NOT NULL,                -- simulated business timestamp
  processing_ms   INTEGER NOT NULL DEFAULT 0,   -- simulated latency of this step
  wall_elapsed_ms INTEGER,                      -- simulator wall-clock (event log "0.0s")
  failure_injected INTEGER NOT NULL DEFAULT 0 CHECK (failure_injected IN (0,1)),
  source          TEXT NOT NULL DEFAULT 'BASELINE' CHECK (source IN ('BASELINE','SIMULATION','NETWORK','MANUAL')),
  iso_payload     TEXT,                         -- generated ISO event JSON string
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_event_stream_seq ON payment_event (payment_id, IFNULL(sim_run_id,'<baseline>'), seq);
CREATE INDEX IF NOT EXISTS ix_event_state ON payment_event(state);
CREATE INDEX IF NOT EXISTS ix_event_run   ON payment_event(sim_run_id);

/* =====================================================================
   E. MANDATES & OBLIGATIONS (the mandate/customer/due-time/linked-payment identity is
      genuine configured reference data \u2014 an obligation is instructed, not derived from
      payment traffic. Its live readiness/coverage, however, is NEVER stored here: the
      Drilldown screen computes those from the linked payment's actual current state at
      query time \u2014 see app/drilldown/queries.py. Top Customers/Rails are likewise fully
      live SQL aggregates over `payment`, not stored anywhere \u2014 there is deliberately no
      customer_metric_daily/rail_metric_daily snapshot table.)
   ===================================================================== */
CREATE TABLE IF NOT EXISTS mandate_obligation (
  mandate_id        TEXT PRIMARY KEY,      -- MND-GTA-1800
  display_no        INTEGER,
  customer_id       TEXT REFERENCES customer(customer_id),
  description       TEXT NOT NULL,         -- 'USD liquidity top-up'
  due_time          TEXT NOT NULL,         -- 'HH:MM' ET
  linked_payment_id TEXT                   -- NOT a hard FK: seeded before Phase 3's payments exist
);
