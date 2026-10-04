"""kuber_mcp \u2014 verifies each read-only tool's output matches the same underlying
query functions Payment 360/Drilldown already use (get_payment_detail/get_mandates),
and that no write tool exists on the server at all."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.ai_agents.mcp_servers import kuber_mcp
from app.db.connection import get_connection
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
from app.drilldown.queries import get_mandates
from app.payments.detail import get_payment_detail
from app.seed.generator import seed_payments

PAYMENT_ID = "PAY-CB-000001"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    run_migration(path)
    seed_reference_data(path)
    seed_payments(path)
    kuber_mcp.use_db_path(path)
    yield path
    kuber_mcp.use_db_path(None)


def test_no_write_tools_exposed() -> None:
    tools = asyncio.run(kuber_mcp.mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert tool_names == {
        "get_payment_identity", "get_payment_lineage", "get_payment_events",
        "get_payment_state", "get_risk_screening", "get_liquidity_position",
        "get_mandate_for_payment", "get_iso_message",
        "get_reconciliation_state", "get_mt_mx_mapping",
    }
    assert not any(n.startswith(("create_", "update_", "post_", "delete_", "set_")) for n in tool_names)


def test_get_payment_identity_matches_detail(db_path: Path) -> None:
    result = kuber_mcp.get_payment_identity(PAYMENT_ID)
    conn = get_connection(db_path)
    expected = get_payment_detail(conn, PAYMENT_ID)["payment"]
    conn.close()
    assert result["paymentId"] == expected["paymentId"] == PAYMENT_ID
    assert result["debtor"] == expected["debtor"]
    assert result["amount"] == expected["amount"]


def test_get_payment_lineage_matches_detail(db_path: Path) -> None:
    result = kuber_mcp.get_payment_lineage(PAYMENT_ID)
    conn = get_connection(db_path)
    expected = get_payment_detail(conn, PAYMENT_ID)["hops"]
    conn.close()
    assert result["hops"] == expected
    assert len(result["hops"]) >= 2  # PAY-CB-000001 has correspondent + intermediary


def test_get_payment_events_matches_detail(db_path: Path) -> None:
    result = kuber_mcp.get_payment_events(PAYMENT_ID)
    conn = get_connection(db_path)
    expected = get_payment_detail(conn, PAYMENT_ID)["events"]
    conn.close()
    assert result["events"] == expected


def test_get_payment_state_and_risk_and_liquidity(db_path: Path) -> None:
    conn = get_connection(db_path)
    expected = get_payment_detail(conn, PAYMENT_ID)["payment"]
    conn.close()

    state = kuber_mcp.get_payment_state(PAYMENT_ID)
    assert state["status"] == expected["status"]
    assert state["ultimateStatus"] == expected["ultimateStatus"]

    risk = kuber_mcp.get_risk_screening(PAYMENT_ID)
    assert risk["screening"] == expected["screening"]
    assert risk["riskScore"] == expected["riskScore"]

    liquidity = kuber_mcp.get_liquidity_position(PAYMENT_ID)
    assert liquidity["liquidityState"] == expected["liquidityState"]
    assert liquidity["nostro"] == expected["nostro"]


def test_get_mandate_for_payment_matches_drilldown(db_path: Path) -> None:
    result = kuber_mcp.get_mandate_for_payment(PAYMENT_ID)
    conn = get_connection(db_path)
    expected = next(m for m in get_mandates(conn) if m["linkedPaymentId"] == PAYMENT_ID)
    conn.close()
    assert result == expected
    assert result["mandateId"] == "MND-GTA-1800"


def test_get_mandate_for_payment_none_when_unlinked(db_path: Path) -> None:
    result = kuber_mcp.get_mandate_for_payment("PAY-SCREEN-000001")
    assert result["mandate"] is None


def test_get_iso_message_matches_detail(db_path: Path) -> None:
    conn = get_connection(db_path)
    expected_xml = get_payment_detail(conn, PAYMENT_ID)["messages"]["pacs.008"]
    conn.close()
    result = kuber_mcp.get_iso_message(PAYMENT_ID, "pacs.008")
    assert result["xml"] == expected_xml


def test_get_iso_message_unknown_type_returns_error() -> None:
    result = kuber_mcp.get_iso_message(PAYMENT_ID, "pacs.999")
    assert "error" in result


def test_get_reconciliation_state_matches_detail(db_path: Path) -> None:
    conn = get_connection(db_path)
    expected = get_payment_detail(conn, PAYMENT_ID)["payment"]["reconState"]
    conn.close()
    result = kuber_mcp.get_reconciliation_state(PAYMENT_ID)
    assert result["reconState"] == expected
    assert result["reconState"] in ("OPEN", "MATCHED", "EXCEPTION")


def test_get_mt_mx_mapping_known_type(db_path: Path) -> None:
    result = kuber_mcp.get_mt_mx_mapping("MT103")
    assert result == {"mtType": "MT103", "mxType": "pacs.008", "meaning": "Customer credit transfer"}


def test_get_mt_mx_mapping_unknown_type_returns_error(db_path: Path) -> None:
    result = kuber_mcp.get_mt_mx_mapping("MT999")
    assert "error" in result


def test_unknown_payment_id_returns_error_not_exception() -> None:
    for tool in (
        kuber_mcp.get_payment_identity, kuber_mcp.get_payment_lineage, kuber_mcp.get_payment_events,
        kuber_mcp.get_payment_state, kuber_mcp.get_risk_screening, kuber_mcp.get_liquidity_position,
        kuber_mcp.get_reconciliation_state,
    ):
        result = tool("PAY-DOES-NOT-EXIST")
        assert "error" in result
