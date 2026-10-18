import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.mesa import chat, gateway, service, solver
from agent.mesa.intents import heuristic_intent
from agent.mesa.models import Member
from agent.mesa.store import Store, say, store


class SearchCatalog:
    def __init__(self):
        self.calls = []

    async def call(self, tool, **args):
        self.calls.append((tool, args))
        await asyncio.sleep(0)  # Exercise concurrent callers sharing an in-flight read.
        return {"results": [{
            "itemId": f"burger-{index}", "itemName": "Hamburguer",
            "itemDescription": "Hamburguer com salada", "unitPrice": 20 + index,
            "merchant": {"merchantId": "shop", "merchantName": "Shop", "isOpen": True},
        } for index in range(8)]}


async def test_identical_wishes_share_reads_and_full_lists_skip_scoped_searches():
    catalog = SearchCatalog()
    people = [Member(id=str(i), name=f"Pessoa {i}", desire="hamburguer", max_budget=40) for i in range(4)]
    candidates = await solver.find_merchant(solver.Searcher(catalog, {}), people,
        {m.id: heuristic_intent(m) for m in people}, lambda *args: None)
    assert len(catalog.calls) == 1  # Previously 4 discovery + 4 scoped reads.
    assert all(len(candidates[0].options[m.id]) == 8 for m in people)
    assert len({id(candidates[0].options[m.id][0]) for m in people}) == 4


async def test_different_restrictions_and_budgets_never_share_searches():
    catalog = SearchCatalog()
    searcher = solver.Searcher(catalog, {})
    refinements = [{"maxItemPrice": 40}, {"maxItemPrice": 30},
                   {"maxItemPrice": 40, "itemRestrictions": ["VEGETARIAN"]}]
    await asyncio.gather(*(searcher._search("hamburguer", r) for r in refinements))
    assert len(catalog.calls) == 3


async def test_failed_read_does_not_poison_search_cache():
    catalog = SearchCatalog()
    original = catalog.call
    catalog.call = AsyncMock(side_effect=[gateway.MCPError("searchOffers", "invalid request")])
    searcher = solver.Searcher(catalog, {})
    with pytest.raises(gateway.MCPError):
        await searcher._search("hamburguer", {})
    catalog.call = original
    assert await searcher._search("hamburguer", {})


async def test_chat_tool_starts_search_before_final_model_reply(monkeypatch):
    session = store.get_or_create("EARLY-SEARCH")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_search(room_id):
        entered.set()
        try:
            await release.wait()
        finally:
            session.searching_recommendations = False
            session.recommend_dirty = False

    monkeypatch.setattr(service.recommend_graph, "run_recommend", slow_search)

    async def answering(session, author, text, images):
        tool = next(t for t in chat.make_tools(session, author) if t.name == "update_my_order")
        await tool.ainvoke({"desire": "smash"})
        await asyncio.wait_for(entered.wait(), 1)
        assert session.chat_busy and session.chat_phase
        assert session.searching_recommendations
        # Stand in for the final acknowledgement from the LLM.
        say(session, "Anotado!")

    monkeypatch.setattr(chat, "handle_chat", answering)
    await service.chat_turn(session, session.host_member_id, "quero um smash")
    assert not session.chat_busy and session.chat_phase is None
    task = service._recommend_tasks[session.room_id]
    release.set()
    await asyncio.wait_for(task, 1)
    assert not session.searching_recommendations


async def test_unchanged_chat_does_not_restart_restaurant_search(monkeypatch):
    session = store.get_or_create("NO-REPEAT")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await service._recommend_tasks[session.room_id]
    searches = len(gateway.get_gateway().calls)
    task = service._recommend_tasks[session.room_id]

    async def acknowledge(session, author, text, images):
        say(session, "De nada!")

    monkeypatch.setattr(chat, "handle_chat", acknowledge)
    await service.chat_turn(session, session.host_member_id, "obrigado")
    assert len(gateway.get_gateway().calls) == searches
    assert service._recommend_tasks[session.room_id] is task


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_cart_activity_lifecycle_and_duplicate_clicks(monkeypatch, outcome):
    session = store.get_or_create("CART-ACTIVITY")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await service._recommend_tasks[session.room_id]
    entered, release = asyncio.Event(), asyncio.Event()
    original = solver.validate_one

    async def validate(*args, **kwargs):
        entered.set()
        await release.wait()
        if outcome == "failure":
            raise solver.NoMatch("Prato indisponivel")
        return await original(*args, **kwargs)

    monkeypatch.setattr(solver, "validate_one", validate)
    task = asyncio.create_task(service.add_to_cart(session, session.host_member_id))
    await asyncio.wait_for(entered.wait(), 1)
    assert session.cart_pending_members == [session.host_member_id]
    assert session.model_dump(by_alias=True)["cartPendingMembers"] == [session.host_member_id]
    with pytest.raises(service.TableError, match="conferindo"):
        await service.add_to_cart(session, session.host_member_id)
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        if outcome == "failure":
            with pytest.raises(service.TableError):
                await task
        else:
            await task
            assert session.host().assigned_item
    assert not session.cart_pending_members


def test_restarting_server_clears_transient_activity(tmp_path, monkeypatch):
    session = store.get_or_create("RESTORE-ACTIVITY")
    session.busy = session.chat_busy = session.searching_recommendations = True
    session.busy_phase = session.chat_phase = session.recommend_phase = "Trabalhando…"
    session.cart_pending_members = [session.host_member_id]
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({session.room_id: session.model_dump(by_alias=True)}), encoding="utf-8")
    monkeypatch.setenv("MESA_PERSIST", str(path))
    restored = Store().get(session.room_id)
    assert not (restored.busy or restored.chat_busy or restored.searching_recommendations)
    assert not (restored.busy_phase or restored.chat_phase or restored.recommend_phase)
    assert not restored.cart_pending_members
