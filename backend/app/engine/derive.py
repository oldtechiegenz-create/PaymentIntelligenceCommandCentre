"""Derived/computed payment fields — the single place these are (re)computed from a payment's
other fields. Called after every state change so the derived columns never go stale.
"""
from __future__ import annotations

import re

MT_EQUIVALENT_BY_MESSAGE_TYPE = {
    "pacs.008": "MT103",
    "pacs.009": "MT202",
    "pacs.004": "MT103 RETN",
    "pain.001": "MT101",
}


def reason_code(reason: str | None) -> str:
    """Leading [A-Z0-9_]{3,} token of a status reason, e.g. 'AC03 \u2014 Invalid...' -> 'AC03'."""
    if not reason:
        return ""
    m = re.match(r"^([A-Z0-9_]{3,})", reason)
    return m.group(1) if m else ""


def derive(p: dict) -> dict:
    """Recompute mt_equivalent, hops, credit_amount, gpi_status, exception_type, sla_state in place."""
    domain = p.get("domain")
    status = p.get("status")

    p["mt_equivalent"] = MT_EQUIVALENT_BY_MESSAGE_TYPE.get(p.get("message_type"), "\u2014") if domain == "CBCC" else "\u2014"

    p["hops"] = sum(
        1 for a in (p.get("debtor_agent"), p.get("correspondent"), p.get("intermediary"), p.get("creditor_agent")) if a
    )

    fx_rate = p.get("fx_rate") or 1
    p["credit_amount"] = round((p.get("amount") or 0) * fx_rate, 2)

    rc = reason_code(p.get("status_reason"))
    if domain != "CBCC":
        p["gpi_status"] = "\u2014"
    elif status == "COMPLETED":
        p["gpi_status"] = "ACCC"
    elif status in ("REJECTED", "FAILED"):
        p["gpi_status"] = f"RJCT/{rc or 'NARR'}"
    elif status == "RETURNED":
        p["gpi_status"] = f"RTND/{rc or 'NARR'}"
    elif status == "INVESTIGATION":
        p["gpi_status"] = "ACSP/G003"
    elif (p.get("sim_state") or p.get("intermediate_status")) == "SETTLEMENT_PENDING":
        p["gpi_status"] = "ACSP/G002"
    elif p.get("intermediary") or p.get("correspondent"):
        p["gpi_status"] = "ACSP/G000"
    else:
        p["gpi_status"] = "ACSP/G001"

    if status in ("REJECTED", "RETURNED"):
        p["exception_type"] = "BENEFICIARY_ACCOUNT" if rc == "AC03" else "REJECTION"
    elif status == "FAILED":
        p["exception_type"] = "NETWORK_SLA"
    elif status == "INVESTIGATION":
        screening = str(p.get("screening") or "")
        reason_text = str(p.get("status_reason") or "")
        p["exception_type"] = "SCREENING_HOLD" if ("MATCH" in screening or "screen" in reason_text) else "CORRESPONDENT_SLA"
    else:
        p["exception_type"] = "\u2014"

    if status == "COMPLETED":
        p["sla_state"] = "MET"
    elif status == "IN_PROGRESS":
        p["sla_state"] = "AT_RISK" if p.get("intermediate_status") == "WAITING_CORRESPONDENT" else "ON_TRACK"
    elif status == "INVESTIGATION":
        p["sla_state"] = "AT_RISK"
    else:
        p["sla_state"] = "BREACHED"

    return p
