"""Read-only HTTP/JSON-RPC diagnostic with no MCP SDK, LangChain or app gateway."""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)


def tool_body(result):
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                body = json.loads(block["text"])
            except ValueError:
                continue
            if isinstance(body, dict):
                return body
    return {}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--room", help="Also test a published item in this saved room (read-only).")
    parser.add_argument("--compare-refresh", action="store_true",
                        help="Explicitly refresh OAuth, then compare old/new credentials on the same saved item.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cache_path = Path(os.getenv("MCP_TOKEN_CACHE", ".mcp-token.json"))
    cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    token = os.getenv("MCP_BEARER_TOKEN")
    if not token and cached.get("expires_at", 0) > time.time():
        token = cached.get("access_token")
    if not token:
        parser.error("Login salvo ausente ou expirado; nenhum login sera aberto pelo teste.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    url = os.getenv("MCP_URL", "https://consumer-mcp.ifood.com.br/marketplace")
    report = {"client": "httpx JSON-RPC; no MCP SDK or application gateway", "read_only": True, "calls": []}
    sequence = 0
    credential_context = "cached"

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        async def rpc(method, params, *, notify=False):
            nonlocal sequence
            sequence += 1
            payload = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notify:
                payload["id"] = sequence
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                meta = {"http_status": response.status_code,
                        "request_id": response.headers.get("x-request-id")}
                if response.headers.get("mcp-session-id"):
                    headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
                if notify or response.status_code >= 400:
                    return {}, meta
                if "text/event-stream" in response.headers.get("content-type", ""):
                    event = []
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            event.append(line[5:].lstrip())
                        elif not line and event:
                            message = json.loads("\n".join(event))
                            event = []
                            if message.get("id") == sequence:
                                return message, meta
                    return {}, meta
                return json.loads(await response.aread()), meta

        async def initialize(access_token):
            headers["Authorization"] = f"Bearer {access_token}"
            headers.pop("Mcp-Session-Id", None)
            headers.pop("MCP-Protocol-Version", None)
            initialized, meta = await rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                        "clientInfo": {"name": "mesa-http-diagnostic", "version": "1.0"}})
            protocol = initialized.get("result", {}).get("protocolVersion")
            print(json.dumps({"phase": "initialize", "credential_context": credential_context,
                              "protocol": protocol, **meta}), flush=True)
            if not protocol:
                return False
            headers["MCP-Protocol-Version"] = protocol
            await rpc("notifications/initialized", {}, notify=True)
            return True

        if not await initialize(token):
            return

        async def tool(name, arguments):
            started = time.perf_counter()
            message, meta = await rpc("tools/call", {"name": name, "arguments": arguments})
            result = message.get("result", {})
            body = tool_body(result)
            row = {"timestamp": datetime.now(timezone.utc).isoformat(), "tool": name,
                   "credential_context": credential_context, **meta,
                   "mcp_is_error": result.get("isError", False), "mcp_code": body.get("code"),
                   "rpc_error_code": message.get("error", {}).get("code"),
                   "upstream_error": "upstream" in str(body.get("message", "")).lower(),
                   "has_results": isinstance(body.get("results"), list),
                   "has_item": bool(body.get("itemName")),
                   "duration_ms": round((time.perf_counter()-started)*1000, 2)}
            report["calls"].append(row)
            print(json.dumps(row), flush=True)
            return body

        await tool("listAddresses", {})
        await tool("getUser", {"includeProfileData": False})
        sessions_path = root / "sessions.json"
        sessions = json.loads(sessions_path.read_text(encoding="utf-8")) if sessions_path.exists() else {}
        choices = [s for s in sessions.values() if s.get("mode") == "live" and s.get("hostAddress")]
        if args.room:
            choices = [s for s in choices if s.get("roomId") == args.room.upper()]
        saved_item_args = None
        if choices:
            session = max(choices, key=lambda s: max((e.get("ts", 0) for e in s.get("events", [])), default=0))
            saved = session["hostAddress"]
            location = {"latitude": saved["latitude"], "longitude": saved["longitude"]}
            if args.room and session.get("selectedMerchantId") and session.get("recommendations"):
                card = session["recommendations"][0]
                saved_item_args = {"itemId": card["itemId"], "merchantId": session["selectedMerchantId"],
                                   "userLocation": location}
                await tool("getItem", saved_item_args)
            listing = await tool("searchOffers", {"query": "hamburguer", "merchantType": "RESTAURANT",
                                                 "resultKind": "ITEM", "pageSize": 5, "userLocation": location,
                                                 "refinements": {"onlyOpen": True}})
            items = listing.get("results", [])
            if items:
                item = items[0]
                merchant_id = item.get("merchant", {}).get("merchantId")
                if item.get("itemId") and merchant_id:
                    await tool("getItem", {"itemId": item["itemId"], "merchantId": merchant_id,
                                           "userLocation": location})
        if args.compare_refresh:
            from agent.oauth import get_access_token

            try:
                renewed = await get_access_token(refresh=True, interactive=False)
            except Exception as exc:
                report["refresh"] = {"succeeded": False, "error_type": type(exc).__name__}
                print(json.dumps({"phase": "refresh", **report["refresh"]}), flush=True)
            else:
                report["refresh"] = {"succeeded": True, "token_changed": renewed != token}
                print(json.dumps({"phase": "refresh", **report["refresh"]}), flush=True)
                credential_context = "renewed"
                if await initialize(renewed):
                    await tool("listAddresses", {})
                    await tool("getUser", {"includeProfileData": False})
                    if saved_item_args:
                        await tool("getItem", saved_item_args)
                        credential_context = "old_after_refresh"
                        if await initialize(token):
                            await tool("getItem", saved_item_args)
                        credential_context = "renewed_recheck"
                        if await initialize(renewed):
                            await tool("getItem", saved_item_args)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 110))
