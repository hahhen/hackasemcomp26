"""Search recovery, transport envelopes, and unnecessary search regressions."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from agent.mesa import chat, gateway, service, solver
from agent.mesa.api import app
from agent.mesa.intents import heuristic_intent
from agent.mesa.mock_data import MockGateway
from agent.mesa.models import Member
from agent.mesa.parsing import decode
from agent.mesa.store import store


async def ready(room):
    session = store.get_or_create(room)
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await service._recommend_tasks[session.room_id]
    assert session.merchant_options
    return session


async def test_adding_blank_friend_does_not_search_or_remove_options():
    session = await ready("BLANK-FRIEND")
    options = list(session.merchant_options)
    cards = list(session.recommendations)
    calls = len(gateway.get_gateway().calls)
    task = service._recommend_tasks[session.room_id]
    response = TestClient(app).post(f"/api/mesas/{session.room_id}/members", json={"name": "jp"})
    assert response.status_code == 201
    await asyncio.sleep(0)
    assert len(gateway.get_gateway().calls) == calls
    assert service._recommend_tasks[session.room_id] is task
    assert session.merchant_options == options
    assert session.recommendations == cards
    assert not session.searching_recommendations


async def test_saving_unchanged_preference_does_not_restart_search():
    session = await ready("UNCHANGED")
    calls = len(gateway.get_gateway().calls)
    service.update_member(session, session.host_member_id, {"desire": "smash", "max_budget": None})
    await asyncio.sleep(0)
    assert len(gateway.get_gateway().calls) == calls
    assert not session.searching_recommendations


@pytest.mark.parametrize("payload", [
    {"content": [{"type": "text", "text": '{"results": []}'}], "isError": False},
    {"structuredContent": {"results": []}, "content": []},
    {"structured_content": {"results": []}},
])
def test_decode_accepts_known_mcp_result_envelopes(payload):
    assert decode(payload) == {"results": []}


async def test_search_retries_incomplete_page_and_recovers():
    class Flaky:
        attempts = 0

        async def call(self, tool, **args):
            self.attempts += 1
            if self.attempts == 1:
                return {"message": "temporarily unavailable"}
            return {"content": [{"type": "text", "text": json.dumps({"results": [{
                "itemId": "burger", "itemName": "Burger", "unitPrice": 37.90,
                "merchant": {"merchantId": "shop", "merchantName": "Shop", "isOpen": True},
            }]})}]}

    api = Flaky()
    who = Member(id="m", name="jp", desire="hamburguer", max_budget=40)
    hits = await solver.Searcher(api, {}).hits(who, heuristic_intent(who))
    assert len(hits) == 1
    assert hits[0].price == 37.90
    assert api.attempts == 2


async def test_transient_search_failure_preserves_results_for_unchanged_wishes(monkeypatch):
    session = await ready("KEEP-RESULTS")
    cards = list(session.recommendations)
    selected = session.selected_merchant_id

    async def unavailable(*args, **kwargs):
        raise gateway.MCPError("searchOffers", "temporariamente indisponivel")

    monkeypatch.setattr(solver, "find_merchant", unavailable)
    service.start_recommend(session)
    await service._recommend_tasks[session.room_id]
    assert session.selected_merchant_id == selected
    assert session.recommendations == cards
    assert session.last_error

    # Old results must not survive if the changed wish could make them invalid.
    service.update_member(session, session.host_member_id, {"desire": "sushi"})
    await service._recommend_tasks[session.room_id]
    assert not session.recommendations
    assert not session.merchant_options


async def test_scoped_failure_does_not_discard_valid_first_pass_results():
    class ScopedFailure:
        async def call(self, tool, **args):
            if args["refinements"].get("merchantId"):
                return {"message": "temporarily unavailable"}
            return {"results": [{"itemId": "burger", "itemName": "Burger", "unitPrice": 20,
                                 "merchant": {"merchantId": "shop", "merchantName": "Shop"}}]}

    who = Member(id="m", name="jp", desire="hamburguer")
    candidates = await solver.find_merchant(solver.Searcher(ScopedFailure(), {}), [who],
                                            {who.id: heuristic_intent(who)}, lambda *args: None)
    assert candidates[0].options[who.id][0].item_id == "burger"


def test_model_usage_logs_provider_counts_only():
    session = store.get_or_create("USAGE")
    chat._record_usage(session, [HumanMessage("oi"), AIMessage("history without usage"),
        AIMessage("tool call", usage_metadata={"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010}),
        AIMessage("reply", usage_metadata={"input_tokens": 2000, "output_tokens": 20, "total_tokens": 2020})])
    assert session.events[-1].text == "LLM: 2 chamada(s), 3000 tokens de entrada e 30 de saida."


async def test_detail_phase_is_visible_while_menu_details_are_loading(monkeypatch):
    session = store.get_or_create("PROGRESS")
    started = asyncio.Event()
    resume = asyncio.Event()
    original = solver.Searcher.describe

    async def slow(self, hit):
        started.set()
        await resume.wait()
        return await original(self, hit)

    monkeypatch.setattr(solver.Searcher, "describe", slow)
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await asyncio.wait_for(started.wait(), 1)
    assert "Conferindo" in session.recommend_phase
    assert session.searching_recommendations
    resume.set()
    await service._recommend_tasks[session.room_id]
    assert session.recommend_phase is None
    assert session.merchant_options


async def test_slow_optional_item_detail_does_not_block_the_search(monkeypatch):
    class SlowItem:
        async def call(self, tool, **kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(solver, "DETAIL_TIMEOUT_SECONDS", 0.01)
    hit = solver.Hit("item", "Burger", 20, solver.MerchantInfo("shop", "Shop"), None, 0)
    result = await solver.Searcher(SlowItem(), {}).describe(hit)
    assert result is hit
    assert result.detail is None


async def test_something_good_under_50_browses_without_inventing_query():
    member = Member(id="m", name="jp", desire="quero algo bom ate 50 reais")
    intent = heuristic_intent(member)
    assert intent.browse
    assert intent.max_price == 50
    api = MockGateway()
    hits = await solver.Searcher(api, {}).hits(member, intent)
    assert hits and all(hit.price <= 50 for hit in hits)
    assert all(tool == "browseOffers" and "query" not in args for tool, args in api.calls)
    assert api.calls[0][1]["refinements"]["maxItemPrice"] == 50


async def test_generic_wish_publishes_cards_end_to_end():
    session = store.get_or_create("GENERIC")
    service.update_member(session, session.host_member_id, {"desire": "algo bom", "max_budget": 50})
    await service._recommend_tasks[session.room_id]
    assert session.merchant_options
    assert session.recommendations
    assert all(card.price <= 50 for card in session.recommendations)
    assert any(tool == "browseOffers" for tool, _ in gateway.get_gateway().calls)


async def test_server_error_is_retried_for_generic_discovery():
    class TemporaryFailure(MockGateway):
        attempts = 0
        discovery_attempts = 0

        async def call(self, tool, **args):
            self.attempts += 1
            if args.get("resultKind") == "MERCHANT":
                self.discovery_attempts += 1
            if self.attempts == 1:
                raise gateway.MCPError(tool, "Server returned an error response")
            return await super().call(tool, **args)

    api = TemporaryFailure()
    member = Member(id="m", name="jp", desire="algo bom", max_budget=50)
    hits = await solver.Searcher(api, {}).hits(member, heuristic_intent(member))
    assert hits
    assert api.discovery_attempts == 2


async def test_persistent_server_error_stops_after_three_attempts():
    class Unavailable:
        attempts = 0

        async def call(self, tool, **args):
            self.attempts += 1
            raise gateway.MCPError(tool, "Server returned an error response")

    api = Unavailable()
    member = Member(id="m", name="jp", desire="algo bom", max_budget=50)
    with pytest.raises(gateway.MCPError, match="apos 3 tentativas"):
        await solver.Searcher(api, {}).hits(member, heuristic_intent(member))
    assert api.attempts == 3


def test_price_words_do_not_become_a_food_query():
    member = Member(id="m", name="jp", desire="quero sushi ate R$ 50,00")
    intent = heuristic_intent(member)
    assert intent.queries == ["sushi"]
    assert intent.max_price == 50
    assert not intent.browse
