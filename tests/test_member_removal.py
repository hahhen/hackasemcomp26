from fastapi.testclient import TestClient

from agent.mesa import service
from agent.mesa.api import app
from agent.mesa.store import store


def test_only_host_can_remove_guest():
    session = store.get_or_create("REMOVE-MEMBER")
    guest = service.join(session, "Bia")
    client = TestClient(app)
    url = f"/api/mesas/{session.room_id}/members/{guest.id}"
    response = client.request("DELETE", url, json={"authorId": guest.id})
    assert response.status_code == 409
    assert session.member(guest.id) is not None
    response = client.request("DELETE", url, json={"authorId": session.host_member_id})
    assert response.status_code == 200
    assert session.member(guest.id) is None


def test_host_cannot_remove_themself():
    session = store.get_or_create("KEEP-HOST")
    response = TestClient(app).request(
        "DELETE", f"/api/mesas/{session.room_id}/members/{session.host_member_id}",
        json={"authorId": session.host_member_id},
    )
    assert response.status_code == 409
    assert session.host() is not None
