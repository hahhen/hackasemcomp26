"""Exercise the supported model paths without contacting external services."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import providers
from agent.mesa import chat, service
from agent.mesa import gateway
from agent.mesa.api import app
from agent.mesa.store import store


@pytest.mark.parametrize("environment,model,host", [
    ("local", "local-test-model", "localhost"),
    ("remote", "paid-test-model", "opencode.ai"),
])
async def test_supported_models_execute_chat_tools_and_publish_suggestions(
    monkeypatch, environment, model, host,
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENCODE_ZEN_MODEL", "paid-test-model")
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test-zen-key")
    monkeypatch.setenv("MESA_CHAT_LLM", "true")
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "wish-call", "type": "function", "function": {
                    "name": "update_my_order",
                    "arguments": json.dumps({"desire": "smash", "max_budget": 50}),
                },
            }]}
            reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Seu smash esta anotado!"}
            reason = "stop"
        return httpx.Response(200, json={
            "id": f"reply-{len(requests)}", "object": "chat.completion", "created": 1,
            "model": model, "choices": [{"index": 0, "message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        })

    actual_client = providers.GuardedChatOpenAI
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(providers, "GuardedChatOpenAI", lambda **kwargs: actual_client(
            **kwargs, http_async_client=client,
        ))
        session = store.get_or_create(f"MODEL-{environment}")
        await service.chat_turn(session, session.host_member_id, "quero um smash ate 50 reais")
        task = service._recommend_tasks.get(session.room_id)
        if task:
            await asyncio.wait_for(asyncio.shield(task), 3)

    assert len(requests) == 2  # Tool call followed by its grounded acknowledgement.
    assert all(request.url.host == host and body["model"] == model for request, body in requests)
    assert session.host().desire == "smash" and session.host().max_budget == 50
    assert session.selected_merchant_id and session.recommendation(session.host_member_id)
    assert any(message.text == "Seu smash esta anotado!" for message in session.chat)
    assert {tool["function"]["name"] for tool in requests[0][1]["tools"]} == {
        "update_my_order", "update_member", "remove_member", "show_table", "search_menu", "refresh_search",
    }
    assert not session.chat_busy


def test_local_needs_no_cloud_keys_and_health_reports_the_configured_model(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("OLLAMA_MODEL", "installed-llama")
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    model = providers.build_model()
    assert model.model_name == "installed-llama"
    health = TestClient(app).get("/api/health").json()
    assert health["model"] == "installed-llama"
    assert health["mode"] == "mock"


def test_remote_requires_zen_key_even_if_retired_provider_keys_exist(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "remote")
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "retired-key")
    monkeypatch.setenv("GEMINI_API_KEY", "audio-only-key")
    with pytest.raises(RuntimeError, match="OPENCODE_ZEN_API_KEY"):
        providers.build_model()


def test_default_models_preserve_existing_configuration(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_ZEN_MODEL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "local")
    assert providers.primary_model_name() == "ifood-agent"
    monkeypatch.setenv("ENVIRONMENT", "remote")
    assert providers.primary_model_name() == "glm-5.3-flash"


@pytest.mark.parametrize("environment", ["local", "remote"])
@pytest.mark.parametrize("mode", ["mock", "live"])
def test_text_model_and_ifood_mode_are_independent(monkeypatch, environment, mode):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("MESA_MODE", mode)
    gateway.set_gateway(None)
    assert gateway.get_gateway().mode == mode
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["environment"] == environment
    assert response.json()["mode"] == mode
