"""Load tools from the iFood (``sudowin-semcomp``) MCP server.

The server is a remote MCP endpoint that requires OAuth. We resolve a bearer
token ourselves (see :mod:`agent.oauth`), let FastMCP speak the protocol, and
``langchain.mcp.MCPAdapter`` converts the discovered tools into LangChain tools
that ``create_react_agent`` can call.

Set ``MCP_ENABLED=false`` to run the agent with only its local tools.
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

_OFF = {"0", "false", "no", "off"}


def mcp_enabled() -> bool:
    """Whether to connect to the MCP server (default: yes)."""
    return os.getenv("MCP_ENABLED", "true").strip().lower() not in _OFF


def mcp_url() -> str:
    """The MCP endpoint to connect to."""
    return os.getenv("MCP_URL", DEFAULT_MCP_URL)


def _comma_set(name: str) -> set[str]:
    """Parse a comma-separated env var into a set of trimmed names."""
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


def filter_tools(tools: list) -> list:
    """Apply the optional tool allow/deny lists.

    ``MCP_TOOLS`` (allow list) and ``MCP_EXCLUDE_TOOLS`` (deny list) matter
    because MCP tool schemas are sent on every model call: the iFood server's
    full set is 10,508 tokens, which alone exceeds Groq's free-tier 8k TPM
    ceiling. Selecting a focused subset keeps requests inside the limit - or use
    a local model, which has no such ceiling.
    """
    only = _comma_set("MCP_TOOLS")
    drop = _comma_set("MCP_EXCLUDE_TOOLS")
    selected = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if only and name not in only:
            continue
        if name in drop:
            continue
        selected.append(tool)
    return selected


async def load_mcp_tools() -> list:
    """Connect to the MCP server and return its tools as LangChain tools.

    The returned tools stay callable after this function returns: each one holds
    the FastMCP client, which reconnects as needed.
    """
    # Imported lazily so the beta warning (raised at import time) can be
    # suppressed here rather than leaking to the caller on every run.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangChainBetaWarning)
        from langchain.mcp import MCPAdapter

        token = await get_access_token()
        adapter = MCPAdapter(Client(mcp_url(), auth=token))
        return filter_tools(await adapter.list_tools())


async def list_tool_names() -> list[str]:
    """Names of the tools the MCP server exposes (for diagnostics)."""
    return [getattr(tool, "name", str(tool)) for tool in await load_mcp_tools()]
