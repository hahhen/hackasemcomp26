from fastapi.testclient import TestClient

from agent.mesa.api import app
from agent.mesa.gateway import get_gateway
from agent.mesa.store import store


def test_create_room_returns_unique_code_and_creator_identity():
    client = TestClient(app)
    first = client.post("/api/rooms", json={"name": "Arthur"})
    second = client.post("/api/rooms", json={"name": "Bia"})
    assert first.status_code == second.status_code == 201
    entry = first.json()
    assert entry["session"]["roomId"] != second.json()["session"]["roomId"]
    assert entry["memberId"] == entry["session"]["hostMemberId"]
    assert entry["session"]["members"][0]["name"] == "Arthur"
    assert not get_gateway().calls  # creating/joining never triggers iFood searches


def test_invite_join_returns_member_identity_not_host():
    client = TestClient(app)
    created = client.post("/api/rooms", json={"name": "Arthur"}).json()
    room = created["session"]["roomId"]
    joined = client.post(f"/api/rooms/{room.lower()}/join", json={"name": "jp"})
    assert joined.status_code == 201
    entry = joined.json()
    assert entry["memberId"] != entry["session"]["hostMemberId"]
    member = next(m for m in entry["session"]["members"] if m["id"] == entry["memberId"])
    assert member["name"] == "jp" and not member["isHost"]
    assert not get_gateway().calls
    assert client.get(f"/api/rooms/{room}").json()["members"][-1]["id"] == entry["memberId"]


def test_unknown_invite_does_not_create_room_or_promote_joiner():
    client = TestClient(app)
    assert client.get("/api/rooms/DOES-NOT-EXIST").status_code == 404
    assert client.post("/api/rooms/DOES-NOT-EXIST/join", json={"name": "jp"}).status_code == 404
    assert not store.sessions


def test_duplicate_or_blank_name_is_reported_without_extra_members():
    client = TestClient(app)
    assert client.post("/api/rooms", json={"name": "  "}).status_code == 422
    created = client.post("/api/rooms", json={"name": "Arthur"}).json()
    room = created["session"]["roomId"]
    assert client.post(f"/api/rooms/{room}/join", json={"name": "arthur"}).status_code == 409
    assert client.post(f"/api/rooms/{room}/join", json={"name": "  "}).status_code == 409
    assert len(store.get(room).members) == 1


def test_joining_one_room_does_not_modify_another():
    client = TestClient(app)
    first = client.post("/api/rooms", json={"name": "Arthur"}).json()["session"]["roomId"]
    second = client.post("/api/rooms", json={"name": "Bia"}).json()["session"]["roomId"]
    client.post(f"/api/rooms/{first}/join", json={"name": "jp"})
    assert len(store.get(first).members) == 2
    assert len(store.get(second).members) == 1
