/* =====================================================================
   KUBER V4 — BIGQUERY ANALYTICS LAYER (GoogleSQL)
   ---------------------------------------------------------------------
   Role in the architecture
   • AlloyDB / PostgreSQL (files 01–05) is the SYSTEM OF RECORD. It holds the
     event-sourced state machine, the simulator, the enforced constraints and
     the human-approval boundary.
   • Datastream (CDC) replicates the kuber.* tables into the dataset
     `kuber_raw`, which is managed by Datastream and has no DDL here.
   • This file builds `kuber_analytics`: partitioned and clustered facts, the
     daily Executive Radar and the drilldowns, plus column descriptions that
     Gemini, Conversational Analytics and Data Agents read as semantic
     context.
   • BigQuery primary and foreign keys are declared NOT ENFORCED. They help the
     optimizer and help LLMs infer joins. Integrity is enforced upstream in
     PostgreSQL.
   Replace `your-project` before running.
   ===================================================================== */

CREATE SCHEMA IF NOT EXISTS `your-project.kuber_analytics`
  OPTIONS (location = 'US', description = 'Kuber Payment Intelligence — analytics & AI-agent read layer (synthetic data)');

/* ---------- 1. PAYMENT FACT (one row per payment, current state, nested parties & hops) ---------- */
CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.payment_fact` (
  payment_id          STRING NOT NULL OPTIONS (description = 'Master payment identifier, e.g. PAY-CB-000001'),
  uetr                STRING OPTIONS (description = 'Unique End-to-End Transaction Reference (UUIDv4), gpi tracking key, NULL for rails without UETR'),
  swift_txn_id        STRING,
  business_msg_id     STRING OPTIONS (description = 'head.001 BizMsgIdr'),
  instruction_id      STRING OPTIONS (description = 'PmtId/InstrId'),
  end_to_end_id       STRING OPTIONS (description = 'PmtId/EndToEndId — carried unchanged end to end'),
  domain              STRING OPTIONS (description = 'CBCC = cross-border correspondent banking, DOME = domestic operations'),
  rail                STRING OPTIONS (description = 'SWIFT CBPR+ | RTP | FedNow | ACH | Fedwire'),
  payment_type        STRING,
  radar_bucket        STRING OPTIONS (description = 'Executive radar type bucket: Customer Credit, Instant, ACH Batch, FI Transfer, Return, Other'),
  scheme              STRING,
  message_type        STRING OPTIONS (description = 'Current ISO 20022 message (pacs.004 after a return)'),
  mt_equivalent       STRING OPTIONS (description = 'Semantic MT equivalent (MT103, MT202 …) — mapping only'),
  address_format      STRING OPTIONS (description = 'STRUCTURED | HYBRID | UNSTRUCTURED — CBPR+ SR2026 (Nov 2026) readiness'),
  amount              NUMERIC OPTIONS (description = 'Interbank settlement amount in debit_ccy'),
  amount_usd          NUMERIC OPTIONS (description = 'Amount converted to USD for portfolio aggregation'),
  debit_ccy           STRING, credit_ccy STRING, fx_rate NUMERIC, credit_amount NUMERIC,
  charge_bearer       STRING, fee_amount_usd NUMERIC,
  settlement_date     DATE NOT NULL,
  initiated_at        TIMESTAMP, completed_at TIMESTAMP OPTIONS (description = 'Only populated when status = COMPLETED'),
  processing_ms       INT64,
  status              STRING OPTIONS (description = 'Authoritative business status projected from the event stream: COMPLETED, IN_PROGRESS, REJECTED, RETURNED, INVESTIGATION, FAILED, CANCELLED. Never treat non-COMPLETED as completed.'),
  ultimate_status     STRING, intermediate_status STRING,
  state_label         STRING OPTIONS (description = 'UI label: Completed / Rejected / Returned / Investigation / Failed / Current State · X'),
  status_indicator    STRING OPTIONS (description = 'e.g. RJCT · AC03, ACCC · Settled, PDNG · INV-90251'),
  gpi_status          STRING, status_reason_code STRING, status_reason STRING, exception_type STRING,
  settlement_method   STRING OPTIONS (description = 'Method of payment: INDA / INGA / COVE / CLRG'),
  screening_state     STRING, risk_score INT64,
  customer_id STRING, customer_name STRING, segment STRING, initiation_department STRING, owner STRING,
  nostro STRING, liquidity_state STRING, investigation_id STRING, recon_state STRING, sla_state STRING,
  ai_recommendation STRING, ai_agent STRING, ai_confidence NUMERIC,
  is_exception        BOOL OPTIONS (description = 'status in REJECTED, RETURNED, INVESTIGATION, FAILED, CANCELLED'),
  is_stp              BOOL OPTIONS (description = 'Completed without any exception or manual event in its lifecycle'),
  parties ARRAY<STRUCT<role STRING, name STRING, account_id STRING, country STRING, address_format STRING>>,
  hops    ARRAY<STRUCT<seq INT64, role STRING, bank_name STRING, bic STRING, country STRING>>,
  _cdc_ts TIMESTAMP OPTIONS (description = 'Replication timestamp'),
  PRIMARY KEY (payment_id) NOT ENFORCED
)
PARTITION BY settlement_date
CLUSTER BY rail, status, domain, customer_id
OPTIONS (description = 'One row per payment (current projected state). Source: AlloyDB kuber.v_payment_current via Datastream + scheduled MERGE.');

/* ---------- 2. EVENT FACT (append-only lifecycle, baseline + simulation streams) ---------- */
CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.payment_event_fact` (
  event_id INT64 NOT NULL, payment_id STRING NOT NULL, sim_run_id STRING, seq INT64,
  state STRING OPTIONS (description = 'State-machine state: INITIATED, ACCEPTED, SCREENING, SENT, IN_TRANSIT, CORRESPONDENT_PROCESSING, SETTLEMENT_PENDING, COMPLETED, REJECTED, RETURNED, INVESTIGATION, FAILED, CANCELLED'),
  prev_state STRING, description STRING, iso_message STRING, iso_msg_type STRING, iso_tx_status STRING,
  actor STRING, reason_code STRING, reason_text STRING,
  event_ts TIMESTAMP NOT NULL, processing_ms INT64, failure_injected BOOL, source STRING,
  iso_payload JSON OPTIONS (description = 'Generated ISO event JSON (BusinessMessageId, UETR, PaymentStatus, StatusReason …)'),
  PRIMARY KEY (event_id) NOT ENFORCED,
  FOREIGN KEY (payment_id) REFERENCES `your-project.kuber_analytics.payment_fact`(payment_id) NOT ENFORCED
)
PARTITION BY DATE(event_ts)
CLUSTER BY payment_id, state
OPTIONS (description = 'Append-only payment lifecycle events');

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.iso_message_fact` (
  message_id INT64 NOT NULL, payment_id STRING NOT NULL, event_id INT64, msg_type STRING, direction STRING,
  from_bic STRING, to_bic STRING, tx_status STRING, reason_code STRING, created_at TIMESTAMP, payload_xml STRING,
  PRIMARY KEY (message_id) NOT ENFORCED
)
PARTITION BY DATE(created_at) CLUSTER BY msg_type, payment_id;

/* ---------- 3. SIMULATION & MONTE CARLO HISTORY ---------- */
CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.sim_run_fact` (
  run_id STRING NOT NULL, scenario_code STRING, scenario_name STRING, payment_id STRING, speed_code STRING,
  outcome STRING OPTIONS (description = 'COMPLETED | FAILED | RETURNED | FAILURE INJECTED | RESET'),
  event_count INT64, iso_message_count INT64, exception_count INT64, simulated_latency_ms INT64, hops INT64,
  failure_injected BOOL, started_at TIMESTAMP, finished_at TIMESTAMP,
  PRIMARY KEY (run_id) NOT ENFORCED
)
PARTITION BY DATE(started_at) CLUSTER BY scenario_code, outcome;

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.mc_run_fact` (
  mc_run_id INT64 NOT NULL, runs INT64, sla_stress INT64, seed INT64, weights JSON,
  stp_pct NUMERIC, mean_latency_ms INT64, p95_latency_ms INT64, iso_messages INT64, exceptions INT64, investigations INT64, kuber_cases INT64,
  outcomes ARRAY<STRUCT<outcome STRING, run_count INT64, pct NUMERIC>>, executed_at TIMESTAMP,
  PRIMARY KEY (mc_run_id) NOT ENFORCED
) PARTITION BY DATE(executed_at);

/* ---------- 4. EXECUTIVE RADAR (daily materialisations) ---------- */
CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.exec_kpi_daily` (
  business_date DATE NOT NULL, kpi_code STRING NOT NULL, sort_order INT64, label STRING,
  numeric_value NUMERIC, unit STRING, display_value STRING, delta_text STRING, delta_is_warning BOOL, accent_token STRING,
  PRIMARY KEY (business_date, kpi_code) NOT ENFORCED
) PARTITION BY business_date
OPTIONS (description = 'Executive Payment Radar KPIs: PAYMENT_VALUE, PAYMENTS, STP_RATE, SUCCESS_RATE, EXCEPTIONS, AVG_LATENCY, AI_CASES');

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.exec_breakdown_daily` (
  business_date DATE NOT NULL, chart_code STRING NOT NULL OPTIONS (description = 'STATUS | RAIL | TYPE'), bucket STRING NOT NULL,
  sort_order INT64, share_pct NUMERIC OPTIONS (description = 'Volume share %'), value_usd NUMERIC, display_value STRING, color_token STRING,
  PRIMARY KEY (business_date, chart_code, bucket) NOT ENFORCED
) PARTITION BY business_date;

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.exec_trend_daily` (
  business_date DATE NOT NULL, payment_count INT64, payment_value_usd NUMERIC,
  PRIMARY KEY (business_date) NOT ENFORCED
) PARTITION BY business_date;

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.customer_metric_daily` (
  business_date DATE NOT NULL, customer_id STRING NOT NULL, display_name STRING, value_usd NUMERIC, stp_rate_pct NUMERIC,
  PRIMARY KEY (business_date, customer_id) NOT ENFORCED
) PARTITION BY business_date;

CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.rail_metric_daily` (
  business_date DATE NOT NULL, rail_label STRING NOT NULL, sort_order INT64, value_usd NUMERIC, display_value STRING,
  latency_display STRING, latency_ms INT64, latency_stat STRING,
  PRIMARY KEY (business_date, rail_label) NOT ENFORCED
) PARTITION BY business_date;

/* ---------- 5. AI OBSERVABILITY (evaluate Kuber quality over time) ---------- */
CREATE TABLE IF NOT EXISTS `your-project.kuber_analytics.ai_interaction_fact` (
  message_id INT64 NOT NULL, conversation_id STRING, created_at TIMESTAMP, role STRING, agent STRING, routed_from STRING,
  intent STRING, payment_id STRING, model_name STRING, confidence NUMERIC, evidence_dims ARRAY<STRING>,
  tool_calls ARRAY<STRUCT<tool_name STRING, succeeded BOOL, latency_ms INT64, row_count INT64>>,
  matched_gold_answer BOOL OPTIONS (description = 'Did the LLM answer agree with fn_kuber_answer (deterministic gold)?'),
  human_feedback STRING,
  PRIMARY KEY (message_id) NOT ENFORCED
) PARTITION BY DATE(created_at) CLUSTER BY agent, intent;

/* ---------- 6. SEMANTIC VIEWS (what Gemini / Conversational Analytics should query) ---------- */
CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_exec_radar_live` AS
SELECT settlement_date AS business_date,
       SUM(amount_usd)                                               AS payment_value_usd,
       COUNT(*)                                                      AS payments,
       ROUND(100 * COUNTIF(is_stp) / COUNT(*), 2)                    AS stp_rate_pct,
       ROUND(100 * COUNTIF(status NOT IN ('REJECTED','FAILED')) / COUNT(*), 2) AS success_rate_pct,
       ROUND(100 * COUNTIF(is_exception) / COUNT(*), 2)              AS exception_rate_pct,
       COUNTIF(is_exception)                                         AS exception_cases,
       ROUND(AVG(processing_ms) / 1000, 1)                           AS avg_latency_s,
       COUNTIF(ai_recommendation IS NOT NULL AND ai_recommendation != 'No action required') AS ai_cases
FROM `your-project.kuber_analytics.payment_fact`
GROUP BY business_date;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_status_mix_daily` AS
SELECT settlement_date AS business_date, status, COUNT(*) AS payments, SUM(amount_usd) AS value_usd,
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY settlement_date), 2) AS share_pct
FROM `your-project.kuber_analytics.payment_fact` GROUP BY 1, 2;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_rail_mix_daily` AS
SELECT settlement_date AS business_date, rail, COUNT(*) AS payments, SUM(amount_usd) AS value_usd,
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY settlement_date), 2) AS share_pct,
       APPROX_QUANTILES(processing_ms, 100)[OFFSET(95)] AS p95_latency_ms
FROM `your-project.kuber_analytics.payment_fact` GROUP BY 1, 2;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_type_mix_daily` AS
SELECT settlement_date AS business_date, radar_bucket, COUNT(*) AS payments, SUM(amount_usd) AS value_usd,
       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY settlement_date), 2) AS volume_share_pct,
       ROUND(100 * SUM(amount_usd) / SUM(SUM(amount_usd)) OVER (PARTITION BY settlement_date), 2) AS value_share_pct
FROM `your-project.kuber_analytics.payment_fact` GROUP BY 1, 2;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_exceptions_by_reason` AS
SELECT settlement_date AS business_date, rail, status, COALESCE(status_reason_code, 'NONE') AS reason_code, exception_type,
       COUNT(*) AS cases, SUM(amount_usd) AS value_usd
FROM `your-project.kuber_analytics.payment_fact` WHERE is_exception GROUP BY 1, 2, 3, 4, 5;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_address_readiness` AS
SELECT settlement_date AS business_date,
       COUNTIF(address_format = 'UNSTRUCTURED') AS unstructured, COUNTIF(address_format = 'HYBRID') AS hybrid,
       COUNTIF(address_format = 'STRUCTURED') AS structured, COUNT(*) AS cross_border_total
FROM `your-project.kuber_analytics.payment_fact` WHERE domain = 'CBCC' GROUP BY 1;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_correspondent_performance` AS
SELECT h.bank_name AS correspondent, h.bic, COUNT(*) AS payments,
       COUNTIF(p.status = 'INVESTIGATION' OR p.exception_type = 'CORRESPONDENT_SLA') AS sla_cases,
       ROUND(AVG(p.processing_ms) / 1000, 1) AS avg_latency_s
FROM `your-project.kuber_analytics.payment_fact` p, UNNEST(p.hops) h
WHERE h.role = 'CORRESPONDENT' GROUP BY 1, 2;

CREATE OR REPLACE VIEW `your-project.kuber_analytics.v_scenario_outcomes` AS
SELECT scenario_name, outcome, COUNT(*) AS runs, AVG(simulated_latency_ms) AS avg_latency_ms,
       SUM(iso_message_count) AS iso_messages, SUM(exception_count) AS exceptions
FROM `your-project.kuber_analytics.sim_run_fact` GROUP BY 1, 2;

/* ---------- 7. DAILY LOAD (scheduled query) from the CDC mirror ---------- */
-- MERGE `your-project.kuber_analytics.payment_fact` T
-- USING (SELECT … FROM `your-project.kuber_raw.payment` p JOIN `your-project.kuber_raw.payment_party` … ) S
-- ON T.payment_id = S.payment_id
-- WHEN MATCHED THEN UPDATE SET … WHEN NOT MATCHED THEN INSERT ROW;
-- (Or query AlloyDB live with EXTERNAL_QUERY('your-project.us.kuber-alloydb', 'SELECT * FROM kuber.v_payment_current').)
