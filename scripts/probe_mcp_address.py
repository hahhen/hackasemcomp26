"""Read-only MCP address diagnostic, independent of the app and LangChain.

Uses only an existing, unexpired login; never opens OAuth or prints addresses.
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import httpx2
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

from agent.oauth import _load_cached


def error_payload(result):
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                body = json.loads(block.text)
            except ValueError:
                continue
            if isinstance(body, dict):
                return body
    return {}


async def probe(token, mode):
    exchanges = []

    async def response_hook(response):
        try:
            request = json.loads(response.request.content)
        except (ValueError, RuntimeError):
            return
        if request.get("method") != "tools/call":
            return
        exchanges.append({"http_status": response.status_code,
                          "tool": request.get("params", {}).get("name"),
                          "request_ids": {key: response.headers[key] for key in (
                              "x-request-id", "x-correlation-id", "traceparent"
                          ) if key in response.headers}})

    def client_factory(**kwargs):
        return httpx2.AsyncClient(**kwargs, event_hooks={"response": [response_hook]})

    transport = StreamableHttpTransport(
        os.getenv("MCP_URL", "https://consumer-mcp.ifood.com.br/marketplace"),
        auth=token, httpx_client_factory=client_factory,
    )
    started = time.perf_counter()
    outcome = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": mode,
               "tool": "listAddresses", "arguments_count": 0}
    try:
        async with Client(transport, mode=mode) as client:
            result = await client.call_tool_mcp("listAddresses", {}, timeout=20)
        body = error_payload(result)
        outcome.update({"mcp_is_error": result.is_error, "mcp_code": body.get("code"),
                        "has_results": isinstance(body.get("results"), list),
                        "upstream_error": "upstream" in str(body.get("message", "")).lower()})
    except Exception as exc:
        outcome["exception_type"] = type(exc).__name__
    outcome.update({"duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "http_exchanges": exchanges})
    return outcome


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-protocols", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    token = os.getenv("MCP_BEARER_TOKEN")
    cached = _load_cached() or {}
    if not token and cached.get("expires_at", 0) > time.time():
        token = cached.get("access_token")
    if not token:
        parser.error("Sem login salvo valido. Autentique no terminal com python -m agent.oauth.")
    report = {"read_only": True, "client": "FastMCP raw protocol; no LangChain or application gateway",
              "versions": {name: version(name) for name in ("fastmcp", "mcp")}, "probes": []}
    for mode in (["auto", "legacy"] if args.compare_protocols else ["auto"]):
        try:
            outcome = await asyncio.wait_for(probe(token, mode), 35)
        except TimeoutError:
            outcome = {"mode": mode, "tool": "listAddresses", "exception_type": "TimeoutError"}
        report["probes"].append(outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
