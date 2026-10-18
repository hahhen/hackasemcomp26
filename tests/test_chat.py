"""Group chat: author tagging, author-bound tools, and the host-only rules.

No LLM runs here: the provider points at a dead port (see conftest), so the
agent falls back - which is exactly the path that must never leave the chat
hanging.
"""

import asyncio

from fastapi.testclient import TestClient

from agent.mesa import chat, service
from agent.mesa.api import app
from agent.mesa.store import store

ROOM = "CHAT"


async def settle(session) -> None:
    for _ in range(200):
        if not (session.busy or session.searching_recommendations):
            return
        await asyncio.sleep(0.01)


def demo():
    session = store.get_or_create(ROOM)
    service.seed_demo(session)
    return session


def tools_for(session, member_id: str) -> dict:
    who = session.host() if member_id == "host" else session.member(member_id)
    return {t.name: t for t in chat.make_tools(session, who)}


async def test_message_is_tagged_with_the_author_and_never_hangs():
    session = demo()

    await service.chat_turn(session, "m2", "oi gente")
    await settle(session)

    assert session.chat[0].kind == "user"
    assert session.chat[0].author_id == "m2" and session.chat[0].author_name == "Bia"
    assert session.chat[-1].kind == "agent"  # the fallback still answers
    assert not session.chat_busy


async def test_a_message_without_the_llm_still_grants_the_desire():
    """The fallback uses the words themselves, but never overwrites a wish."""
    session = store.get_or_create(ROOM)
    guest = service.join(session, "Duda")

    await service.chat_turn(session, guest.id, "quero um lanche de costela com queijo")
    await settle(session)

    assert "costela" in session.member(guest.id).desire
    assert any("Anotei" in m.text for m in session.chat)

    await service.chat_turn(session, guest.id, "mudei de ideia, quero sushi")
    await settle(session)
    assert session.member(guest.id).desire == "quero um lanche de costela com queijo"


async def test_unknown_author_falls_back_to_the_host():
    session = demo()
    await service.chat_turn(session, "quem-e-esse", "oi")
    await settle(session)
    assert session.chat[0].author_id == session.host_member_id


async def test_update_my_order_only_touches_the_speaker():
    session = demo()
    tools = tools_for(session, "m2")

    assert tools["update_my_order"].invoke({"desire": "quero pizza", "max_budget": 22})
    assert session.member("m2").desire == "quero pizza"
    assert session.member("m2").max_budget == 22
    assert session.member("m3").desire != "quero pizza"
    assert session.member("m1").name.endswith("(host)")


def test_only_the_host_can_edit_others():
    session = demo()
    friend = tools_for(session, "m2")
    assert "erro" in friend["update_member"].invoke({"name": "Joao", "desire": "nada"})
    assert "erro" in friend["remove_member"].invoke({"name": "Joao"})
    # searching is read-only, so anyone may ask for it
    assert "erro" not in friend["refresh_search"].invoke({})

    host = tools_for(session, "host")
    assert "erro" not in host["update_member"].invoke({"name": "Novo", "desire": "sushi"})
    assert session.member("m2").desire == "Lanche ou prato vegetariano gostoso"


async def test_menu_search_is_read_only_and_reports_what_it_found():
    session = demo()
    tools = tools_for(session, "m2")

    answer = await tools["search_menu"].ainvoke({"query": "veggie"})

    assert "achei no iFood" in answer
    assert session.mcp_cart.cart_id is None  # nothing was added
    assert session.host_address is not None  # the address was resolved on demand


def test_chat_endpoint_records_the_message_and_returns_202():
    client = TestClient(app)
    client.post(f"/api/mesas/{ROOM}/demo")

    response = client.post(f"/api/mesas/{ROOM}/chat", json={"authorId": "m2", "message": "oi"})
    assert response.status_code == 202
    last = response.json()["chat"][-1]
    assert last["kind"] == "user" and last["authorId"] == "m2" and last["authorName"] == "Bia"

    assert client.post(f"/api/mesas/{ROOM}/chat", json={"message": "  "}).status_code == 422


def test_join_and_host_only_http_guards():
    client = TestClient(app)
    client.post(f"/api/mesas/{ROOM}/demo")

    joined = client.post(f"/api/mesas/{ROOM}/join", json={"name": "Duda"})
    assert joined.status_code == 201
    body = joined.json()
    ids = {m["name"]: m["id"] for m in body["members"]}
    assert ids["Duda"] == body["members"][-1]["id"]

    assert client.post(f"/api/mesas/{ROOM}/join", json={"name": "duda"}).status_code == 409
    # a friend cannot pick the restaurant, close the cart, or pay someone else
    assert (
        client.patch(
            f"/api/mesas/{ROOM}/merchant", json={"merchantId": "x", "authorId": ids["Duda"]}
        ).status_code
        == 409
    )
    assert client.post(f"/api/mesas/{ROOM}/cart/finish", json={"authorId": ids["Duda"]}).status_code == 409
    assert (
        client.post(f"/api/mesas/{ROOM}/members/{ids['Bia']}/pay", json={"authorId": ids["Duda"]}).status_code
        == 409
    )
    # searching is read-only, so anyone can ask for it (202)
    assert client.post(f"/api/mesas/{ROOM}/recommend").status_code == 202
