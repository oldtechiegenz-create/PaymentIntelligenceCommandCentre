"""Tool-assignment invariant \u2014 every specialist must have a non-empty, curated tool
subset (never the full unscoped toolset, never zero tools), and every tool name it
references must actually exist on kuber_mcp, so a typo'd name fails loudly here
instead of silently resolving to an empty bound-tools list at runtime."""
from __future__ import annotations

import asyncio

from app.ai_agents.graph.agents import SPECIALISTS
from app.ai_agents.mcp_servers import kuber_mcp


def test_every_specialist_has_at_least_one_tool() -> None:
    for key, cfg in SPECIALISTS.items():
        assert len(cfg["tool_names"]) > 0, f"{key!r} has no tools assigned"


def test_every_specialist_tool_name_exists_on_kuber_mcp() -> None:
    real_tool_names = {t.name for t in asyncio.run(kuber_mcp.mcp.list_tools())}
    for key, cfg in SPECIALISTS.items():
        unknown = set(cfg["tool_names"]) - real_tool_names
        assert not unknown, f"{key!r} references unknown tool(s): {unknown}"


def test_no_specialist_gets_the_full_unscoped_toolset() -> None:
    real_tool_names = {t.name for t in asyncio.run(kuber_mcp.mcp.list_tools())}
    for key, cfg in SPECIALISTS.items():
        assert set(cfg["tool_names"]) != real_tool_names, f"{key!r} is bound to every tool \u2014 should be curated, not unscoped"
