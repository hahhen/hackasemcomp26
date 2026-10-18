import asyncio
import json

import pytest

from agent.mesa import observability as obs
from agent.mesa.gateway import LiveGateway, MCPError
from agent.mesa.store import store


async def test_concurrent_rooms_keep_correlation_and_count_retries(monkeypatch):
    events = []
    monkeypatch.setattr(obs, "emit", lambda event, **fields: events.append({
        "event": event, **(obs._run.get() or {}), "stage": obs._stage.get(), **fields}))

    class Tool:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, payload):
            self.calls += 1
            await asyncio.sleep(0)
            if self.calls == 1:
                return {"code": "INTERNAL", "message": "upstream failure"}
            return {"results": []}

    @obs.operation("recommend")
    async def run(room):
        gateway = LiveGateway()
        async def loaded(force=False):
            return tools
        tools = {"listAddresses": Tool()}
        gateway._load = loaded

        @obs.node("load_context")
        async def load():
            return await gateway.call("listAddresses")

        await load()

    for room in ("TRACE-A", "TRACE-B"):
        store.get_or_create(room)
    await asyncio.gather(run("TRACE-A"), run("TRACE-B"))
    ends = [row for row in events if row["event"] == "run.end"]
    assert {row["room_id"] for row in ends} == {"TRACE-A", "TRACE-B"}
    assert len({row["run_id"] for row in ends}) == 2
    for row in ends:
        assert row["tool_calls"] == {"load_context/listAddresses": 1}
        assert row["tool_attempts"] == {"load_context/listAddresses": 2}
    errors = [row for row in events if row["event"] == "tool.attempt.end" and row["status"] == "error"]
    assert len(errors) == 2
    assert all(row["error_type"] == "MCPError" for row in errors)
    assert obs._run.get() is None


async def test_returned_error_and_cancellation_are_traced(monkeypatch):
    events = []
    monkeypatch.setattr(obs, "emit", lambda event, **fields: events.append({"event": event, **fields}))

    @obs.node("gate")
    async def gate():
        return {"error": "not ready"}

    @obs.node("slow")
    async def cancelled():
        raise asyncio.CancelledError()

    await gate()
    with pytest.raises(asyncio.CancelledError):
        await cancelled()
    assert events[1]["status"] == "error"
    assert events[-1]["status"] == "cancelled"


def test_jsonl_contains_no_exception_payload_or_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("MESA_OBSERVABILITY", "true")
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("MESA_TRACE_FILE", str(path))
    obs.emit("tool.call.end", **obs.failure(MCPError("getItem", "upstream private-secret-address")))
    for handler in list(obs._logger.handlers):
        handler.close()
        obs._logger.removeHandler(handler)
    raw = path.read_text(encoding="utf-8")
    assert "private-secret-address" not in raw
    assert json.loads(raw)["error_category"] == "upstream"
