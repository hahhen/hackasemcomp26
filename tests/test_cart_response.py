import pytest

from agent.mesa import cart, gateway, service
from agent.mesa.store import store


@pytest.mark.parametrize("response", [
    {"cartId": "cart-1"}, {"cart_id": "cart-1"}, {"id": "cart-1"},
    {"data": {"cart": {"id": "cart-1"}}},
    {"structuredContent": {"cartId": "cart-1"}},
    ([], {"cartId": "cart-1"}),
    {"content": [{"type": "text", "text": '{"cartId":"cart-1"}'}]},
])
def test_cart_identifiers_are_read_from_supported_response_shapes(response):
    assert cart.created_cart_id(response) == "cart-1"


@pytest.mark.parametrize("response", [
    {"merchant": {"id": "wrong-merchant"}}, {"items": [{"id": "wrong-item"}]}, {},
])
def test_nested_unrelated_ids_do_not_become_cart_ids(response):
    with pytest.raises(gateway.MCPError, match="nao confirmou"):
        cart.created_cart_id(response)


def test_business_error_is_shown_instead_of_missing_cart_id():
    with pytest.raises(gateway.MCPError, match="Restaurante fechado"):
        cart.created_cart_id({"success": False, "message": "Restaurante fechado"})


def test_mcp_error_marker_is_not_lost_when_decoding_envelope():
    with pytest.raises(gateway.MCPError):
        cart.created_cart_id({"isError": True, "content": [
            {"type": "text", "text": '{"message":"Invalid item"}'}]})


async def test_uncertain_cart_creation_is_not_retried(monkeypatch):
    session = store.get_or_create("UNCERTAIN-CART")
    service.update_member(session, session.host_member_id, {"desire": "smash"})
    await service._recommend_tasks[session.room_id]
    await service.add_to_cart(session, session.host_member_id)
    api = gateway.get_gateway()
    original = api.call
    calls = []

    async def no_id(tool, **args):
        calls.append(tool)
        if tool == "createCart":
            return {}
        return await original(tool, **args)

    monkeypatch.setattr(api, "call", no_id)
    option = session.selected_option()
    merchant = cart.MerchantInfo(option.merchant_id, option.name)
    with pytest.raises(gateway.MCPError, match="nao confirmou"):
        await cart.build_cart(api, session, merchant, lambda *args: None)
    assert calls == ["createCart"]
    assert session.mcp_cart.cart_id is None
