"""The single write path onto a payment's projected state \u2014 given a payment and one incoming
event, updates every authoritative field and re-derives the computed ones. Used by the seed
loader (Phase 3), the live ingestion endpoint and the simulation ticker (Phase 4/7): one
implementation, never a second copy.
"""
from __future__ import annotations

from app.engine.derive import derive

TERMINAL_OR_EXCEPTION_STATES = {"COMPLETED", "REJECTED", "RETURNED", "FAILED", "INVESTIGATION", "CANCELLED"}


def actor_for(state: str, payment: dict, prev_state: str | None) -> str:
    next_hop = payment.get("correspondent") or payment.get("intermediary") or payment.get("creditor_agent")
    dept = payment.get("payment_initiation_dept") or "Channel"
    owner = payment.get("owner") or "Ops"
    mapping = {
        "INITIATED": f"{payment.get('debtor')} \u00b7 {dept}",
        "ACCEPTED": f"{payment.get('debtor_agent')} \u00b7 Payment Hub",
        "SCREENING": f"Screening analyst \u00b7 {owner}" if prev_state == "INVESTIGATION" else "Sanctions / AML engine",
        "SENT": payment.get("debtor_agent"),
        "IN_TRANSIT": next_hop,
        "CORRESPONDENT_PROCESSING": payment.get("intermediary") or payment.get("correspondent") or payment.get("rail"),
        "SETTLEMENT_PENDING": (payment.get("clearing_system") or payment.get("rail"))
        if payment.get("domain") == "DOME"
        else payment.get("creditor_agent"),
        "COMPLETED": payment.get("creditor_agent"),
        "REJECTED": payment.get("creditor_agent"),
        "RETURNED": payment.get("creditor_agent"),
        "INVESTIGATION": f"Operations \u00b7 {owner}",
        "FAILED": "Network SLA monitor",
        "CANCELLED": payment.get("debtor_agent"),
    }
    return mapping.get(state, "\u2014")


def pay_status_from_state(state: str) -> str:
    return state if state in TERMINAL_OR_EXCEPTION_STATES else "IN_PROGRESS"


def apply_event(
    payment: dict,
    event: dict,
    *,
    duration_text: str,
    run_seq: int = 0,
    original_message_type: str | None = None,
) -> dict:
    """Apply one incoming event to `payment` in place, then re-derive computed fields.

    `event` needs: state, description, iso_message, reason (optional), event_ts,
    failure_injected (optional). `duration_text` is the already-formatted cumulative
    simulated latency for this run (see engine/latency.py::format_ms).
    """
    state = event["state"]
    description = event.get("description", "")

    payment["sim_state"] = state
    payment["status"] = pay_status_from_state(state)
    payment["intermediate_status"] = "SETTLED" if state == "COMPLETED" else state
    payment["ultimate_status"] = state if state in TERMINAL_OR_EXCEPTION_STATES - {"INVESTIGATION"} else "IN_PROGRESS"
    payment["status_reason"] = event.get("reason", "\u2014") if state in ("REJECTED", "RETURNED", "FAILED", "INVESTIGATION") else "\u2014"

    if state == "SCREENING":
        if "Potential" in description:
            payment["screening"] = "POTENTIAL MATCH"
        elif "Human" in description:
            payment["screening"] = "CLEARED (human review)"
        else:
            payment["screening"] = "CLEARED"
    if state == "INVESTIGATION" and "analyst" in description:
        payment["screening"] = "HOLD \u2014 POTENTIAL MATCH"

    payment["completed_at"] = event.get("event_ts") if state == "COMPLETED" else None
    payment["duration"] = duration_text

    if state == "RETURNED":
        payment["message_type"] = "pacs.004"
    elif original_message_type is not None and state == "INITIATED":
        payment["message_type"] = "pacs.008" if original_message_type == "pacs.004" else original_message_type

    if state == "INVESTIGATION" and not payment.get("investigation_id"):
        payment["investigation_id"] = f"INV-{93000 + run_seq}"

    payment["recon_state"] = (
        "MATCHED" if state == "COMPLETED" else ("EXCEPTION" if state in ("REJECTED", "RETURNED", "FAILED") else "OPEN")
    )

    inv_screen = state == "INVESTIGATION" and "analyst" in description
    iso_message = event.get("iso_message", "\u2014")
    ai_recommendation_by_state = {
        "INITIATED": "Monitor \u2014 within SLA",
        "ACCEPTED": "Monitor \u2014 within SLA",
        "SCREENING": "Route alert to screening analyst" if payment.get("screening") == "POTENTIAL MATCH" else "Monitor \u2014 within SLA",
        "SENT": "Monitor \u2014 awaiting correspondent ACK",
        "IN_TRANSIT": "Escalate correspondent acknowledgement" if iso_message == "\u2014" else "Monitor \u2014 within SLA",
        "CORRESPONDENT_PROCESSING": "Monitor \u2014 within SLA",
        "SETTLEMENT_PENDING": "Pre-stage camt.054 reconciliation",
        "COMPLETED": "Close case; reconcile camt.054",
        "REJECTED": "Validate beneficiary account before re-submit",
        "RETURNED": "Credit pacs.004 return to debtor and notify client",
        "INVESTIGATION": "Analyst review of screening alert \u2014 human decision required" if inv_screen else "Chase correspondent via investigation request",
        "FAILED": "Confirm failure root cause; verify no duplicate before re-send" if event.get("failure_injected") else "Open network incident; hold re-send pending camt.029",
    }
    payment["ai_recommendation"] = ai_recommendation_by_state.get(state, payment.get("ai_recommendation"))

    ai_agent_by_state = {
        "REJECTED": "Payment Investigator",
        "RETURNED": "Payment Investigator",
        "INVESTIGATION": "Risk & Screening Agent" if inv_screen else "Correspondent Agent",
        "FAILED": "Orchestrator",
        "COMPLETED": "Reconciliation Agent",
    }
    payment["ai_agent"] = ai_agent_by_state.get(state, "Orchestrator")

    derive(payment)
    return payment
