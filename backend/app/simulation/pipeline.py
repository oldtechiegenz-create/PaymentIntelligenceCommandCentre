"""process_event \u2014 the single shared entrypoint for writing a *live* payment event.

Called by both the REST ingestion endpoint (app/simulation/api.py) and the
guided-simulation ticker (app/simulation/ticker.py), so a manually-pushed ISO
event and a ticked scenario step are guaranteed to update a payment identically
\u2014 same engine/apply_event.py call, same payment_event row shape, same
WebSocket broadcast. Reserved for genuinely new/live events only \u2014 seeding
(app/seed/generator.py) never calls this, see architecture decisions memory.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.engine.apply_event import actor_for, apply_event
from app.engine.derive import derive, reason_code as extract_reason_code
from app.engine.latency import format_ms
from app.seed.generator import PAYMENT_COLUMNS, legal_entity_for


def _load_payment(conn: sqlite3.Connection, payment_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def _next_seq_and_prev_state(conn: sqlite3.Connection, payment_id: str, sim_run_id: Optional[str]) -> tuple[int, Optional[str]]:
    row = conn.execute(
        "SELECT seq, state FROM payment_event WHERE payment_id = ? AND sim_run_id IS ? ORDER BY seq DESC LIMIT 1",
        (payment_id, sim_run_id),
    ).fetchone()
    return (row["seq"] + 1, row["state"]) if row else (1, None)


def _new_payment_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT payment_id FROM payment WHERE payment_id LIKE 'PAY-LIVE-%' ORDER BY payment_id DESC LIMIT 1"
    ).fetchone()
    next_n = int(row["payment_id"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"PAY-LIVE-{next_n:06d}"


def insert_new_payment(conn: sqlite3.Connection, fields: dict) -> dict:
    """Create a brand-new payment row from identity fields, applying only plain
    (non-engine) defaults \u2014 the first event still goes through apply_event()."""
    payment = {c: fields.get(c) for c in PAYMENT_COLUMNS}
    payment["payment_id"] = fields.get("payment_id") or _new_payment_id(conn)
    payment["message_type"] = fields.get("msg_type") or "pain.001"
    payment["business_msg_id"] = fields.get("business_msg_id") or f"{payment['payment_id']}-BMI"
    payment["instruction_id"] = fields.get("instruction_id") or f"{payment['payment_id']}-INSTR"
    payment["end_to_end_id"] = fields.get("end_to_end_id") or f"{payment['payment_id']}-E2E"
    payment["legal_entity_id"] = fields.get("legal_entity_id") or legal_entity_for(fields.get("domain"), fields.get("debtor_country"))
    payment["credit_ccy"] = fields.get("credit_ccy") or fields.get("debit_ccy")
    payment["fx_rate"] = fields.get("fx_rate") or 1
    payment["charges"] = fields.get("charges") or "SHAR"
    payment["fee_amount"] = fields.get("fee_amount") or 0
    payment["priority"] = fields.get("priority") or "NORM"
    payment["settlement_date"] = fields.get("settlement_date") or fields.get("initiated_at")
    payment["status"] = "IN_PROGRESS"
    payment["ultimate_status"] = "IN_PROGRESS"
    payment["intermediate_status"] = "INITIATED"
    payment["screening"] = "CLEARED"
    payment["recon_state"] = "OPEN"
    payment["liquidity_state"] = fields.get("liquidity_state") or "AVAILABLE"
    payment["is_simulated"] = 0
    derive(payment)

    placeholders = ", ".join(["?"] * len(PAYMENT_COLUMNS))
    conn.execute(
        f"INSERT INTO payment ({', '.join(PAYMENT_COLUMNS)}) VALUES ({placeholders})",
        tuple(payment.get(c) for c in PAYMENT_COLUMNS),
    )
    return payment


def _write_back_payment(conn: sqlite3.Connection, payment: dict) -> None:
    columns = [c for c in PAYMENT_COLUMNS if c != "payment_id"]
    set_clause = ", ".join(f"{c} = ?" for c in columns)
    conn.execute(
        f"UPDATE payment SET {set_clause}, updated_at = datetime('now') WHERE payment_id = ?",
        tuple(payment.get(c) for c in columns) + (payment["payment_id"],),
    )


def process_event(
    conn: sqlite3.Connection,
    *,
    payment_id: Optional[str],
    state: str,
    msg_type: str,
    tx_sts: Optional[str] = None,
    reason_code: Optional[str] = None,
    reason_text_override: Optional[str] = None,
    orgnl_end_to_end_id: Optional[str] = None,
    orgnl_uetr: Optional[str] = None,
    description: Optional[str] = None,
    failure_injected: bool = False,
    new_payment_fields: Optional[dict] = None,
    sim_run_id: Optional[str] = None,
    processing_ms: int = 0,
    duration_text: Optional[str] = None,
    wall_elapsed_ms: Optional[int] = None,
    source: str = "NETWORK",
) -> dict:
    """Apply one live event to `payment_id` (or a brand-new payment) and broadcast it.

    Caller owns the surrounding transaction (commit/rollback) \u2014 this only
    executes statements, it never calls publisher.notify() itself. The caller
    must commit first, then publish `result["notification"]` — broadcasting
    before a commit could tell clients about data a later rollback erases.
    """
    if payment_id is None:
        payment = insert_new_payment(conn, new_payment_fields or {})
        payment_id = payment["payment_id"]
    else:
        payment = _load_payment(conn, payment_id)
        if payment is None:
            raise ValueError(f"Unknown payment_id {payment_id!r}")

    seq, prev_state = _next_seq_and_prev_state(conn, payment_id, sim_run_id)
    event_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if reason_text_override is not None:
        reason_text = reason_text_override
    else:
        reason_row = (
            conn.execute("SELECT reason_name FROM ref_reason_code WHERE reason_code = ?", (reason_code,)).fetchone()
            if reason_code
            else None
        )
        reason_text = f"{reason_code} \u2014 {reason_row['reason_name']}" if reason_row else (reason_code or "\u2014")

    event = {
        "seq": seq,
        "state": state,
        "prev_state": prev_state,
        "description": description or state.replace("_", " ").title(),
        "iso_message": f"{msg_type} {tx_sts}" if tx_sts else msg_type,
        "reason": reason_text,
        "event_ts": event_ts,
        "processing_ms": processing_ms,
        "failure_injected": failure_injected,
    }

    apply_event(
        payment, event,
        duration_text=duration_text if duration_text is not None else format_ms(processing_ms),
        original_message_type=payment.get("message_type"),
    )
    _write_back_payment(conn, payment)

    actor = actor_for(state, payment, prev_state)
    iso_payload = json.dumps(
        {
            "msg_type": msg_type,
            "tx_sts": tx_sts,
            "reason_code": reason_code,
            "orgnl_end_to_end_id": orgnl_end_to_end_id,
            "orgnl_uetr": orgnl_uetr,
        }
    )
    cursor = conn.execute(
        "INSERT INTO payment_event (payment_id, sim_run_id, seq, state, prev_state, description, iso_message, "
        "actor, reason_code, reason_text, event_ts, processing_ms, wall_elapsed_ms, failure_injected, source, iso_payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            payment_id, sim_run_id, seq, state, prev_state, event["description"], event["iso_message"],
            actor, extract_reason_code(reason_text) or None, reason_text if reason_text != "\u2014" else None,
            event_ts, processing_ms, wall_elapsed_ms, int(failure_injected), source, iso_payload,
        ),
    )
    event_id = cursor.lastrowid

    notification = {
        "type": "payment_event",
        "event_id": event_id,
        "payment_id": payment_id,
        "sim_run_id": sim_run_id,
        "seq": seq,
        "state": state,
        "prev_state": prev_state,
        "status": payment["status"],
        "iso_message": event["iso_message"],
        "event_ts": event_ts,
    }

    return {"event_id": event_id, "payment": payment, "notification": notification}
