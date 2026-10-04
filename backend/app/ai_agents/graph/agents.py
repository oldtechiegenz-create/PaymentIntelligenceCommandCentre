"""Specialist roster (v1: a smaller starting set, not the POC's full 7) \u2014 each
specialist gets a curated, pre-bound subset of kuber_mcp's read-only tools rather than
the full toolset, matching the reference project's per-agent tools_renderer() pattern.
More specialists (Correspondent, Liquidity, Reconciliation, ISO Interpreter) can be
added the same way once this pattern is proven."""
from __future__ import annotations

SPECIALISTS: dict[str, dict[str, object]] = {
    "orchestrator": {
        "label": "Orchestrator",
        "role": "Routes intents and composes evidence across the whole payment.",
        "tool_names": ["get_payment_identity", "get_payment_state", "get_payment_lineage", "get_mandate_for_payment"],
    },
    "investigator": {
        "label": "Payment Investigator",
        "role": "Exceptions, returns, and root cause analysis.",
        "tool_names": ["get_payment_state", "get_payment_events", "get_iso_message", "get_mandate_for_payment"],
    },
    "risk": {
        "label": "Risk & Screening Agent",
        "role": "Sanctions, AML, and fraud signals.",
        "tool_names": ["get_payment_identity", "get_risk_screening"],
    },
    "liquidity": {
        "label": "Liquidity Agent",
        "role": "Nostro and intraday liquidity, funding position, and mandate/obligation readiness.",
        "tool_names": ["get_liquidity_position", "get_mandate_for_payment", "get_payment_identity", "get_payment_state"],
    },
}
