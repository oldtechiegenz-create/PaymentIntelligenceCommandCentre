"""Config for the ai_agents MCP servers \u2014 domain name + port, so an eventual
mcp_client adapter can discover them the same way as the PAE reference project's
`MCP_ADAPTER_CONFIG` (config.py at that repo's root)."""
from __future__ import annotations

KUBER_MCP_DOMAIN = "kuber"
KUBER_MCP_PORT = 7020

MCP_ADAPTER_CONFIG = {
    KUBER_MCP_DOMAIN: f"http://localhost:{KUBER_MCP_PORT}",
}
