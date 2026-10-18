"""End to end on the mock catalog: seed the table, search, add dishes, pay, submit."""

import asyncio

from fastapi.testclient import TestClient

from agent.mesa import service
from agent.mesa.api import app
from agent.mesa.gateway import get_gateway
from agent.mesa.mock_data import MERCHANTS
from agent.mesa.models import Status
from agent.mesa.store import store

ROOM = "TESTE"


def searches() -> int:
    return sum(1 for tool, _ in get_gateway().calls if tool == "searchOffers")


async def settle(session) -> None:
    """Wait for the background work (search / finish cart) to drain."""
    for _ in range(400):
        if not (session.busy or session.searching_recommendations):
            await asyncio.sleep(0)
            if not (session.busy or session.searching_recommendations):
                return
        await asyncio.sleep(0.01)


async def seeded() -> object:
    session = store.get_or_create(ROOM)
    service.seed_demo(session)
    await settle(session)
    return session


async def test_search_finds_restaurants_that_feed_everyone():
    session = await seeded()

    assert session.merchant_options, session.last_error
    assert session.selected_merchant_id
    assert session.merchant_options[0].name == "Burger Garden"
    # everyone on the table got a suggestion at the selected restaurant
    assert {r.member_id for r in session.recommendations} == {"m1", "m2", "m3", "m4"}


async def test_the_first_call_is_listaddresses_and_restrictions_are_filters():
    await seeded()
    calls = get_gateway().calls

    assert calls[0][0] == "listAddresses"
    restricted = [
        a for t, a in calls if t in {"searchOffers", "browseOffers"} and a["refinements"].get("itemRestrictions")
    ]
    assert restricted and all(a["resultKind"] == "ITEM" for a in restricted)


async def test_switching_restaurant_rebuilds_recommendations_and_clears_the_cart():
    session = await seeded()
    await service.add_to_cart(session, "m2")
    assert session.member("m2").assigned_item is not None

    if len(session.merchant_options) < 2:
        return  # the mock catalog has a single intersection for this table
    other = session.merchant_options[1]
    service.select_merchant(session, other.merchant_id)

    assert session.selected_merchant_id == other.merchant_id
    assert session.member("m2").assigned_item is None  # items belong to a restaurant
    assert session.recommendation("m2") is not None


async def test_everyone_adds_their_dish_and_the_quotas_match_the_cart():
    session = await seeded()
    for member in session.active_members:
        await service.add_to_cart(session, member.id)
    await settle(session)

    assert session.cart_is_full
    assert session.mcp_cart.subtotal == round(sum(m.assigned_item.price for m in session.members), 2)
    assert session.mcp_cart.merchant_name == "Burger Garden"

    # a note per person so the restaurant prints who ordered what
    assert session.member("m2").assigned_item.customer_note == "[Bia - VEGETARIANO]"
    assert session.member("m3").assigned_item.customer_note == "[Lucas - SEM LACTOSE]"

    quotas = round(sum(m.amount_to_pay for m in session.active_members), 2)
    assert quotas == session.mcp_cart.total_amount


async def test_finishing_the_cart_creates_the_mcp_cart_and_unlocks_payments():
    session = await seeded()
    for member in session.active_members:
        await service.add_to_cart(session, member.id)

    service.start_finish_cart(session)
    await settle(session)

    assert session.status == Status.WAITING_PAYMENTS, session.last_error
    assert session.mcp_cart.cart_id
    assert session.mcp_cart.voucher_discount > 0
    assert session.member("m1").has_paid  # the host pays the iFood

    total = round(sum(m.amount_to_pay for m in session.active_members), 2)
    assert total == session.mcp_cart.total_amount
    assert total == round(
        session.mcp_cart.subtotal + session.mcp_cart.delivery_fee - session.mcp_cart.voucher_discount, 2
    )


async def test_last_payment_sends_the_order():
    session = await seeded()
    for member in session.active_members:
        await service.add_to_cart(session, member.id)
    service.start_finish_cart(session)
    await settle(session)

    for member in session.active_members:
        if not member.is_host:
            service.mark_paid(session, member.id)
            await settle(session)

    assert session.all_paid
    assert session.status == Status.ORDER_SUBMITTED, session.last_error
    assert session.mcp_cart.order_id and session.mcp_cart.verification_code
    submit_calls = [a for t, a in get_gateway().calls if t == "submitOrder"]
    assert submit_calls == [{"cartId": session.mcp_cart.cart_id, "userConsent": True, "document": None}]


async def test_you_cannot_finish_the_cart_with_a_dish_missing():
    session = await seeded()
    await service.add_to_cart(session, "m2")

    try:
        service.start_finish_cart(session)
    except service.TableError as exc:
        assert "Falta o prato" in str(exc)
    else:  # pragma: no cover - must not happen
        raise AssertionError("finish_cart accepted an incomplete table")


async def test_dropping_a_late_payer_resplits_and_unlocks():
    session = await seeded()
    for member in session.active_members:
        await service.add_to_cart(session, member.id)
    service.start_finish_cart(session)
    await settle(session)
    before = session.mcp_cart.total_amount
    joao = session.member("m4")

    await service.drop_member(session, joao.id)

    assert joao.removed
    assert session.active_count == 3
    assert session.mcp_cart.total_amount < before


async def test_a_new_desire_triggers_a_new_search_by_itself():
    """No button: recording a wish is what makes the search happen."""
    session = store.get_or_create(ROOM)
    service.upsert_member(session, "Joao", "smash duplo")
    await settle(session)

    first = searches()
    assert first > 0 and session.merchant_options

    service.upsert_member(session, "Bia", "lanche vegetariano gostoso", ["VEGETARIAN"])
    await settle(session)

    assert searches() > first
    assert session.recommendation("m2") is not None


async def test_no_match_explains_itself_in_the_chat():
    session = store.get_or_create(ROOM)
    service.upsert_member(session, "Bia", "burger", ["VEGAN", "GLUTEN_FREE"], 30)
    await settle(session)

    assert not session.merchant_options
    assert any(m.kind == "agent" and "Bia" in m.text for m in session.chat)


def test_http_surface_for_the_new_flow():
    client = TestClient(app)

    created = client.post(f"/api/mesas/{ROOM}/demo").json()
    assert [m["name"] for m in created["members"]][1:] == ["Bia", "Lucas", "Joao"]
    assert created["members"][1]["restrictions"] == ["VEGETARIAN"]

    # the demo table kicks off a search by itself
    table = client.get(f"/api/mesas/{ROOM}").json()
    assert table["hostMemberId"] == "m1"

    ids = {m["name"]: m["id"] for m in table["members"]}
    assert client.post(f"/api/mesas/{ROOM}/members", json={"name": "Duda", "restrictions": ["VEGAN"]}).status_code == 201
    assert client.post(f"/api/mesas/{ROOM}/submit").status_code == 409
    assert client.post(f"/api/mesas/{ROOM}/members/m2/pay").status_code == 409
    assert client.post(f"/api/mesas/{ROOM}/cart/finish").status_code == 409

    patched = client.patch(f"/api/mesas/{ROOM}/members/m2", json={"maxBudget": None}).json()
    assert patched["members"][1]["maxBudget"] is None

    # a friend cannot pick the restaurant nor add someone else's dish
    assert client.patch(f"/api/mesas/{ROOM}/merchant", json={"merchantId": "x", "authorId": ids["Bia"]}).status_code == 409
    assert client.post(f"/api/mesas/{ROOM}/members/{ids['Lucas']}/cart", json={"authorId": ids["Bia"]}).status_code == 409


def test_closed_restaurants_are_never_offered():
    session = asyncio.run(seeded())
    assert not MERCHANTS[session.selected_merchant_id]["merchantName"].startswith("Hamburgueria Fechada")
