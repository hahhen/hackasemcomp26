import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.mesa import audio
from agent.mesa.api import app

DATA = "data:audio/webm;base64," + base64.b64encode(b"sample-audio").decode()


def provider(monkeypatch, status=200, payload=None):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else {
            "candidates": [{"content": {"parts": [{"text": "Quero hamburger sem tomate ate 35 reais"}]}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(audio.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    return seen


async def test_audio_sent_to_gemini_returns_only_transcript(monkeypatch):
    seen = provider(monkeypatch)
    assert await audio.transcribe(DATA) == "Quero hamburger sem tomate ate 35 reais"
    request = seen[0]
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "audio/webm"
    assert "tools" not in payload


async def test_browser_codec_parameter_is_accepted(monkeypatch):
    provider(monkeypatch)
    assert await audio.transcribe(DATA.replace("audio/webm", "audio/webm;codecs=opus"))


@pytest.mark.parametrize("data", ["data:image/png;base64,YQ==", "broken", "data:audio/webm;base64,!!!!"])
async def test_invalid_audio_is_rejected_before_calling_provider(data):
    with pytest.raises(audio.TranscriptionError):
        await audio.transcribe(data)


async def test_missing_key_is_actionable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(audio.TranscriptionError, match="GEMINI_API_KEY"):
        await audio.transcribe(DATA)


async def test_quota_error_is_not_a_transcript(monkeypatch):
    provider(monkeypatch, status=429, payload={"error": {"message": "quota exceeded"}})
    with pytest.raises(audio.TranscriptionError, match="429"):
        await audio.transcribe(DATA)


async def test_empty_audio_response_is_not_sent_as_chat(monkeypatch):
    provider(monkeypatch, payload={"candidates": []})
    with pytest.raises(audio.TranscriptionError):
        await audio.transcribe(DATA)


def test_transcription_endpoint_returns_text_without_mutating_rooms(monkeypatch):
    from agent.mesa.store import store

    async def fake(data):
        assert data == DATA
        return "quero pizza"

    monkeypatch.setattr(audio, "transcribe", fake)
    response = TestClient(app).post("/api/transcribe", json={"audio": DATA})
    assert response.status_code == 200
    assert response.json() == {"text": "quero pizza"}
    assert not store.sessions
