"""Load the complete iFood MCP tool catalog for Mesa's live gateway.

OAuth supplies the bearer token; FastMCP and MCPAdapter handle the protocol.
The gateway calls these tools directly. Their schemas are not bound to chat.
``MESA_MODE=mock`` selects offline data without loading the live gateway.
"""

from __future__ import annotations

import os
import warnings

from dotenv import load_dotenv
from fastmcp.client import Client
from langchain_core._api import LangChainBetaWarning

from agent.oauth import get_access_token

load_dotenv()

DEFAULT_MCP_URL = "https://consumer-mcp.ifood.com.br/marketplace"

def mcp_url() -> str:
    """The MCP endpoint to connect to."""
    return os.getenv("MCP_URL", DEFAULT_MCP_URL)


async def load_mcp_tools(*, access_token: str | None = None) -> list:
    """Connect to the MCP server and return its tools as LangChain tools.

    The returned tools stay callable after this function returns: each one holds
    the FastMCP client, which reconnects as needed.
    """
    # Imported lazily so the beta warning (raised at import time) can be
    # suppressed here rather than leaking to the caller on every run.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangChainBetaWarning)
        from langchain.mcp import MCPAdapter

        token = access_token if access_token is not None else await get_access_token()
        adapter = MCPAdapter(Client(mcp_url(), auth=token))
        return await adapter.list_tools()
