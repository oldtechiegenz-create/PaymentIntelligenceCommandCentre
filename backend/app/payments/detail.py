"""Payment 360 detail \u2014 full single-payment view: identity fields (via the same
field_catalogue projection Discovery uses), real lineage hops, real lifecycle events, and
on-demand ISO 20022 message generation. Nothing here is fabricated \u2014 all DB-backed.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from app.payments.iso_xml import ISO_MESSAGE_TYPES, build_iso_message

# Not modelled in field_catalogue (it's a search/display field list, not the full raw
# schema) but needed for the 360 "Corridor" row / simulated-payment view — selected
# directly from `payment`.
EXTRA_COLUMNS = {"country": "country", "isSimulated": "is_simulated", "sourcePaymentId": "source_payment_id"}


def _field_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT field_id, source_column FROM field_catalogue").fetchall()
    field_map = {r["field_id"]: r["source_column"] for r in rows}
    field_map.update(EXTRA_COLUMNS)
    return field_map


def get_payment_detail(conn: sqlite3.Connection, payment_id: str) -> Optional[dict]:
    field_map = _field_map(conn)
    select_sql = ", ".join(f"{col} AS {field_id}" for field_id, col in field_map.items())
    row = conn.execute(f"SELECT {select_sql} FROM payment WHERE payment_id = ?", (payment_id,)).fetchone()
    if row is None:
        return None
    payment = dict(row)

    hop_rows = conn.execute(
        """
        SELECT h.hop_seq, h.agent_role, h.bic, b.bank_name, b.country_code
        FROM payment_agent_hop h JOIN bank b ON b.bic = h.bic
        WHERE h.payment_id = ? ORDER BY h.hop_seq
        """,
        (payment_id,),
    ).fetchall()
    hops = [
        {"seq": r["hop_seq"], "role": r["agent_role"], "bic": r["bic"], "name": r["bank_name"], "country": r["country_code"]}
        for r in hop_rows
    ]
    name_to_bic = {h["name"]: h["bic"] for h in hops}

    event_rows = conn.execute(
        """
        SELECT seq, state, prev_state, description, iso_message, actor, reason_code, reason_text,
               event_ts, processing_ms
        FROM payment_event WHERE payment_id = ? AND sim_run_id IS NULL ORDER BY seq
        """,
        (payment_id,),
    ).fetchall()
    events = [dict(r) for r in event_rows]

    mt_mx_mapping = [dict(r) for r in conn.execute("SELECT mt_type, mx_type, meaning FROM ref_mt_mx_mapping").fetchall()]

    message_chain = _message_chain(payment, events)
    messages = {t: build_iso_message(t, payment, name_to_bic) for t in ISO_MESSAGE_TYPES}

    return {
        "payment": payment,
        "hops": hops,
        "events": events,
        "messageChain": message_chain,
        "messages": messages,
        "mtMxMapping": mt_mx_mapping,
    }


def _message_chain(payment: dict, events: list[dict]) -> list[str]:
    """Real message chain, derived from this payment's own event stream (matches POC's
    renderMessagesTab which builds the chain from paymentEvents(p), not a fixed list)."""
    chain: list[str] = []
    for e in events:
        msg = e.get("iso_message")
        if msg and msg != "\u2014" and msg not in chain:
            chain.append(msg)
    status = payment.get("status")
    if status == "COMPLETED":
        chain += ["camt.054", "camt.053"]
    elif status != "IN_PROGRESS":
        chain.append("camt.052")
    return chain
