"""Build the LangGraph agent graph.

Groq (and most free providers) expose an OpenAI-compatible API, so we use
``langchain-openai``'s ``ChatOpenAI`` pointed at the provider's base URL. The
model chain (retries + provider fallbacks) lives in :mod:`agent.providers`.
"""

from langchain.agents import create_agent

from agent.mcp import load_mcp_tools, mcp_enabled
from agent.providers import build_model
from agent.tools import TOOLS


async def build_agent():
    """Create a tool-calling agent graph.

    Loads the local tools plus, unless ``MCP_ENABLED=false``, every tool the
    iFood MCP server exposes. Returns a compiled LangGraph graph that runs a
    ReAct-style loop: the model decides which tools to call, the tools run, and
    the results are fed back until the model produces a final answer.
    """
    tools = list(TOOLS)
    if mcp_enabled():
        tools.extend(await load_mcp_tools())

    return create_agent(model=build_model(), tools=tools)
