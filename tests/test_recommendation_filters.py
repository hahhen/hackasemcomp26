"""Regressions for multiple cards, item budgets, and explicit food qualities."""

import pytest
from fastapi.testclient import TestClient

from agent.mesa import gateway, service, solver
from agent.mesa.api import app
from agent.mesa.intents import heuristic_intent
from agent.mesa.models import Member
from agent.mesa.store import store


def row(item_id, name, price, description="", merchant_id="shop", merchant_name="Burger Shop"):
    return {"itemId": item_id, "itemName": name, "unitPrice": price,
            "itemDescription": description,
            "merchant": {"merchantId": merchant_id, "merchantName": merchant_name,
                         "isOpen": True}}


class Catalog:
    def __init__(self, rows, broken_budget=False):
        self.rows = rows
        self.broken_budget = broken_budget
        self.calls = []

    async def call(self, tool, **args):
        self.calls.append((tool, args))
        if tool == "getItem":
            return next(r for r in self.rows if r["itemId"] == args["itemId"])
        if self.broken_budget and "maxItemPrice" in args.get("refinements", {}):
            return {"results": []}
        return {"results": self.rows}


@pytest.mark.parametrize("ceiling,expected", [(40, {"cheap"}), (50, {"cheap", "medium"})])
async def test_budget_fallback_keeps_3790_and_never_exceeds_ceiling(ceiling, expected):
    catalog = Catalog([row("cheap", "Burger", 37.90), row("medium", "Burger duplo", 45),
                       row("expensive", "Burger triplo", 56)], broken_budget=True)
    member = Member(id="m", name="jp", desire="hamburguer", max_budget=ceiling)
    hits = await solver.Searcher(catalog, {}).hits(member, heuristic_intent(member))
    assert {h.item_id for h in hits} == expected
    assert all(h.price <= ceiling for h in hits)
    assert any("maxItemPrice" not in args["refinements"] for tool, args in catalog.calls
               if tool == "searchOffers")


async def test_artisanal_is_required_even_when_server_returns_fast_food():
    catalog = Catalog([
        row("mc", "Big Mac", 29.90, "Hamburguer com queijo", merchant_name="McDonald's"),
        row("bk", "Whopper", 34.99, "Hamburguer grelhado", merchant_name="Burger King"),
        row("craft", "X-Americano", 37.90, "Hamburguer artesanal 180g"),
    ])
    member = Member(id="m", name="jp", desire="hamburguer artesanal", max_budget=40)
    intent = heuristic_intent(member)
    assert intent.queries == ["hamburguer artesanal"]
    hits = await solver.Searcher(catalog, {}).hits(member, intent)
    assert [h.item_id for h in hits] == ["craft"]


async def test_excluded_bacon_is_not_just_removed_from_search_query():
    catalog = Catalog([
        row("bacon", "X-Bacon", 25, "Hamburguer artesanal com bacon"),
        row("plain", "X-Salada", 24, "Hamburguer artesanal com salada"),
        row("without", "Burger sem bacon", 23, "Hamburguer artesanal sem bacon"),
    ])
    member = Member(id="m", name="Host", desire="hamburguer artesanal sem bacon")
    hits = await solver.Searcher(catalog, {}).hits(member, heuristic_intent(member))
    assert {h.item_id for h in hits} == {"plain", "without"}


async def test_price_cap_is_per_item_delivery_does_not_hide_valid_restaurant():
    data = row("craft", "Burger artesanal", 37.90, "Carne artesanal")
    data["merchant"]["deliveryMethod"] = {"fee": 18}
    member = Member(id="m", name="jp", desire="hamburguer artesanal", max_budget=40)
    candidates = await solver.find_merchant(solver.Searcher(Catalog([data]), {}), [member],
                                             {"m": heuristic_intent(member)}, lambda *args: None)
    assert candidates[0].options["m"][0].price == 37.90
    assert candidates[0].merchant.fee == 18


async def test_scoped_search_loads_all_pages_and_deduplicates():
    class Paged(Catalog):
        async def call(self, tool, **args):
            if args.get("nextPageToken"):
                return {"results": [self.rows[0], self.rows[1]], "nextPageToken": None}
            return {"results": [self.rows[0]], "nextPageToken": "page-2"}

    catalog = Paged([row("first", "Burger", 20), row("second", "Burger duplo", 25)])
    member = Member(id="m", name="jp", desire="hamburguer")
    hits = await solver.Searcher(catalog, {}).hits(member, heuristic_intent(member), "shop")
    assert {h.item_id for h in hits} == {"first", "second"}


async def test_multiple_cards_publish_and_plus_adds_the_clicked_item():
    session = store.get_or_create("MULTI-CARDS")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await service._recommend_tasks[session.room_id]
    cards = [r for r in session.recommendations if r.member_id == session.host_member_id]
    assert len(cards) > 1
    target = cards[-1]
    client = TestClient(app)
    response = client.post(f"/api/mesas/{session.room_id}/members/{session.host_member_id}/cart",
                           json={"authorId": session.host_member_id, "itemId": target.item_id})
    assert response.status_code == 200, response.text
    assert session.host().assigned_item.item_id == target.item_id
    assert session.host().assigned_item.item_id != cards[0].item_id
    invalid = client.post(f"/api/mesas/{session.room_id}/members/{session.host_member_id}/cart",
                          json={"itemId": "not-a-recommendation"})
    assert invalid.status_code == 409


async def test_required_extras_cannot_push_item_above_budget():
    data = row("burger", "Burger", 37.90)
    data["optionGroups"] = [{"minSelections": 1, "options": [
        {"optionId": "bread", "name": "Pao", "additionalPrice": 4}]}]
    member = Member(id="m", name="jp", desire="hamburguer", max_budget=40)
    with pytest.raises(solver.NoMatch, match="complementos obrigatorios"):
        await solver.validate_one(Catalog([data]), {}, "shop", member, "burger")


async def test_missing_search_results_are_an_error_not_no_restaurants():
    class Malformed(Catalog):
        async def call(self, tool, **args):
            return {"message": "temporarily unavailable"}

    member = Member(id="m", name="jp", desire="hamburguer")
    with pytest.raises(gateway.MCPError, match="incompleta"):
        await solver.Searcher(Malformed([]), {}).hits(member, heuristic_intent(member))
