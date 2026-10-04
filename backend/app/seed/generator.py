"""Loads the 68 canonical + generated payments and their ~450 events into the DB.

Source data lives in `app/seed/data/*.json` (extracted once from the reference schema's seed
file, folded into this app's own flat schema) \u2014 this module is fully self-contained at
runtime; it never reads anything outside `app/seed/data/`.

Field derivation rules below were verified against the POC's actual `genPayment()` source
(payment_intelligence_command_center.html) rather than guessed:
  - status is only directly knowable from the payment's own recorded `intermediate_status`
    because genPayment() sets intermediate_status equal to status for every status except
    IN_PROGRESS (several sub-states) and COMPLETED (SETTLED/ACCC) \u2014 see status_from_intermediate().
  - ultimate_status is `status`, except INVESTIGATION maps to IN_PROGRESS (mirrors genPayment()
    exactly, and also matches the one canonical payment that needed this, PAY-TIMEOUT-000001).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.engine.derive import derive
from app.engine.latency import fnv1a_hash

DATA_DIR = Path(__file__).parent / "data"

# Not modeled as DB tables (see architecture notes) \u2014 small enough to hardcode here.
OWNER_DISPLAY_NAME = {
    "u.achen": "A. Chen", "u.dschmidt": "D. Schmidt", "u.jalvarez": "J. Alvarez",
    "u.knakamura": "K. Nakamura", "u.lmoreau": "L. Moreau", "u.mpatel": "M. Patel",
    "u.rokafor": "R. Okafor", "u.siyer": "S. Iyer", "u.supervisor": "Ops Supervisor",
}
NOSTRO_DISPLAY_NAME = {
    "ACC-0001": "USD NOSTRO \u2022 004821", "ACC-0002": "GBP NOSTRO \u2022 110937",
    "ACC-0003": "TCH RTP JOINT ACCT \u2022 7781", "ACC-0004": "FED MASTER ACCT \u2022 0021",
    "ACC-0005": "EUR NOSTRO \u2022 975574", "ACC-0006": "CHF NOSTRO \u2022 914627",
    "ACC-0007": "SGD NOSTRO \u2022 569864", "ACC-0008": "CAD NOSTRO \u2022 568434",
    "ACC-0009": "JPY NOSTRO \u2022 346095", "ACC-0010": "CAD NOSTRO \u2022 439538",
    "ACC-0011": "USD NOSTRO \u2022 689959", "ACC-0012": "GBP NOSTRO \u2022 422185",
    "ACC-0013": "USD NOSTRO \u2022 119483", "ACC-0014": "JPY NOSTRO \u2022 920037",
    "ACC-0015": "EUR NOSTRO \u2022 693607", "ACC-0016": "USD NOSTRO \u2022 520777",
    "ACC-0017": "EUR NOSTRO \u2022 445821", "ACC-0018": "CAD NOSTRO \u2022 313317",
    "ACC-0019": "SGD NOSTRO \u2022 366727", "ACC-0020": "EUR NOSTRO \u2022 898396",
    "ACC-0021": "USD NOSTRO \u2022 489972", "ACC-0022": "EUR NOSTRO \u2022 200053",
    "ACC-0023": "CHF NOSTRO \u2022 717152", "ACC-0024": "GBP NOSTRO \u2022 251447",
    "ACC-0025": "GBP NOSTRO \u2022 494739", "ACC-0026": "SGD NOSTRO \u2022 237763",
}
CANONICAL_AI_OVERRIDES = {
    "PAY-CB-000001": ("Escalate correspondent acknowledgement", "Correspondent Agent", 0.94),
    "PAY-RETURN-000001": ("Validate beneficiary account before re-submit", "Payment Investigator", 0.91),
    "PAY-TIMEOUT-000001": ("Chase Citibank N.A. via investigation request; hold re-send", "Correspondent Agent", 0.88),
    "PAY-SCREEN-000001": ("Close screening case; document analyst rationale", "Risk & Screening Agent", 0.9),
}
CANONICAL_IDS = {
    "PAY-CB-000001", "PAY-RETURN-000001", "PAY-TIMEOUT-000001", "PAY-SCREEN-000001",
    "PAY-RTP-000001", "PAY-ACH-000001", "PAY-FEDNOW-000001", "PAY-FEDWIRE-000001",
}


def status_from_intermediate(intermediate_status: str) -> str:
    if intermediate_status in ("SETTLED", "ACCC"):
        return "COMPLETED"
    if intermediate_status in ("COMPLETED", "REJECTED", "RETURNED", "FAILED", "INVESTIGATION", "CANCELLED"):
        return intermediate_status
    return "IN_PROGRESS"


def ai_fields(payment_id: str, status: str, intermediate_status: str, status_reason: str | None, screening: str | None) -> tuple[str, str, float]:
    if payment_id in CANONICAL_AI_OVERRIDES:
        return CANONICAL_AI_OVERRIDES[payment_id]
    if payment_id in CANONICAL_IDS:
        return "No action required", "Orchestrator", 0.9

    reason = status_reason or ""
    if status == "COMPLETED":
        rec, agent = "No action required", "Reconciliation Agent"
    elif status == "IN_PROGRESS":
        rec = "Escalate correspondent acknowledgement" if intermediate_status == "WAITING_CORRESPONDENT" else "Monitor \u2014 within SLA"
        agent = "Orchestrator"
    elif status == "REJECTED":
        rec = "Validate beneficiary account before re-submit" if reason.startswith("AC03") else "Repair and re-submit after client confirmation"
        agent = "Payment Investigator"
    elif status == "RETURNED":
        rec, agent = "Credit return to debtor and notify client", "Payment Investigator"
    elif status == "INVESTIGATION":
        rec = "Analyst review of screening alert" if screening == "POTENTIAL MATCH" else "Chase correspondent via investigation request"
        agent = "Correspondent Agent"
    elif status == "FAILED":
        rec, agent = "Open network incident; confirm no duplicate before re-send", "Orchestrator"
    else:
        rec, agent = "No action required", "Orchestrator"

    confidence = round(0.78 + (fnv1a_hash(payment_id) % 200) / 1000, 2)
    return rec, agent, confidence


def _load_json(name: str) -> list[dict]:
    return json.loads((DATA_DIR / f"{name}.json").read_text())


def legal_entity_for(domain: str, debtor_country: str | None) -> str:
    """DOME rails (RTP/FedNow/ACH/Fedwire) are US-only \u2014 always the US entity. CBCC payments are
    booked by whichever entity is closest to the debtor's jurisdiction."""
    if domain == "DOME":
        return "ENT-US"
    return "ENT-US" if (debtor_country or "US") == "US" else "ENT-UK"


def build_payments(conn: sqlite3.Connection) -> tuple[list[dict], dict[str, list[dict]]]:
    """Returns (payment_rows, hops_by_payment_id) fully mapped to this app's flat schema."""
    raw_payments = _load_json("payment")
    parties = _load_json("payment_party")
    hops = _load_json("payment_agent_hop")
    investigations = _load_json("investigation")

    bank_names = {r["bic"]: r["bank_name"] for r in conn.execute("SELECT bic, bank_name FROM bank")}
    customer_segment = {r["customer_id"]: r["segment"] for r in conn.execute("SELECT customer_id, segment FROM customer")}
    mandate_due_time = {r["mandate_id"]: r["due_time"] for r in conn.execute("SELECT mandate_id, due_time FROM mandate_obligation")}

    parties_by_payment: dict[str, dict[str, dict]] = {}
    for p in parties:
        parties_by_payment.setdefault(p["payment_id"], {})[p["party_role"]] = p

    hops_by_payment: dict[str, dict[str, dict]] = {}
    for h in hops:
        hops_by_payment.setdefault(h["payment_id"], {})[h["agent_role"]] = h

    investigation_by_payment = {i["payment_id"]: i["investigation_id"] for i in investigations}

    payment_rows: list[dict] = []
    hops_out: dict[str, list[dict]] = {}

    for raw in raw_payments:
        payment_id = raw["payment_id"]
        party = parties_by_payment.get(payment_id, {})
        hop = hops_by_payment.get(payment_id, {})
        debtor = party.get("DEBTOR", {})
        ult_debtor = party.get("ULTIMATE_DEBTOR", {})
        creditor = party.get("CREDITOR", {})
        ult_creditor = party.get("ULTIMATE_CREDITOR", {})

        def hop_name(role: str) -> str | None:
            h = hop.get(role)
            return bank_names.get(h["bic"]) if h else None

        intermediate_status = raw["record_intermediate_status"]
        status = status_from_intermediate(intermediate_status)
        ultimate_status = "IN_PROGRESS" if status == "INVESTIGATION" else status
        status_reason = raw["record_status_reason_text"] or "\u2014"
        screening = raw["record_screening_state"]
        ai_recommendation, ai_agent, ai_confidence = ai_fields(payment_id, status, intermediate_status, status_reason, screening)

        payment = {
            "payment_id": payment_id,
            "uetr": raw["uetr"] or None,
            "swift_txn_id": raw["swift_txn_id"] or None,
            "message_type": raw["message_type"],
            "business_msg_id": raw["business_msg_id"],
            "instruction_id": raw["instruction_id"],
            "end_to_end_id": raw["end_to_end_id"],
            "legal_entity_id": legal_entity_for(raw["domain_code"], debtor.get("country_code")),
            "domain": raw["domain_code"],
            "rail": raw["rail_code"],
            "payment_type": raw["payment_type"],
            "scheme": raw["scheme_code"],
            "country": raw["corridor"],
            "purpose": raw["purpose_code"],
            "iso_version": raw["iso_version"],
            "address_format": debtor.get("address_format"),
            "priority": raw["priority"],
            "settlement_method": raw["settlement_method"],
            "clearing_system": raw["clearing_system"],
            "customer_id": raw["customer_id"],
            "debtor": debtor.get("party_name"),
            "ultimate_debtor": ult_debtor.get("party_name"),
            "debtor_account": debtor.get("account_id"),
            "debtor_country": debtor.get("country_code"),
            "creditor": creditor.get("party_name"),
            "ultimate_creditor": ult_creditor.get("party_name"),
            "creditor_account": creditor.get("account_id"),
            "creditor_country": creditor.get("country_code"),
            "debtor_agent": hop_name("DEBTOR_AGENT"),
            "correspondent": hop_name("CORRESPONDENT"),
            "intermediary": hop_name("INTERMEDIARY"),
            "creditor_agent": hop_name("CREDITOR_AGENT"),
            "bic": (hop.get("DEBTOR_AGENT") or {}).get("bic"),
            "amount": raw["amount"],
            "debit_ccy": raw["debit_ccy"],
            "credit_ccy": raw["credit_ccy"],
            "fx_rate": raw["fx_rate"],
            "charges": raw["charge_bearer"],
            "fee_amount": raw["fee_amount_usd"],
            "settlement_date": raw["settlement_date"],
            "initiated_at": raw["initiated_at"],
            "completed_at": raw["record_completed_at"],
            "duration": raw["record_duration_text"],
            "sim_state": None,
            "status": status,
            "ultimate_status": ultimate_status,
            "intermediate_status": intermediate_status,
            "status_reason": status_reason,
            "screening": screening,
            "recon_state": raw["record_recon_state"],
            "segment": customer_segment.get(raw["customer_id"]),
            "payment_initiation_dept": raw["initiation_department"],
            "owner": OWNER_DISPLAY_NAME.get(raw["owner_user_id"], raw["owner_user_id"]),
            "nostro": NOSTRO_DISPLAY_NAME.get(raw["nostro_account_id"], raw["nostro_account_id"]),
            "liquidity_state": raw["liquidity_state"],
            "risk_score": raw["risk_score"],
            "investigation_id": investigation_by_payment.get(payment_id),
            "mandate_ref": raw["mandate_id"],
            "obligation_due": mandate_due_time.get(raw["mandate_id"]) if raw["mandate_id"] else None,
            "ai_recommendation": ai_recommendation,
            "ai_agent": ai_agent,
            "ai_confidence": ai_confidence,
            "is_simulated": 0,
            "source_payment_id": None,
        }
        derive(payment)
        payment_rows.append(payment)
        hops_out[payment_id] = sorted(hop.values(), key=lambda h: h["hop_seq"])

    return payment_rows, hops_out


def build_events() -> dict[str, list[dict]]:
    events_by_payment: dict[str, list[dict]] = {}
    for e in _load_json("payment_event"):
        events_by_payment.setdefault(e["payment_id"], []).append(e)
    for payment_id, events in events_by_payment.items():
        prev_state = None
        for seq, event in enumerate(events, start=1):
            event["seq"] = seq
            event["prev_state"] = prev_state
            prev_state = event["state"]
    return events_by_payment


PAYMENT_COLUMNS = [
    "payment_id", "uetr", "swift_txn_id", "message_type", "business_msg_id", "instruction_id",
    "end_to_end_id", "legal_entity_id", "domain", "rail", "payment_type", "scheme", "country", "purpose",
    "iso_version", "address_format", "priority", "settlement_method", "clearing_system",
    "customer_id", "debtor", "ultimate_debtor", "debtor_account", "debtor_country", "creditor",
    "ultimate_creditor", "creditor_account", "creditor_country", "debtor_agent",
    "correspondent", "intermediary", "creditor_agent", "bic", "hops", "amount", "debit_ccy",
    "credit_ccy", "credit_amount", "fx_rate", "charges", "fee_amount", "settlement_date",
    "initiated_at", "completed_at", "duration", "sim_state", "status", "ultimate_status",
    "intermediate_status", "status_reason", "exception_type", "screening", "gpi_status",
    "mt_equivalent", "sla_state", "segment", "payment_initiation_dept", "owner", "nostro",
    "liquidity_state", "risk_score", "investigation_id", "recon_state", "mandate_ref",
    "obligation_due", "ai_recommendation", "ai_agent", "ai_confidence",
    "is_simulated", "source_payment_id",
]


def seed_payments(db_path=None) -> None:
    from app.db.connection import DB_PATH, get_connection

    conn = get_connection(db_path or DB_PATH)
    try:
        payment_rows, hops_by_payment = build_payments(conn)
        events_by_payment = build_events()

        placeholders = ", ".join(["?"] * len(PAYMENT_COLUMNS))
        conn.executemany(
            f"INSERT OR IGNORE INTO payment ({', '.join(PAYMENT_COLUMNS)}) VALUES ({placeholders})",
            [tuple(p.get(c) for c in PAYMENT_COLUMNS) for p in payment_rows],
        )

        hop_rows = [
            (payment_id, hop["hop_seq"], hop["agent_role"], hop["bic"])
            for payment_id, hops in hops_by_payment.items()
            for hop in hops
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO payment_agent_hop (payment_id, hop_seq, agent_role, bic) VALUES (?, ?, ?, ?)",
            hop_rows,
        )

        event_rows = [
            (
                e["payment_id"], None, e["seq"], e["state"], e["prev_state"], e["description"],
                e["iso_message"], e["actor"], e["reason_code"], e["reason_text"], e["event_ts"],
                e["processing_ms"], e["source"],
            )
            for events in events_by_payment.values()
            for e in events
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO payment_event "
            "(payment_id, sim_run_id, seq, state, prev_state, description, iso_message, actor, "
            "reason_code, reason_text, event_ts, processing_ms, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            event_rows,
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    seed_payments()
    print("68 payments + hops + events seeded.")
