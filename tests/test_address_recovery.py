import pytest
from fastapi.testclient import TestClient

from agent.mesa import gateway, service
from agent.mesa.api import app
from agent.mesa.models import HostAddress
from agent.mesa.store import store


def saved_room(room="PREVIOUS"):
    session = store.get_or_create(room)
    session.host_address = HostAddress(address_id="saved", alias="Casa", label="Endereco confirmado",
                                       latitude=-22.0, longitude=-47.0)
    return session


async def test_host_confirmed_recovery_searches_without_list_addresses():
    previous = saved_room()
    session = store.get_or_create("RECOVERY")
    session.host().desire = "smash"
    service.reuse_address(session, previous.room_id, "saved", session.host_member_id)
    await service._recommend_tasks[session.room_id]
    assert session.host_address == previous.host_address
    assert session.host_address is not previous.host_address
    assert session.merchant_options
    assert all(tool != "listAddresses" for tool, _ in gateway.get_gateway().calls)


def test_choices_are_deduplicated_and_guest_cannot_read_or_apply():
    saved_room()
    saved_room("SAME-ADDRESS")
    session = store.get_or_create("CHOICES")
    guest = service.join(session, "Bia")
    assert len(service.previous_addresses(session, session.host_member_id)) == 1
    client = TestClient(app)
    assert client.get(f"/api/mesas/{session.room_id}/addresses/previous", params={"authorId": guest.id}).status_code == 409
    response = client.post(f"/api/mesas/{session.room_id}/addresses/reuse", json={
        "authorId": guest.id, "sourceRoom": "PREVIOUS", "addressId": "saved"})
    assert response.status_code == 409
    assert session.host_address is None


def test_cannot_inject_an_address_or_replace_existing_delivery():
    previous = saved_room()
    session = store.get_or_create("INVALID-ADDRESS")
    with pytest.raises(service.TableError, match="nao encontrado"):
        service.reuse_address(session, previous.room_id, "invented", session.host_member_id)
    assert session.host_address is None
    session.host_address = previous.host_address.model_copy()
    with pytest.raises(service.TableError):
        service.reuse_address(session, previous.room_id, "saved", session.host_member_id)


def test_live_addresses_do_not_leak_into_mock_choices():
    previous = saved_room()
    previous.mode = "live"
    session = store.get_or_create("MOCK")
    assert service.previous_addresses(session, session.host_member_id) == []
