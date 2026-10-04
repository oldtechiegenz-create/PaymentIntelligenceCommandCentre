"""Thin MCP client bridge \u2014 connects to a FastMCP server (over HTTP, or in-memory
for tests, by passing the FastMCP instance itself as `transport`) and exposes its
tools as LangChain StructuredTool objects for bind_tools().

Hand-rolled instead of using langchain-mcp-adapters, which pins an incompatible
mcp<2 SDK against our installed fastmcp 4.x / mcp 2.x stack.
"""
from __future__ import annotations

from typing import Any

from fastmcp import Client
from langchain_core.tools import StructuredTool


async def load_mcp_tools(client: Client, names: list[str] | None = None) -> list[StructuredTool]:
    """Lists tools on an already-connected fastmcp Client (optionally filtered to
    `names`) and wraps each as a LangChain StructuredTool that calls back into the
    MCP server \u2014 so a specialist only ever sees the read-only tools it's scoped to."""
    mcp_tools = await client.list_tools()
    if names is not None:
        mcp_tools = [t for t in mcp_tools if t.name in names]

    langchain_tools = []
    for t in mcp_tools:
        async def _call(_tool_name: str = t.name, **kwargs: Any) -> Any:
            try:
                result = await client.call_tool(_tool_name, kwargs)
            except Exception as exc:  # transport/protocol failure, not an app-level {"error": ...}
                return {"error": f"{_tool_name} call failed: {exc}"}
            return result.data

        langchain_tools.append(
            StructuredTool.from_function(
                coroutine=_call,
                name=t.name,
                description=t.description or "",
                args_schema=t.input_schema,
            )
        )
    return langchain_tools
