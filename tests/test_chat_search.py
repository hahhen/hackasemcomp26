"""Chat-to-card integration and cancellation; all iFood calls use the mock gateway."""

import asyncio

import pytest

from agent.mesa import chat, gateway, recommend_graph, service, solver
from agent.mesa.mock_data import MockGateway
from agent.mesa.store import store


async def finish_search(session):
    task = service._recommend_tasks.get(session.room_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 3)
    assert not session.searching_recommendations


async def record_wish(session, author, text, images=None):
    tool = next(t for t in chat.make_tools(session, author) if t.name == "update_my_order")
    # This is how LangChain executes synchronous tools: no event loop in the worker.
    await asyncio.to_thread(tool.invoke, {"desire": text})


async def test_threaded_chat_tool_publishes_cards_and_cart_owner(monkeypatch):
    monkeypatch.setattr(chat, "handle_chat", record_wish)
    session = store.get_or_create("THREAD-CARDS")
    guest = service.join(session, "Bia")  # no wish yet: must not block the host
    await service.chat_turn(session, session.host_member_id, "smash")
    await finish_search(session)

    assert session.selected_merchant_id
    recommendation = session.recommendation(session.host_member_id)
    assert recommendation and recommendation.description
    assert session.recommendation(guest.id) is None
    assert any(tool == "searchOffers" for tool, _ in gateway.get_gateway().calls)
    await service.add_to_cart(session, session.host_member_id, session.host_member_id)
    assert session.host().assigned_item
    assert not guest.assigned_item


async def test_new_message_cancels_turn_without_losing_either_person(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    first = True

    async def interruptible(session, author, text, images=None):
        nonlocal first
        if first:
            first = False
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        await record_wish(session, author, text, images)

    monkeypatch.setattr(chat, "handle_chat", interruptible)
    session = store.get_or_create("INTERRUPT-CHAT")
    guest = service.join(session, "Bia")
    service.start_chat_turn(session, session.host_member_id, "smash")
    await asyncio.wait_for(entered.wait(), 1)
    service.start_chat_turn(session, guest.id, "veggie")
    await asyncio.wait_for(service._chat_tasks[session.room_id], 3)
    await finish_search(session)

    assert cancelled.is_set()
    assert session.host().desire == "smash"
    assert guest.desire == "veggie"
    assert {r.member_id for r in session.recommendations} == {session.host_member_id, guest.id}
    assert not session.chat_busy


async def test_desire_change_aborts_inflight_mcp_search():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowGateway(MockGateway):
        first = True

        async def call(self, tool, **args):
            if tool == "searchOffers" and self.first:
                self.first = False
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return await super().call(tool, **args)

    gateway.set_gateway(SlowGateway())
    session = store.get_or_create("INTERRUPT-SEARCH")
    guest = service.join(session, "Bia")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await asyncio.wait_for(entered.wait(), 1)
    service.update_member(session, guest.id, {"desire": "veggie"})
    await finish_search(session)

    assert cancelled.is_set()
    assert {r.member_id for r in session.recommendations} == {session.host_member_id, guest.id}


async def test_no_common_restaurant_clears_previous_suggestions(monkeypatch):
    session = store.get_or_create("NO-COMMON")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await finish_search(session)
    assert session.recommendations

    async def no_match(*args, **kwargs):
        raise solver.NoMatch("Nenhum restaurante atende a todos.")

    monkeypatch.setattr(solver, "find_merchant", no_match)
    service.update_member(session, session.host_member_id, {"desire": "sushi"})
    await finish_search(session)
    assert not session.merchant_options
    assert not session.recommendations
    assert session.selected_merchant_id is None
    assert session.chat[-1].text == "Nenhum restaurante atende a todos."
    assert session.room_id not in recommend_graph._CANDIDATES


async def test_mcp_failure_is_reported_as_failure_not_empty_search():
    class BrokenGateway(MockGateway):
        async def call(self, tool, **args):
            if tool == "searchOffers":
                raise gateway.MCPError(tool, "invalid_client")
            return await super().call(tool, **args)

    gateway.set_gateway(BrokenGateway())
    session = store.get_or_create("MCP-FAILURE")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await finish_search(session)
    assert "invalid_client" in session.last_error
    assert not session.recommendations


def test_specific_wish_never_broadens_to_unrelated_food():
    from agent.mesa.intents import heuristic_intent
    from agent.mesa.models import Member

    intent = heuristic_intent(Member(id="m", name="Bia", desire="quero sushi"))
    assert intent.queries == ["sushi"]


async def test_new_wish_invalidates_only_that_persons_cart_item():
    session = store.get_or_create("CHANGE-CART")
    guest = service.join(session, "Bia")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    service.update_member(session, guest.id, {"desire": "veggie"})
    await finish_search(session)
    await service.add_to_cart(session, session.host_member_id, session.host_member_id)
    await service.add_to_cart(session, guest.id, guest.id)
    guests_item = guest.assigned_item
    service.update_member(session, session.host_member_id, {"desire": "burger"})
    assert session.host().assigned_item is None
    assert guest.assigned_item == guests_item
    await finish_search(session)


async def test_inflight_add_cannot_commit_an_outdated_suggestion(monkeypatch):
    session = store.get_or_create("STALE-ADD")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await finish_search(session)
    entered = asyncio.Event()
    resume = asyncio.Event()
    original = solver.validate_one

    async def slow_validation(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(solver, "validate_one", slow_validation)
    addition = asyncio.create_task(service.add_to_cart(session, session.host_member_id))
    await asyncio.wait_for(entered.wait(), 1)
    service.update_member(session, session.host_member_id, {"desire": "veggie"})
    await finish_search(session)
    resume.set()
    with pytest.raises(service.TableError, match="sugestao mudou"):
        await addition
    assert session.host().assigned_item is None


async def test_restart_drains_pending_chat_before_replacing_room(monkeypatch):
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chat, "handle_chat", blocked)
    session = store.get_or_create("RESTART-CHAT")
    service.start_chat_turn(session, session.host_member_id, "smash")
    await asyncio.wait_for(entered.wait(), 1)
    job = service._chat_tasks[session.room_id]
    await service.cancel_processing(session)
    replacement = store.reset(session.room_id)
    assert job.cancelled()
    assert session.room_id not in service._pending_turns
    assert not replacement.chat
    assert not replacement.host().desire
