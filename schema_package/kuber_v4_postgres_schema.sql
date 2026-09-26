-- =====================================================================
-- KUBER V4 — POSTGRESQL / ALLOYDB SYSTEM-OF-RECORD SCHEMA (complete)
-- Load order: this file → kuber_v4_seed.sql → kuber_v4_smoke_tests.sql
-- Tested on PostgreSQL 16.13 + pgvector. Requires PostgreSQL 15+ (UNIQUE NULLS NOT DISTINCT).
-- =====================================================================

/* =====================================================================
   KUBER V4 — PAYMENT INTELLIGENCE COMMAND CENTER
   System-of-record schema for PostgreSQL 15+ / AlloyDB for PostgreSQL / Cloud SQL
   ---------------------------------------------------------------------
   Design principles
   1. EVENT-SOURCED STATE. payment_event is append-only and is the only way the
      state of a payment changes. The current-state columns on "payment" are a
      projection, maintained by fn_project_payment().
      Result: the "a rejected payment can never show Completed" rule is enforced
      by the database itself, not by UI code.
   2. LOOKUP TABLES rather than enums. Every code (state, rail, ISO message,
      reason) has a row with a human description, so an LLM agent can read
      the meanings directly.
   3. AGENT SEMANTIC LAYER. The views and functions in 03_logic.sql compute
      exactly what the HTML computes: journey, status indicator, evidence,
      confidence and proposed actions. The agent calls deterministic tools and
      only narrates the result.
   4. HUMAN-IN-THE-LOOP BOUNDARY. The database enforces it: agents can only
      PROPOSE; approvals must come from a human user; external_executed is
      always false.
   ===================================================================== */

CREATE SCHEMA IF NOT EXISTS kuber;
SET search_path = kuber, public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;   -- gen_random_uuid() (core in PG13+, kept for older engines)

/* =====================================================================
   A. ORGANISATION & REFERENCE DATA
   ===================================================================== */
CREATE TABLE ref_region (
  region_code   varchar(8) PRIMARY KEY,
  region_name   text NOT NULL
);
CREATE TABLE ref_country (
  country_code  char(2) PRIMARY KEY,               -- ISO 3166-1 alpha-2
  country_name  text NOT NULL,
  region_code   varchar(8) REFERENCES ref_region
);
CREATE TABLE ref_currency (
  ccy           char(3) PRIMARY KEY,               -- ISO 4217
  ccy_name      text NOT NULL,
  minor_units   smallint NOT NULL DEFAULT 2
);
CREATE TABLE legal_entity (
  legal_entity_id varchar(32) PRIMARY KEY,
  name            text NOT NULL,
  country_code    char(2) REFERENCES ref_country,
  lei             char(20)                          -- ISO 17442 LEI (optional)
);
CREATE TABLE bank (                                  -- network participants (agents)
  bic           varchar(11) PRIMARY KEY,           -- BICFI
  bank_name     text NOT NULL UNIQUE,
  country_code  char(2) REFERENCES ref_country,
  is_gpi_member boolean NOT NULL DEFAULT true
);
CREATE TABLE ref_domain (                            -- persona / business domain
  domain_code   varchar(8) PRIMARY KEY,            -- CBCC | DOME
  domain_name   text NOT NULL,
  description   text
);
CREATE TABLE payment_business (
  business_code   varchar(32) PRIMARY KEY,
  legal_entity_id varchar(32) REFERENCES legal_entity,
  domain_code     varchar(8) REFERENCES ref_domain,
  business_name   text NOT NULL
);
CREATE TABLE ref_rail (
  rail_code          varchar(24) PRIMARY KEY,      -- 'SWIFT CBPR+','RTP','FedNow','ACH','Fedwire'
  rail_name          text NOT NULL,
  domain_code        varchar(8) NOT NULL REFERENCES ref_domain,
  latency_class      varchar(8) NOT NULL CHECK (latency_class IN ('XB','INSTANT','WIRE','BATCH')),
  network_limit_usd  numeric(20,2),                -- NULL = no network cap
  operates_24x7      boolean NOT NULL,
  settlement_model   text,
  iso20022_since     date,
  color_token        varchar(16),                  -- UI color (cyan/violet/…)
  description        text
);
CREATE TABLE ref_scheme (
  scheme_code   varchar(40) PRIMARY KEY,           -- 'CBPR+','TCH RTP','FedNow','Nacha CCD',…
  rail_code     varchar(24) REFERENCES ref_rail,
  description   text
);
CREATE TABLE ref_payment_type (
  payment_type  varchar(64) PRIMARY KEY,
  radar_bucket  varchar(32),                       -- Customer Credit / Instant / ACH Batch / FI Transfer / Return / Other
  description   text
);
CREATE TABLE ref_iso_message_type (
  msg_type       varchar(16) PRIMARY KEY,          -- pacs.008, camt.056 …
  msg_family     varchar(8) NOT NULL,              -- pain | pacs | camt | head
  msg_name       text NOT NULL,
  default_version varchar(24),                     -- pacs.008.001.08
  description    text
);
CREATE TABLE ref_mt_mx_mapping (                    -- semantic mapping only
  mt_type       varchar(24) PRIMARY KEY,
  mx_type       varchar(24) NOT NULL,
  meaning       text NOT NULL
);
CREATE TABLE ref_iso_tx_status (                    -- ExternalPaymentTransactionStatus1Code subset
  tx_status     varchar(4) PRIMARY KEY,            -- ACCC ACSP RJCT RTND PDNG RCVD CANC
  meaning       text NOT NULL
);
CREATE TABLE ref_reason_code (                      -- ExternalStatusReason1Code subset + internal
  reason_code   varchar(24) PRIMARY KEY,           -- AC03, AM04, NACK_TIMEOUT …
  reason_name   text NOT NULL,
  category      varchar(24),                       -- ACCOUNT | AMOUNT | REGULATORY | NETWORK | SCREENING
  is_iso        boolean NOT NULL DEFAULT true
);

/* ---------- state machine ---------- */
CREATE TABLE ref_payment_status (                   -- business status shown in grid / badges
  status        varchar(24) PRIMARY KEY,           -- COMPLETED IN_PROGRESS REJECTED RETURNED INVESTIGATION FAILED CANCELLED
  state_label   text NOT NULL,                     -- 'Completed','Rejected',… ('Current State' for IN_PROGRESS)
  badge_color   varchar(8) NOT NULL,               -- g c a r v n bl
  is_exception  boolean NOT NULL,
  is_final      boolean NOT NULL
);
CREATE TABLE ref_payment_state (                    -- authoritative state-machine states
  state          varchar(32) PRIMARY KEY,
  sort_order     smallint NOT NULL,
  iso_tx_status  varchar(4) NOT NULL REFERENCES ref_iso_tx_status,
  payment_status varchar(24) NOT NULL REFERENCES ref_payment_status,   -- projection mapping
  is_terminal    boolean NOT NULL,
  is_exception   boolean NOT NULL,
  is_happy_path  boolean NOT NULL,
  badge_color    varchar(8) NOT NULL,
  description    text
);
CREATE TABLE ref_state_transition (
  from_state      varchar(32) REFERENCES ref_payment_state,  -- NULL = start of stream
  to_state        varchar(32) NOT NULL REFERENCES ref_payment_state,
  transition_type varchar(16) NOT NULL DEFAULT 'NORMAL'
                  CHECK (transition_type IN ('NORMAL','EXCEPTION','FAILURE','CANCEL','RECOVERY')),
  note            text,
  UNIQUE NULLS NOT DISTINCT (from_state, to_state)
);

/* ---------- customers, people, accounts ---------- */
CREATE TABLE customer (
  customer_id    varchar(32) PRIMARY KEY,
  customer_name  text NOT NULL UNIQUE,
  segment        varchar(32),                      -- Corporate, Global Corporate, Financial Institutions …
  country_code   char(2) REFERENCES ref_country,
  legal_entity_id varchar(32) REFERENCES legal_entity
);
CREATE TABLE department (
  department_code varchar(40) PRIMARY KEY,         -- 'Equity Operations', 'FI Payments' …
  department_name text NOT NULL
);
CREATE TABLE app_user (                             -- humans AND agents (principal table)
  user_id        varchar(40) PRIMARY KEY,
  display_name   text NOT NULL,
  principal_type varchar(8) NOT NULL CHECK (principal_type IN ('HUMAN','AGENT','SYSTEM')),
  department_code varchar(40) REFERENCES department,
  role_code      varchar(24) NOT NULL DEFAULT 'OPERATOR',  -- OPERATOR, SUPERVISOR, ANALYST, AGENT
  is_active      boolean NOT NULL DEFAULT true
);
CREATE TABLE settlement_account (                   -- nostro / Fed master / TCH joint account
  account_id     varchar(32) PRIMARY KEY,
  display_name   text NOT NULL UNIQUE,             -- 'USD NOSTRO • 004821'
  account_type   varchar(16) NOT NULL CHECK (account_type IN ('NOSTRO','VOSTRO','FED_MASTER','RTP_JOINT','INTERNAL')),
  ccy            char(3) NOT NULL REFERENCES ref_currency,
  servicer_bic   varchar(11) REFERENCES bank,
  legal_entity_id varchar(32) REFERENCES legal_entity
);

/* =====================================================================
   B. CORE PAYMENT
   ===================================================================== */
CREATE TABLE payment (
  -- identity
  payment_id          varchar(64) PRIMARY KEY,
  uetr                uuid UNIQUE,                  -- UUIDv4; NULL for rails without UETR
  swift_txn_id        varchar(64),
  business_msg_id     varchar(64) NOT NULL,         -- head.001 BizMsgIdr
  instruction_id      varchar(64) NOT NULL,
  end_to_end_id       varchar(64) NOT NULL,
  -- classification
  domain_code         varchar(8)  NOT NULL REFERENCES ref_domain,
  rail_code           varchar(24) NOT NULL REFERENCES ref_rail,
  payment_type        varchar(64) NOT NULL REFERENCES ref_payment_type,
  scheme_code         varchar(40) REFERENCES ref_scheme,
  business_code       varchar(32) REFERENCES payment_business,
  message_type        varchar(16) NOT NULL REFERENCES ref_iso_message_type,   -- primary / current message
  iso_version         varchar(24),
  purpose_code        varchar(8),                   -- CtgyPurp: SUPP TREA INTC SALA GDDS CASH
  priority            varchar(8) NOT NULL DEFAULT 'NORM' CHECK (priority IN ('NORM','HIGH','URGP')),
  settlement_method   varchar(4) NOT NULL CHECK (settlement_method IN ('INDA','INGA','COVE','CLRG')),
  clearing_system     text,
  corridor            text,                         -- 'US → GB'
  -- amounts
  amount              numeric(20,2) NOT NULL CHECK (amount > 0),
  debit_ccy           char(3) NOT NULL REFERENCES ref_currency,
  credit_ccy          char(3) NOT NULL REFERENCES ref_currency,
  fx_rate             numeric(18,8) NOT NULL DEFAULT 1 CHECK (fx_rate > 0),
  charge_bearer       varchar(4) NOT NULL DEFAULT 'SHAR' CHECK (charge_bearer IN ('SHAR','DEBT','CRED','SLEV','OUR')),
  fee_amount_usd      numeric(14,4) NOT NULL DEFAULT 0,
  -- dates
  settlement_date     date NOT NULL,
  initiated_at        timestamp NOT NULL,           -- business-local timestamp as shown in UI
  -- values as received from the payment platform (the "record"); used when the baseline stream is active
  record_completed_at timestamp,
  record_processing_ms bigint,
  record_duration_text text,                        -- as displayed by the source platform ('17m 42s')
  record_intermediate_status varchar(40),           -- WAITING_CORRESPONDENT, SETTLED, ACCC, …
  record_status_reason_code varchar(24),            -- FK added after ref_reason_code seed-safe
  record_status_reason_text text,
  record_screening_state varchar(40) NOT NULL DEFAULT 'CLEARED',
  record_recon_state  varchar(16) NOT NULL DEFAULT 'OPEN' CHECK (record_recon_state IN ('OPEN','MATCHED','EXCEPTION')),
  -- ownership & operations
  customer_id         varchar(32) REFERENCES customer,
  initiation_department varchar(40) REFERENCES department,
  owner_user_id       varchar(40) REFERENCES app_user,
  nostro_account_id   varchar(32) REFERENCES settlement_account,
  liquidity_state     varchar(16) NOT NULL DEFAULT 'AVAILABLE' CHECK (liquidity_state IN ('AVAILABLE','TIGHT','SHORTFALL')),
  risk_score          smallint CHECK (risk_score BETWEEN 0 AND 100),
  mandate_id          varchar(32),                  -- FK added after mandate_obligation
  -- ===== projection of the event stream (written ONLY by fn_project_payment) =====
  current_state       varchar(32) REFERENCES ref_payment_state,
  current_message_type varchar(16) REFERENCES ref_iso_message_type,  -- pacs.004 after a return
  completed_at        timestamp,                    -- ONLY when status = COMPLETED (see CHECK)
  processing_ms       bigint,                       -- end-to-end duration
  status              varchar(24) NOT NULL DEFAULT 'IN_PROGRESS' REFERENCES ref_payment_status,
  ultimate_status     varchar(24) NOT NULL DEFAULT 'IN_PROGRESS' REFERENCES ref_payment_status,
  intermediate_status varchar(40) NOT NULL DEFAULT 'INITIATED',  -- state or operational sub-state (WAITING_CORRESPONDENT, SETTLED, ACCC)
  status_reason_code  varchar(24) REFERENCES ref_reason_code,
  status_reason_text  text,
  screening_state     varchar(40) NOT NULL DEFAULT 'CLEARED',
  recon_state         varchar(16) NOT NULL DEFAULT 'OPEN' CHECK (recon_state IN ('OPEN','MATCHED','EXCEPTION')),
  active_sim_run_id   varchar(32),                  -- FK added later; NULL = baseline stream is authoritative
  current_investigation_id varchar(32),             -- FK added later
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  -- ===== consistency rules (the HTML "never show Completed" rule, enforced) =====
  CONSTRAINT ck_completed_at_only_when_completed CHECK (completed_at IS NULL OR status = 'COMPLETED'),
  CONSTRAINT ck_uetr_for_cbcc CHECK (domain_code <> 'CBCC' OR uetr IS NOT NULL),
  CONSTRAINT ck_ultimate_vs_status CHECK (NOT (status = 'COMPLETED' AND ultimate_status <> 'COMPLETED')),
  CONSTRAINT fk_record_reason FOREIGN KEY (record_status_reason_code) REFERENCES ref_reason_code
);
CREATE INDEX ix_payment_status   ON payment(status);
CREATE INDEX ix_payment_rail     ON payment(rail_code, status);
CREATE INDEX ix_payment_domain   ON payment(domain_code);
CREATE INDEX ix_payment_sdate    ON payment(settlement_date);
CREATE INDEX ix_payment_e2e      ON payment(end_to_end_id);
CREATE INDEX ix_payment_instr    ON payment(instruction_id);
CREATE INDEX ix_payment_customer ON payment(customer_id);
CREATE INDEX ix_payment_amount   ON payment(amount);

CREATE TABLE payment_party (                        -- Dbtr / UltmtDbtr / Cdtr / UltmtCdtr
  payment_id     varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  party_role     varchar(20) NOT NULL CHECK (party_role IN ('DEBTOR','ULTIMATE_DEBTOR','CREDITOR','ULTIMATE_CREDITOR')),
  party_name     text NOT NULL,
  customer_id    varchar(32) REFERENCES customer,
  account_id     text,                             -- IBAN / proprietary / routing-account
  country_code   char(2) REFERENCES ref_country,
  address_format varchar(12) CHECK (address_format IN ('STRUCTURED','HYBRID','UNSTRUCTURED')),  -- CBPR+ SR2026 readiness
  street_name    text, building_no text, post_code text, town_name text,
  address_lines  text[],                           -- only for UNSTRUCTURED / HYBRID remainder
  PRIMARY KEY (payment_id, party_role)
);
CREATE INDEX ix_party_name ON payment_party (lower(party_name));

CREATE TABLE payment_agent_hop (                    -- the bank chain (lineage)
  payment_id     varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  hop_seq        smallint NOT NULL CHECK (hop_seq BETWEEN 1 AND 9),
  agent_role     varchar(20) NOT NULL CHECK (agent_role IN ('DEBTOR_AGENT','CORRESPONDENT','INTERMEDIARY','CREDITOR_AGENT')),
  bic            varchar(11) NOT NULL REFERENCES bank,
  received_at    timestamp,
  forwarded_at   timestamp,
  PRIMARY KEY (payment_id, hop_seq),
  UNIQUE (payment_id, agent_role)
);
CREATE INDEX ix_hop_bic ON payment_agent_hop(bic);

CREATE TABLE payment_charge (                       -- fees detail (optional granularity)
  charge_id      bigserial PRIMARY KEY,
  payment_id     varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  charged_by_bic varchar(11) REFERENCES bank,
  amount         numeric(14,4) NOT NULL,
  ccy            char(3) NOT NULL REFERENCES ref_currency,
  charge_type    varchar(16) NOT NULL DEFAULT 'PROCESSING'
);

/* =====================================================================
   C. SIMULATION DEFINITIONS (needed before events reference runs)
   ===================================================================== */
CREATE TABLE sim_scenario (
  scenario_code  varchar(16) PRIMARY KEY,          -- HAPPY TIMEOUT AC03 SCREEN
  scenario_name  text NOT NULL,
  description    text NOT NULL,
  expected_outcome varchar(32) NOT NULL REFERENCES ref_payment_state
);
CREATE TABLE sim_scenario_step (
  scenario_code  varchar(16) NOT NULL REFERENCES sim_scenario,
  step_no        smallint NOT NULL,
  state          varchar(32) NOT NULL REFERENCES ref_payment_state,
  description    text NOT NULL,
  iso_message    varchar(32) NOT NULL,             -- 'pacs.002 ACSP' or '—'
  PRIMARY KEY (scenario_code, step_no)
);
CREATE TABLE sim_speed (
  speed_code     varchar(4) PRIMARY KEY,           -- 1x 2x 5x 10x
  step_delay_ms  integer NOT NULL
);
CREATE TABLE sim_latency_profile (                  -- simulated network latency base (ms)
  latency_class  varchar(8) NOT NULL,
  state          varchar(32) NOT NULL REFERENCES ref_payment_state,
  base_ms        bigint NOT NULL,
  PRIMARY KEY (latency_class, state)
);
CREATE TABLE sim_run (
  run_id         varchar(32) PRIMARY KEY,          -- SIM-20260925-0001
  scenario_code  varchar(16) NOT NULL REFERENCES sim_scenario,
  payment_id     varchar(64) NOT NULL REFERENCES payment,
  speed_code     varchar(4) NOT NULL DEFAULT '1x' REFERENCES sim_speed,
  mode           varchar(12) NOT NULL DEFAULT 'RUNNING' CHECK (mode IN ('READY','RUNNING','PAUSED','FINISHED','RESET')),
  outcome        varchar(24) NOT NULL DEFAULT 'RUNNING', -- RUNNING PAUSED COMPLETED FAILED RETURNED 'FAILURE INJECTED'
  next_step_no   smallint NOT NULL DEFAULT 1,
  failure_injected boolean NOT NULL DEFAULT false,
  event_count    integer NOT NULL DEFAULT 0,
  iso_message_count integer NOT NULL DEFAULT 0,
  exception_count integer NOT NULL DEFAULT 0,
  simulated_latency_ms bigint NOT NULL DEFAULT 0,
  wall_elapsed_ms bigint NOT NULL DEFAULT 0,
  hops           smallint,
  started_by     varchar(40) REFERENCES app_user,
  started_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);
CREATE INDEX ix_simrun_payment ON sim_run(payment_id);
ALTER TABLE payment ADD CONSTRAINT fk_payment_active_run FOREIGN KEY (active_sim_run_id) REFERENCES sim_run DEFERRABLE INITIALLY DEFERRED;

/* =====================================================================
   D. EVENTS & ISO MESSAGES (append-only, single source of truth)
   ===================================================================== */
CREATE TABLE payment_event (
  event_id        bigserial PRIMARY KEY,
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  sim_run_id      varchar(32) REFERENCES sim_run,  -- NULL = baseline / network stream
  seq             smallint NOT NULL,               -- 1..n within (payment, stream)
  state           varchar(32) NOT NULL REFERENCES ref_payment_state,
  prev_state      varchar(32) REFERENCES ref_payment_state,
  description     text NOT NULL,
  iso_message     varchar(32) NOT NULL DEFAULT '—',-- as displayed: 'pacs.002 ACSP'
  iso_msg_type    varchar(16) REFERENCES ref_iso_message_type,  -- base type parsed from iso_message
  iso_tx_status   varchar(4)  REFERENCES ref_iso_tx_status,     -- TxSts carried by the message (if any)
  actor           text NOT NULL,
  reason_code     varchar(24) REFERENCES ref_reason_code,
  reason_text     text,
  event_ts        timestamp NOT NULL,              -- simulated business timestamp
  processing_ms   bigint NOT NULL DEFAULT 0,       -- simulated latency of this step
  wall_elapsed_ms bigint,                          -- simulator wall-clock (event log "0.0s")
  failure_injected boolean NOT NULL DEFAULT false,
  source          varchar(12) NOT NULL DEFAULT 'BASELINE' CHECK (source IN ('BASELINE','SIMULATION','NETWORK','MANUAL')),
  iso_payload     json,                            -- generated ISO event JSON (json keeps key order)
  recorded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_event_stream_seq ON payment_event (payment_id, COALESCE(sim_run_id,'<baseline>'), seq);
CREATE INDEX ix_event_state ON payment_event(state);
CREATE INDEX ix_event_run   ON payment_event(sim_run_id);

CREATE TABLE iso_message (                          -- full messages (XML) linked to events
  message_id      bigserial PRIMARY KEY,
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  event_id        bigint REFERENCES payment_event ON DELETE CASCADE,
  msg_type        varchar(16) NOT NULL REFERENCES ref_iso_message_type,
  msg_version     varchar(24),
  direction       varchar(3) NOT NULL CHECK (direction IN ('IN','OUT')),
  business_msg_id varchar(64),
  from_bic        varchar(11) REFERENCES bank,
  to_bic          varchar(11) REFERENCES bank,
  tx_status       varchar(4) REFERENCES ref_iso_tx_status,
  reason_code     varchar(24) REFERENCES ref_reason_code,
  payload_xml     text,
  created_at      timestamp NOT NULL
);
CREATE INDEX ix_isomsg_payment ON iso_message(payment_id, msg_type);

/* =====================================================================
   E. EXCEPTIONS, INVESTIGATIONS, SCREENING, LIQUIDITY, RECON, MANDATES
   ===================================================================== */
CREATE TABLE payment_exception (
  exception_id    bigserial PRIMARY KEY,
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  event_id        bigint REFERENCES payment_event,
  exception_type  varchar(24) NOT NULL CHECK (exception_type IN ('BENEFICIARY_ACCOUNT','REJECTION','NETWORK_SLA','SCREENING_HOLD','CORRESPONDENT_SLA','LIQUIDITY','OTHER')),
  reason_code     varchar(24) REFERENCES ref_reason_code,
  severity        varchar(8) NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  status          varchar(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','CLOSED')),
  opened_at       timestamp NOT NULL,
  closed_at       timestamp
);
CREATE TABLE investigation (
  investigation_id varchar(32) PRIMARY KEY,        -- INV-90244
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  case_type       varchar(24) NOT NULL CHECK (case_type IN ('CORRESPONDENT_CHASE','RETURN','SCREENING_REVIEW','RECALL','CLAIM_NON_RECEIPT','OTHER')),
  status          varchar(16) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PENDING','RESOLVED','CLOSED')),
  assignee_bic    varchar(11) REFERENCES bank,     -- camt.056 Assgne
  owner_user_id   varchar(40) REFERENCES app_user,
  sim_run_id      varchar(32) REFERENCES sim_run,  -- set when opened by a simulation run
  opened_at       timestamp NOT NULL,
  resolution_code varchar(4) CHECK (resolution_code IN ('CNCL','RJCR','PDCR')),   -- camt.029 Conf
  resolved_at     timestamp,
  notes           text
);
ALTER TABLE payment ADD CONSTRAINT fk_payment_investigation FOREIGN KEY (current_investigation_id) REFERENCES investigation DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE screening_result (
  screening_id    bigserial PRIMARY KEY,
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  screened_at     timestamp NOT NULL,
  engine          text NOT NULL DEFAULT 'Sanctions / AML engine',
  result          varchar(40) NOT NULL,            -- CLEARED | POTENTIAL MATCH | HOLD — POTENTIAL MATCH | CLEARED (human review) | RELEASED (human review)
  match_score     numeric(5,2),
  hit_details     jsonb,
  reviewed_by     varchar(40) REFERENCES app_user,
  decision        varchar(12) CHECK (decision IN ('RELEASE','BLOCK','ESCALATE'))
);
CREATE TABLE liquidity_position (                   -- intraday liquidity per settlement account
  account_id      varchar(32) NOT NULL REFERENCES settlement_account,
  as_of           timestamp NOT NULL,
  ledger_balance  numeric(20,2) NOT NULL,
  available_balance numeric(20,2) NOT NULL,
  buffer_amount   numeric(20,2) NOT NULL DEFAULT 0,
  liquidity_state varchar(16) NOT NULL CHECK (liquidity_state IN ('AVAILABLE','TIGHT','SHORTFALL')),
  PRIMARY KEY (account_id, as_of)
);
CREATE TABLE reconciliation_item (
  recon_id        bigserial PRIMARY KEY,
  payment_id      varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  account_id      varchar(32) REFERENCES settlement_account,
  source_msg_type varchar(16) REFERENCES ref_iso_message_type,   -- camt.053 / camt.054 / camt.052
  statement_ref   text,
  recon_state     varchar(16) NOT NULL CHECK (recon_state IN ('OPEN','MATCHED','EXCEPTION')),
  break_reason    text,
  matched_at      timestamp
);
CREATE TABLE mandate_obligation (
  mandate_id      varchar(32) PRIMARY KEY,         -- MND-GTA-1800
  display_no      smallint,
  customer_id     varchar(32) REFERENCES customer,
  description     text NOT NULL,                   -- 'USD liquidity top-up'
  due_time        time NOT NULL,                   -- ET
  due_tz          text NOT NULL DEFAULT 'America/New_York',
  linked_payment_id varchar(64) REFERENCES payment,
  coverage_pct    numeric(5,2),
  readiness       varchar(16) NOT NULL CHECK (readiness IN ('Ready','Needs review')),
  readiness_note  text
);
ALTER TABLE payment ADD CONSTRAINT fk_payment_mandate FOREIGN KEY (mandate_id) REFERENCES mandate_obligation DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE sla_policy (
  rail_code       varchar(24) NOT NULL REFERENCES ref_rail,
  state           varchar(32) NOT NULL REFERENCES ref_payment_state,
  max_ms          bigint NOT NULL,
  PRIMARY KEY (rail_code, state)
);

/* =====================================================================
   F. METADATA: FIELD CATALOGUE, PERSONAS, COLUMN PRESETS, SAVED VIEWS
   The agent uses these tables to resolve business terms such as
   "beneficiary" or "method of payment" to physical columns.
   ===================================================================== */
SET search_path = kuber, public;

CREATE TABLE field_category (
  category_name  text PRIMARY KEY,
  sort_order     smallint NOT NULL
);
CREATE TABLE field_catalogue (
  field_id       varchar(40) PRIMARY KEY,          -- camelCase id used by UI / API
  display_name   text NOT NULL,
  category_name  text NOT NULL REFERENCES field_category,
  domain_code    varchar(8) NOT NULL DEFAULT 'ALL',-- ALL | CBCC | DOME
  message_types  text[] NOT NULL DEFAULT '{}',
  data_type      varchar(12) NOT NULL,             -- id uuid enum status text amount number date datetime
  searchable     boolean NOT NULL DEFAULT true,
  displayable    boolean NOT NULL DEFAULT true,
  filter_type    varchar(8) NOT NULL CHECK (filter_type IN ('text','enum','range','date')),
  sortable       boolean NOT NULL DEFAULT true,
  groupable      boolean NOT NULL DEFAULT false,
  description    text NOT NULL,
  example        text,
  source_view    text NOT NULL DEFAULT 'kuber.v_payment_current',  -- where the agent reads it
  source_column  text NOT NULL,                    -- snake_case column in source_view
  iso_path_hint  text,                             -- semantic hint only (e.g. 'CdtTrfTxInf/PmtId/UETR')
  synonyms       text[] NOT NULL DEFAULT '{}'      -- NL aliases for the agent
);
CREATE TABLE persona (
  mode_code      varchar(8) PRIMARY KEY,           -- CBCC | DOME | ALL
  domain_label   text NOT NULL,
  title          text NOT NULL,
  description    text NOT NULL,
  mode_label     text NOT NULL,
  default_agent_id varchar(24)                     -- FK added after ai_agent
);
CREATE TABLE persona_tag (
  mode_code      varchar(8) NOT NULL REFERENCES persona,
  tag            text NOT NULL,
  sort_order     smallint NOT NULL,
  PRIMARY KEY (mode_code, tag)
);
CREATE TABLE column_preset (
  preset_code    varchar(40) PRIMARY KEY,          -- DEFAULT_ALL, DEFAULT_CBCC, DEFAULT_DOME, Operations, ISO 20022, Network, Risk & Liquidity
  preset_label   text NOT NULL,
  mode_code      varchar(8) REFERENCES persona     -- set for persona defaults
);
CREATE TABLE column_preset_field (
  preset_code    varchar(40) NOT NULL REFERENCES column_preset ON DELETE CASCADE,
  position       smallint NOT NULL,
  field_id       varchar(40) NOT NULL REFERENCES field_catalogue,
  PRIMARY KEY (preset_code, position)
);
CREATE TABLE saved_view (                           -- user / agent saved discovery views
  view_id        bigserial PRIMARY KEY,
  owner_user_id  varchar(40) NOT NULL REFERENCES app_user,
  view_name      text NOT NULL,
  mode_code      varchar(8) REFERENCES persona,
  query_text     text,                             -- raw NL query ('rejected swift over 1m')
  parsed_query   jsonb,                            -- {status[],rail[],domain,msg[],ccy[],min,max,text[],keys{}}
  filters        jsonb,                            -- select filters + drilldowns
  columns        text[] NOT NULL,
  sort_field     varchar(40), sort_dir smallint,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE nl_query_vocabulary (                  -- the discovery parser's dictionary (shared with agent)
  token          text PRIMARY KEY,
  token_kind     varchar(12) NOT NULL CHECK (token_kind IN ('STATUS','RAIL','DOMAIN','CURRENCY','KEY_ALIAS','STOPWORD','AMOUNT_OP')),
  maps_to        text NOT NULL
);

/* =====================================================================
   G. EXECUTIVE RADAR / ANALYTICS SNAPSHOTS
   (In production these are materialised in BigQuery from the event
   stream. They are stored here too, so the command center renders
   the exact T-1 figures.)
   ===================================================================== */
CREATE TABLE exec_kpi_snapshot (
  business_date  date NOT NULL,
  kpi_code       varchar(24) NOT NULL,             -- PAYMENT_VALUE PAYMENTS STP_RATE SUCCESS_RATE EXCEPTIONS AVG_LATENCY AI_CASES
  sort_order     smallint NOT NULL,
  label          text NOT NULL,
  numeric_value  numeric(24,6) NOT NULL,
  unit           varchar(8) NOT NULL,              -- USD COUNT PCT SEC COUNT
  display_value  text NOT NULL,                    -- '$1.84T'
  delta_text     text,
  delta_is_warning boolean NOT NULL DEFAULT false,
  accent_token   varchar(8),
  PRIMARY KEY (business_date, kpi_code)
);
CREATE TABLE exec_kpi_spark (
  business_date  date NOT NULL,
  kpi_code       varchar(24) NOT NULL,
  point_date     date NOT NULL,
  value          numeric(24,6) NOT NULL,
  PRIMARY KEY (business_date, kpi_code, point_date),
  FOREIGN KEY (business_date, kpi_code) REFERENCES exec_kpi_snapshot
);
CREATE TABLE exec_breakdown (                       -- donuts + type bars
  business_date  date NOT NULL,
  chart_code     varchar(16) NOT NULL CHECK (chart_code IN ('STATUS','RAIL','TYPE')),
  bucket         text NOT NULL,
  sort_order     smallint NOT NULL,
  share_pct      numeric(6,2) NOT NULL,            -- volume share
  value_usd      numeric(24,2) NOT NULL,
  display_value  text NOT NULL,
  color_token    varchar(8),
  PRIMARY KEY (business_date, chart_code, bucket)
);
CREATE TABLE exec_trend_daily (
  business_date  date PRIMARY KEY,
  payment_count  bigint NOT NULL,                  -- 8,720,000
  payment_value_usd numeric(24,2) NOT NULL         -- 1.84e12
);
CREATE TABLE customer_metric_daily (
  business_date  date NOT NULL,
  customer_id    varchar(32) NOT NULL REFERENCES customer,
  display_name   text NOT NULL,                    -- 'HSBC Treasury'
  value_usd      numeric(24,2) NOT NULL,
  stp_rate_pct   numeric(5,2) NOT NULL,
  PRIMARY KEY (business_date, customer_id)
);
CREATE TABLE rail_metric_daily (
  business_date  date NOT NULL,
  rail_label     text NOT NULL,                    -- includes 'Other'
  rail_code      varchar(24) REFERENCES ref_rail,
  sort_order     smallint NOT NULL,
  value_usd      numeric(24,2) NOT NULL,
  display_value  text NOT NULL,
  latency_display text NOT NULL,                   -- '2.1s p95', '6.0h avg', '—'
  latency_ms     bigint,
  latency_stat   varchar(4),                       -- p95 | avg
  PRIMARY KEY (business_date, rail_label)
);

/* =====================================================================
   H. KUBER AGENTIC OPERATIONS
   ===================================================================== */
CREATE TABLE ai_agent (
  agent_id       varchar(24) PRIMARY KEY,          -- orchestrator investigator correspondent risk liquidity recon iso
  agent_name     text NOT NULL UNIQUE,
  role_text      text NOT NULL,
  icon           text NOT NULL,
  color_token    varchar(8) NOT NULL,
  status_text    text NOT NULL,                    -- 'watching 31 chains'
  system_prompt  text NOT NULL,                    -- agent instructions (see seed)
  allowed_tools  text[] NOT NULL DEFAULT '{}',     -- MCP tool names this agent may call
  principal_id   varchar(40) NOT NULL REFERENCES app_user,  -- AGENT principal
  sort_order     smallint NOT NULL
);
ALTER TABLE persona ADD CONSTRAINT fk_persona_agent FOREIGN KEY (default_agent_id) REFERENCES ai_agent;
CREATE TABLE ai_quick_prompt (
  agent_id       varchar(24) NOT NULL REFERENCES ai_agent,
  sort_order     smallint NOT NULL,
  prompt_text    text NOT NULL,
  PRIMARY KEY (agent_id, sort_order)
);
CREATE TABLE ai_intent (                            -- deterministic intent router (same as HTML)
  intent_code    varchar(16) PRIMARY KEY,          -- mandate risk liquidity address iso recon owner reject lineage action summary
  priority       smallint NOT NULL UNIQUE,         -- evaluation order
  pattern        text NOT NULL,                    -- POSIX regex, case-insensitive
  routed_agent_id varchar(24) NOT NULL REFERENCES ai_agent,
  evidence_dims  text[] NOT NULL,                  -- evidence chips cited in the answer
  description    text
);
CREATE TABLE ai_evidence_dimension (
  dimension_code varchar(12) PRIMARY KEY,          -- IDENTITY ISO LINEAGE STATE RISK LIQUIDITY OWNER
  sort_order     smallint NOT NULL,
  description    text NOT NULL
);
CREATE TABLE ai_action_rule (                       -- proposed-action catalogue (templated)
  rule_id        serial PRIMARY KEY,
  applies_status varchar(24) NOT NULL REFERENCES ref_payment_status,
  condition_sql  text,                             -- optional predicate over v_payment_current (documented, evaluated in fn_kuber_actions)
  variant        varchar(16) NOT NULL DEFAULT 'DEFAULT',  -- e.g. SCREENING vs OTHER for INVESTIGATION
  sort_order     smallint NOT NULL,
  action_code    varchar(24) NOT NULL,
  title_template text NOT NULL,                    -- tokens: {next_hop} {nostro} {creditor_agent} …
  detail_template text NOT NULL,
  owning_agent_id varchar(24) NOT NULL REFERENCES ai_agent,
  UNIQUE (applies_status, variant, action_code)
);
CREATE TABLE ai_conversation (
  conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        varchar(40) NOT NULL REFERENCES app_user,
  mode_code      varchar(8) REFERENCES persona,
  started_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ai_message (
  message_id     bigserial PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES ai_conversation ON DELETE CASCADE,
  role           varchar(8) NOT NULL CHECK (role IN ('user','agent','system','tool')),
  agent_id       varchar(24) REFERENCES ai_agent,
  routed_from_agent_id varchar(24) REFERENCES ai_agent,     -- 'Orchestrator → routed to X'
  payment_id     varchar(64) REFERENCES payment,
  intent_code    varchar(16) REFERENCES ai_intent,
  content        text NOT NULL,
  confidence     numeric(4,2),
  evidence_dims  text[],
  model_name     text,                             -- e.g. gemini-3.1-pro (for audit)
  boundary_text  text NOT NULL DEFAULT 'No external action was executed. Human approval remains required.',
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_aimsg_conv ON ai_message(conversation_id, message_id);
CREATE TABLE ai_tool_call (                         -- full audit of every tool the LLM invoked
  tool_call_id   bigserial PRIMARY KEY,
  message_id     bigint REFERENCES ai_message ON DELETE CASCADE,
  agent_id       varchar(24) REFERENCES ai_agent,
  tool_name      text NOT NULL,
  arguments      jsonb NOT NULL,
  result_digest  text,                             -- hash / summary (not the full PII payload)
  row_count      integer,
  latency_ms     integer,
  succeeded      boolean NOT NULL,
  called_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ai_evidence_item (
  message_id     bigint NOT NULL REFERENCES ai_message ON DELETE CASCADE,
  dimension_code varchar(12) NOT NULL REFERENCES ai_evidence_dimension,
  value_text     text NOT NULL,
  is_present     boolean NOT NULL,
  PRIMARY KEY (message_id, dimension_code)
);
CREATE TABLE ai_recommendation (                    -- latest recommendation per payment is shown in grid / 360
  recommendation_id bigserial PRIMARY KEY,
  payment_id     varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  agent_id       varchar(24) NOT NULL REFERENCES ai_agent,
  recommendation text NOT NULL,
  confidence     numeric(4,2) CHECK (confidence BETWEEN 0 AND 1),
  source_event_id bigint REFERENCES payment_event,
  created_at     timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ix_airec_payment ON ai_recommendation(payment_id, created_at DESC);
CREATE TABLE ai_proposed_action (                   -- HUMAN-IN-THE-LOOP boundary lives here
  action_id      bigserial PRIMARY KEY,
  payment_id     varchar(64) NOT NULL REFERENCES payment ON DELETE CASCADE,
  action_code    varchar(24) NOT NULL,
  title          text NOT NULL,
  detail         text NOT NULL,
  proposed_by_agent_id varchar(24) NOT NULL REFERENCES ai_agent,
  confidence     numeric(4,2),
  status         varchar(12) NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED','APPROVED','DECLINED','EXPIRED')),
  decided_by     varchar(40) REFERENCES app_user,
  decided_at     timestamptz,
  external_executed boolean NOT NULL DEFAULT false CHECK (external_executed = false),  -- simulator: never executes
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_decision_consistency CHECK ((status IN ('APPROVED','DECLINED')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  UNIQUE (payment_id, action_code, created_at)
);

/* ---------- RAG knowledge (ISO guidelines, runbooks, scheme rules) ---------- */
CREATE TABLE knowledge_document (
  doc_id         bigserial PRIMARY KEY,
  title          text NOT NULL,
  source_uri     text,
  doc_type       varchar(24) NOT NULL,             -- ISO_GUIDELINE RUNBOOK SCHEME_RULE REGULATION
  effective_date date
);
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public';
    EXECUTE 'CREATE TABLE kuber.knowledge_chunk (
               chunk_id  bigserial PRIMARY KEY,
               doc_id    bigint NOT NULL REFERENCES kuber.knowledge_document ON DELETE CASCADE,
               chunk_no  integer NOT NULL,
               content   text NOT NULL,
               embedding vector(768),          -- e.g. Gemini embedding truncated to 768 dims
               UNIQUE (doc_id, chunk_no))';
    EXECUTE 'CREATE INDEX ix_chunk_embedding ON kuber.knowledge_chunk USING hnsw (embedding vector_cosine_ops)';
  ELSE
    EXECUTE 'CREATE TABLE kuber.knowledge_chunk (
               chunk_id  bigserial PRIMARY KEY,
               doc_id    bigint NOT NULL REFERENCES kuber.knowledge_document ON DELETE CASCADE,
               chunk_no  integer NOT NULL,
               content   text NOT NULL,
               embedding real[],
               UNIQUE (doc_id, chunk_no))';
  END IF;
END $$;

/* =====================================================================
   I. INNOVATION LAB: RAIL ROUTER, REGULATORY HORIZON, MONTE CARLO
   ===================================================================== */
CREATE TABLE rail_route_rule (
  option_code    varchar(24) PRIMARY KEY,          -- 'SWIFT CBPR+','RTP','FedNow','Fedwire','Same Day ACH','ACH (next day)'
  rail_code      varchar(24) REFERENCES ref_rail,
  destination    varchar(4) NOT NULL CHECK (destination IN ('DOM','XB')),
  max_amount_usd numeric(20,2),                    -- NULL = no cap
  business_hours_only boolean NOT NULL,
  allowed_urgency text[] NOT NULL,                 -- INSTANT SAMEDAY NEXT
  speed_score    smallint NOT NULL CHECK (speed_score BETWEEN 1 AND 5),
  cost_score     smallint NOT NULL CHECK (cost_score BETWEEN 1 AND 5),
  eligible_note  text NOT NULL,
  over_limit_note text,
  sort_order     smallint NOT NULL
);
CREATE TABLE regulatory_item (
  reg_id         serial PRIMARY KEY,
  effective_label text NOT NULL,                   -- '14 Jul 2025', 'Nov 2026'
  effective_date date NOT NULL,                    -- approximate date used for countdowns when only the month is published
  title          text NOT NULL,
  applies_domain varchar(8) REFERENCES ref_domain,
  impact_check   varchar(24),                      -- e.g. ADDRESS_FORMAT → drives at-risk counts
  source_uri     text
);
CREATE TABLE mc_run (
  mc_run_id      bigserial PRIMARY KEY,
  runs           integer NOT NULL CHECK (runs IN (500,1000,5000) OR runs > 0),
  sla_stress     smallint NOT NULL CHECK (sla_stress IN (1,2,4)),
  seed           bigint NOT NULL,
  weights        jsonb NOT NULL,                   -- {"HAPPY":0.86,"SCREEN":0.05,"AC03":0.05,"TIMEOUT":0.04}
  injection_rate numeric(6,4) NOT NULL DEFAULT 0.005,
  stp_pct        numeric(6,2), mean_latency_ms bigint, p95_latency_ms bigint,
  iso_messages   integer, exceptions integer, investigations integer, kuber_cases integer,
  executed_by    varchar(40) REFERENCES app_user,
  executed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mc_outcome (
  mc_run_id      bigint NOT NULL REFERENCES mc_run ON DELETE CASCADE,
  outcome        varchar(24) NOT NULL,             -- COMPLETED RETURNED FAILED 'FAILURE INJECTED'
  run_count      integer NOT NULL,
  pct            numeric(6,2) NOT NULL,
  PRIMARY KEY (mc_run_id, outcome)
);

/* =====================================================================
   J. GOVERNANCE & AUDIT
   ===================================================================== */
CREATE TABLE audit_log (
  audit_id       bigserial PRIMARY KEY,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  actor_id       varchar(40),
  action         text NOT NULL,                    -- SIM_START SIM_INJECT ACTION_APPROVE EXPORT_CSV …
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  details        jsonb
);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);

/* =====================================================================
   K. LOGIC LAYER: projection, state-machine guards, simulation engine,
      agent semantic views and functions.
   Everything below mirrors the HTML reference implementation, so an agent
   calling these functions reproduces the same numbers and labels.
   ===================================================================== */
SET search_path = kuber, public;

/* ---------- helpers ---------- */
CREATE OR REPLACE FUNCTION fn_fnv1a(s text) RETURNS bigint LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE h bigint := 2166136261; i int;
BEGIN
  FOR i IN 1..length(s) LOOP
    h := h # ascii(substr(s, i, 1));
    h := (h * 16777619) % 4294967296;
  END LOOP;
  RETURN h;
END $$;

CREATE OR REPLACE FUNCTION fn_fmt_ms(ms bigint) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN ms IS NULL THEN NULL
    WHEN ms < 1000 THEN round(ms)::text || ' ms'
    WHEN ms < 60000 THEN to_char(ms/1000.0, 'FM999990.0') || 's'
    WHEN ms < 3600000 THEN floor(ms/60000)::text || 'm ' || round((ms % 60000)/1000.0)::text || 's'
    ELSE to_char(ms/3600000.0, 'FM999990.0') || 'h' END
$$;

CREATE OR REPLACE FUNCTION fn_fmt_amt(amount numeric, ccy text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(ccy || ' ', '') || to_char(amount, 'FM999,999,999,999,990.00')
$$;

CREATE OR REPLACE FUNCTION fn_fmt_short(n numeric) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN n >= 1e12 THEN to_char(n/1e12,'FM9990.00')||'T' WHEN n >= 1e9 THEN to_char(n/1e9,'FM9990.0')||'B'
              WHEN n >= 1e6 THEN to_char(n/1e6,'FM9990.00')||'M' WHEN n >= 1e3 THEN to_char(n/1e3,'FM9990.0')||'K' ELSE round(n)::text END
$$;

CREATE OR REPLACE FUNCTION fn_gpi_status(p_domain text, p_status text, p_reason_code text, p_state text, p_has_hop boolean)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN p_domain <> 'CBCC' THEN '—'
    WHEN p_status = 'COMPLETED' THEN 'ACCC'
    WHEN p_status IN ('REJECTED','FAILED') THEN 'RJCT/' || coalesce(p_reason_code,'NARR')
    WHEN p_status = 'RETURNED' THEN 'RTND/' || coalesce(p_reason_code,'NARR')
    WHEN p_status = 'INVESTIGATION' THEN 'ACSP/G003'
    WHEN p_state = 'SETTLEMENT_PENDING' THEN 'ACSP/G002'
    WHEN p_has_hop THEN 'ACSP/G000' ELSE 'ACSP/G001' END
$$;

/* =====================================================================
   K1. PROJECTION (event stream → payment current state)
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_project_payment(p_id varchar) RETURNS void LANGUAGE plpgsql AS $$
DECLARE p payment%ROWTYPE; e payment_event%ROWTYPE; st ref_payment_state%ROWTYPE; is_sim boolean;
        v_scr text; v_inv varchar; v_lat bigint;
BEGIN
  SELECT * INTO p FROM payment WHERE payment_id = p_id FOR UPDATE;
  is_sim := p.active_sim_run_id IS NOT NULL;
  SELECT * INTO e FROM payment_event WHERE payment_id = p_id AND sim_run_id IS NOT DISTINCT FROM p.active_sim_run_id ORDER BY seq DESC LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT * INTO st FROM ref_payment_state WHERE state = e.state;

  -- screening (simulation derives it from events; baseline keeps the record value)
  v_scr := p.record_screening_state;
  IF is_sim THEN
    SELECT CASE WHEN x.state='SCREENING' AND x.description LIKE 'Potential%' THEN 'POTENTIAL MATCH'
                WHEN x.state='SCREENING' AND x.description LIKE 'Human%' THEN 'CLEARED (human review)'
                WHEN x.state='SCREENING' THEN 'CLEARED'
                ELSE 'HOLD — POTENTIAL MATCH' END
      INTO v_scr
      FROM payment_event x
     WHERE x.payment_id = p_id AND x.sim_run_id = p.active_sim_run_id
       AND (x.state = 'SCREENING' OR (x.state = 'INVESTIGATION' AND x.description LIKE '%analyst%'))
     ORDER BY x.seq DESC LIMIT 1;
    v_scr := coalesce(v_scr, p.record_screening_state);
    SELECT sum(processing_ms) INTO v_lat FROM payment_event WHERE payment_id = p_id AND sim_run_id = p.active_sim_run_id;
  END IF;
  SELECT investigation_id INTO v_inv FROM investigation WHERE payment_id = p_id AND sim_run_id IS NULL ORDER BY opened_at DESC LIMIT 1;
  IF is_sim AND v_inv IS NULL THEN
    SELECT investigation_id INTO v_inv FROM investigation WHERE payment_id = p_id AND sim_run_id = p.active_sim_run_id ORDER BY opened_at DESC LIMIT 1;
  END IF;

  PERFORM set_config('kuber.projecting', 'on', true);
  UPDATE payment SET
    current_state       = e.state,
    status              = st.payment_status,
    ultimate_status     = CASE WHEN e.state IN ('COMPLETED','REJECTED','RETURNED','FAILED','CANCELLED') THEN st.payment_status ELSE 'IN_PROGRESS' END,
    intermediate_status = CASE WHEN is_sim THEN (CASE WHEN e.state = 'COMPLETED' THEN 'SETTLED' ELSE e.state END)
                               ELSE coalesce(record_intermediate_status, e.state) END,
    status_reason_code  = CASE WHEN NOT is_sim THEN record_status_reason_code
                               WHEN e.state IN ('REJECTED','RETURNED','FAILED','INVESTIGATION') THEN e.reason_code END,
    status_reason_text  = CASE WHEN NOT is_sim THEN record_status_reason_text
                               WHEN e.state IN ('REJECTED','RETURNED','FAILED','INVESTIGATION') THEN e.reason_text END,
    screening_state     = v_scr,
    recon_state         = CASE WHEN NOT is_sim THEN record_recon_state
                               WHEN e.state = 'COMPLETED' THEN 'MATCHED'
                               WHEN e.state IN ('REJECTED','RETURNED','FAILED') THEN 'EXCEPTION' ELSE 'OPEN' END,
    current_message_type = CASE WHEN NOT is_sim THEN message_type WHEN e.state = 'RETURNED' THEN 'pacs.004'
                                WHEN message_type = 'pacs.004' THEN 'pacs.008' ELSE message_type END,
    completed_at        = CASE WHEN st.payment_status = 'COMPLETED' THEN (CASE WHEN is_sim THEN e.event_ts ELSE coalesce(record_completed_at, e.event_ts) END) END,
    processing_ms       = CASE WHEN is_sim THEN v_lat ELSE record_processing_ms END,
    current_investigation_id = v_inv,
    updated_at          = now()
  WHERE payment_id = p_id;
  PERFORM set_config('kuber.projecting', 'off', true);
END $$;

/* ---------- guard: state columns may only be written by the projection ---------- */
CREATE OR REPLACE FUNCTION trg_payment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.status := 'IN_PROGRESS'; NEW.ultimate_status := 'IN_PROGRESS'; NEW.current_state := NULL;
    NEW.completed_at := NULL; NEW.active_sim_run_id := NULL;
    NEW.intermediate_status := coalesce(NEW.record_intermediate_status, 'INITIATED');
    NEW.current_message_type := NEW.message_type;
    NEW.screening_state := NEW.record_screening_state; NEW.recon_state := NEW.record_recon_state;
    RETURN NEW;
  END IF;
  IF (NEW.status IS DISTINCT FROM OLD.status OR NEW.ultimate_status IS DISTINCT FROM OLD.ultimate_status
      OR NEW.current_state IS DISTINCT FROM OLD.current_state OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
      OR NEW.intermediate_status IS DISTINCT FROM OLD.intermediate_status)
     AND coalesce(current_setting('kuber.projecting', true), 'off') <> 'on' THEN
    RAISE EXCEPTION 'Payment % state is a projection of payment_event. Insert an event (or run the simulator) instead of updating status directly.', OLD.payment_id
      USING ERRCODE = 'check_violation';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER payment_guard BEFORE INSERT OR UPDATE ON payment FOR EACH ROW EXECUTE FUNCTION trg_payment_guard();

/* ---------- event stream: validation, append-only, projection, run counters ---------- */
CREATE OR REPLACE FUNCTION trg_event_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_prev varchar; v_seq int; v_mode text;
BEGIN
  IF NEW.sim_run_id IS NOT NULL THEN
    SELECT mode INTO v_mode FROM sim_run WHERE run_id = NEW.sim_run_id;
    IF v_mode IS DISTINCT FROM 'RUNNING' THEN
      RAISE EXCEPTION 'START SIMULATION FIRST (run % is %)', NEW.sim_run_id, coalesce(v_mode,'missing');
    END IF;
    NEW.source := 'SIMULATION';
  END IF;
  SELECT state, seq INTO v_prev, v_seq FROM payment_event
   WHERE payment_id = NEW.payment_id AND sim_run_id IS NOT DISTINCT FROM NEW.sim_run_id ORDER BY seq DESC LIMIT 1;
  NEW.prev_state := v_prev;
  NEW.seq := coalesce(v_seq, 0) + 1;
  IF NOT EXISTS (SELECT 1 FROM ref_state_transition WHERE from_state IS NOT DISTINCT FROM v_prev AND to_state = NEW.state) THEN
    RAISE EXCEPTION 'Illegal state transition % → % for payment %', coalesce(v_prev,'∅'), NEW.state, NEW.payment_id USING ERRCODE = 'check_violation';
  END IF;
  NEW.iso_msg_type  := substring(NEW.iso_message from '^((?:pain|pacs|camt)\.\d{3})');
  NEW.iso_tx_status := substring(NEW.iso_message from '\s(ACSP|ACCC|RJCT|PDNG|RCVD|RTND|CANC)$');
  IF NEW.state = 'COMPLETED' AND NEW.iso_tx_status IS DISTINCT FROM 'ACCC' THEN
    RAISE EXCEPTION 'COMPLETED requires a pacs.002 ACCC confirmation (payment %)', NEW.payment_id USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER event_before_insert BEFORE INSERT ON payment_event FOR EACH ROW EXECUTE FUNCTION trg_event_before_insert();

CREATE OR REPLACE FUNCTION trg_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND coalesce(current_setting('kuber.allow_purge', true), 'off') = 'on' THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'payment_event is append-only' USING ERRCODE = 'check_violation';
END $$;
CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON payment_event FOR EACH ROW EXECUTE FUNCTION trg_event_append_only();

CREATE OR REPLACE FUNCTION trg_event_after_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_active varchar;
BEGIN
  SELECT active_sim_run_id INTO v_active FROM payment WHERE payment_id = NEW.payment_id;
  IF NEW.sim_run_id IS NOT DISTINCT FROM v_active THEN PERFORM fn_project_payment(NEW.payment_id); END IF;
  IF NEW.sim_run_id IS NOT NULL THEN
    UPDATE sim_run SET event_count = event_count + 1,
                       iso_message_count = iso_message_count + (NEW.iso_msg_type IS NOT NULL)::int,
                       exception_count = exception_count + (SELECT is_exception::int FROM ref_payment_state WHERE state = NEW.state),
                       simulated_latency_ms = simulated_latency_ms + NEW.processing_ms,
                       wall_elapsed_ms = greatest(wall_elapsed_ms, coalesce(NEW.wall_elapsed_ms, 0))
     WHERE run_id = NEW.sim_run_id;
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER event_after_insert AFTER INSERT ON payment_event FOR EACH ROW EXECUTE FUNCTION trg_event_after_insert();

/* =====================================================================
   K2. AGENT SEMANTIC VIEW: one row per payment, business names
   (field_catalogue.source_column points to these columns)
   ===================================================================== */
CREATE OR REPLACE VIEW v_payment_current AS
WITH hop AS (
  SELECT h.payment_id,
         max(b.bank_name) FILTER (WHERE agent_role='DEBTOR_AGENT')   AS debtor_agent,
         max(b.bank_name) FILTER (WHERE agent_role='CORRESPONDENT')  AS correspondent,
         max(b.bank_name) FILTER (WHERE agent_role='INTERMEDIARY')   AS intermediary,
         max(b.bank_name) FILTER (WHERE agent_role='CREDITOR_AGENT') AS creditor_agent,
         max(h.bic)       FILTER (WHERE agent_role='DEBTOR_AGENT')   AS debtor_agent_bic,
         max(h.bic)       FILTER (WHERE agent_role='CORRESPONDENT')  AS correspondent_bic,
         max(h.bic)       FILTER (WHERE agent_role='INTERMEDIARY')   AS intermediary_bic,
         max(h.bic)       FILTER (WHERE agent_role='CREDITOR_AGENT') AS creditor_agent_bic,
         count(*)                                                    AS hops
  FROM payment_agent_hop h JOIN bank b USING (bic) GROUP BY h.payment_id),
pty AS (
  SELECT payment_id,
         max(party_name)   FILTER (WHERE party_role='DEBTOR')            AS debtor,
         max(party_name)   FILTER (WHERE party_role='ULTIMATE_DEBTOR')   AS ultimate_debtor,
         max(party_name)   FILTER (WHERE party_role='CREDITOR')          AS creditor,
         max(party_name)   FILTER (WHERE party_role='ULTIMATE_CREDITOR') AS ultimate_creditor,
         max(account_id)   FILTER (WHERE party_role='DEBTOR')            AS debtor_account,
         max(account_id)   FILTER (WHERE party_role='CREDITOR')          AS creditor_account,
         max(country_code) FILTER (WHERE party_role='DEBTOR')            AS debtor_country,
         max(country_code) FILTER (WHERE party_role='CREDITOR')          AS creditor_country,
         CASE WHEN bool_or(address_format='UNSTRUCTURED') THEN 'UNSTRUCTURED'
              WHEN bool_or(address_format='HYBRID') THEN 'HYBRID'
              WHEN bool_or(address_format='STRUCTURED') THEN 'STRUCTURED' ELSE '—' END AS address_format
  FROM payment_party GROUP BY payment_id),
rec AS (
  SELECT DISTINCT ON (r.payment_id) r.payment_id, r.recommendation, a.agent_name, r.confidence
  FROM ai_recommendation r JOIN ai_agent a USING (agent_id)
  JOIN payment p ON p.payment_id = r.payment_id
  LEFT JOIN payment_event e ON e.event_id = r.source_event_id
  WHERE r.source_event_id IS NULL OR e.sim_run_id IS NOT DISTINCT FROM p.active_sim_run_id
  ORDER BY r.payment_id, (r.source_event_id IS NOT NULL AND p.active_sim_run_id IS NOT NULL) DESC, r.created_at DESC, r.recommendation_id DESC)
SELECT
  p.payment_id, p.uetr::text AS uetr, p.swift_txn_id, p.business_msg_id, p.instruction_id, p.end_to_end_id,
  p.domain_code AS domain, p.rail_code AS rail, p.payment_type, p.scheme_code AS scheme, p.purpose_code AS purpose,
  p.current_message_type AS message_type, p.message_type AS original_message_type,
  CASE WHEN p.domain_code = 'CBCC' THEN coalesce((SELECT CASE p.current_message_type WHEN 'pacs.008' THEN 'MT103' WHEN 'pacs.009' THEN 'MT202' WHEN 'pacs.004' THEN 'MT103 RETN' WHEN 'pain.001' THEN 'MT101' END),'—') ELSE '—' END AS mt_equivalent,
  coalesce(p.iso_version, (SELECT default_version FROM ref_iso_message_type m WHERE m.msg_type = p.current_message_type)) AS iso_version,
  pty.address_format,
  pty.debtor, coalesce(pty.ultimate_debtor, '') AS ultimate_debtor, pty.debtor_account, pty.debtor_country,
  pty.creditor, coalesce(pty.ultimate_creditor, '') AS ultimate_creditor, pty.creditor_account, pty.creditor_country,
  hop.debtor_agent, coalesce(hop.correspondent,'') AS correspondent, coalesce(hop.intermediary,'') AS intermediary, hop.creditor_agent,
  hop.debtor_agent_bic AS bic, hop.correspondent_bic, hop.intermediary_bic, hop.creditor_agent_bic,
  hop.hops::int AS hops,
  p.amount, p.debit_ccy, p.credit_ccy, round(p.amount * p.fx_rate, 2) AS credit_amount, p.fx_rate,
  p.settlement_date, p.initiated_at, p.completed_at,
  CASE WHEN p.active_sim_run_id IS NOT NULL THEN fn_fmt_ms(p.processing_ms) ELSE coalesce(p.record_duration_text, fn_fmt_ms(p.processing_ms)) END AS duration,
  p.processing_ms,
  p.status, p.ultimate_status, p.intermediate_status,
  CASE WHEN p.active_sim_run_id IS NOT NULL THEN p.current_state END AS sim_state,
  p.current_state,
  fn_gpi_status(p.domain_code, p.status, p.status_reason_code, coalesce(CASE WHEN p.active_sim_run_id IS NOT NULL THEN p.current_state END, p.intermediate_status),
                (hop.correspondent IS NOT NULL OR hop.intermediary IS NOT NULL)) AS gpi_status,
  coalesce(p.status_reason_text, '—') AS status_reason, p.status_reason_code,
  CASE WHEN p.status IN ('REJECTED','RETURNED') THEN (CASE WHEN p.status_reason_code = 'AC03' THEN 'BENEFICIARY_ACCOUNT' ELSE 'REJECTION' END)
       WHEN p.status = 'FAILED' THEN 'NETWORK_SLA'
       WHEN p.status = 'INVESTIGATION' THEN (CASE WHEN p.screening_state LIKE '%MATCH%' OR coalesce(p.status_reason_text,'') LIKE '%screen%' THEN 'SCREENING_HOLD' ELSE 'CORRESPONDENT_SLA' END)
       ELSE '—' END AS exception_type,
  p.settlement_method, p.clearing_system, p.priority,
  p.screening_state AS screening, p.risk_score,
  c.segment, p.initiation_department AS payment_initiation_dept,
  p.charge_bearer AS charges, p.fee_amount_usd AS fee_amount,
  sa.display_name AS nostro, p.liquidity_state,
  p.mandate_id AS mandate_ref, to_char(m.due_time, 'HH24:MI') AS obligation_due,
  coalesce(p.current_investigation_id, '') AS investigation_id,
  p.recon_state, u.display_name AS owner,
  CASE WHEN p.status = 'COMPLETED' THEN 'MET'
       WHEN p.status = 'IN_PROGRESS' THEN (CASE WHEN p.intermediate_status = 'WAITING_CORRESPONDENT' THEN 'AT_RISK' ELSE 'ON_TRACK' END)
       WHEN p.status = 'INVESTIGATION' THEN 'AT_RISK' ELSE 'BREACHED' END AS sla_state,
  coalesce(rec.recommendation, 'No action required') AS ai_recommendation, coalesce(rec.agent_name, 'Orchestrator') AS ai_agent, rec.confidence AS ai_confidence,
  p.corridor AS country,
  CASE p.status WHEN 'COMPLETED' THEN 'Completed' WHEN 'REJECTED' THEN 'Rejected' WHEN 'RETURNED' THEN 'Returned'
       WHEN 'INVESTIGATION' THEN 'Investigation' WHEN 'FAILED' THEN 'Failed' WHEN 'CANCELLED' THEN 'Cancelled'
       ELSE 'Current State · ' || coalesce(CASE WHEN p.active_sim_run_id IS NOT NULL THEN p.current_state END, p.intermediate_status) END AS state_label,
  CASE p.status WHEN 'COMPLETED' THEN 'ACCC · Settled'
       WHEN 'REJECTED' THEN 'RJCT · ' || coalesce(p.status_reason_code, 'NARR')
       WHEN 'RETURNED' THEN 'RTND · pacs.004' || coalesce(' · ' || p.status_reason_code, '')
       WHEN 'FAILED' THEN 'RJCT · ' || coalesce(p.status_reason_code, 'NACK_TIMEOUT')
       WHEN 'INVESTIGATION' THEN 'PDNG · ' || coalesce(p.current_investigation_id, 'case open')
       WHEN 'CANCELLED' THEN 'CANC · camt.029'
       ELSE 'ACSP · ' || coalesce(CASE WHEN p.active_sim_run_id IS NOT NULL THEN p.current_state END, p.intermediate_status) END AS status_indicator,
  ps.badge_color, p.active_sim_run_id, p.customer_id
FROM payment p
LEFT JOIN hop USING (payment_id)
LEFT JOIN pty USING (payment_id)
LEFT JOIN rec USING (payment_id)
LEFT JOIN customer c ON c.customer_id = p.customer_id
LEFT JOIN settlement_account sa ON sa.account_id = p.nostro_account_id
LEFT JOIN mandate_obligation m ON m.mandate_id = p.mandate_id
LEFT JOIN app_user u ON u.user_id = p.owner_user_id
JOIN ref_payment_status ps ON ps.status = p.status;

COMMENT ON VIEW v_payment_current IS 'Kuber semantic layer: one row per payment with business-named columns; the authoritative state is projected from payment_event. Use this, never raw tables, for agent answers.';

/* events of the ACTIVE stream (baseline or active simulation run) */
CREATE OR REPLACE VIEW v_payment_timeline AS
SELECT e.payment_id, e.seq, e.state, e.prev_state, coalesce(e.prev_state,'—') || ' → ' || e.state AS transition,
       e.description, e.iso_message, e.iso_msg_type, e.iso_tx_status, e.actor,
       coalesce(e.reason_text, '—') AS reason, e.event_ts, e.processing_ms, fn_fmt_ms(e.processing_ms) AS processing_time,
       e.wall_elapsed_ms, to_char(coalesce(e.wall_elapsed_ms,0)/1000.0,'FM99990.0') || 's' AS elapsed_display,
       e.failure_injected, e.sim_run_id, e.source, e.iso_payload, e.event_id
FROM payment_event e JOIN payment p ON p.payment_id = e.payment_id
WHERE e.sim_run_id IS NOT DISTINCT FROM p.active_sim_run_id;

/* =====================================================================
   K3. LINEAGE & JOURNEY (state-aware packet animation logic)
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_payment_journey(p_id varchar)
RETURNS TABLE (kind text, target numeric, back_idx int, journey_state text, node_count int, i_da int, i_ca int, where_text text)
LANGUAGE plpgsql STABLE AS $$
DECLARE v record; n int; iCorr int; iInt int; iCA int; iEnd int; lastProg numeric := 0; q numeric; e record; cur text; names text[];
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  IF NOT FOUND THEN RETURN; END IF;
  names := ARRAY[v.debtor, v.debtor_agent] || CASE WHEN v.correspondent <> '' THEN ARRAY[v.correspondent] ELSE '{}' END
           || CASE WHEN v.intermediary <> '' THEN ARRAY[v.intermediary] ELSE '{}' END
           || ARRAY[v.creditor_agent, coalesce(nullif(v.ultimate_creditor,''), v.creditor)];
  n := array_length(names,1); iCA := n - 2; iEnd := n - 1;
  iCorr := CASE WHEN v.correspondent <> '' THEN 2 END;
  iInt  := CASE WHEN v.intermediary <> '' THEN (CASE WHEN v.correspondent <> '' THEN 3 ELSE 2 END) END;
  FOR e IN SELECT t.state FROM v_payment_timeline t WHERE t.payment_id = p_id ORDER BY t.seq LOOP
    q := CASE e.state WHEN 'INITIATED' THEN 0 WHEN 'ACCEPTED' THEN 1 WHEN 'SCREENING' THEN 1 WHEN 'SENT' THEN 1.5
           WHEN 'IN_TRANSIT' THEN coalesce(iCorr, iInt, iCA - 0.5) WHEN 'CORRESPONDENT_PROCESSING' THEN coalesce(iInt, iCorr, iCA - 0.5)
           WHEN 'SETTLEMENT_PENDING' THEN iCA END;
    IF q IS NOT NULL THEN lastProg := q; END IF;
    cur := e.state;
  END LOOP;
  i_da := 1; i_ca := iCA; node_count := n; back_idx := NULL;
  IF v.status = 'COMPLETED' THEN kind := 'ok'; target := iEnd; journey_state := 'COMPLETED';
  ELSIF v.status = 'REJECTED' THEN kind := 'err'; target := CASE WHEN lastProg >= 1.5 THEN iCA ELSE lastProg END; journey_state := 'REJECTED';
  ELSIF v.status = 'RETURNED' THEN kind := 'ret'; target := iCA; back_idx := 1; journey_state := 'RETURNED';
  ELSIF v.status = 'FAILED' THEN kind := 'err'; target := lastProg; journey_state := 'FAILED';
  ELSIF v.status = 'INVESTIGATION' THEN kind := 'warn'; target := lastProg; journey_state := 'INVESTIGATION';
  ELSIF v.status = 'CANCELLED' THEN kind := 'canc'; target := lastProg; journey_state := 'CANCELLED';
  ELSE
    cur := coalesce(v.sim_state, cur);
    q := CASE cur WHEN 'INITIATED' THEN 0 WHEN 'ACCEPTED' THEN 1 WHEN 'SCREENING' THEN 1 WHEN 'SENT' THEN 1.5
           WHEN 'IN_TRANSIT' THEN coalesce(iCorr, iInt, iCA - 0.5) WHEN 'CORRESPONDENT_PROCESSING' THEN coalesce(iInt, iCorr, iCA - 0.5)
           WHEN 'SETTLEMENT_PENDING' THEN iCA WHEN 'COMPLETED' THEN iEnd END;
    kind := 'prog'; target := coalesce(q, lastProg); journey_state := cur;
  END IF;
  where_text := CASE
    WHEN kind = 'ok' THEN 'Packet delivered to ' || names[n]
    WHEN kind = 'ret' THEN 'Rejected at ' || names[iCA+1] || '; return flowing back to ' || names[2]
    WHEN target % 1 <> 0 THEN 'In flight between ' || names[floor(target)::int+1] || ' and ' || names[ceil(target)::int+1]
    WHEN kind = 'prog' THEN 'Packet at ' || names[target::int+1]
    ELSE 'Packet stopped at ' || names[target::int+1] END;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION fn_payment_lineage(p_id varchar)
RETURNS TABLE (node_idx int, node_role text, node_name text, node_sub text, node_state text, status_tag text, msg_to_next text)
LANGUAGE plpgsql STABLE AS $$
DECLARE v record; j record; roles text[]; names text[]; subs text[]; n int; i int; ns text; tg text;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT * INTO j FROM fn_payment_journey(p_id);
  roles := ARRAY['Debtor','Debtor Agent']; names := ARRAY[v.debtor, v.debtor_agent];
  subs  := ARRAY[CASE WHEN v.ultimate_debtor <> '' AND v.ultimate_debtor <> v.debtor THEN 'UD · ' || v.ultimate_debtor ELSE coalesce(v.debtor_country,'') END, v.bic];
  IF v.correspondent <> '' THEN roles := roles || 'Correspondent'::text; names := names || v.correspondent; subs := subs || v.correspondent_bic; END IF;
  IF v.intermediary  <> '' THEN roles := roles || 'Intermediary'::text;  names := names || v.intermediary;  subs := subs || v.intermediary_bic;  END IF;
  roles := roles || 'Creditor Agent'::text || CASE WHEN v.ultimate_creditor <> '' THEN 'Ultimate Creditor' ELSE 'Creditor' END;
  names := names || v.creditor_agent || coalesce(nullif(v.ultimate_creditor,''), v.creditor);
  subs  := subs || v.creditor_agent_bic || CASE WHEN v.ultimate_creditor <> '' AND v.ultimate_creditor <> v.creditor THEN 'Cdtr · ' || v.creditor ELSE coalesce(v.creditor_country,'') END;
  n := array_length(names, 1);
  FOR i IN 0..n-1 LOOP
    IF j.kind = 'ok' THEN ns := 'done';
    ELSIF j.kind = 'ret' THEN ns := CASE WHEN i = j.i_ca THEN 'err' WHEN i >= j.i_da AND i < j.i_ca THEN 'ret' WHEN i < j.i_da THEN 'done' ELSE 'pend' END;
    ELSIF i < floor(j.target) OR (i = floor(j.target) AND j.target % 1 <> 0) THEN ns := 'done';
    ELSIF i = j.target THEN ns := CASE WHEN j.kind = 'prog' THEN 'cur' ELSE j.kind END;
    ELSE ns := 'pend'; END IF;
    tg := CASE WHEN j.kind = 'ret' AND i = j.i_ca THEN 'RJCT · ' || coalesce(v.status_reason_code, 'AC03')
               WHEN j.kind = 'ret' AND i = j.i_da THEN 'RTND · returned'
               WHEN i = j.target AND j.kind <> 'ok' THEN v.status_indicator
               WHEN i = n-1 THEN CASE WHEN j.kind = 'ok' THEN 'Credited ✓' ELSE 'Not credited' END END;
    node_idx := i; node_role := roles[i+1]; node_name := names[i+1]; node_sub := subs[i+1]; node_state := ns; status_tag := tg;
    msg_to_next := CASE WHEN i = n-1 THEN NULL
                        WHEN i = 0 THEN CASE WHEN v.rail = 'ACH' THEN 'pain.001 → ACH file' ELSE 'pain.001' END
                        WHEN i = n-2 THEN 'credit · camt.054'
                        WHEN v.rail = 'ACH' THEN 'ACH batch'
                        WHEN v.original_message_type = 'pacs.009' OR v.message_type = 'pacs.009' THEN 'pacs.009' ELSE 'pacs.008' END;
    RETURN NEXT;
  END LOOP;
END $$;

/* =====================================================================
   K4. KUBER: EVIDENCE, CONFIDENCE, ACTIONS, ROUTING, ANSWERS
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_kuber_evidence(p_id varchar)
RETURNS TABLE (dimension_code text, sort_order int, value_text text, is_present boolean) LANGUAGE sql STABLE AS $$
  WITH v AS (SELECT * FROM v_payment_current WHERE payment_id = p_id),
  chain AS (SELECT array_remove(ARRAY[debtor_agent, nullif(correspondent,''), nullif(intermediary,''), creditor_agent], NULL) AS c FROM v)
  SELECT * FROM (
    SELECT 'IDENTITY', 1, payment_id || CASE WHEN uetr IS NOT NULL THEN ' · UETR ' || uetr ELSE ' · domestic (no UETR)' END, (payment_id IS NOT NULL AND (uetr IS NOT NULL OR domain = 'DOME')) FROM v
    UNION ALL SELECT 'ISO', 2, message_type || ' · InstrId ' || instruction_id || ' · E2E ' || end_to_end_id, (message_type IS NOT NULL AND instruction_id IS NOT NULL AND end_to_end_id IS NOT NULL) FROM v
    UNION ALL SELECT 'LINEAGE', 3, array_to_string(c, ' → '), coalesce(array_length(c,1),0) >= 2 FROM chain
    UNION ALL SELECT 'STATE', 4, status || ' · ' || coalesce(sim_state, intermediate_status) || ' · ' || status_reason, status IS NOT NULL FROM v
    UNION ALL SELECT 'RISK', 5, 'Screening ' || screening || ' · score ' || coalesce(risk_score::text,'—'), screening IS NOT NULL FROM v
    UNION ALL SELECT 'LIQUIDITY', 6, liquidity_state || ' · ' || coalesce(nostro,'—'), (liquidity_state IS NOT NULL AND nostro IS NOT NULL) FROM v
    UNION ALL SELECT 'OWNER', 7, coalesce(owner,'—') || ' · ' || coalesce(payment_initiation_dept,'—'), (owner IS NOT NULL AND payment_initiation_dept IS NOT NULL) FROM v
  ) x(dimension_code, sort_order, value_text, is_present)
$$;

CREATE OR REPLACE FUNCTION fn_kuber_confidence(p_id varchar)
RETURNS TABLE (confidence numeric, completeness_pct int, present int, total int) LANGUAGE sql STABLE AS $$
  WITH ev AS (SELECT count(*) FILTER (WHERE is_present) AS n, count(*) AS t FROM fn_kuber_evidence(p_id)),
  v AS (SELECT status, screening FROM v_payment_current WHERE payment_id = p_id)
  SELECT round(least(0.99, greatest(0.5, 0.73 + 0.03*ev.n - CASE WHEN v.status='FAILED' THEN 0.06 ELSE 0 END - CASE WHEN v.screening LIKE '%MATCH%' THEN 0.05 ELSE 0 END)), 2),
         round(100.0*ev.n/ev.t)::int, ev.n::int, ev.t::int
  FROM ev, v
$$;

CREATE OR REPLACE FUNCTION fn_render_template(tpl text, p_id varchar) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v record; r text := tpl;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  r := replace(r, '{next_hop}', coalesce(nullif(v.correspondent,''), v.creditor_agent));
  r := replace(r, '{nostro}', coalesce(v.nostro,'—'));
  r := replace(r, '{creditor_agent}', v.creditor_agent);
  r := replace(r, '{creditor_account}', coalesce(v.creditor_account,'—'));
  r := replace(r, '{reason_code}', coalesce(v.status_reason_code,'AC03'));
  r := replace(r, '{amount_fmt}', fn_fmt_amt(v.amount, v.debit_ccy));
  r := replace(r, '{debtor}', v.debtor);
  r := replace(r, '{owner}', coalesce(v.owner,'—'));
  r := replace(r, '{investigation_id_or_investigation}', coalesce(nullif(v.investigation_id,''),'investigation'));
  r := replace(r, '{investigation_id_or_new}', coalesce(nullif(v.investigation_id,''),'new'));
  r := replace(r, '{uetr_or_na}', coalesce(v.uetr,'n/a'));
  r := replace(r, '{uetr_or_e2e}', coalesce(v.uetr, v.end_to_end_id));
  r := replace(r, '{current_state}', coalesce(v.sim_state, v.intermediate_status));
  r := replace(r, '{sla_state}', v.sla_state);
  r := replace(r, '{liquidity_state}', v.liquidity_state);
  RETURN r;
END $$;

CREATE OR REPLACE FUNCTION fn_kuber_actions(p_id varchar)
RETURNS TABLE (sort_order int, action_code text, title text, detail text, owning_agent text, decision text) LANGUAGE sql STABLE AS $$
  WITH v AS (SELECT * FROM v_payment_current WHERE payment_id = p_id),
  var AS (SELECT CASE
            WHEN v.status = 'INVESTIGATION' THEN CASE WHEN v.screening LIKE '%MATCH%' THEN 'SCREENING' ELSE 'OTHER' END
            WHEN v.status = 'IN_PROGRESS' THEN CASE WHEN coalesce(v.sim_state, v.intermediate_status) = 'WAITING_CORRESPONDENT' OR v.sla_state = 'AT_RISK' THEN 'AT_RISK' ELSE 'DEFAULT' END
            ELSE 'DEFAULT' END AS variant, v.status FROM v)
  SELECT r.sort_order, r.action_code, fn_render_template(r.title_template, p_id), fn_render_template(r.detail_template, p_id), a.agent_name,
         (SELECT pa.status FROM ai_proposed_action pa WHERE pa.payment_id = p_id AND pa.action_code = r.action_code ORDER BY pa.created_at DESC LIMIT 1)
  FROM ai_action_rule r JOIN var ON r.applies_status = var.status AND r.variant = var.variant JOIN ai_agent a ON a.agent_id = r.owning_agent_id
  ORDER BY r.sort_order
$$;

CREATE OR REPLACE FUNCTION fn_kuber_route(p_question text, p_active_agent varchar DEFAULT 'orchestrator')
RETURNS TABLE (intent_code text, answering_agent_id text, routed_from text) LANGUAGE sql STABLE AS $$
  WITH hit AS (
    SELECT i.intent_code, i.routed_agent_id FROM ai_intent i
    WHERE i.pattern = '.*' OR lower(p_question) ~ i.pattern ORDER BY i.priority LIMIT 1)
  SELECT hit.intent_code,
         CASE WHEN p_active_agent = 'orchestrator' THEN hit.routed_agent_id ELSE p_active_agent END,
         CASE WHEN p_active_agent = 'orchestrator' AND hit.routed_agent_id <> 'orchestrator' THEN 'orchestrator' END
  FROM hit
$$;

CREATE OR REPLACE FUNCTION fn_kuber_summary(p_id varchar) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v record; chain text; wh text; head text;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  chain := array_to_string(array_remove(ARRAY[v.debtor_agent, nullif(v.correspondent,''), nullif(v.intermediary,''), v.creditor_agent], NULL), ' → ');
  wh := CASE WHEN v.domain = 'CBCC' THEN 'through the correspondent chain' ELSE 'on ' || v.rail END;
  head := CASE v.status
    WHEN 'COMPLETED' THEN 'Payment completed end-to-end ' || wh || '; final status ACCC and the beneficiary was credited.'
    WHEN 'REJECTED' THEN 'Payment was rejected by ' || v.creditor_agent || ' with ' || v.status_reason || '. It is not complete and must not be reported as settled.'
    WHEN 'RETURNED' THEN 'Payment was rejected (' || v.status_reason || ') and returned via pacs.004. The beneficiary was not credited.'
    WHEN 'INVESTIGATION' THEN 'Payment is under investigation (' || coalesce(nullif(v.investigation_id,''),'case open') || '): ' || v.status_reason || '. It is not complete.'
    WHEN 'FAILED' THEN 'Payment failed: ' || v.status_reason || '. It remains unresolved and is not complete.'
    WHEN 'CANCELLED' THEN 'Payment was cancelled.'
    ELSE 'Payment is progressing ' || wh || '. Current intermediate status is ' || coalesce(v.sim_state, v.intermediate_status) || '.' END;
  RETURN head || ' Evidence includes the ' || CASE WHEN v.uetr IS NOT NULL THEN 'UETR' ELSE 'end-to-end ID' END || ', ' || v.message_type || ' instruction, '
      || CASE WHEN v.domain = 'CBCC' THEN 'correspondent ' ELSE '' END || 'lineage (' || chain || '), current '
      || CASE WHEN v.status = 'IN_PROGRESS' THEN 'intermediate ' ELSE '' END || 'status, screening result (' || v.screening
      || '), liquidity state (' || v.liquidity_state || ') and operations owner (' || coalesce(v.owner,'—') || ').';
END $$;

/* Deterministic "gold" answer for Kuber. The LLM may rephrase it but must not
   contradict it. Returns everything the console needs to render. */
CREATE OR REPLACE FUNCTION fn_kuber_answer(p_id varchar, p_question text, p_active_agent varchar DEFAULT 'orchestrator')
RETURNS json LANGUAGE plpgsql STABLE AS $$
DECLARE v record; r record; c record; body text; dims text[]; chain text; j record; code text; first_act record; at_txt text;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  SELECT * INTO r FROM fn_kuber_route(p_question, p_active_agent);
  SELECT * INTO c FROM fn_kuber_confidence(p_id);
  SELECT evidence_dims INTO dims FROM ai_intent WHERE intent_code = r.intent_code;
  code := v.status_reason_code;
  CASE r.intent_code
  WHEN 'risk' THEN
    body := 'Screening state is ' || v.screening || ' with synthetic risk score ' || coalesce(v.risk_score::text,'—') || '/100. ' ||
      CASE WHEN v.screening LIKE '%MATCH%' THEN 'A potential match holds the payment; release requires a documented four-eyes human decision.'
           WHEN v.screening LIKE '%RELEASED%' OR v.screening LIKE '%human%' THEN 'A prior alert was released by a human analyst; rationale should be retained for audit.'
           ELSE 'No sanctions or AML exposure detected on parties or agents.' END;
  WHEN 'liquidity' THEN
    body := 'Settlement account ' || coalesce(v.nostro,'—') || ' is ' || v.liquidity_state || '. ' ||
      CASE v.liquidity_state WHEN 'AVAILABLE' THEN 'Sufficient intraday headroom to cover ' || fn_fmt_amt(v.amount, v.debit_ccy) || '.'
           WHEN 'TIGHT' THEN 'Headroom is tight — consider sequencing lower-priority releases after this payment.' ELSE 'Shortfall — funding required before release.' END ||
      CASE WHEN v.status IN ('REJECTED','RETURNED') THEN ' Funds are expected back via the return path; do not double-fund.' ELSE '' END;
  WHEN 'mandate' THEN
    body := 'Mandate readiness is derived from linked payment ' || v.payment_id || ' (' || v.state_label || '), liquidity ' || v.liquidity_state ||
      ' and open cases (' || coalesce(nullif(v.investigation_id,''),'none') || '). ' ||
      CASE WHEN v.status IN ('COMPLETED','IN_PROGRESS') AND v.liquidity_state <> 'SHORTFALL' THEN 'Assessment: Ready.' ELSE 'Assessment: Needs review — the linked payment is not in a clean state.' END;
  WHEN 'address' THEN
    body := CASE WHEN v.domain <> 'CBCC' THEN 'Domestic payment — CBPR+ postal-address rules do not apply.'
      ELSE 'Postal address format is ' || v.address_format || '. ' ||
        CASE v.address_format WHEN 'UNSTRUCTURED' THEN 'Under CBPR+ SR2026 (Nov 2026) fully unstructured addresses will no longer be accepted — remediate to structured or hybrid (town + country structured).'
             WHEN 'HYBRID' THEN 'Hybrid is accepted after Nov 2026 provided town name and country are structured.' ELSE 'Structured — compliant with the Nov 2026 requirement.' END END;
  WHEN 'iso' THEN
    SELECT string_agg(m, ' → ' ORDER BY s) INTO chain FROM (SELECT iso_message m, min(seq) s FROM v_payment_timeline WHERE payment_id = p_id AND iso_message <> '—' GROUP BY iso_message) z;
    body := 'ISO chain observed: ' || coalesce(chain,'—') || '. Primary message ' || v.iso_version ||
      CASE WHEN v.mt_equivalent <> '—' THEN ' (semantically ≈ ' || v.mt_equivalent || ')' ELSE '' END || '. InstrId ' || v.instruction_id || ', E2E ' || v.end_to_end_id ||
      CASE WHEN v.uetr IS NOT NULL THEN ', UETR ' || v.uetr ELSE '' END || '. Mapping is semantic only; element-level XPaths are not asserted.';
  WHEN 'recon' THEN
    body := 'Reconciliation state is ' || v.recon_state || '. ' ||
      CASE WHEN v.status = 'COMPLETED' THEN 'Expect camt.054 credit/debit notification and camt.053 end-of-day entry to match on E2E ID ' || v.end_to_end_id || '.'
           ELSE 'No final booking exists; the nostro entry remains ' || CASE WHEN v.recon_state = 'EXCEPTION' THEN 'an exception until the return/repair is booked.' ELSE 'open.' END END;
  WHEN 'owner' THEN
    body := 'Operations owner is ' || coalesce(v.owner,'—') || ' in ' || coalesce(v.payment_initiation_dept,'—') || '. SLA state ' || v.sla_state || '; latest agent recommendation: “' || v.ai_recommendation || '”.';
  WHEN 'reject' THEN
    body := CASE WHEN v.status IN ('REJECTED','RETURNED') THEN 'Root cause: ' || v.status_reason || ' reported by ' || v.creditor_agent || '. ' ||
        CASE WHEN code = 'AC03' THEN 'The creditor account number is invalid. Validate the beneficiary account (e.g. via a pre-validation service) before re-submitting; a pacs.004 return path applies.'
             ELSE 'Repair data with the client before re-submitting.' END || ' Investigation ' || coalesce(nullif(v.investigation_id,''),'to be opened') || '.'
      ELSE 'This payment is not rejected or returned (current: ' || v.state_label || ').' END;
  WHEN 'lineage' THEN
    SELECT * INTO j FROM fn_payment_journey(p_id);
    SELECT string_agg(node_name, ' → ' ORDER BY node_idx) INTO chain FROM fn_payment_lineage(p_id);
    at_txt := CASE WHEN j.kind = 'ok' THEN 'delivered to ' || (SELECT node_name FROM fn_payment_lineage(p_id) ORDER BY node_idx DESC LIMIT 1)
                   WHEN j.kind = 'ret' THEN 'returned from ' || v.creditor_agent || ' to ' || v.debtor_agent
                   WHEN j.target % 1 <> 0 THEN 'currently in flight between ' || (SELECT node_name FROM fn_payment_lineage(p_id) WHERE node_idx = floor(j.target)) || ' and ' || (SELECT node_name FROM fn_payment_lineage(p_id) WHERE node_idx = ceil(j.target))
                   ELSE 'currently ' || (SELECT node_name FROM fn_payment_lineage(p_id) WHERE node_idx = j.target) END;
    body := 'Route: ' || chain || '. The payment is ' || at_txt || '. Tracker status ' || v.gpi_status || '.';
  WHEN 'action' THEN
    SELECT * INTO first_act FROM fn_kuber_actions(p_id) ORDER BY sort_order LIMIT 1;
    body := 'Recommended: ' || coalesce(first_act.title,'Continue monitoring') || '. ' || coalesce(first_act.detail,'') || '. All proposed actions sit behind the human approval boundary.';
  ELSE
    body := fn_kuber_summary(p_id);
  END CASE;
  RETURN json_build_object(
    'payment_id', p_id, 'intent', r.intent_code, 'agent', (SELECT agent_name FROM ai_agent WHERE agent_id = r.answering_agent_id),
    'routed_from', CASE WHEN r.routed_from IS NOT NULL THEN 'Orchestrator' END,
    'text', body, 'evidence', dims, 'confidence', c.confidence,
    'action', 'human approval required',
    'boundary', 'No external action was executed. Human approval remains required.');
END $$;

/* =====================================================================
   K5. PAYMENT 360 — one call returns everything the 360 panel renders
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_payment_360(p_id varchar) RETURNS json LANGUAGE plpgsql STABLE AS $$
DECLARE v record; j record; res json; ac03 boolean;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO j FROM fn_payment_journey(p_id);
  ac03 := v.status_reason_code = 'AC03';
  SELECT json_build_object(
    'identity', json_build_object('payment_id', v.payment_id, 'uetr', v.uetr, 'swift_txn_id', v.swift_txn_id, 'business_msg_id', v.business_msg_id,
        'instruction_id', v.instruction_id, 'end_to_end_id', v.end_to_end_id, 'message', v.iso_version, 'mt_equivalent', v.mt_equivalent,
        'rail', v.rail, 'domain', v.domain, 'payment_type', v.payment_type, 'amount', v.amount, 'amount_display', fn_fmt_amt(v.amount, v.debit_ccy),
        'credit_amount_display', CASE WHEN v.debit_ccy <> v.credit_ccy THEN fn_fmt_amt(v.credit_amount, v.credit_ccy) || ' @ ' || v.fx_rate END,
        'corridor', v.country || ' · ' || v.settlement_method, 'settlement_date', v.settlement_date, 'initiated_at', v.initiated_at,
        'completion', CASE WHEN v.status = 'COMPLETED' AND v.completed_at IS NOT NULL THEN json_build_object('label','Completed At','value', v.completed_at)
                           ELSE json_build_object('label','Final Credit','value','— none (' || v.state_label || ')') END,
        'duration', v.duration, 'charges', v.charges || ' · fees ' || to_char(v.fee_amount,'FM999990.00') || ' USD'),
    'state', json_build_object('status', v.status, 'ultimate_status', v.ultimate_status, 'intermediate', coalesce(v.sim_state, v.intermediate_status),
        'state_label', v.state_label, 'status_indicator', v.status_indicator, 'status_reason', v.status_reason, 'gpi_status', v.gpi_status,
        'journey_kind', j.kind, 'badge_color', v.badge_color, 'active_sim_run_id', v.active_sim_run_id),
    'journey', row_to_json(j),
    'lineage', (SELECT json_agg(l ORDER BY l.node_idx) FROM fn_payment_lineage(p_id) l),
    'events', (SELECT json_agg(json_build_object('seq', t.seq, 'state', t.state, 'transition', t.transition, 'description', t.description, 'iso_message', t.iso_message,
                  'actor', t.actor, 'reason', t.reason, 'timestamp', t.event_ts, 'processing', t.processing_time, 'elapsed', t.elapsed_display) ORDER BY t.seq)
               FROM v_payment_timeline t WHERE t.payment_id = p_id),
    'iso_chain', (SELECT json_agg(m ORDER BY s) FROM (
                    SELECT iso_message m, min(seq) s FROM v_payment_timeline WHERE payment_id = p_id AND iso_message <> '—' GROUP BY iso_message
                    UNION ALL SELECT 'camt.054', 1000 WHERE v.status = 'COMPLETED'
                    UNION ALL SELECT 'camt.053', 1001 WHERE v.status = 'COMPLETED'
                    UNION ALL SELECT 'camt.052', 1000 WHERE v.status NOT IN ('COMPLETED','IN_PROGRESS')) z),
    'parties', (SELECT json_agg(json_build_object('role', initcap(replace(pp.party_role,'_',' ')), 'name', pp.party_name, 'country', pp.country_code, 'account', pp.account_id, 'address_format', pp.address_format)
                ORDER BY array_position(ARRAY['ULTIMATE_DEBTOR','DEBTOR','CREDITOR','ULTIMATE_CREDITOR'], pp.party_role::text)) FROM payment_party pp WHERE pp.payment_id = p_id),
    'banks', (SELECT json_agg(json_build_object('role', initcap(replace(h.agent_role,'_',' ')), 'name', b.bank_name, 'bic', h.bic, 'country', b.country_code) ORDER BY h.hop_seq)
              FROM payment_agent_hop h JOIN bank b USING (bic) WHERE h.payment_id = p_id),
    'lifecycle', (SELECT json_agg(x ORDER BY x.ord) FROM fn_payment_lifecycle(p_id) x),
    'hop_tracker', (SELECT json_agg(json_build_object('role', l.node_role, 'name', l.node_name,
        'status', CASE WHEN j.kind = 'ok' THEN '✓ settled'
                       WHEN j.kind = 'ret' AND l.node_idx = j.i_ca THEN 'RJCT ' || coalesce(v.status_reason_code,'')
                       WHEN j.kind = 'ret' AND l.node_idx = j.i_da THEN 'RTND received'
                       WHEN l.node_idx < j.target THEN '✓ passed'
                       WHEN l.node_idx = j.target THEN CASE WHEN j.kind = 'prog' THEN '● ' || j.journey_state ELSE v.status_indicator END
                       WHEN l.node_idx - 0.5 = j.target THEN 'incoming' ELSE '—' END) ORDER BY l.node_idx)
        FROM fn_payment_lineage(p_id) l WHERE l.node_idx BETWEEN 1 AND j.node_count - 2),
    'operations', json_build_object('owner', v.owner, 'department', v.payment_initiation_dept, 'sla', v.sla_state, 'screening', v.screening, 'risk_score', v.risk_score,
        'liquidity', v.liquidity_state, 'nostro', v.nostro, 'investigation_id', nullif(v.investigation_id,''), 'reconciliation', v.recon_state, 'kuber', v.ai_recommendation),
    'readiness', json_build_array(
        json_build_object('check','CBPR+ SR2026 postal address','result', CASE WHEN v.domain <> 'CBCC' OR v.address_format = 'STRUCTURED' THEN 'PASS' WHEN v.address_format = 'HYBRID' THEN 'WARN' ELSE 'FAIL' END,
          'detail', CASE WHEN v.domain <> 'CBCC' THEN 'Not applicable (domestic)' WHEN v.address_format = 'STRUCTURED' THEN 'Structured address — compliant'
                         WHEN v.address_format = 'HYBRID' THEN 'Hybrid — accepted; town + country must be structured' ELSE 'Unstructured — will be rejected after Nov 2026 cut-over' END),
        json_build_object('check','Beneficiary account pre-validation','result', CASE WHEN ac03 THEN 'FAIL' WHEN v.domain = 'CBCC' THEN 'WARN' ELSE 'PASS' END,
          'detail', CASE WHEN ac03 THEN 'AC03 — account invalid; pre-validation (e.g. Swift Payment Pre-validation) would have caught this'
                         WHEN v.domain = 'CBCC' THEN 'Recommended before release for high-value cross-border' ELSE 'Domestic routing/account verified' END),
        json_build_object('check','Sanctions / AML screening','result', CASE WHEN v.screening LIKE '%MATCH%' THEN 'FAIL' WHEN v.screening LIKE '%RELEASED%' THEN 'WARN' ELSE 'PASS' END, 'detail', v.screening),
        json_build_object('check','Intraday liquidity','result', CASE v.liquidity_state WHEN 'AVAILABLE' THEN 'PASS' WHEN 'TIGHT' THEN 'WARN' ELSE 'FAIL' END, 'detail', v.liquidity_state || ' · ' || coalesce(v.nostro,'—'))),
    'evidence', json_build_object('items', (SELECT json_agg(e ORDER BY e.sort_order) FROM fn_kuber_evidence(p_id) e),
                                  'confidence', (SELECT confidence FROM fn_kuber_confidence(p_id)), 'completeness_pct', (SELECT completeness_pct FROM fn_kuber_confidence(p_id))),
    'proposed_actions', (SELECT json_agg(a ORDER BY a.sort_order) FROM fn_kuber_actions(p_id) a),
    'boundary', 'No external action was executed. Human approval remains required.'
  ) INTO res;
  RETURN res;
END $$;

CREATE OR REPLACE FUNCTION fn_payment_lifecycle(p_id varchar)
RETURNS TABLE (ord int, state text, step_class text, ts text, note text) LANGUAGE plpgsql STABLE AS $$
DECLARE v record; e record; last_seq int; cur text; hp text[]; ci int; s text; o int := 0;
BEGIN
  SELECT * INTO v FROM v_payment_current WHERE payment_id = p_id;
  SELECT max(seq) INTO last_seq FROM v_payment_timeline WHERE payment_id = p_id;
  FOR e IN SELECT * FROM v_payment_timeline WHERE payment_id = p_id ORDER BY seq LOOP
    o := o + 1; ord := o; state := e.state; ts := to_char(e.event_ts, 'HH24:MI:SS'); note := NULL;
    step_class := CASE WHEN e.seq < last_seq THEN (CASE WHEN e.state IN ('REJECTED','FAILED') THEN 'err' WHEN e.state = 'INVESTIGATION' THEN 'warn' ELSE 'done' END)
                       ELSE (CASE v.status WHEN 'COMPLETED' THEN 'done' WHEN 'REJECTED' THEN 'err' WHEN 'FAILED' THEN 'err' WHEN 'RETURNED' THEN 'ret'
                                           WHEN 'INVESTIGATION' THEN 'warn' WHEN 'CANCELLED' THEN 'err' ELSE 'cur' END) END;
    cur := e.state;
    RETURN NEXT;
  END LOOP;
  IF v.status = 'IN_PROGRESS' THEN
    hp := ARRAY(SELECT st.state FROM sim_scenario_step st WHERE st.scenario_code = 'HAPPY'
                 AND NOT ((v.correspondent = '' AND v.intermediary = '') AND st.state IN ('IN_TRANSIT','CORRESPONDENT_PROCESSING')) ORDER BY st.step_no);
    ci := coalesce(array_position(hp, cur), 0);
    FOREACH s IN ARRAY hp[ci+1:] LOOP
      IF s <> 'COMPLETED' THEN o := o + 1; ord := o; state := s; step_class := 'pend'; ts := NULL; note := 'expected'; RETURN NEXT; END IF;
    END LOOP;
    o := o + 1; ord := o; state := 'Awaiting final confirmation (ACCC)'; step_class := 'pend'; ts := NULL; note := 'pending'; RETURN NEXT;
  ELSIF v.status <> 'COMPLETED' THEN
    o := o + 1; ord := o; state := NULL; step_class := 'note'; ts := NULL;
    note := 'Path terminates here — ' || v.state_label || '. No completion event exists for this payment.'; RETURN NEXT;
  END IF;
END $$;

/* =====================================================================
   K6. SIMULATION ENGINE (server-side, deterministic, same rules as the UI)
   ===================================================================== */
CREATE SEQUENCE IF NOT EXISTS sim_run_seq;
CREATE SEQUENCE IF NOT EXISTS sim_investigation_seq START 93001;

CREATE OR REPLACE FUNCTION fn_step_latency(p_id varchar, p_state text, p_idx int, p_prev text) RETURNS bigint LANGUAGE sql STABLE AS $$
  SELECT round(CASE WHEN p_state = 'SCREENING' AND p_prev = 'INVESTIGATION' THEN 600000 ELSE coalesce(lp.base_ms, 0) END
               * (0.7 + (fn_fnv1a(p_id || '|' || p_idx || p_state) % 600) / 1000.0))::bigint
  FROM payment p JOIN ref_rail r ON r.rail_code = p.rail_code
  LEFT JOIN sim_latency_profile lp ON lp.latency_class = r.latency_class AND lp.state = p_state
  WHERE p.payment_id = p_id
$$;

CREATE OR REPLACE FUNCTION fn_actor_for(p_id varchar, p_state text, p_prev text) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT CASE p_state
    WHEN 'INITIATED' THEN v.debtor || ' · ' || coalesce(v.payment_initiation_dept, 'Channel')
    WHEN 'ACCEPTED' THEN v.debtor_agent || ' · Payment Hub'
    WHEN 'SCREENING' THEN CASE WHEN p_prev = 'INVESTIGATION' THEN 'Screening analyst · ' || coalesce(v.owner, 'Ops') ELSE 'Sanctions / AML engine' END
    WHEN 'SENT' THEN v.debtor_agent
    WHEN 'IN_TRANSIT' THEN coalesce(nullif(v.correspondent,''), nullif(v.intermediary,''), v.creditor_agent)
    WHEN 'CORRESPONDENT_PROCESSING' THEN coalesce(nullif(v.intermediary,''), nullif(v.correspondent,''), v.rail)
    WHEN 'SETTLEMENT_PENDING' THEN CASE WHEN v.domain = 'DOME' THEN coalesce(v.clearing_system, v.rail) ELSE v.creditor_agent END
    WHEN 'COMPLETED' THEN v.creditor_agent WHEN 'REJECTED' THEN v.creditor_agent WHEN 'RETURNED' THEN v.creditor_agent
    WHEN 'INVESTIGATION' THEN 'Operations · ' || coalesce(v.owner, 'Investigations')
    WHEN 'FAILED' THEN 'Network SLA monitor' WHEN 'CANCELLED' THEN v.debtor_agent ELSE '—' END
  FROM v_payment_current v WHERE v.payment_id = p_id
$$;

CREATE OR REPLACE FUNCTION fn_build_iso_event(p_event_id bigint) RETURNS json LANGUAGE sql STABLE AS $$
  SELECT json_build_object(
    'BusinessMessageId', v.business_msg_id, 'UETR', v.uetr, 'PaymentId', v.payment_id,
    'MessageType', CASE WHEN e.iso_message = '—' THEN '— (internal event, no ISO message)' ELSE split_part(e.iso_message, ' ', 1) END,
    'PaymentStatus', s.iso_tx_status,
    'StatusReason', CASE e.state WHEN 'REJECTED' THEN 'AC03' WHEN 'FAILED' THEN 'NACK_TIMEOUT' ELSE '—' END,
    'Event', e.state, 'Description', e.description,
    'DebtorAgent', v.debtor_agent || coalesce(' (' || v.bic || ')', ''),
    'Correspondent', CASE WHEN v.correspondent <> '' THEN v.correspondent || ' (' || v.correspondent_bic || ')' ELSE '—' END,
    'Intermediary', CASE WHEN v.intermediary <> '' THEN v.intermediary || ' (' || v.intermediary_bic || ')' ELSE '—' END,
    'CreditorAgent', v.creditor_agent || coalesce(' (' || v.creditor_agent_bic || ')', ''),
    'Amount', v.amount, 'Currency', v.debit_ccy,
    'EventId', coalesce(e.sim_run_id, 'BASELINE') || '-E' || lpad(e.seq::text, 2, '0'), 'Sequence', e.seq, 'SimulationRunId', e.sim_run_id,
    'Scenario', (SELECT sc.scenario_name FROM sim_run r JOIN sim_scenario sc USING (scenario_code) WHERE r.run_id = e.sim_run_id),
    'Timestamp', to_char(e.event_ts, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), 'PreviousState', coalesce(e.prev_state, '—'), 'Actor', e.actor,
    'TrackerStatus', v.gpi_status, 'SimulatedLatencyMs', e.processing_ms, 'FailureInjected', e.failure_injected, 'ExternalActionExecuted', false,
    'ReturnReason', CASE WHEN e.state = 'RETURNED' THEN 'AC03' END, 'TxSts', e.iso_tx_status)
  FROM payment_event e JOIN v_payment_current v ON v.payment_id = e.payment_id JOIN ref_payment_state s ON s.state = e.state
  WHERE e.event_id = p_event_id
$$;

/* internal: append one simulation event (normal step or injected failure) */
CREATE OR REPLACE FUNCTION fn_sim_append(p_run varchar, p_state text, p_desc text, p_msg text, p_injected boolean, p_wall_ms bigint)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE r sim_run%ROWTYPE; v_prev text; v_lat bigint; v_ts timestamp; v_id bigint; v_code text; v_text text; v_rec text; v_agent text; v_inv text;
BEGIN
  SELECT * INTO r FROM sim_run WHERE run_id = p_run FOR UPDATE;
  SELECT state INTO v_prev FROM payment_event WHERE sim_run_id = p_run ORDER BY seq DESC LIMIT 1;
  v_lat := fn_step_latency(r.payment_id, p_state, r.event_count, v_prev);
  SELECT initiated_at + ((r.simulated_latency_ms + v_lat) * interval '1 millisecond') INTO v_ts FROM payment WHERE payment_id = r.payment_id;
  v_code := CASE WHEN p_state IN ('REJECTED','RETURNED') THEN 'AC03' WHEN p_state = 'FAILED' THEN 'NACK_TIMEOUT'
                 WHEN p_state = 'INVESTIGATION' THEN CASE WHEN p_desc LIKE '%analyst%' THEN 'SCREEN_MATCH' ELSE 'CORR_ACK_OVERDUE' END END;
  v_text := CASE WHEN p_state IN ('REJECTED','RETURNED') THEN 'AC03 — Invalid Creditor Account Number'
                 WHEN p_state = 'FAILED' THEN CASE WHEN p_injected THEN 'NACK_TIMEOUT — Failure injected' ELSE 'NACK_TIMEOUT — Network SLA breached' END
                 WHEN p_state = 'INVESTIGATION' THEN CASE WHEN p_desc LIKE '%analyst%' THEN 'Potential sanctions match' ELSE 'Correspondent ACK overdue' END END;
  IF p_state = 'INVESTIGATION' AND NOT EXISTS (SELECT 1 FROM investigation WHERE payment_id = r.payment_id AND (sim_run_id IS NULL OR sim_run_id = p_run)) THEN
    v_inv := 'INV-' || nextval('sim_investigation_seq');
    INSERT INTO investigation (investigation_id, payment_id, case_type, status, sim_run_id, opened_at)
    VALUES (v_inv, r.payment_id, CASE WHEN p_desc LIKE '%analyst%' THEN 'SCREENING_REVIEW' ELSE 'CORRESPONDENT_CHASE' END, 'OPEN', p_run, v_ts);
  END IF;
  INSERT INTO payment_event (payment_id, sim_run_id, state, description, iso_message, actor, reason_code, reason_text, event_ts, processing_ms, wall_elapsed_ms, failure_injected, source)
  VALUES (r.payment_id, p_run, p_state, p_desc, p_msg, fn_actor_for(r.payment_id, p_state, v_prev), v_code, v_text, v_ts, v_lat,
          coalesce(p_wall_ms, r.event_count * (SELECT step_delay_ms FROM sim_speed WHERE speed_code = r.speed_code)), p_injected, 'SIMULATION')
  RETURNING event_id INTO v_id;
  -- ISO event payload is built AFTER projection so TrackerStatus reflects the new state
  PERFORM set_config('kuber.allow_payload', 'on', true);
  UPDATE payment_event SET iso_payload = fn_build_iso_event(v_id) WHERE event_id = v_id;
  PERFORM set_config('kuber.allow_payload', 'off', true);
  -- Kuber recommendation for this state (same catalogue as the UI)
  v_rec := CASE p_state
    WHEN 'SCREENING' THEN CASE WHEN p_desc LIKE 'Potential%' THEN 'Route alert to screening analyst' ELSE 'Monitor — within SLA' END
    WHEN 'SENT' THEN 'Monitor — awaiting correspondent ACK'
    WHEN 'IN_TRANSIT' THEN CASE WHEN p_msg = '—' THEN 'Escalate correspondent acknowledgement' ELSE 'Monitor — within SLA' END
    WHEN 'SETTLEMENT_PENDING' THEN 'Pre-stage camt.054 reconciliation'
    WHEN 'COMPLETED' THEN 'Close case; reconcile camt.054'
    WHEN 'REJECTED' THEN 'Validate beneficiary account before re-submit'
    WHEN 'RETURNED' THEN 'Credit pacs.004 return to debtor and notify client'
    WHEN 'INVESTIGATION' THEN CASE WHEN p_desc LIKE '%analyst%' THEN 'Analyst review of screening alert — human decision required' ELSE 'Chase correspondent via investigation request' END
    WHEN 'FAILED' THEN CASE WHEN p_injected THEN 'Confirm failure root cause; verify no duplicate before re-send' ELSE 'Open network incident; hold re-send pending camt.029' END
    ELSE 'Monitor — within SLA' END;
  v_agent := CASE p_state WHEN 'REJECTED' THEN 'investigator' WHEN 'RETURNED' THEN 'investigator' WHEN 'FAILED' THEN 'orchestrator' WHEN 'COMPLETED' THEN 'recon'
                          WHEN 'INVESTIGATION' THEN CASE WHEN p_desc LIKE '%analyst%' THEN 'risk' ELSE 'correspondent' END ELSE 'orchestrator' END;
  INSERT INTO ai_recommendation (payment_id, agent_id, recommendation, confidence, source_event_id)
  VALUES (r.payment_id, v_agent, v_rec, (SELECT confidence FROM fn_kuber_confidence(r.payment_id)), v_id);
  RETURN v_id;
END $$;

-- allow the one controlled payload write on the otherwise append-only event table
CREATE OR REPLACE FUNCTION trg_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND coalesce(current_setting('kuber.allow_purge', true), 'off') = 'on' THEN RETURN OLD; END IF;
  IF TG_OP = 'UPDATE' AND coalesce(current_setting('kuber.allow_payload', true), 'off') = 'on' AND OLD.iso_payload IS NULL
     AND (to_jsonb(NEW) - 'iso_payload') = (to_jsonb(OLD) - 'iso_payload') THEN RETURN NEW; END IF;
  RAISE EXCEPTION 'payment_event is append-only' USING ERRCODE = 'check_violation';
END $$;

CREATE OR REPLACE FUNCTION fn_sim_finish(p_run varchar) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r sim_run%ROWTYPE; v_last text;
BEGIN
  SELECT * INTO r FROM sim_run WHERE run_id = p_run;
  SELECT state INTO v_last FROM payment_event WHERE sim_run_id = p_run ORDER BY seq DESC LIMIT 1;
  UPDATE sim_run SET mode = 'FINISHED', outcome = CASE WHEN failure_injected THEN 'FAILURE INJECTED' ELSE v_last END, finished_at = now() WHERE run_id = p_run;
  INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details)
  VALUES (r.started_by, 'SIM_FINISH', 'sim_run', p_run, json_build_object('outcome', CASE WHEN r.failure_injected THEN 'FAILURE INJECTED' ELSE v_last END)::jsonb);
END $$;

CREATE OR REPLACE FUNCTION fn_sim_step(p_run varchar, p_wall_ms bigint DEFAULT NULL) RETURNS json LANGUAGE plpgsql AS $$
DECLARE r sim_run%ROWTYPE; st sim_scenario_step%ROWTYPE; v_id bigint; v_total int;
BEGIN
  SELECT * INTO r FROM sim_run WHERE run_id = p_run FOR UPDATE;
  IF r.mode <> 'RUNNING' THEN RAISE EXCEPTION 'START SIMULATION FIRST (run % is %)', p_run, r.mode; END IF;
  SELECT * INTO st FROM sim_scenario_step WHERE scenario_code = r.scenario_code AND step_no = r.next_step_no;
  SELECT count(*) INTO v_total FROM sim_scenario_step WHERE scenario_code = r.scenario_code;
  v_id := fn_sim_append(p_run, st.state, st.description, st.iso_message, false, p_wall_ms);
  UPDATE sim_run SET next_step_no = next_step_no + 1 WHERE run_id = p_run;
  IF r.next_step_no >= v_total THEN PERFORM fn_sim_finish(p_run); END IF;
  RETURN (SELECT iso_payload FROM payment_event WHERE event_id = v_id);
END $$;

CREATE OR REPLACE FUNCTION fn_sim_start(p_payment varchar, p_scenario varchar, p_speed varchar DEFAULT '1x', p_user varchar DEFAULT NULL)
RETURNS varchar LANGUAGE plpgsql AS $$
DECLARE v_run varchar;
BEGIN
  IF EXISTS (SELECT 1 FROM sim_run WHERE payment_id = p_payment AND mode IN ('RUNNING','PAUSED')) THEN
    RAISE EXCEPTION 'A simulation is already active on % — pause/reset it first', p_payment;
  END IF;
  v_run := 'SIM-20260925-' || lpad(nextval('sim_run_seq')::text, 4, '0');
  INSERT INTO sim_run (run_id, scenario_code, payment_id, speed_code, mode, outcome, hops, started_by)
  VALUES (v_run, p_scenario, p_payment, p_speed, 'RUNNING', 'RUNNING', (SELECT hops FROM v_payment_current WHERE payment_id = p_payment), p_user);
  UPDATE payment SET active_sim_run_id = v_run WHERE payment_id = p_payment;
  INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details) VALUES (p_user, 'SIM_START', 'payment', p_payment, json_build_object('run', v_run, 'scenario', p_scenario)::jsonb);
  PERFORM fn_sim_step(v_run, 0);             -- first event lands at 0.0s
  RETURN v_run;
END $$;

CREATE OR REPLACE FUNCTION fn_sim_pause(p_run varchar) RETURNS void LANGUAGE sql AS $$
  UPDATE sim_run SET mode = 'PAUSED', outcome = 'PAUSED' WHERE run_id = p_run AND mode = 'RUNNING';
$$;
CREATE OR REPLACE FUNCTION fn_sim_resume(p_run varchar) RETURNS void LANGUAGE sql AS $$
  UPDATE sim_run SET mode = 'RUNNING', outcome = 'RUNNING' WHERE run_id = p_run AND mode = 'PAUSED';
$$;
CREATE OR REPLACE FUNCTION fn_sim_inject_failure(p_run varchar, p_wall_ms bigint DEFAULT NULL) RETURNS json LANGUAGE plpgsql AS $$
DECLARE v_id bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM sim_run WHERE run_id = p_run AND mode = 'RUNNING') THEN
    RAISE EXCEPTION 'START SIMULATION FIRST';
  END IF;
  UPDATE sim_run SET failure_injected = true WHERE run_id = p_run;
  v_id := fn_sim_append(p_run, 'FAILED', 'Failure injected — processing aborted by chaos control', 'pacs.002 RJCT', true, p_wall_ms);
  PERFORM fn_sim_finish(p_run);
  RETURN (SELECT iso_payload FROM payment_event WHERE event_id = v_id);
END $$;
CREATE OR REPLACE FUNCTION fn_sim_reset(p_run varchar) RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_pay varchar;
BEGIN
  UPDATE sim_run SET mode = 'RESET', outcome = CASE WHEN mode = 'FINISHED' THEN outcome ELSE 'RESET' END, finished_at = coalesce(finished_at, now())
   WHERE run_id = p_run RETURNING payment_id INTO v_pay;
  UPDATE payment SET active_sim_run_id = NULL WHERE payment_id = v_pay AND active_sim_run_id = p_run;
  PERFORM fn_project_payment(v_pay);         -- payment returns to its record (baseline) state
END $$;
/* Headless convenience: run a whole scenario (used by batch tests / Monte-Carlo backfills) */
CREATE OR REPLACE FUNCTION fn_sim_run_to_end(p_payment varchar, p_scenario varchar, p_speed varchar DEFAULT '10x') RETURNS varchar LANGUAGE plpgsql AS $$
DECLARE v_run varchar;
BEGIN
  v_run := fn_sim_start(p_payment, p_scenario, p_speed);
  WHILE (SELECT mode FROM sim_run WHERE run_id = v_run) = 'RUNNING' LOOP PERFORM fn_sim_step(v_run); END LOOP;
  RETURN v_run;
END $$;

CREATE OR REPLACE VIEW v_sim_run_summary AS
SELECT r.run_id, sc.scenario_name, r.payment_id, r.speed_code, r.mode, r.outcome, r.event_count, r.iso_message_count, r.exception_count,
       r.event_count || ' events' AS events_display, r.iso_message_count || ' ISO message' || CASE WHEN r.iso_message_count = 1 THEN '' ELSE 's' END AS iso_display,
       r.exception_count || ' exception' || CASE WHEN r.exception_count = 1 THEN '' ELSE 's' END AS exceptions_display,
       fn_fmt_ms(r.simulated_latency_ms) AS latency, r.hops, r.failure_injected, r.started_at, r.finished_at,
       (SELECT string_agg(s.state, ' → ' ORDER BY s.step_no) FROM sim_scenario_step s WHERE s.scenario_code = r.scenario_code) AS simulation_path,
       sc.description AS scenario_note
FROM sim_run r JOIN sim_scenario sc USING (scenario_code);

/* =====================================================================
   K7. DISCOVERY SEARCH (structured parameters produced by the agent / UI parser)
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_search_payments(
  p_mode text DEFAULT 'ALL', p_status text[] DEFAULT NULL, p_rail text[] DEFAULT NULL, p_domain text DEFAULT NULL,
  p_msg text[] DEFAULT NULL, p_ccy text[] DEFAULT NULL, p_min numeric DEFAULT NULL, p_max numeric DEFAULT NULL,
  p_text text[] DEFAULT NULL, p_bic text DEFAULT NULL, p_debtor text DEFAULT NULL, p_creditor text DEFAULT NULL,
  p_customer text DEFAULT NULL, p_address_format text DEFAULT NULL, p_limit int DEFAULT 100, p_offset int DEFAULT 0)
RETURNS SETOF v_payment_current LANGUAGE sql STABLE AS $$
  SELECT v.* FROM v_payment_current v
  WHERE (p_mode = 'ALL' OR v.domain = p_mode)
    AND (p_status IS NULL OR v.status = ANY(p_status) OR v.intermediate_status = ANY(p_status))
    AND (p_rail IS NULL OR v.rail = ANY(p_rail))
    AND (p_domain IS NULL OR v.domain = p_domain)
    AND (p_msg IS NULL OR v.message_type = ANY(p_msg))
    AND (p_ccy IS NULL OR v.debit_ccy = ANY(p_ccy) OR v.credit_ccy = ANY(p_ccy))
    AND (p_min IS NULL OR v.amount >= p_min) AND (p_max IS NULL OR v.amount <= p_max)
    AND (p_bic IS NULL OR concat_ws(' ', v.bic, v.correspondent_bic, v.intermediary_bic, v.creditor_agent_bic) ILIKE '%' || p_bic || '%')
    AND (p_debtor IS NULL OR concat_ws(' ', v.debtor, v.ultimate_debtor) ILIKE '%' || p_debtor || '%')
    AND (p_creditor IS NULL OR concat_ws(' ', v.creditor, v.ultimate_creditor) ILIKE '%' || p_creditor || '%')
    AND (p_customer IS NULL OR concat_ws(' ', v.debtor, v.ultimate_debtor, v.creditor, v.ultimate_creditor) ILIKE '%' || p_customer || '%')
    AND (p_address_format IS NULL OR v.address_format = p_address_format)
    AND (p_text IS NULL OR NOT EXISTS (
          SELECT 1 FROM unnest(p_text) t
          WHERE lower(concat_ws(' | ', v.payment_id, v.uetr, v.swift_txn_id, v.business_msg_id, v.instruction_id, v.end_to_end_id, v.rail, v.payment_type, v.scheme,
                  v.message_type, v.debtor, v.ultimate_debtor, v.creditor, v.ultimate_creditor, v.debtor_agent, v.correspondent, v.intermediary, v.creditor_agent,
                  v.bic, v.status, v.intermediate_status, v.status_reason, v.screening, v.owner, v.payment_initiation_dept, v.nostro, v.investigation_id,
                  v.ai_recommendation, v.country)) NOT LIKE '%' || lower(t) || '%'))
  ORDER BY v.payment_id
  LIMIT p_limit OFFSET p_offset
$$;

/* =====================================================================
   K8. EXECUTIVE RADAR, DRILLDOWN, MANDATES, LAB
   ===================================================================== */
CREATE OR REPLACE VIEW v_exec_radar_kpi AS
SELECT k.*, (SELECT array_agg(s.value ORDER BY s.point_date) FROM exec_kpi_spark s WHERE s.business_date = k.business_date AND s.kpi_code = k.kpi_code) AS sparkline
FROM exec_kpi_snapshot k WHERE k.business_date = (SELECT max(business_date) FROM exec_kpi_snapshot);

CREATE OR REPLACE VIEW v_exec_breakdown AS
SELECT b.*, round(100.0 * b.value_usd / sum(b.value_usd) OVER (PARTITION BY b.business_date, b.chart_code), 1) AS value_share_pct
FROM exec_breakdown b WHERE b.business_date = (SELECT max(business_date) FROM exec_breakdown);

CREATE OR REPLACE VIEW v_exec_trend AS
SELECT business_date, to_char(business_date, 'DD Mon') AS label, round(payment_count/1e6, 2) AS volume_m, round(payment_value_usd/1e12, 2) AS value_t,
       round(100.0*(payment_count::numeric / lag(payment_count) OVER w - 1), 1) AS volume_delta_pct,
       round(100.0*(payment_value_usd / lag(payment_value_usd) OVER w - 1), 1) AS value_delta_pct,
       round(payment_value_usd / payment_count) AS avg_ticket_usd
FROM exec_trend_daily WINDOW w AS (ORDER BY business_date) ORDER BY business_date;

/* live metrics computed from the stored payments (the agent can compare with the T-1 snapshot) */
CREATE OR REPLACE VIEW v_live_status_mix AS
SELECT status, count(*) AS payments, round(100.0*count(*)/sum(count(*)) OVER (), 2) AS share_pct, sum(amount) AS notional
FROM v_payment_current GROUP BY status;
CREATE OR REPLACE VIEW v_live_rail_mix AS
SELECT rail, count(*) AS payments, round(100.0*count(*)/sum(count(*)) OVER (), 2) AS share_pct, sum(amount) AS notional,
       round(100.0*count(*) FILTER (WHERE status IN ('REJECTED','RETURNED','FAILED','INVESTIGATION','CANCELLED'))/count(*), 2) AS exception_pct
FROM v_payment_current GROUP BY rail;

CREATE OR REPLACE VIEW v_customer_drilldown AS
SELECT m.*, CASE WHEN stp_rate_pct >= 99 THEN 'g' WHEN stp_rate_pct >= 98.5 THEN 'c' ELSE 'a' END AS badge_color,
       '$' || round(value_usd/1e9) || 'B' AS value_display
FROM customer_metric_daily m WHERE business_date = (SELECT max(business_date) FROM customer_metric_daily) ORDER BY value_usd DESC;

CREATE OR REPLACE VIEW v_rail_drilldown AS
SELECT * FROM rail_metric_daily WHERE business_date = (SELECT max(business_date) FROM rail_metric_daily) ORDER BY sort_order;

CREATE OR REPLACE VIEW v_mandate_readiness AS
SELECT m.display_no, m.mandate_id, c.customer_name, m.description, to_char(m.due_time,'HH24:MI') AS due, m.due_tz,
       m.linked_payment_id, m.coverage_pct, m.readiness AS stored_readiness, m.readiness_note,
       v.status AS linked_status, v.state_label AS linked_state_label, v.liquidity_state,
       CASE WHEN v.status IN ('COMPLETED','IN_PROGRESS') AND v.liquidity_state <> 'SHORTFALL' THEN 'Ready' ELSE 'Needs review' END AS live_readiness
FROM mandate_obligation m LEFT JOIN customer c USING (customer_id) LEFT JOIN v_payment_current v ON v.payment_id = m.linked_payment_id
ORDER BY m.display_no;

CREATE OR REPLACE VIEW v_address_readiness AS
SELECT count(*) FILTER (WHERE address_format = 'UNSTRUCTURED') AS unstructured,
       count(*) FILTER (WHERE address_format = 'HYBRID') AS hybrid,
       count(*) FILTER (WHERE address_format = 'STRUCTURED') AS structured,
       count(*) AS cross_border_total
FROM v_payment_current WHERE domain = 'CBCC';

CREATE OR REPLACE VIEW v_regulatory_horizon AS
SELECT r.*, CASE WHEN r.effective_date <= DATE '2026-09-24' THEN 'LIVE'
                 ELSE '~' || round((r.effective_date - DATE '2026-09-24')/7.0) || ' WEEKS' END AS status_display
FROM regulatory_item r ORDER BY r.effective_date, r.reg_id;

CREATE OR REPLACE FUNCTION fn_route_rail(p_amount numeric, p_dest text, p_urgency text, p_timing text)
RETURNS TABLE (option_code text, eligible boolean, score int, recommended boolean, reason text, speed_score int, cost_score int) LANGUAGE sql STABLE AS $$
  WITH w AS (SELECT CASE p_urgency WHEN 'INSTANT' THEN 3 WHEN 'SAMEDAY' THEN 2 ELSE 1 END AS ws,
                    CASE p_urgency WHEN 'INSTANT' THEN 1 WHEN 'SAMEDAY' THEN 2 ELSE 3 END AS wc),
  e AS (SELECT r.*, (r.destination = p_dest AND (r.max_amount_usd IS NULL OR p_amount <= r.max_amount_usd)
                     AND NOT (r.business_hours_only AND p_timing = 'OFF') AND p_urgency = ANY(r.allowed_urgency)) AS ok,
               CASE WHEN r.destination <> p_dest THEN CASE WHEN r.destination = 'XB' THEN 'Cross-border only' ELSE 'Domestic only' END
                    WHEN r.max_amount_usd IS NOT NULL AND p_amount > r.max_amount_usd THEN coalesce(r.over_limit_note, 'Exceeds limit')
                    WHEN r.business_hours_only AND p_timing = 'OFF' THEN 'Operates on business days only'
                    WHEN NOT (p_urgency = ANY(r.allowed_urgency)) THEN 'Not suitable for requested urgency'
                    ELSE r.eligible_note END AS why
        FROM rail_route_rule r),
  s AS (SELECT e.*, CASE WHEN ok THEN e.speed_score*w.ws + e.cost_score*w.wc + CASE WHEN p_urgency = 'INSTANT' AND e.speed_score = 5 THEN 3 ELSE 0 END ELSE -1 END AS sc FROM e, w)
  SELECT s.option_code, s.ok, s.sc, (s.ok AND row_number() OVER (ORDER BY s.sc DESC, s.sort_order) = 1), s.why, s.speed_score, s.cost_score
  FROM s ORDER BY s.sc DESC, s.sort_order
$$;

/* =====================================================================
   K9. HUMAN-IN-THE-LOOP: agents propose, humans decide
   ===================================================================== */
CREATE OR REPLACE FUNCTION fn_kuber_propose_actions(p_payment varchar, p_agent varchar DEFAULT 'orchestrator') RETURNS int LANGUAGE plpgsql AS $$
DECLARE n int;
BEGIN
  INSERT INTO ai_proposed_action (payment_id, action_code, title, detail, proposed_by_agent_id, confidence)
  SELECT p_payment, a.action_code, a.title, a.detail, p_agent, (SELECT confidence FROM fn_kuber_confidence(p_payment))
  FROM fn_kuber_actions(p_payment) a
  WHERE NOT EXISTS (SELECT 1 FROM ai_proposed_action x WHERE x.payment_id = p_payment AND x.action_code = a.action_code AND x.status = 'PROPOSED');
  GET DIAGNOSTICS n = ROW_COUNT; RETURN n;
END $$;

CREATE OR REPLACE FUNCTION trg_action_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IN ('APPROVED','DECLINED') AND NOT EXISTS (SELECT 1 FROM app_user WHERE user_id = NEW.decided_by AND principal_type = 'HUMAN') THEN
    RAISE EXCEPTION 'Human approval remains required: % is not a human principal', coalesce(NEW.decided_by, '∅') USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER action_decision_guard BEFORE INSERT OR UPDATE ON ai_proposed_action FOR EACH ROW EXECUTE FUNCTION trg_action_decision_guard();

CREATE OR REPLACE FUNCTION fn_decide_action(p_action_id bigint, p_user varchar, p_decision text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE a ai_proposed_action%ROWTYPE;
BEGIN
  UPDATE ai_proposed_action SET status = CASE WHEN p_decision = 'APPROVE' THEN 'APPROVED' ELSE 'DECLINED' END, decided_by = p_user, decided_at = now()
   WHERE action_id = p_action_id AND status = 'PROPOSED' RETURNING * INTO a;
  IF NOT FOUND THEN RAISE EXCEPTION 'Action % is not pending', p_action_id; END IF;
  INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details)
  VALUES (p_user, 'ACTION_' || p_decision, 'ai_proposed_action', p_action_id::text, json_build_object('payment', a.payment_id, 'title', a.title)::jsonb);
  RETURN (CASE WHEN p_decision = 'APPROVE' THEN 'Human approval recorded' ELSE 'Human declined' END) || ' for “' || a.title || '” on ' || a.payment_id
         || '. No external action was executed — this is a simulation.';
END $$;

/* =====================================================================
   L. SECURITY: least-privilege roles for the Kuber agent runtime
   - kuber_agent_reader : what the LLM tools (MCP Toolbox) connect as
   - kuber_agent_writer : the agent may LOG conversations and PROPOSE actions
   - kuber_operator     : humans running simulations & approving actions
   The agent can never UPDATE payment, INSERT payment_event, or approve actions.
   ===================================================================== */
SET search_path = kuber, public;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kuber_agent_reader') THEN CREATE ROLE kuber_agent_reader NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kuber_agent_writer') THEN CREATE ROLE kuber_agent_writer NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kuber_operator')     THEN CREATE ROLE kuber_operator NOLOGIN; END IF;
END $$;

GRANT USAGE ON SCHEMA kuber TO kuber_agent_reader, kuber_agent_writer, kuber_operator;

-- API functions run with owner rights (SECURITY DEFINER + pinned search_path), so tool
-- connections never need raw-table grants. Functions are PUBLIC-executable by default in
-- PostgreSQL, so we revoke that and grant explicitly below. Pure helpers (fn_fmt_*, fn_gpi_status)
-- stay public because the views call them.
DO $$
DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY[
    'fn_payment_360(varchar)', 'fn_payment_journey(varchar)', 'fn_payment_lineage(varchar)', 'fn_payment_lifecycle(varchar)',
    'fn_kuber_evidence(varchar)', 'fn_kuber_confidence(varchar)', 'fn_kuber_actions(varchar)', 'fn_kuber_route(text, varchar)',
    'fn_kuber_summary(varchar)', 'fn_kuber_answer(varchar, text, varchar)',
    'fn_search_payments(text, text[], text[], text, text[], text[], numeric, numeric, text[], text, text, text, text, text, int, int)',
    'fn_route_rail(numeric, text, text, text)', 'fn_build_iso_event(bigint)', 'fn_kuber_propose_actions(varchar, varchar)',
    'fn_sim_start(varchar, varchar, varchar, varchar)', 'fn_sim_step(varchar, bigint)', 'fn_sim_pause(varchar)', 'fn_sim_resume(varchar)',
    'fn_sim_inject_failure(varchar, bigint)', 'fn_sim_reset(varchar)', 'fn_sim_run_to_end(varchar, varchar, varchar)', 'fn_decide_action(bigint, varchar, text)',
    'fn_sim_append(varchar, text, text, text, boolean, bigint)', 'fn_sim_finish(varchar)', 'fn_project_payment(varchar)', 'fn_render_template(text, varchar)']
  LOOP
    EXECUTE format('ALTER FUNCTION kuber.%s SECURITY DEFINER SET search_path = kuber, public', f);
    EXECUTE format('REVOKE EXECUTE ON FUNCTION kuber.%s FROM PUBLIC', f);
  END LOOP;
END $$;

-- reader: semantic views + read functions + reference/metadata tables only
GRANT SELECT ON v_payment_current, v_payment_timeline, v_sim_run_summary, v_exec_radar_kpi, v_exec_breakdown, v_exec_trend,
               v_live_status_mix, v_live_rail_mix, v_customer_drilldown, v_rail_drilldown, v_mandate_readiness,
               v_address_readiness, v_regulatory_horizon TO kuber_agent_reader;
GRANT SELECT ON ref_region, ref_country, ref_currency, bank, ref_domain, ref_rail, ref_scheme, ref_payment_type, ref_iso_message_type,
               ref_mt_mx_mapping, ref_iso_tx_status, ref_reason_code, ref_payment_status, ref_payment_state, ref_state_transition,
               field_category, field_catalogue, persona, persona_tag, column_preset, column_preset_field, nl_query_vocabulary,
               ai_agent, ai_quick_prompt, ai_intent, ai_evidence_dimension, ai_action_rule, sim_scenario, sim_scenario_step, sim_speed,
               rail_route_rule, regulatory_item, knowledge_document, knowledge_chunk, mc_run, mc_outcome, iso_message TO kuber_agent_reader;
GRANT EXECUTE ON FUNCTION fn_payment_360(varchar), fn_payment_journey(varchar), fn_payment_lineage(varchar), fn_payment_lifecycle(varchar),
               fn_kuber_evidence(varchar), fn_kuber_confidence(varchar), fn_kuber_actions(varchar), fn_kuber_route(text, varchar),
               fn_kuber_summary(varchar), fn_kuber_answer(varchar, text, varchar), fn_search_payments(text, text[], text[], text, text[], text[], numeric, numeric, text[], text, text, text, text, text, int, int),
               fn_route_rail(numeric, text, text, text), fn_build_iso_event(bigint) TO kuber_agent_reader;

-- writer (agent): may log & propose, nothing else
GRANT kuber_agent_reader TO kuber_agent_writer;
GRANT INSERT ON ai_conversation, ai_message, ai_tool_call, ai_evidence_item, ai_recommendation, ai_proposed_action TO kuber_agent_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA kuber TO kuber_agent_writer;
GRANT EXECUTE ON FUNCTION fn_kuber_propose_actions(varchar, varchar) TO kuber_agent_writer;

-- operator (human): simulations + decisions
GRANT kuber_agent_reader TO kuber_operator;
GRANT EXECUTE ON FUNCTION fn_sim_start(varchar, varchar, varchar, varchar), fn_sim_step(varchar, bigint), fn_sim_pause(varchar), fn_sim_resume(varchar),
               fn_sim_inject_failure(varchar, bigint), fn_sim_reset(varchar), fn_sim_run_to_end(varchar, varchar, varchar),
               fn_decide_action(bigint, varchar, text) TO kuber_operator;
GRANT SELECT, UPDATE ON ai_proposed_action TO kuber_operator;
GRANT INSERT ON saved_view, audit_log TO kuber_operator;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA kuber TO kuber_operator;

-- belt and braces: nobody but the owner writes the event stream or state directly
REVOKE INSERT, UPDATE, DELETE ON payment, payment_event FROM kuber_agent_reader, kuber_agent_writer, kuber_operator;

-- Persona scoping: the agent passes p_mode ('CBCC' | 'DOME' | 'ALL') to fn_search_payments.
-- For hard isolation in production, recreate the views WITH (security_invoker = true),
-- enable RLS on payment and add a policy on current_setting('kuber.mode').
