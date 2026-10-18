import pytest

from agent.mesa import gateway, solver
from agent.mesa.intents import heuristic_intent
from agent.mesa.models import Member


def item(shop="local", name="Hamburguer", price=25):
    return {"itemId": shop, "itemName": name, "unitPrice": price,
            "itemDescription": "Carne, pao e alface",
            "merchant": {"merchantId": shop, "merchantName": shop, "isOpen": True}}


async def test_generic_discovery_uses_stores_not_first_page_product_volume():
    class Api:
        def __init__(self):
            self.calls = []

        async def call(self, tool, **args):
            self.calls.append((tool, args))
            if args["resultKind"] == "MERCHANT":
                return {"results": [{"merchantId": "chain"}, {"merchantId": "independent"}]}
            mid = args["refinements"].get("merchantId")
            return {"results": [item(mid or "chain")]}

    api = Api()
    who = Member(id="m", name="Host", desire="algo bom", max_budget=50)
    hits = await solver.Searcher(api, {}).hits(who, heuristic_intent(who))
    assert {hit.merchant.merchant_id for hit in hits} == {"chain", "independent"}
    assert api.calls[0][1]["resultKind"] == "MERCHANT"


async def test_global_search_reads_next_page_to_find_another_store():
    class Api:
        async def call(self, tool, **args):
            if args.get("nextPageToken"):
                return {"results": [item("local")], "nextPageToken": None}
            return {"results": [item("chain")], "nextPageToken": "next"}

    who = Member(id="m", name="jp", desire="hamburger", max_budget=35)
    hits = await solver.Searcher(Api(), {}).hits(who, heuristic_intent(who))
    assert {h.merchant.merchant_id for h in hits} == {"chain", "local"}


async def test_failed_person_global_search_can_recover_inside_other_persons_store():
    class Api:
        async def call(self, tool, **args):
            if tool == "getItem":
                return item()
            if args["resultKind"] == "MERCHANT":
                return {"results": [{"merchantId": "local"}]}
            if args.get("query") == "hamburger" and not args["refinements"].get("merchantId"):
                return {"message": "incomplete"}
            return {"results": [item()]}

    members = [Member(id="host", name="Host", desire="algo bom", max_budget=50),
               Member(id="jp", name="jp", desire="hamburger sem tomate", max_budget=35)]
    candidates = await solver.find_merchant(solver.Searcher(Api(), {}), members,
                                            {m.id: heuristic_intent(m) for m in members}, lambda *args: None)
    assert candidates[0].merchant.merchant_id == "local"
    assert set(candidates[0].options) == {"host", "jp"}
    assert all(hit.price <= 35 for hit in candidates[0].options["jp"])


async def test_incomplete_later_page_does_not_discard_successful_first_page():
    class Api:
        async def call(self, tool, **args):
            if args.get("nextPageToken"):
                return {"message": "incomplete"}
            return {"results": [item()], "nextPageToken": "broken"}

    hits = await solver.Searcher(Api(), {})._search("hamburger", {"onlyOpen": True})
    assert hits == [item()]


async def test_malformed_remote_price_filter_retries_without_relaxing_local_limit():
    class Api:
        async def call(self, tool, **args):
            if "maxItemPrice" in args["refinements"]:
                return {"message": "incomplete"}
            return {"results": [item("cheap", price=37.90), item("costly", price=55)]}

    who = Member(id="m", name="jp", desire="hamburger", max_budget=40)
    hits = await solver.Searcher(Api(), {}).hits(who, heuristic_intent(who))
    assert [hit.item_id for hit in hits] == ["cheap"]


async def test_auth_errors_are_not_hidden_by_other_persons_successful_search():
    class Api:
        async def call(self, tool, **args):
            raise gateway.MCPError(tool, "invalid_client")

    who = Member(id="m", name="jp", desire="hamburger", max_budget=40)
    with pytest.raises(gateway.MCPError, match="invalid_client"):
        await solver.find_merchant(solver.Searcher(Api(), {}), [who],
                                  {who.id: heuristic_intent(who)}, lambda *args: None)
