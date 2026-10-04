"""kuber_mcp \u2014 read-only MCP tool server for the Kuber Agentic Operations screen.

Every tool is a thin, read-only projection of `payments/detail.py::get_payment_detail()`
(the same data Payment 360 renders) or `drilldown/queries.py::get_mandates()` \u2014 no
tool here ever writes to the database. There is deliberately no create/update/post tool
in this file at all, so an agent calling into this server structurally cannot mutate a
payment; the Approve/Decline actions in the UI stay outside the MCP tool surface,
handled by a plain REST endpoint the human clicks.

Domain: kuber · Port: 7020 (see config.py)

Run:
    uv run python -m app.ai_agents.mcp_servers.kuber_mcp   # -> http://localhost:7020/mcp
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

from app.ai_agents.mcp_servers.config import KUBER_MCP_DOMAIN, KUBER_MCP_PORT
from app.db.connection import DB_PATH, get_connection
from app.drilldown.queries import get_mandates
from app.payments.detail import get_payment_detail

mcp = FastMCP(KUBER_MCP_DOMAIN)

# Test-only override, same pattern as app/simulation/ticker.py — points tools at a
# temp DB instead of the real one.
_db_path_override: Optional[Path] = None


def use_db_path(path: Optional[Path]) -> None:
    global _db_path_override
    _db_path_override = path


def current_db_path() -> Path:
    """The DB path this server's tools are actually reading from right now \u2014 so
    callers scoring confidence/evidence outside the tool calls (e.g. the LangGraph)
    can stay consistent with whichever DB (real or a test's temp DB) is active."""
    return _db_path_override or DB_PATH


def _detail(payment_id: str) -> Optional[dict]:
    conn = get_connection(_db_path_override or DB_PATH)
    try:
        return get_payment_detail(conn, payment_id)
    finally:
        conn.close()


@mcp.tool()
def get_payment_identity(payment_id: str) -> dict[str, Any]:
    """Core identity fields for a payment: IDs, rail/scheme, debtor/creditor, amount."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    p = detail["payment"]
    keys = ("paymentId", "uetr", "swiftTxnId", "rail", "scheme", "domain", "messageType",
            "debtor", "creditor", "amount", "debitCcy", "country")
    return {k: p.get(k) for k in keys}


@mcp.tool()
def get_payment_lineage(payment_id: str) -> dict[str, Any]:
    """The real populated agent-hop chain (debtor agent -> correspondent -> intermediary
    -> creditor agent) for a payment, i.e. its network lineage."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    return {"payment_id": payment_id, "hops": detail["hops"]}


@mcp.tool()
def get_payment_events(payment_id: str) -> dict[str, Any]:
    """The payment's real lifecycle event stream (state, description, ISO message,
    actor, timestamp) in order."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    return {"payment_id": payment_id, "events": detail["events"]}


@mcp.tool()
def get_payment_state(payment_id: str) -> dict[str, Any]:
    """Current status/state for a payment: status, ultimate status, intermediate
    status, and the reason code/text if it's in an exception state."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    p = detail["payment"]
    keys = ("status", "ultimateStatus", "intermediateStatus", "statusReason", "slaState")
    return {"payment_id": payment_id, **{k: p.get(k) for k in keys}}


@mcp.tool()
def get_risk_screening(payment_id: str) -> dict[str, Any]:
    """Sanctions/AML screening state and synthetic risk score for a payment."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    p = detail["payment"]
    return {"payment_id": payment_id, "screening": p.get("screening"), "riskScore": p.get("riskScore")}


@mcp.tool()
def get_liquidity_position(payment_id: str) -> dict[str, Any]:
    """Intraday liquidity state and nostro/settlement account for a payment."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    p = detail["payment"]
    return {"payment_id": payment_id, "liquidityState": p.get("liquidityState"), "nostro": p.get("nostro")}


@mcp.tool()
def get_mandate_for_payment(payment_id: str) -> dict[str, Any]:
    """The mandate/obligation linked to this payment, if any \u2014 due time, coverage,
    readiness, derived live from the linked payment's current state."""
    conn = get_connection(_db_path_override or DB_PATH)
    try:
        match = next((m for m in get_mandates(conn) if m["linkedPaymentId"] == payment_id), None)
    finally:
        conn.close()
    return match or {"payment_id": payment_id, "mandate": None}


@mcp.tool()
def get_iso_message(payment_id: str, msg_type: str) -> dict[str, Any]:
    """The generated ISO 20022 XML for one message type on this payment (e.g.
    'pacs.008', 'pacs.002', 'camt.056'). Use get_payment_events first to see which
    message types actually occurred in this payment's history."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    xml = detail["messages"].get(msg_type)
    if xml is None:
        return {"error": f"Unknown msg_type {msg_type!r}; available: {sorted(detail['messages'])}"}
    return {"payment_id": payment_id, "msg_type": msg_type, "xml": xml}


@mcp.tool()
def get_reconciliation_state(payment_id: str) -> dict[str, Any]:
    """Nostro/ledger reconciliation state for a payment: OPEN, MATCHED, or EXCEPTION."""
    detail = _detail(payment_id)
    if detail is None:
        return {"error": f"Unknown payment_id {payment_id!r}"}
    return {"payment_id": payment_id, "reconState": detail["payment"].get("reconState")}


@mcp.tool()
def get_mt_mx_mapping(mt_type: str) -> dict[str, Any]:
    """Legacy SWIFT MT \u2192 ISO 20022 MX message-type mapping (e.g. 'MT103' -> 'pacs.008').
    Use this to explain MT/MX equivalence questions; it is static reference data, not
    specific to any one payment."""
    conn = get_connection(_db_path_override or DB_PATH)
    try:
        row = conn.execute(
            "SELECT mt_type, mx_type, meaning FROM ref_mt_mx_mapping WHERE mt_type = ?", (mt_type,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": f"Unknown mt_type {mt_type!r}"}
    return {"mtType": row["mt_type"], "mxType": row["mx_type"], "meaning": row["meaning"]}


if __name__ == "__main__":
    mcp.run(transport="http", port=KUBER_MCP_PORT)
