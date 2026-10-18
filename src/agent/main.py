"""Command-line entry point for the agent.

Run interactively:    python -m agent.main
Run a single prompt:  python -m agent.main "what is 12 * 7?"
Check connectivity:   python -m agent.main --check
List loaded tools:    python -m agent.main --tools
"""

import asyncio
import sys

from langchain_core.messages import AIMessage

from agent.graph import build_agent
from agent.mcp import load_mcp_tools, mcp_enabled, mcp_url
from agent.providers import build_primary, describe_chain, is_local_endpoint
from agent.tools import TOOLS


def message_text(message: AIMessage) -> str:
    """Extract plain text from a message.

    Some models return ``content`` as a string, others as a list of content
    blocks (text, reasoning, etc.). This flattens either shape to text.
    """
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def first_line(text: str | None) -> str:
    """First line of a description, for compact listings."""
    return (text or "").strip().splitlines()[0] if text else ""


def estimate_schema_tokens(tools: list) -> int:
    """Rough token cost of sending these tool schemas (~4 characters/token).

    Tool schemas ride along on every model call, so this is often what pushes a
    request past a free tier's tokens-per-minute ceiling.
    """
    import json

    from langchain_core.utils.function_calling import convert_to_openai_tool

    return sum(len(json.dumps(convert_to_openai_tool(tool))) // 4 for tool in tools)


async def ask(agent, prompt: str) -> None:
    result = await agent.ainvoke({"messages": [("user", prompt)]})
    print(f"Agent: {message_text(result['messages'][-1])}")


async def check() -> None:
    """Smoke-test the provider chain and the whole tool surface."""
    print("model chain:")
    for role, model, base_url in describe_chain():
        print(f"  {role:<18} {model:<42} {base_url}")

    print("\n1) plain completion ...")
    reply = await build_primary().ainvoke("Reply with exactly the word: pong")
    print(f"   {message_text(reply)}")

    print("\n2) loading tools ...")
    print(f"   local: {', '.join(t.name for t in TOOLS)}")
    if mcp_enabled():
        mcp_tools = await load_mcp_tools()
        size = estimate_schema_tokens(mcp_tools)
        print(f"   {mcp_url()}: {len(mcp_tools)} tool(s), ~{size} schema tokens")
        if size > 7000:
            if is_local_endpoint():
                print("   ok - local endpoint, no tokens-per-minute ceiling")
            else:
                print("   ! tool schemas alone may exceed a free tier's TPM ceiling")
                print("     (Groq free = 8k TPM -> HTTP 413). Use a local model,")
                print("     add a higher-TPM provider, or restrict with MCP_TOOLS.")
    else:
        print("   mcp: disabled (MCP_ENABLED=false)")

    print("\n3) tool-calling round trip (full chain) ...")
    agent = await build_agent()
    try:
        result = await agent.ainvoke(
            {"messages": [("user", "Use the multiply tool to compute 6 * 7.")]}
        )
    except Exception as exc:  # we report the cause and exit
        print(f"   ! {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
        print()
        print("note: a provider chain re-raises the FIRST provider's error, so the")
        print("      message above may not be the real cause. Likely reasons:")
        print("      - 413 on the primary: the tool schemas alone exceed its")
        print("        tokens-per-minute limit (Groq free tier = 8k TPM).")
        print("      - 429 on a fallback: free-tier quota exhausted (Gemini 2.5")
        print("        Flash free tier is about 20 requests per minute).")
        raise SystemExit(1) from exc

    reply = message_text(result["messages"][-1])
    print(f"   {reply}")
    if not reply.strip():
        print("\nFAILED - the model returned an empty reply.")
        print("  Nothing was raised, so a provider answered HTTP 200 with no content")
        print("  and no tool call. Re-run; if it keeps happening, restrict MCP_TOOLS")
        print("  so the primary provider can fit the request inside its TPM limit.")
        raise SystemExit(1)

    print("\nOK - provider reachable and tool calling works.")


async def show_tools() -> None:
    """Print every tool the agent will have at its disposal."""
    print("local tools:")
    for tool in TOOLS:
        print(f"  - {tool.name}: {first_line(tool.description)}")

    if not mcp_enabled():
        print("\nMCP disabled (MCP_ENABLED=false).")
        return

    print(f"\nMCP tools from {mcp_url()}:")
    for tool in await load_mcp_tools():
        print(f"  - {tool.name}: {first_line(getattr(tool, 'description', ''))}")


async def run() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        await check()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--tools":
        await show_tools()
        return

    agent = await build_agent()

    # One-shot mode: pass the prompt as arguments.
    if len(sys.argv) > 1:
        await ask(agent, " ".join(sys.argv[1:]))
        return

    print("Tool-calling agent ready. Type a message (Ctrl+C or 'exit' to quit).\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            break

        await ask(agent, user_input)
        print()


def configure_output() -> None:
    """Make stdout/stderr tolerate the characters models actually emit.

    A Windows console defaults to a legacy code page (cp1252 here), which cannot
    encode characters such as the U+202F narrow no-break space that appears in
    model output, or the accented Portuguese that iFood data contains. Without
    this, printing a valid reply crashes the CLI.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main() -> None:
    configure_output()
    asyncio.run(run())


if __name__ == "__main__":
    main()
