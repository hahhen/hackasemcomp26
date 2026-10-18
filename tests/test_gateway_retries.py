from unittest.mock import AsyncMock

import pytest

from agent.mesa.gateway import LiveGateway, MCPError


@pytest.mark.parametrize('tool', ['listAddresses', 'getMerchant', 'getItem'])
async def test_read_recovers_from_upstream_failure(monkeypatch, tool):
    gateway = LiveGateway()
    gateway._call_once = AsyncMock(side_effect=[MCPError(tool, 'An internal failure at an upstream service'), {'results': []}])
    monkeypatch.setattr('agent.mesa.gateway.asyncio.sleep', AsyncMock())
    assert await gateway.call(tool) == {'results': []}
    assert gateway._call_once.await_count == 2


async def test_read_failure_has_bounded_retries_and_tool_name(monkeypatch):
    gateway = LiveGateway()
    gateway._call_once = AsyncMock(side_effect=MCPError('listAddresses', 'upstream failure'))
    monkeypatch.setattr('agent.mesa.gateway.asyncio.sleep', AsyncMock())
    with pytest.raises(MCPError, match='listAddresses apos 3 tentativas'):
        await gateway.call('listAddresses')
    assert gateway._call_once.await_count == 3


@pytest.mark.parametrize('tool,message', [('createCart', 'upstream failure'), ('submitOrder', 'upstream failure'), ('searchOffers', 'upstream failure'), ('listAddresses', 'Client authentication failed')])
async def test_mutations_search_and_auth_are_not_retried(tool, message):
    gateway = LiveGateway()
    gateway._call_once = AsyncMock(side_effect=MCPError(tool, message))
    with pytest.raises(MCPError, match=message):
        await gateway.call(tool)
    assert gateway._call_once.await_count == 1
