"""mcp_client bridge \u2014 verifies a transport-level failure from the MCP client
(not an application-level {"error": ...} dict a tool returns itself) degrades to a
{"error": ...} tool result instead of raising an exception into the graph runtime."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from app.ai_agents.mcp_client import load_mcp_tools
from app.ai_agents.mcp_servers import kuber_mcp
from app.db.migrate import run_migration
from app.db.seed_reference_data import seed_reference_data
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


@pytest.mark.asyncio
async def test_transport_failure_degrades_to_error_dict(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with Client(kuber_mcp.mcp) as client:
        tools = await load_mcp_tools(client, ["get_payment_identity"])
        tool = tools[0]

        async def _broken_call_tool(name: str, args: dict) -> None:
            raise RuntimeError("connection reset")

        monkeypatch.setattr(client, "call_tool", _broken_call_tool)

        result = await tool.coroutine(payment_id=PAYMENT_ID)

    assert "error" in result
    assert "connection reset" in result["error"]


@pytest.mark.asyncio
async def test_successful_call_is_unaffected(db_path: Path) -> None:
    async with Client(kuber_mcp.mcp) as client:
        tools = await load_mcp_tools(client, ["get_payment_identity"])
        result = await tools[0].coroutine(payment_id=PAYMENT_ID)

    assert result["paymentId"] == PAYMENT_ID
