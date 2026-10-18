"""Run the solver against the REAL iFood MCP, read-only (no cart, no order).

    python scripts/probe_solver.py            # uses the demo table
    MESA_USE_LLM=false python scripts/probe_solver.py

Calls listAddresses, searchOffers, getMerchant and getItem only.
Prints every restaurant that can feed the table, with each person's dish.
"""

import asyncio
import os
import sys

os.environ.setdefault("MESA_MODE", "live")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent.mesa import solver
from agent.mesa.gateway import get_gateway
from agent.mesa.intents import extract_intents
from agent.mesa.models import Member

MEMBERS = [
    Member(id="m1", name="Arthur", is_host=True, desire="Hamburguer artesanal com bacon", max_budget=40),
    Member(id="m2", name="Bia", desire="Lanche vegetariano gostoso", restrictions=["VEGETARIAN"], max_budget=35),
    Member(id="m3", name="Lucas", desire="Qualquer lanche bom, mas zero queijo", restrictions=["LAC_FREE"], max_budget=40),
    Member(id="m4", name="Joao", desire="Smash duplo barato", max_budget=30),
]


async def main() -> None:
    gateway = get_gateway()
    address = (await gateway.call("listAddresses"))["results"][0]
    location = {
        "latitude": address["coordinates"]["latitude"],
        "longitude": address["coordinates"]["longitude"],
    }

    intents, note = await extract_intents(MEMBERS)
    if note:
        print("note:", note)

    def log(text: str, kind: str = "info") -> None:
        print(f"  [{kind}] {text}")

    searcher = solver.Searcher(gateway, location)
    try:
        candidates = await solver.find_merchant(searcher, MEMBERS, intents, log)
    except solver.NoMatch as exc:
        print("NO MATCH:", exc)
        return

    print(f"\n{len(candidates)} restaurante(s) atendem a mesa:\n")
    for candidate in candidates:
        merchant = candidate.merchant
        print(f"{merchant.name}  fee R$ {merchant.fee:.2f}  minimo R$ {merchant.minimum:.2f}")
        for member in MEMBERS:
            hit = next(iter(candidate.options.get(member.id, [])), None)
            if hit is None:
                continue
            item = await solver.validate_one(
                gateway, location, merchant.merchant_id, member, hit.item_id,
                fallback_name=hit.name, fallback_price=hit.price, fallback_image=hit.image,
            )
            print(f"  {member.name:<8} {item.name}  R$ {item.price:.2f}  {item.option_labels}  {item.warnings}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
