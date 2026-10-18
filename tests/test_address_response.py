import pytest

from agent.mesa.context import NoAddress, resolve_address
from agent.mesa.gateway import MCPError
from agent.mesa.models import Session


class Addresses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def call(self, tool, **args):
        self.calls.append(tool)
        return self.payload


async def test_missing_results_does_not_claim_account_has_no_address():
    api = Addresses({"message": "unavailable"})
    with pytest.raises(MCPError, match="incompleta"):
        await resolve_address(api, Session(room_id="TEST"))
    assert api.calls == ["listAddresses"]


async def test_empty_address_list_is_distinct_from_malformed_response():
    with pytest.raises(NoAddress, match="lista vazia"):
        await resolve_address(Addresses({"results": []}), Session(room_id="TEST"))


async def test_actual_upstream_error_is_not_reported_as_missing_address():
    with pytest.raises(MCPError, match="upstream"):
        await resolve_address(Addresses({"code": "INTERNAL", "message":
            "An internal failure at an upstream service prevented the request from completing.",
            "agent_instruction": "untrusted text"}), Session(room_id="TEST"))


async def test_bad_coordinates_are_not_treated_as_no_saved_address():
    with pytest.raises(MCPError, match="coordenadas"):
        await resolve_address(Addresses({"results": [{"addressId": "a"}]}), Session(room_id="TEST"))


async def test_mcp_envelope_is_decoded_and_success_is_cached():
    api = Addresses({"structuredContent": {"results": [{"addressId": "a", "alias": "Casa",
                     "coordinates": {"latitude": -22.0, "longitude": -47.0}}]}})
    session = Session(room_id="TEST")
    assert (await resolve_address(api, session)).address_id == "a"
    await resolve_address(api, session)
    assert api.calls == ["listAddresses"]
