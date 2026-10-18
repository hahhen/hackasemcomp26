import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent import mcp, oauth
from agent.mesa.gateway import LiveGateway, MCPError
from agent.mesa.observability import failure


async def test_reuses_tools_only_while_access_token_is_unchanged(monkeypatch):
    token = AsyncMock(side_effect=["old-token", "old-token", "new-token"])
    tools = AsyncMock(side_effect=[[SimpleNamespace(name="getItem")], [SimpleNamespace(name="getItem")]])
    monkeypatch.setattr(oauth, "get_access_token", token)
    monkeypatch.setattr(mcp, "load_mcp_tools", tools)
    gw = LiveGateway()
    first = await gw._load()
    assert await gw._load() is first
    assert await gw._load() is not first
    assert tools.await_count == 2
    assert tools.await_args_list[1].kwargs == {"access_token": "new-token"}
    assert all(call.kwargs["interactive"] is False for call in token.await_args_list)


async def test_auth_error_refreshes_token_before_rebuilding_tools(monkeypatch):
    old = SimpleNamespace(name="getItem", ainvoke=AsyncMock(return_value={"code": "UNAUTHENTICATED", "message": "expired"}))
    new = SimpleNamespace(name="getItem", ainvoke=AsyncMock(return_value={"itemName": "Burger"}))
    token = AsyncMock(side_effect=["old-token", "new-token"])
    monkeypatch.setattr(oauth, "get_access_token", token)
    monkeypatch.setattr(mcp, "load_mcp_tools", AsyncMock(side_effect=[[old], [new]]))
    assert await LiveGateway().call("getItem") == {"itemName": "Burger"}
    assert token.await_args_list[1].kwargs == {"refresh": True, "interactive": False}


async def test_repeated_address_internal_recovers_with_new_token(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    active = "old-token"

    async def get_token(*, refresh=False, interactive=True):
        nonlocal active
        if refresh:
            active = "new-token"
        return active

    old = SimpleNamespace(name="listAddresses", ainvoke=AsyncMock(return_value={"code": "INTERNAL", "message": "upstream failure"}))
    new = SimpleNamespace(name="listAddresses", ainvoke=AsyncMock(return_value={"results": []}))
    tokens = AsyncMock(side_effect=get_token)
    monkeypatch.setattr(oauth, "get_access_token", tokens)
    monkeypatch.setattr(mcp, "load_mcp_tools", AsyncMock(side_effect=[[old], [new]]))
    monkeypatch.setattr("agent.mesa.gateway.asyncio.sleep", AsyncMock())
    assert await LiveGateway().call("listAddresses") == {"results": []}
    assert old.ainvoke.await_count == 2
    assert new.ainvoke.await_count == 1
    assert sum(call.kwargs["refresh"] for call in tokens.await_args_list) == 1


async def test_address_recovery_has_shared_cooldown(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    gw = LiveGateway()
    gw._token_fingerprint = "old"

    async def load(force=False):
        gw._token_fingerprint = "new"

    gw._load = AsyncMock(side_effect=load)
    await asyncio.gather(*(gw._recover_address_auth("old") for _ in range(4)))
    await gw._recover_address_auth("new")
    gw._load.assert_awaited_once_with(force=True)


async def test_internal_mutation_is_never_retried_or_refreshed(monkeypatch):
    gw = LiveGateway()
    gw._token_fingerprint = "token"
    adapter = SimpleNamespace(ainvoke=AsyncMock(return_value={"code": "INTERNAL", "message": "upstream failure"}))
    gw._load = AsyncMock(return_value={"createCart": adapter})
    with pytest.raises(MCPError):
        await gw.call("createCart")
    adapter.ainvoke.assert_awaited_once()
    gw._load.assert_awaited_once_with()


async def test_static_bearer_does_not_trigger_oauth_recovery(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "static")
    gw = LiveGateway()
    gw._token_fingerprint = "token"
    gw._load = AsyncMock()
    await gw._recover_address_auth("token")
    gw._load.assert_not_awaited()


async def test_expired_token_is_refreshed_without_browser_login(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(oauth, "load_config", lambda: object())
    monkeypatch.setattr(oauth, "_load_cached", lambda: {"access_token": "old", "refresh_token": "refresh", "expires_at": 0})
    refresh = Mock(return_value={"access_token": "new"})
    browser = AsyncMock()
    monkeypatch.setattr(oauth, "_token_request", refresh)
    monkeypatch.setattr(oauth, "_save_cached", Mock())
    monkeypatch.setattr(oauth, "_authorize", browser)
    assert await oauth.get_access_token(interactive=False) == "new"
    browser.assert_not_awaited()
    assert refresh.call_args.args[1]["grant_type"] == "refresh_token"


async def test_background_auth_failure_does_not_open_browser(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(oauth, "load_config", lambda: object())
    monkeypatch.setattr(oauth, "_load_cached", lambda: None)
    browser = AsyncMock()
    monkeypatch.setattr(oauth, "_authorize", browser)
    with pytest.raises(RuntimeError, match="python -m agent.oauth"):
        await oauth.get_access_token(interactive=False)
    browser.assert_not_awaited()


async def test_upstream_code_survives_all_retries(monkeypatch):
    gw = LiveGateway()
    adapter = SimpleNamespace(ainvoke=AsyncMock(return_value={"code": "INTERNAL", "message": "upstream failure"}))
    gw._load = AsyncMock(return_value={"getItem": adapter})
    monkeypatch.setattr("agent.mesa.gateway.asyncio.sleep", AsyncMock())
    with pytest.raises(MCPError) as info:
        await gw.call("getItem")
    assert adapter.ainvoke.await_count == 3
    assert info.value.code == "INTERNAL"
    assert failure(info.value)["error_category"] == "upstream"
    assert failure(info.value)["mcp_code"] == "INTERNAL"


async def test_concurrency_limit_applies_across_operations(monkeypatch):
    monkeypatch.setenv("MCP_MAX_CONCURRENT_CALLS", "2")
    current = peak = 0

    async def invoke(payload):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(.01)
        current -= 1
        return {"itemName": "Burger"}

    gw = LiveGateway()
    gw._load = AsyncMock(return_value={"getItem": SimpleNamespace(ainvoke=invoke)})
    results = await asyncio.gather(*(gw.call("getItem") for _ in range(7)))
    assert len(results) == 7
    assert peak == 2
