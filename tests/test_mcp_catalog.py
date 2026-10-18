"""The live gateway must retain the entire MCP catalog independently of chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent import mcp


async def test_loads_full_catalog_even_with_retired_filter_settings(monkeypatch):
    import langchain.mcp

    tools = [SimpleNamespace(name=name) for name in ("listAddresses", "searchOffers", "futureTool")]
    monkeypatch.setenv("MCP_TOOLS", "listAddresses")
    monkeypatch.setenv("MCP_EXCLUDE_TOOLS", "searchOffers")
    monkeypatch.setattr(mcp, "Client", lambda *args, **kwargs: object())
    monkeypatch.setattr(langchain.mcp, "MCPAdapter", lambda client: SimpleNamespace(
        list_tools=AsyncMock(return_value=tools),
    ))
    assert await mcp.load_mcp_tools(access_token="test-token") == tools
