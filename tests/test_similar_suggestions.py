import pytest

from agent.mesa import gateway, service, solver
from agent.mesa.intents import heuristic_intent
from agent.mesa.mock_data import MockGateway
from agent.mesa.models import Member
from agent.mesa.store import store


def dish(mid, item, name, price=25):
    return {"itemId": item, "itemName": name, "unitPrice": price, "itemDescription": name,
            "merchant": {"merchantId": mid, "merchantName": mid, "isOpen": True}}


class Catalog(MockGateway):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    async def call(self, tool, **args):
        if tool in {"searchOffers", "browseOffers"}:
            self.calls.append((tool, args))
            mid = args.get("refinements", {}).get("merchantId")
            return {"results": [row for row in self.rows if mid is None or row["merchant"]["merchantId"] == mid]}
        if tool == "getItem":
            return next(row for row in self.rows if row["itemId"] == args["itemId"])
        return await super().call(tool, **args)


def members():
    return [Member(id="a", name="Ana", desire="hamburguer bacon", max_budget=40),
            Member(id="b", name="Bia", desire="hamburguer frango", max_budget=40)]


async def candidates(rows, people=None):
    people = people or members()
    return await solver.find_merchant(solver.Searcher(Catalog(rows), {}), people,
                                     {m.id: heuristic_intent(m) for m in people}, lambda *args: None)


async def test_exact_common_restaurant_wins_even_if_alternatives_cost_less():
    result = await candidates([dish("cheap", "plain", "Hamburguer", 10),
        dish("exact", "bacon", "Hamburguer bacon", 35), dish("exact", "chicken", "Hamburguer frango", 35)])
    assert result[0].merchant.merchant_id == "exact"
    assert all(not h.alternative_query for c in result for hits in c.options.values() for h in hits)


async def test_similar_suggestions_preserve_exact_person_and_original_wishes():
    people = members()
    result = await candidates([dish("shop", "bacon", "Hamburguer bacon"),
                               dish("shop", "salad", "Hamburguer salada")], people)
    assert result[0].options["a"][0].alternative_query is None
    assert all(hit.alternative_query == "hamburguer" for hit in result[0].options["b"])
    assert people[1].desire == "hamburguer frango"


async def test_unknown_or_unrelated_food_is_not_called_similar():
    people = [Member(id="a", name="Ana", desire="pizza"), Member(id="b", name="Bia", desire="sushi")]
    result = await candidates([dish("pizza", "p", "Pizza"), dish("sushi", "s", "Sushi")], people)
    assert len(result) == 2
    assert all(sum(bool(options) for options in c.options.values()) == 1 for c in result)
    assert all(not h.alternative_query for c in result for hits in c.options.values() for h in hits)


async def test_alternatives_do_not_relax_budget_or_ingredient_exclusions():
    people = [Member(id="a", name="Ana", desire="hamburguer bacon"),
              Member(id="b", name="Bia", desire="hamburguer frango sem tomate", max_budget=30)]
    result = await candidates([dish("shop", "bacon", "Hamburguer bacon tomate"),
                               dish("shop", "safe", "Hamburguer salada", 45)], people)
    assert result[0].options["a"]
    assert not result[0].options["b"]


async def test_only_similar_results_can_seed_discovery():
    person = Member(id="a", name="Ana", desire="hamburguer artesanal")
    result = await candidates([dish("shop", "plain", "Hamburguer")], [person])
    assert result[0].options["a"][0].alternative_query == "hamburguer"


async def test_published_alternative_is_explained_and_can_be_added():
    gateway.set_gateway(Catalog([dish("shop", "bacon", "Hamburguer bacon"),
                                dish("shop", "salad", "Hamburguer salada")]))
    session = store.get_or_create("SIMILAR")
    friend = service.join(session, "Bia")
    session.host().desire = "hamburguer bacon"
    friend.desire = "hamburguer frango"
    service.start_recommend(session)
    await service._recommend_tasks[session.room_id]
    assert session.merchant_options
    card = next(r for r in session.recommendations if r.member_id == friend.id)
    assert card.alternative_query == "hamburguer"
    assert "Alternativa similar" in card.note
    assert any("Bia pediu" in m.text and "desejos exatos" in m.text for m in session.chat)
    await service.add_to_cart(session, friend.id, friend.id, item_id=card.item_id)
    assert friend.assigned_item.item_id == card.item_id
    assert friend.desire == "hamburguer frango"


async def test_mcp_error_is_not_reported_as_missing_desires():
    class Broken(Catalog):
        async def call(self, tool, **args):
            raise gateway.MCPError(tool, "invalid_client")

    people = members()
    with pytest.raises(gateway.MCPError):
        await solver.find_merchant(solver.Searcher(Broken([]), {}), people,
                                  {m.id: heuristic_intent(m) for m in people}, lambda *args: None)


def test_absence_of_requested_ingredient_is_not_an_exact_match():
    hit = solver.Hit("plain", "Hamburguer sem bacon", 20, solver.MerchantInfo("shop", "Shop"), None, 0)
    assert not solver.matches_exact(hit, heuristic_intent(members()[0]))


async def test_dietary_filter_is_retained_when_looking_for_similars():
    api = Catalog([dish("shop", "beef", "Hamburguer bacon"), dish("shop", "veg", "Hamburguer vegetal")])
    people = members()
    people[1].desire = "hamburguer artesanal"
    people[1].restrictions = ["VEGETARIAN"]
    result = await solver.find_merchant(solver.Searcher(api, {}), people,
                                      {m.id: heuristic_intent(m) for m in people}, lambda *args: None)
    assert [h.item_id for h in result[0].options["b"]] == ["veg"]
    assert any(args["refinements"].get("itemRestrictions") == ["VEGETARIAN"]
               for tool, args in api.calls if args.get("refinements", {}).get("merchantId"))


@pytest.mark.parametrize("wish,menu_name", [("hamburguer bacon", "X-Bacon"),
                                          ("x-salada", "Hamburguer salada"),
                                          ("x bacon egg", "Hamburguer bacon egg")])
async def test_brazilian_burger_names_are_recognized(wish, menu_name):
    person = Member(id="a", name="Ana", desire=wish)
    result = await candidates([dish("shop", "item", menu_name)], [person])
    assert result[0].options["a"]
    assert result[0].options["a"][0].alternative_query is None
    assert solver.similar_query(heuristic_intent(person)) == "hamburguer"


async def test_partial_results_publish_explanations_and_checkout_remains_blocked():
    gateway.set_gateway(Catalog([dish("shop", "pizza", "Pizza calabresa")]))
    session = store.get_or_create("PARTIAL")
    friend = service.join(session, "Bia")
    session.host().desire = "pizza calabresa"
    friend.desire = "sushi"
    service.start_recommend(session)
    await service._recommend_tasks[session.room_id]
    assert not session.recommend_error
    assert session.merchant_options[0].covers == 1
    assert session.merchant_options[0].requested_count == 2
    assert session.merchant_options[0].missing_members == ["Bia"]
    assert session.recommendations
    assert not any(card.member_id == friend.id for card in session.recommendations)
    assert any('Bia: "sushi"' in m.text for m in session.chat)
    await service.add_to_cart(session, session.host_member_id, session.host_member_id)
    with pytest.raises(service.TableError, match="Falta o prato"):
        service.start_finish_cart(session, session.host_member_id)


async def test_related_savory_snacks_are_offered_as_alternatives():
    people = [Member(id="a", name="Ana", desire="hamburguer bacon"),
              Member(id="b", name="Bia", desire="coxinha")]
    result = await candidates([dish("shop", "burger", "X-Bacon"),
                               dish("shop", "pastry", "Pastel queijo")], people)
    assert result[0].options["b"][0].alternative_query == "salgado"
    assert result[0].options["b"][0].item_id == "pastry"


async def test_transient_scoped_failures_keep_confirmed_partial_cards(monkeypatch):
    class ScopedFailure(Catalog):
        async def call(self, tool, **args):
            if tool in {"searchOffers", "browseOffers"} and args.get("refinements", {}).get("merchantId"):
                raise gateway.MCPError(tool, "upstream failure")
            return await super().call(tool, **args)

    people = [Member(id="a", name="Ana", desire="pizza"), Member(id="b", name="Bia", desire="sushi")]
    result = await solver.find_merchant(solver.Searcher(ScopedFailure([dish("shop", "p", "Pizza")]), {}),
                                       people, {m.id: heuristic_intent(m) for m in people}, lambda *args: None)
    assert result[0].options["a"]
    assert not result[0].options["b"]
    assert result[0].search_incomplete


async def test_detail_filter_does_not_discard_everyone_when_one_dish_exceeds_budget():
    class RequiredExtra(Catalog):
        async def call(self, tool, **args):
            data = await super().call(tool, **args)
            if tool == "getItem" and args["itemId"] == "chicken":
                return {**data, "unitPrice": 80}
            return data

    gateway.set_gateway(RequiredExtra([dish("shop", "bacon", "Hamburguer bacon"),
                                      dish("shop", "chicken", "Hamburguer frango")]))
    session = store.get_or_create("DETAIL-PARTIAL")
    friend = service.join(session, "Bia")
    session.host().desire = "hamburguer bacon"
    friend.desire = "hamburguer frango"
    friend.max_budget = 40
    service.start_recommend(session)
    await service._recommend_tasks[session.room_id]
    assert not session.recommend_error
    assert session.merchant_options[0].missing_members == ["Bia"]
    assert all(card.member_id == session.host_member_id for card in session.recommendations)
