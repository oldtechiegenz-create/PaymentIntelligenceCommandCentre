"""Populates ONLY reference/config tables (banks, state machine, scenarios, field catalogue,
personas, mandates, etc). Idempotent (INSERT OR IGNORE), runnable standalone.

Usage: uv run python -m app.db.seed_reference_data
Never invoked automatically from app.main's lifespan (see architecture decisions memory).

Does NOT seed synthetic payment/event data — that is Phase 3 (app/seed/generator.py), which
depends on this reference data already existing.
"""
from __future__ import annotations

import json
import sqlite3

from app.db.connection import get_connection


def seed_banks(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO bank (bic, bank_name, country_code) VALUES (?, ?, ?)",
        [
            ("CHASUS33", "JPMorgan Chase Bank N.A.", "US"),
            ("MIDLGB22", "HSBC Bank plc", "GB"),
            ("CITIUS33", "Citibank N.A.", "US"),
            ("DEUTDEFF", "Deutsche Bank AG", "DE"),
            ("BOFAUS3N", "Bank of America N.A.", "US"),
            ("BARCGB22", "Barclays Bank plc", "GB"),
            ("WFBIUS6S", "Wells Fargo Bank N.A.", "US"),
            ("PNCCUS33", "PNC Bank N.A.", "US"),
            ("BNPAFRPP", "BNP Paribas SA", "FR"),
            ("BOTKJPJT", "MUFG Bank Ltd", "JP"),
            ("SCBLSGSG", "Standard Chartered Bank", "SG"),
            ("SBININBB", "State Bank of India", "IN"),
            ("UBSWCHZH", "UBS Switzerland AG", "CH"),
            ("ROYCCAT2", "Royal Bank of Canada", "CA"),
        ],
    )


def seed_domains_and_rails(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO ref_domain (domain_code, domain_name, description) VALUES (?, ?, ?)",
        [
            ("CBCC", "Cross-Border Correspondent Banking", "SWIFT CBPR+ correspondent chains, FX, screening, nostro"),
            ("DOME", "Domestic Payments Operations", "US instant, batch and high-value rails"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_rail "
        "(rail_code, rail_name, domain_code, latency_class, network_limit_usd, operates_24x7, "
        "settlement_model, iso20022_since, color_token, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("SWIFT CBPR+", "SWIFT Cross-Border Payments & Reporting Plus", "CBCC", "XB", None, 1,
             "Correspondent nostro/vostro (INDA/INGA/COVE)", "2023-03-20", "cyan",
             "ISO 20022 cross-border; MT/MX coexistence ended Nov 2025"),
            ("Fedwire", "Fedwire Funds Service", "DOME", "WIRE", None, 0,
             "RTGS in Fed master accounts", "2025-07-14", "violet",
             "High-value RTGS; ISO 20022 since 14 Jul 2025"),
            ("ACH", "Automated Clearing House (FedACH / Nacha)", "DOME", "BATCH", None, 0,
             "Deferred net settlement; Same Day ACH \u2264 $1M per payment", None, "blue", "Batch credits/debits"),
            ("RTP", "The Clearing House RTP", "DOME", "INSTANT", 10000000, 1,
             "Prefunded joint account; final & irrevocable", None, "green",
             "Instant 24\u00d77\u00d7365; $10M network limit"),
            ("FedNow", "FedNow Service", "DOME", "INSTANT", 10000000, 1,
             "Master accounts; final & irrevocable", None, "amber",
             "Instant 24\u00d77\u00d7365; $10M network limit since Nov 2025"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_scheme (scheme_code, rail_code, description) VALUES (?, ?, ?)",
        [
            ("CBPR+", "SWIFT CBPR+", None),
            ("FedNow", "FedNow", None),
            ("Fedwire Funds (ISO 20022)", "Fedwire", None),
            ("Nacha CCD", "ACH", None),
            ("Nacha CTX", "ACH", None),
            ("Nacha PPD", "ACH", None),
            ("TCH RTP", "RTP", None),
        ],
    )


def seed_payment_type_and_iso(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO ref_payment_type (payment_type, radar_bucket) VALUES (?, ?)",
        [
            ("Cross-Border Customer Credit Transfer", "Customer Credit"),
            ("Domestic Instant Credit Transfer", "Instant"),
            ("Domestic Batch Credit", "ACH Batch"),
            ("Financial Institution Transfer", "FI Transfer"),
            ("Cross-Border Return", "Return"),
            ("Domestic High-Value Credit", "Other"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_iso_message_type (msg_type, msg_family, msg_name, default_version, description) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("head.001", "head", "Business Application Header", "head.001.001.02", "BAH carried with every CBPR+ message"),
            ("pain.001", "pain", "Customer Credit Transfer Initiation", "pain.001.001.09", "Customer \u2192 debtor agent instruction"),
            ("pacs.008", "pacs", "FI To FI Customer Credit Transfer", "pacs.008.001.08", "Interbank customer credit transfer (\u2248 MT103)"),
            ("pacs.009", "pacs", "Financial Institution Credit Transfer", "pacs.009.001.08", "Bank-to-bank transfer / cover (\u2248 MT202 / MT202 COV)"),
            ("pacs.002", "pacs", "FI To FI Payment Status Report", "pacs.002.001.10", "Status: ACSP / ACCC / RJCT / PDNG"),
            ("pacs.004", "pacs", "Payment Return", "pacs.004.001.09", "Return of funds (e.g. AC03)"),
            ("camt.052", "camt", "Bank To Customer Account Report", "camt.052.001.08", "Intraday report (\u2248 MT942)"),
            ("camt.053", "camt", "Bank To Customer Statement", "camt.053.001.08", "End-of-day statement (\u2248 MT940)"),
            ("camt.054", "camt", "Bank To Customer Debit Credit Notification", "camt.054.001.08", "Debit/credit advice (\u2248 MT900/MT910)"),
            ("camt.056", "camt", "FI To FI Payment Cancellation Request", "camt.056.001.08", "Cancellation / recall request (investigation)"),
            ("camt.029", "camt", "Resolution Of Investigation", "camt.029.001.09", "Answer to camt.056: CNCL / RJCR / PDCR"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_mt_mx_mapping (mt_type, mx_type, meaning) VALUES (?, ?, ?)",
        [
            ("MT103", "pacs.008", "Customer credit transfer"),
            ("MT202", "pacs.009", "FI credit transfer"),
            ("MT202 COV", "pacs.009 COV", "Cover payment"),
            ("MT101", "pain.001", "Customer credit transfer initiation"),
            ("MT940", "camt.053", "End-of-day statement"),
            ("MT900 / MT910", "camt.054", "Debit / credit notification"),
            ("MT942", "camt.052", "Intraday report"),
            ("MT192 / n92", "camt.056", "Cancellation request"),
            ("MT196 / n96", "camt.029", "Resolution of investigation"),
            ("MT103 RETN", "pacs.004", "Payment return"),
            ("MT199 gpi", "pacs.002", "Payment status report"),
        ],
    )


def seed_state_machine(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO ref_iso_tx_status (tx_status, meaning) VALUES (?, ?)",
        [
            ("ACCC", "Accepted Settlement Completed (creditor account credited)"),
            ("ACSP", "Accepted Settlement In Process"),
            ("RJCT", "Rejected"),
            ("RTND", "Returned (internal display code for pacs.004 return)"),
            ("PDNG", "Pending"),
            ("RCVD", "Received"),
            ("CANC", "Cancelled"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_reason_code (reason_code, reason_name, category, is_iso) VALUES (?, ?, ?, ?)",
        [
            ("AC03", "Invalid Creditor Account Number", "ACCOUNT", 1),
            ("AC04", "Closed Account Number", "ACCOUNT", 1),
            ("AM04", "Insufficient Funds", "AMOUNT", 1),
            ("RR04", "Regulatory Reason", "REGULATORY", 1),
            ("BE04", "Missing Creditor Address", "ACCOUNT", 1),
            ("AG01", "Transaction Forbidden", "REGULATORY", 1),
            ("NARR", "Narrative", "OTHER", 1),
            ("FRAD", "Fraudulent Origin (cancellation reason)", "SCREENING", 1),
            ("AGNT", "Incorrect Agent (cancellation reason)", "NETWORK", 1),
            ("NACK_TIMEOUT", "No acknowledgement within network SLA", "NETWORK", 0),
            ("CORR_ACK_OVERDUE", "Correspondent acknowledgement overdue (SLA)", "NETWORK", 0),
            ("SCREEN_MATCH", "Potential sanctions match \u2014 analyst review", "SCREENING", 0),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_payment_status (status, state_label, badge_color, is_exception, is_final) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("COMPLETED", "Completed", "g", 0, 1),
            ("IN_PROGRESS", "Current State", "c", 0, 0),
            ("REJECTED", "Rejected", "r", 1, 1),
            ("RETURNED", "Returned", "v", 1, 1),
            ("INVESTIGATION", "Investigation", "a", 1, 0),
            ("FAILED", "Failed", "r", 1, 1),
            ("CANCELLED", "Cancelled", "n", 1, 1),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_payment_state "
        "(state, sort_order, iso_tx_status, payment_status, is_terminal, is_exception, is_happy_path, badge_color, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("INITIATED", 1, "RCVD", "IN_PROGRESS", 0, 0, 1, "c", "Instruction received"),
            ("ACCEPTED", 2, "ACSP", "IN_PROGRESS", 0, 0, 1, "c", "Structural & business validation passed"),
            ("SCREENING", 3, "PDNG", "IN_PROGRESS", 0, 0, 1, "a", "Sanctions / AML screening"),
            ("SENT", 4, "ACSP", "IN_PROGRESS", 0, 0, 1, "c", "Interbank message sent"),
            ("IN_TRANSIT", 5, "ACSP", "IN_PROGRESS", 0, 0, 1, "c", "At / towards the correspondent"),
            ("CORRESPONDENT_PROCESSING", 6, "ACSP", "IN_PROGRESS", 0, 0, 1, "c", "Intermediary processing"),
            ("SETTLEMENT_PENDING", 7, "ACSP", "IN_PROGRESS", 0, 0, 1, "c", "Settlement instruction accepted"),
            ("COMPLETED", 8, "ACCC", "COMPLETED", 1, 0, 1, "g", "Final acceptance / settlement confirmed"),
            ("REJECTED", 9, "RJCT", "REJECTED", 1, 1, 0, "r", "Rejected by an agent"),
            ("RETURNED", 10, "RTND", "RETURNED", 1, 1, 0, "v", "Funds returned via pacs.004"),
            ("INVESTIGATION", 11, "PDNG", "INVESTIGATION", 0, 1, 0, "a", "Case open (camt.056)"),
            ("FAILED", 12, "RJCT", "FAILED", 1, 1, 0, "r", "Unresolved failure / SLA breach"),
            ("CANCELLED", 13, "CANC", "CANCELLED", 1, 1, 0, "n", "Cancelled (camt.029 CNCL)"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO ref_state_transition (from_state, to_state, transition_type) VALUES (?, ?, ?)",
        [
            (None, "INITIATED", "NORMAL"),
            ("INITIATED", "ACCEPTED", "NORMAL"),
            ("ACCEPTED", "SCREENING", "NORMAL"),
            ("SCREENING", "SENT", "NORMAL"),
            ("SENT", "IN_TRANSIT", "NORMAL"),
            ("IN_TRANSIT", "CORRESPONDENT_PROCESSING", "NORMAL"),
            ("CORRESPONDENT_PROCESSING", "SETTLEMENT_PENDING", "NORMAL"),
            ("SETTLEMENT_PENDING", "COMPLETED", "NORMAL"),
            ("SENT", "SETTLEMENT_PENDING", "NORMAL"),
            ("IN_TRANSIT", "SETTLEMENT_PENDING", "NORMAL"),
            ("IN_TRANSIT", "INVESTIGATION", "EXCEPTION"),
            ("INVESTIGATION", "FAILED", "EXCEPTION"),
            ("IN_TRANSIT", "REJECTED", "EXCEPTION"),
            ("REJECTED", "RETURNED", "EXCEPTION"),
            ("SCREENING", "INVESTIGATION", "EXCEPTION"),
            ("INVESTIGATION", "SCREENING", "RECOVERY"),
            ("CORRESPONDENT_PROCESSING", "REJECTED", "EXCEPTION"),
            ("SETTLEMENT_PENDING", "REJECTED", "EXCEPTION"),
            ("INITIATED", "FAILED", "FAILURE"),
            ("ACCEPTED", "FAILED", "FAILURE"),
            ("SCREENING", "FAILED", "FAILURE"),
            ("SENT", "FAILED", "FAILURE"),
            ("IN_TRANSIT", "FAILED", "FAILURE"),
            ("CORRESPONDENT_PROCESSING", "FAILED", "FAILURE"),
            ("SETTLEMENT_PENDING", "FAILED", "FAILURE"),
            ("REJECTED", "FAILED", "FAILURE"),
            ("INITIATED", "CANCELLED", "CANCEL"),
            ("ACCEPTED", "CANCELLED", "CANCEL"),
            ("SCREENING", "CANCELLED", "CANCEL"),
            ("SENT", "CANCELLED", "CANCEL"),
            ("IN_TRANSIT", "CANCELLED", "CANCEL"),
            ("INVESTIGATION", "CANCELLED", "CANCEL"),
        ],
    )


def seed_customers(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO customer (customer_id, customer_name, segment, country_code) VALUES (?, ?, ?, ?)",
        [
            ("C-ACME-MANUFACTURING", "Acme Manufacturing", "Global Corporate", "US"),
            ("C-APEX-ENERGY", "Apex Energy", "Global Corporate", "US"),
            ("C-ATLAS-MANUFACTURING", "Atlas Manufacturing", "Corporate", "US"),
            ("C-BLUEPEAK-FOODS", "BluePeak Foods", "Global Corporate", "US"),
            ("C-CRESCENT-BANK", "Crescent Bank", "Corporate", "US"),
            ("C-GLOBAL-TRADING-AG", "Global Trading AG", "Global Corporate", "CH"),
            ("C-HSBC-TREASURY-LTD", "HSBC Treasury Ltd", "Financial Institutions", "US"),
            ("C-MERCURY-TRADING", "Mercury Trading", "Commercial", "US"),
            ("C-METRO-SERVICES-INC", "Metro Services Inc", "Commercial", "US"),
            ("C-NORTHSTAR-BANK", "Northstar Bank", "Financial Institutions", "US"),
            ("C-NORTHSTAR-SERVICES", "Northstar Services", "Commercial", "US"),
            ("C-NOVA-RETAIL", "Nova Retail", "Commercial", "US"),
            ("C-ORION-CAPITAL", "Orion Capital", "Commercial", "US"),
            ("C-SUMMIT-LOGISTICS", "Summit Logistics", "Public Sector", "US"),
            ("C-TREASURY-SERVICES-INC", "Treasury Services Inc", "Financial Institutions", "US"),
            ("C-US-RETAIL-CORP", "US Retail Corp", "Commercial", "US"),
            ("C-VERTEX-COMPONENTS", "Vertex Components", "Global Corporate", "US"),
        ],
    )


def seed_legal_entities(conn: sqlite3.Connection) -> None:
    """The deploying bank's own regulated booking entities \u2014 NOT counterparty banks (see `bank`).
    Deliberately generic names/codes: whoever deploys this app renames/adds rows for their own group.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO legal_entity (legal_entity_id, name, country_code, lei) VALUES (?, ?, ?, ?)",
        [
            ("ENT-US", "US Banking Entity", "US", None),
            ("ENT-UK", "UK Banking Entity", "GB", None),
        ],
    )



def seed_simulation_definitions(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sim_scenario (scenario_code, scenario_name, description, expected_outcome) VALUES (?, ?, ?, ?)",
        [
            ("HAPPY", "Happy Path",
             "Validates \u2192 screens \u2192 creates pacs.008 \u2192 routes through correspondent/intermediary \u2192 settles \u2192 emits pacs.002 ACCC.",
             "COMPLETED"),
            ("TIMEOUT", "Correspondent Timeout",
             "The payment reaches the correspondent but receives no acknowledgement. The simulator opens an investigation rather than falsely marking the payment complete.",
             "FAILED"),
            ("AC03", "AC03 Return",
             "Beneficiary account validation fails. The original credit transfer is rejected and a return path is created using pacs.004.",
             "RETURNED"),
            ("SCREEN", "Screening Hold",
             "Potential screening match creates a hold. Human review is required before release; the simulator then continues with a cleared payment.",
             "COMPLETED"),
        ],
    )
    steps = [
        ("HAPPY", 1, "INITIATED", "Payment instruction accepted", "pain.001"),
        ("HAPPY", 2, "ACCEPTED", "Structural and business validation passed", "pain.001"),
        ("HAPPY", 3, "SCREENING", "Sanctions / AML screening cleared", "\u2014"),
        ("HAPPY", 4, "SENT", "pacs.008 created and sent to originating network", "pacs.008"),
        ("HAPPY", 5, "IN_TRANSIT", "Correspondent acknowledged receipt", "pacs.002 ACSP"),
        ("HAPPY", 6, "CORRESPONDENT_PROCESSING", "Intermediary processing completed", "pacs.002 ACSP"),
        ("HAPPY", 7, "SETTLEMENT_PENDING", "Settlement instruction accepted", "pacs.002 ACSP"),
        ("HAPPY", 8, "COMPLETED", "Final acceptance / settlement confirmed", "pacs.002 ACCC"),
        ("TIMEOUT", 1, "INITIATED", "Payment instruction accepted", "pain.001"),
        ("TIMEOUT", 2, "ACCEPTED", "Validation passed", "pain.001"),
        ("TIMEOUT", 3, "SCREENING", "Screening cleared", "\u2014"),
        ("TIMEOUT", 4, "SENT", "pacs.008 sent to correspondent", "pacs.008"),
        ("TIMEOUT", 5, "IN_TRANSIT", "No correspondent acknowledgement within SLA", "\u2014"),
        ("TIMEOUT", 6, "INVESTIGATION", "Investigation opened; correspondent case prepared", "camt.056"),
        ("TIMEOUT", 7, "FAILED", "Network SLA breached; payment remains unresolved", "pacs.002 RJCT"),
        ("AC03", 1, "INITIATED", "Payment instruction accepted", "pain.001"),
        ("AC03", 2, "ACCEPTED", "Validation passed", "pain.001"),
        ("AC03", 3, "SCREENING", "Screening cleared", "\u2014"),
        ("AC03", 4, "SENT", "pacs.008 sent to beneficiary bank", "pacs.008"),
        ("AC03", 5, "IN_TRANSIT", "Creditor account validation requested", "pacs.002 ACSP"),
        ("AC03", 6, "REJECTED", "Creditor account invalid \u00b7 AC03", "pacs.002 RJCT"),
        ("AC03", 7, "RETURNED", "Return instruction generated", "pacs.004"),
        ("SCREEN", 1, "INITIATED", "Payment instruction accepted", "pain.001"),
        ("SCREEN", 2, "ACCEPTED", "Validation passed", "pain.001"),
        ("SCREEN", 3, "SCREENING", "Potential screening match detected", "\u2014"),
        ("SCREEN", 4, "INVESTIGATION", "Payment placed on hold for analyst review", "camt.056"),
        ("SCREEN", 5, "SCREENING", "Human review cleared the payment", "\u2014"),
        ("SCREEN", 6, "SENT", "pacs.008 released to network", "pacs.008"),
        ("SCREEN", 7, "SETTLEMENT_PENDING", "Settlement pending", "pacs.002 ACSP"),
        ("SCREEN", 8, "COMPLETED", "Final acceptance / settlement confirmed", "pacs.002 ACCC"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO sim_scenario_step (scenario_code, step_no, state, description, iso_message) VALUES (?, ?, ?, ?, ?)",
        steps,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO sim_speed (speed_code, step_delay_ms) VALUES (?, ?)",
        [("1x", 1700), ("2x", 850), ("5x", 340), ("10x", 170)],
    )
    latency_rows = []
    profile = {
        "XB": [0, 800, 2200, 1400, 42000, 65000, 38000, 12000, 20000, 45000, 900000, 1800000, 60000],
        "INSTANT": [0, 120, 350, 180, 400, 300, 600, 450, 500, 900, 300000, 20000, 2000],
        "WIRE": [0, 400, 1500, 900, 1800, 2000, 3500, 2500, 3000, 9000, 600000, 120000, 5000],
        "BATCH": [0, 60000, 120000, 600000, 1800000, 900000, 7200000, 3600000, 3600000, 7200000, 3600000, 7200000, 600000],
    }
    states = ["INITIATED", "ACCEPTED", "SCREENING", "SENT", "IN_TRANSIT", "CORRESPONDENT_PROCESSING",
              "SETTLEMENT_PENDING", "COMPLETED", "REJECTED", "RETURNED", "INVESTIGATION", "FAILED", "CANCELLED"]
    for latency_class, values in profile.items():
        for state, base_ms in zip(states, values):
            latency_rows.append((latency_class, state, base_ms))
    conn.executemany(
        "INSERT OR IGNORE INTO sim_latency_profile (latency_class, state, base_ms) VALUES (?, ?, ?)",
        latency_rows,
    )


def seed_personas(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO persona (mode_code, domain_label, title, description, mode_label, default_agent_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("CBCC", "CBCC", "Cross-Border Correspondent Banking",
             "Correspondent chains, nostro funding, FX, sanctions screening and gpi-style tracking on SWIFT CBPR+ "
             "(ISO 20022 pacs.008 / pacs.009 / pacs.004).",
             "CBCC \u2014 Cross-Border Correspondent Banking", "correspondent"),
            ("DOME", "DOME", "Domestic Payments Operations",
             "US instant, batch and high-value rails \u2014 RTP and FedNow (24\u00d77, final), ACH batches and "
             "Fedwire Funds (ISO 20022 since Jul 2025) with intraday liquidity.",
             "DOME \u2014 Domestic Payments Operations", "liquidity"),
            ("ALL", "ENTERPRISE", "Enterprise / All",
             "Unified command view over every rail, legal entity and corridor \u2014 exceptions, investigations, "
             "reconciliation and Kuber AI operations.",
             "Enterprise / All \u2014 Cross-Border and Domestic payment universe", "orchestrator"),
        ],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO persona_tag (mode_code, tag, sort_order) VALUES (?, ?, ?)",
        [
            ("CBCC", "SWIFT CBPR+", 1), ("CBCC", "Correspondent Banking", 2), ("CBCC", "Cross-border", 3),
            ("CBCC", "FX", 4), ("CBCC", "Screening", 5), ("CBCC", "Nostro", 6),
            ("DOME", "RTP", 1), ("DOME", "ACH", 2), ("DOME", "FedNow", 3),
            ("DOME", "Fedwire", 4), ("DOME", "Domestic", 5), ("DOME", "Liquidity", 6),
            ("ALL", "All rails", 1), ("ALL", "ISO 20022", 2), ("ALL", "Exceptions", 3),
            ("ALL", "Investigations", 4), ("ALL", "Reconciliation", 5), ("ALL", "Kuber AI", 6),
        ],
    )


def seed_field_catalogue(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO field_category (category_name, sort_order) VALUES (?, ?)",
        [
            ("Payment Identity", 1), ("Payment Classification", 2), ("ISO 20022", 3), ("Debtor", 4),
            ("Creditor / Beneficiary", 5), ("Correspondent / Intermediary Banking", 6), ("Amount & Currency", 7),
            ("Dates & Timing", 8), ("Payment Status", 9), ("Status Reason / Exception", 10),
            ("Clearing & Settlement", 11), ("Screening / Risk", 12), ("Customer / Business", 13),
            ("Fees & FX", 14), ("Liquidity", 15), ("Mandates & Obligations", 16), ("Investigations", 17),
            ("Reconciliation", 18), ("Operations", 19), ("AI Agent Activity", 20),
        ],
    )
    # (field_id, display_name, category_name, domain_code, message_types, data_type, filter_type,
    #  groupable, description, example, source_column, iso_path_hint, synonyms)
    fields = [
        ("paymentId", "Payment ID", "Payment Identity", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "id", "text", 0, "Master simulator payment identifier", "PAY-CB-000001", "payment_id", None, []),
        ("uetr", "UETR", "Payment Identity", "CBCC", ["pacs.008", "pacs.009", "pacs.004", "pacs.002"], "uuid", "text", 0, "Unique End-to-End Transaction Reference (UUIDv4) \u2014 gpi tracking key", "a9f4c1e8-2b3d-\u2026", "uetr", "CdtTrfTxInf/PmtId/UETR", ["tracking reference", "gpi reference"]),
        ("swiftTxnId", "Swift Transaction ID", "Payment Identity", "CBCC", ["pacs.008", "pacs.009"], "id", "text", 0, "Network transaction reference", "SWF-88419372", "swift_txn_id", None, []),
        ("businessMsgId", "Business Message ID", "Payment Identity", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "id", "text", 0, "Business Application Header BizMsgIdr (head.001)", "BIZ-20260924-00191", "business_msg_id", "AppHdr/BizMsgIdr", []),
        ("instructionId", "Instruction ID", "Payment Identity", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "id", "text", 0, "PmtId/InstrId \u2014 point-to-point reference", "INST-774991", "instruction_id", "PmtId/InstrId", []),
        ("endToEndId", "End-to-End ID", "Payment Identity", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "id", "text", 0, "PmtId/EndToEndId \u2014 carried unchanged end to end", "E2E-INV-492018", "end_to_end_id", "PmtId/EndToEndId", []),
        ("domain", "Domain", "Payment Classification", "ALL", [], "enum", "enum", 1, "CBCC (cross-border) or DOME (domestic)", "CBCC", "domain", None, []),
        ("rail", "Rail", "Payment Classification", "ALL", [], "enum", "enum", 1, "Payment rail / network", "SWIFT CBPR+", "rail", None, []),
        ("paymentType", "Payment Type", "Payment Classification", "ALL", [], "enum", "enum", 1, "Business payment type", "Cross-Border Customer Credit Transfer", "payment_type", None, []),
        ("scheme", "Scheme", "Payment Classification", "ALL", [], "enum", "enum", 1, "Scheme / usage guideline", "CBPR+", "scheme", None, []),
        ("purpose", "Category Purpose", "Payment Classification", "ALL", ["pacs.008", "pain.001"], "enum", "enum", 1, "CtgyPurp code", "SUPP", "purpose", "PmtTpInf/CtgyPurp/Cd", []),
        ("messageType", "Message Type", "ISO 20022", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "enum", "enum", 1, "Primary / current ISO 20022 message", "pacs.008", "message_type", None, []),
        ("mtEquivalent", "MT Equivalent", "ISO 20022", "CBCC", [], "enum", "enum", 1, "Legacy MT semantic equivalent (mapping only)", "MT103", "mt_equivalent", None, []),
        ("isoVersion", "Message Version", "ISO 20022", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "Message definition version", "pacs.008.001.08", "iso_version", None, []),
        ("addressFormat", "Postal Address Format", "ISO 20022", "CBCC", ["pacs.008", "pacs.009"], "enum", "enum", 1, "STRUCTURED / HYBRID / UNSTRUCTURED \u2014 CBPR+ SR2026 readiness", "STRUCTURED", "address_format", None, []),
        ("debtor", "Debtor Name", "Debtor", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "Dbtr/Nm", "Global Trading AG", "debtor", "Dbtr/Nm", ["payer", "originator", "ordering customer"]),
        ("ultimateDebtor", "Ultimate Debtor", "Debtor", "ALL", ["pacs.008", "pain.001"], "text", "text", 0, "UltmtDbtr/Nm", "Global Trading Holdings AG", "ultimate_debtor", "UltmtDbtr/Nm", []),
        ("debtorAccount", "Debtor Account", "Debtor", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "DbtrAcct/Id", "US64CHAS00012345678", "debtor_account", None, []),
        ("debtorCountry", "Debtor Country", "Debtor", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "enum", "enum", 1, "Dbtr/PstlAdr/Ctry", "US", "debtor_country", None, []),
        ("creditor", "Beneficiary", "Creditor / Beneficiary", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "Cdtr/Nm \u2014 beneficiary", "HSBC Treasury Ltd", "creditor", "Cdtr/Nm", ["beneficiary", "payee"]),
        ("ultimateCreditor", "Ultimate Creditor", "Creditor / Beneficiary", "ALL", ["pacs.008", "pain.001"], "text", "text", 0, "UltmtCdtr/Nm", "HSBC Treasury Ltd", "ultimate_creditor", "UltmtCdtr/Nm", []),
        ("creditorAccount", "Creditor Account", "Creditor / Beneficiary", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "CdtrAcct/Id (IBAN or proprietary)", "GB29MIDL40051512345678", "creditor_account", None, []),
        ("creditorCountry", "Creditor Country", "Creditor / Beneficiary", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "enum", "enum", 1, "Cdtr/PstlAdr/Ctry", "GB", "creditor_country", None, []),
        ("debtorAgent", "Debtor Agent", "Correspondent / Intermediary Banking", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "DbtrAgt \u2014 originating bank", "JPMorgan Chase Bank N.A.", "debtor_agent", "DbtrAgt/FinInstnId/BICFI", []),
        ("correspondent", "Correspondent Bank", "Correspondent / Intermediary Banking", "CBCC", ["pacs.008", "pacs.009"], "text", "text", 0, "Instructed agent / correspondent hop", "Citibank N.A.", "correspondent", None, ["correspondent bank", "instructed agent"]),
        ("intermediary", "Intermediary Bank", "Correspondent / Intermediary Banking", "CBCC", ["pacs.008", "pacs.009"], "text", "text", 0, "IntrmyAgt1", "Deutsche Bank AG", "intermediary", "IntrmyAgt1", []),
        ("creditorAgent", "Creditor Agent", "Correspondent / Intermediary Banking", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "CdtrAgt \u2014 beneficiary bank", "HSBC Bank plc", "creditor_agent", "CdtrAgt/FinInstnId/BICFI", []),
        ("bic", "BIC", "Correspondent / Intermediary Banking", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "text", "text", 0, "Debtor agent BICFI", "CHASUS33", "bic", None, []),
        ("hops", "Network Hops", "Correspondent / Intermediary Banking", "ALL", [], "number", "range", 0, "Populated agents in the chain", "4", "hops", None, []),
        ("amount", "Amount", "Amount & Currency", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "amount", "range", 0, "Instructed / interbank settlement amount", "18,420,000.00", "amount", "IntrBkSttlmAmt", []),
        ("debitCcy", "Debit Currency", "Amount & Currency", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "enum", "enum", 1, "Debit account currency", "USD", "debit_ccy", None, []),
        ("creditCcy", "Credit Currency", "Amount & Currency", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "enum", "enum", 1, "Credit currency to beneficiary", "EUR", "credit_ccy", None, []),
        ("creditAmount", "Credit Amount", "Amount & Currency", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "amount", "range", 0, "Amount credited after FX", "17,008,999.00", "credit_amount", None, []),
        ("settlementDate", "Settlement Date", "Dates & Timing", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "date", "date", 0, "IntrBkSttlmDt", "2026-09-24", "settlement_date", "IntrBkSttlmDt", []),
        ("initiatedAt", "Initiated At", "Dates & Timing", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "datetime", "date", 0, "Initiation timestamp", "2026-09-24 09:42:11", "initiated_at", None, []),
        ("completedAt", "Completed At", "Dates & Timing", "ALL", ["pain.001", "pacs.008", "pacs.009", "pacs.004", "pacs.002"], "datetime", "date", 0, "Terminal completion timestamp (blank unless COMPLETED)", "2026-09-24 10:00:03", "completed_at", None, []),
        ("duration", "Processing Duration", "Dates & Timing", "ALL", [], "text", "text", 0, "End-to-end processing time", "17m 42s", "duration", None, []),
        ("status", "Payment Status", "Payment Status", "ALL", [], "status", "enum", 1, "Authoritative current state (simulator-driven)", "IN_PROGRESS", "status", None, []),
        ("ultimateStatus", "Ultimate Payment Status", "Payment Status", "ALL", [], "status", "enum", 1, "Final business outcome", "IN_PROGRESS", "ultimate_status", None, []),
        ("intermediateStatus", "Intermediate Payment Status", "Payment Status", "ALL", [], "status", "enum", 1, "Operational state machine position", "WAITING_CORRESPONDENT", "intermediate_status", None, ["current state", "sub-status"]),
        ("gpiStatus", "gpi Tracker Status", "Payment Status", "CBCC", ["pacs.002"], "enum", "enum", 1, "Tracker-style status (TxSts / reason)", "ACSP/G000", "gpi_status", None, []),
        ("statusReason", "Status Reason", "Status Reason / Exception", "ALL", ["pacs.002", "pacs.004"], "text", "text", 0, "StsRsnInf/Rsn/Cd with description", "AC03 \u2014 Invalid Creditor Account Number", "status_reason", "StsRsnInf/Rsn/Cd", []),
        ("exceptionType", "Exception Type", "Status Reason / Exception", "ALL", [], "enum", "enum", 1, "Exception classification", "BENEFICIARY_ACCOUNT", "exception_type", None, []),
        ("settlementMethod", "Method of Payment", "Clearing & Settlement", "ALL", ["pacs.008", "pacs.009"], "enum", "enum", 1, "SttlmMtd \u2014 INDA / INGA / COVE / CLRG", "INDA", "settlement_method", "GrpHdr/SttlmInf/SttlmMtd", ["method of payment"]),
        ("clearingSystem", "Clearing System", "Clearing & Settlement", "ALL", [], "enum", "enum", 1, "Clearing / settlement system", "CBPR+ / Nostro", "clearing_system", None, []),
        ("priority", "Priority", "Clearing & Settlement", "ALL", ["pacs.008"], "enum", "enum", 1, "InstrPrty / service level", "HIGH", "priority", None, []),
        ("screening", "Screening State", "Screening / Risk", "ALL", [], "status", "enum", 1, "Sanctions / AML screening state", "CLEARED", "screening", None, []),
        ("riskScore", "Risk Score", "Screening / Risk", "ALL", [], "number", "range", 0, "Synthetic fraud/AML score 0\u2013100", "12", "risk_score", None, []),
        ("segment", "Customer Segment", "Customer / Business", "ALL", [], "enum", "enum", 1, "Business segment", "Corporate", "segment", None, []),
        ("paymentInitiationDept", "Payment Initiation Department", "Customer / Business", "ALL", [], "enum", "enum", 1, "Initiating department", "Equity Operations", "payment_initiation_dept", None, ["initiating department"]),
        ("fxRate", "FX Rate", "Fees & FX", "CBCC", ["pacs.008"], "number", "range", 0, "XchgRate debit\u2192credit currency", "0.9234", "fx_rate", "XchgRate", []),
        ("charges", "Charge Bearer", "Fees & FX", "ALL", ["pacs.008"], "enum", "enum", 1, "ChrgBr \u2014 SHAR / DEBT / CRED", "SHAR", "charges", "ChrgBr", []),
        ("feeAmount", "Fees", "Fees & FX", "ALL", ["pacs.008"], "amount", "range", 0, "Total charges (USD)", "45.00", "fee_amount", None, []),
        ("nostro", "Nostro Account", "Liquidity", "ALL", [], "text", "text", 0, "Nostro / settlement account", "USD NOSTRO \u2022 004821", "nostro", None, ["settlement account"]),
        ("liquidityState", "Liquidity State", "Liquidity", "ALL", [], "status", "enum", 1, "Intraday liquidity for the settlement account", "AVAILABLE", "liquidity_state", None, []),
        ("mandateRef", "Mandate / Obligation", "Mandates & Obligations", "ALL", [], "text", "text", 0, "Linked mandate or obligation", "MND-GTA-1800", "mandate_ref", None, []),
        ("obligationDue", "Obligation Due", "Mandates & Obligations", "ALL", [], "text", "text", 0, "Obligation cut-off", "18:00", "obligation_due", None, []),
        ("investigationId", "Investigation ID", "Investigations", "ALL", ["camt.056", "camt.029"], "id", "text", 0, "Case reference", "INV-90244", "investigation_id", None, []),
        ("reconState", "Reconciliation State", "Reconciliation", "ALL", ["camt.053", "camt.054"], "status", "enum", 1, "Nostro / ledger reconciliation state", "MATCHED", "recon_state", None, []),
        ("owner", "Operations Owner", "Operations", "ALL", [], "text", "text", 0, "Accountable operations owner", "M. Patel", "owner", None, []),
        ("slaState", "SLA State", "Operations", "ALL", [], "status", "enum", 1, "SLA health", "ON_TRACK", "sla_state", None, []),
        ("aiRecommendation", "AI Recommendation", "AI Agent Activity", "ALL", [], "text", "text", 0, "Latest Kuber recommendation", "Validate beneficiary account before re-submit", "ai_recommendation", None, []),
        ("aiAgent", "AI Agent", "AI Agent Activity", "ALL", [], "enum", "enum", 1, "Kuber agent that produced the latest recommendation", "Payment Investigator", "ai_agent", None, []),
        ("aiConfidence", "AI Confidence", "AI Agent Activity", "ALL", [], "number", "range", 0, "Recommendation confidence", "0.94", "ai_confidence", None, []),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO field_catalogue "
        "(field_id, display_name, category_name, domain_code, message_types, data_type, filter_type, "
        "groupable, description, example, source_column, iso_path_hint, synonyms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (f[0], f[1], f[2], f[3], json.dumps(f[4]), f[5], f[6], f[7], f[8], f[9], f[10], f[11], json.dumps(f[12]))
            for f in fields
        ],
    )


def seed_column_presets(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO column_preset (preset_code, preset_label, mode_code) VALUES (?, ?, ?)",
        [
            ("DEFAULT_ALL", "Default (ALL)", "ALL"),
            ("DEFAULT_CBCC", "Default (CBCC)", "CBCC"),
            ("DEFAULT_DOME", "Default (DOME)", "DOME"),
            ("Operations", "Operations", None),
            ("ISO 20022", "ISO 20022", None),
            ("Network", "Network", None),
            ("Risk & Liquidity", "Risk & Liquidity", None),
        ],
    )
    fields_by_preset = {
        "DEFAULT_ALL": ["paymentId", "rail", "messageType", "debtor", "creditor", "amount", "debitCcy", "status", "intermediateStatus", "settlementDate"],
        "DEFAULT_CBCC": ["paymentId", "uetr", "messageType", "debtor", "correspondent", "creditor", "amount", "debitCcy", "creditCcy", "status", "intermediateStatus"],
        "DEFAULT_DOME": ["paymentId", "rail", "scheme", "debtor", "creditor", "amount", "status", "liquidityState", "settlementDate"],
        "Operations": ["paymentId", "rail", "status", "intermediateStatus", "statusReason", "owner", "slaState", "investigationId", "reconState"],
        "ISO 20022": ["paymentId", "uetr", "messageType", "mtEquivalent", "businessMsgId", "instructionId", "endToEndId", "addressFormat", "status"],
        "Network": ["paymentId", "debtorAgent", "correspondent", "intermediary", "creditorAgent", "bic", "hops", "settlementMethod"],
        "Risk & Liquidity": ["paymentId", "amount", "debitCcy", "screening", "riskScore", "nostro", "liquidityState", "aiRecommendation"],
    }
    rows = [
        (preset_code, position, field_id)
        for preset_code, field_ids in fields_by_preset.items()
        for position, field_id in enumerate(field_ids, start=1)
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO column_preset_field (preset_code, position, field_id) VALUES (?, ?, ?)",
        rows,
    )


def seed_nl_query_vocabulary(conn: sqlite3.Connection) -> None:
    rows = [
        ("completed", "STATUS", "COMPLETED"), ("complete", "STATUS", "COMPLETED"), ("settled", "STATUS", "COMPLETED"),
        ("success", "STATUS", "COMPLETED"), ("successful", "STATUS", "COMPLETED"),
        ("rejected", "STATUS", "REJECTED"), ("reject", "STATUS", "REJECTED"), ("rjct", "STATUS", "REJECTED"),
        ("returned", "STATUS", "RETURNED"), ("return", "STATUS", "RETURNED"), ("returns", "STATUS", "RETURNED"),
        ("failed", "STATUS", "FAILED"), ("failure", "STATUS", "FAILED"),
        ("investigation", "STATUS", "INVESTIGATION"), ("investigations", "STATUS", "INVESTIGATION"),
        ("pending", "STATUS", "IN_PROGRESS"), ("in_progress", "STATUS", "IN_PROGRESS"),
        ("inprogress", "STATUS", "IN_PROGRESS"), ("stuck", "STATUS", "IN_PROGRESS"),
        ("cancelled", "STATUS", "CANCELLED"),
        ("swift", "RAIL", "SWIFT CBPR+"), ("cbpr", "RAIL", "SWIFT CBPR+"), ("cbpr+", "RAIL", "SWIFT CBPR+"),
        ("rtp", "RAIL", "RTP"), ("fednow", "RAIL", "FedNow"), ("ach", "RAIL", "ACH"),
        ("fedwire", "RAIL", "Fedwire"), ("wire", "RAIL", "Fedwire"), ("wires", "RAIL", "Fedwire"),
        ("status", "KEY_ALIAS", "status"), ("rail", "KEY_ALIAS", "rail"), ("ccy", "KEY_ALIAS", "ccy"),
        ("currency", "KEY_ALIAS", "ccy"), ("msg", "KEY_ALIAS", "msg"), ("type", "KEY_ALIAS", "msg"),
        ("bic", "KEY_ALIAS", "bic"), ("domain", "KEY_ALIAS", "domain"), ("country", "KEY_ALIAS", "country"),
        ("debtor", "KEY_ALIAS", "debtor"), ("beneficiary", "KEY_ALIAS", "creditor"), ("creditor", "KEY_ALIAS", "creditor"),
        ("id", "KEY_ALIAS", "id"), ("uetr", "KEY_ALIAS", "uetr"), ("owner", "KEY_ALIAS", "owner"),
        ("reason", "KEY_ALIAS", "reason"),
        ("show", "STOPWORD", ""), ("me", "STOPWORD", ""), ("all", "STOPWORD", ""), ("payments", "STOPWORD", ""),
        ("payment", "STOPWORD", ""), ("with", "STOPWORD", ""), ("in", "STOPWORD", ""), ("the", "STOPWORD", ""),
        ("for", "STOPWORD", ""), ("from", "STOPWORD", ""), ("to", "STOPWORD", ""), ("of", "STOPWORD", ""),
        ("and", "STOPWORD", ""), ("that", "STOPWORD", ""), ("are", "STOPWORD", ""), ("is", "STOPWORD", ""),
        ("which", "STOPWORD", ""), ("list", "STOPWORD", ""), ("find", "STOPWORD", ""), ("where", "STOPWORD", ""),
        ("a", "STOPWORD", ""), ("an", "STOPWORD", ""), ("by", "STOPWORD", ""), ("today", "STOPWORD", ""),
        ("yesterday", "STOPWORD", ""), ("any", "STOPWORD", ""),
        ("cbcc", "DOMAIN", "CBCC"), ("cross-border", "DOMAIN", "CBCC"), ("dome", "DOMAIN", "DOME"),
        ("domestic", "DOMAIN", "DOME"),
        ("usd", "CURRENCY", "USD"), ("eur", "CURRENCY", "EUR"), ("gbp", "CURRENCY", "GBP"), ("jpy", "CURRENCY", "JPY"),
        ("inr", "CURRENCY", "INR"), ("sgd", "CURRENCY", "SGD"), ("chf", "CURRENCY", "CHF"), ("cad", "CURRENCY", "CAD"),
        ("over", "AMOUNT_OP", "MIN"), ("above", "AMOUNT_OP", "MIN"), ("greater than", "AMOUNT_OP", "MIN"),
        ("more than", "AMOUNT_OP", "MIN"), (">", "AMOUNT_OP", "MIN"), (">=", "AMOUNT_OP", "MIN"),
        ("at least", "AMOUNT_OP", "MIN"),
        ("under", "AMOUNT_OP", "MAX"), ("below", "AMOUNT_OP", "MAX"), ("less than", "AMOUNT_OP", "MAX"),
        ("<", "AMOUNT_OP", "MAX"), ("<=", "AMOUNT_OP", "MAX"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO nl_query_vocabulary (token, token_kind, maps_to) VALUES (?, ?, ?)",
        rows,
    )


def seed_mandates(conn: sqlite3.Connection) -> None:
    """Mandate identity (who/what/due time/linked payment) is genuine configured reference
    data \u2014 an obligation is instructed, not derived from payment traffic. Its live
    readiness/coverage is computed from the linked payment's actual state at query time
    instead (app/drilldown/queries.py), never stored here."""
    conn.executemany(
        "INSERT OR IGNORE INTO mandate_obligation "
        "(mandate_id, display_no, customer_id, description, due_time, linked_payment_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("MND-GTA-1800", 1, "C-GLOBAL-TRADING-AG", "USD liquidity top-up", "18:00", "PAY-CB-000001"),
            ("MND-ACM-1630", 2, "C-ACME-MANUFACTURING", "supplier batch", "16:30", "PAY-TIMEOUT-000001"),
            ("MND-HSB-1700", 3, "C-HSBC-TREASURY-LTD", "nostro funding", "17:00", "PAY-FEDWIRE-000001"),
            ("MND-NSB-1500", 4, "C-NORTHSTAR-BANK", "return investigation", "15:00", "PAY-RETURN-000001"),
        ],
    )


def seed_reference_data(db_path=None) -> None:
    from app.db.connection import DB_PATH, get_connection

    conn = get_connection(db_path or DB_PATH)
    try:
        seed_banks(conn)
        seed_domains_and_rails(conn)
        seed_payment_type_and_iso(conn)
        seed_state_machine(conn)
        seed_customers(conn)
        seed_legal_entities(conn)
        seed_simulation_definitions(conn)
        seed_personas(conn)
        seed_field_catalogue(conn)
        seed_column_presets(conn)
        seed_nl_query_vocabulary(conn)
        seed_mandates(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    seed_reference_data()
    print("Reference/config data seeded.")
