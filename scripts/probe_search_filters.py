"""Read-only comparison of iFood search filters. Uses an existing token only."""

import asyncio
import json
import os
import sys
import time

from agent.oauth import _load_cached


async def main():
    cached = _load_cached() or {}
    token = os.getenv("MCP_BEARER_TOKEN")
    if not token and cached.get("expires_at", 0) > time.time():
        token = cached.get("access_token")
    if not token:
        print("No valid cached token; sign in manually before running this probe.")
        return
    os.environ["MCP_BEARER_TOKEN"] = token
    from agent.mesa.gateway import LiveGateway

    gateway = LiveGateway()
    addresses = await gateway.call("listAddresses")
    if "--addresses" in sys.argv:
        print(json.dumps({"keys": list(addresses), "results_type": type(addresses.get("results")).__name__,
                          "code": addresses.get("code"), "message": addresses.get("message"),
                          "address_count": len(addresses.get("results") or [])}), flush=True)
        return
    location = addresses["results"][0]["coordinates"]
    if "--coverage" in sys.argv:
        from agent.mesa.intents import heuristic_intent
        from agent.mesa.models import Member
        from agent.mesa.solver import Searcher

        who = Member(id="probe", name="Probe", desire="algo bom", max_budget=50)
        hits = await Searcher(gateway, location).hits(who, heuristic_intent(who))
        print(json.dumps({"items": len(hits), "merchants": sorted({hit.merchant.name for hit in hits}),
                          "all_within_50": all(hit.price <= 50 for hit in hits)}), flush=True)
        return
    if "--diagnose" in sys.argv:
        for tool, args in (("browseOffers", {}), ("searchOffers", {"query": "hamburger"}),
                           ("searchOffers", {"query": "hamburguer"})):
            data = await gateway.call(tool, merchantType="RESTAURANT", resultKind="ITEM", pageSize=25,
                                      userLocation=location, refinements={"onlyOpen": True, "maxItemPrice": 35}, **args)
            print(json.dumps({"tool": tool, "args": args, "shape": {
                k: {"type": type(v).__name__, "keys": list(v) if isinstance(v, dict) else None}
                for k, v in data.items()}, "count": len(data.get("results") or []),
                "merchants": len({r.get("merchant", {}).get("merchantId") for r in data.get("results") or []})}), flush=True)
        return
    for filters in ({}, {"onlyOpen": True}, {"onlyOpen": True, "maxItemPrice": 40},
                    {"onlyOpen": True, "maxItemPrice": 50}, {"maxItemPrice": 40}):
        data = await gateway.call(
            "searchOffers", query="hamburguer", merchantType="RESTAURANT", resultKind="ITEM",
            pageSize=25, userLocation=location, refinements=filters,
        )
        rows = data.get("results", [])
        print(json.dumps({"filters": filters, "keys": list(data), "count": len(rows),
                          "hasMore": data.get("hasMore"),
                          "sample": [{"price": r.get("promotionalUnitPrice") or r.get("unitPrice"),
                                      "open": (r.get("merchant") or {}).get("isOpen")}
                                     for r in rows[:3]]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
