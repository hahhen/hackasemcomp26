"""JSONL traces without prompts, tool arguments, addresses or credentials."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

_run: ContextVar[dict | None] = ContextVar("mesa_trace", default=None)
_stage: ContextVar[str] = ContextVar("mesa_stage", default="unscoped")
_logger = logging.getLogger("mesa.telemetry")
_logger.setLevel(logging.INFO)
_logger.propagate = False


def emit(event: str, **fields) -> None:
    if os.getenv("MESA_OBSERVABILITY", "true").lower() in {"false", "0", "off"}:
        return
    try:
        if not any(isinstance(handler, RotatingFileHandler) for handler in _logger.handlers):
            path = Path(os.getenv("MESA_TRACE_FILE", "logs/observability.jsonl"))
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
        current = _run.get() or {}
        row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event,
               **{key: current.get(key) for key in ("run_id", "order_id", "room_id", "graph")},
               "stage": _stage.get(), **fields}
        _logger.info(json.dumps(row, ensure_ascii=False))
    except (OSError, ValueError):
        # Diagnostics must never stop an order.
        logging.getLogger(__name__).warning("Could not write observability event")


def failure(exc: BaseException) -> dict:
    code = getattr(exc, "code", None)
    message = f"{code or ''} {exc}".lower()
    category = next((name for name, markers in {
        "upstream": ("upstream", "internal"), "auth": ("invalid_client", "unauthor", "401"),
        "rate_limit": ("429", "quota"), "timeout": ("timeout", "timed out"),
        "incomplete": ("incompleta", "sem cartid", "identificador"),
    }.items() if any(marker in message for marker in markers)), "unknown")
    return {"error_type": type(exc).__name__, "error_category": category,
            "mcp_code": code,
            "mcp_tool": getattr(exc, "tool", None)}


def operation(graph: str):
    """Create a run; nested tools/tasks inherit correlation through ContextVars."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(first, *args, **kwargs):
            from agent.mesa.store import store
            room = first if isinstance(first, str) else first.room_id
            session = store.sessions.get(room)
            trace = {"run_id": uuid.uuid4().hex, "room_id": room, "graph": graph,
                     "order_id": session.observability_order_id if session else None,
                     "calls": Counter(), "attempts": Counter(), "failures": Counter()}
            token = _run.set(trace)
            stage_token = _stage.set(graph)
            started = time.perf_counter()
            status = "ok"
            previous_error = session.last_error if session else None
            emit("run.start")
            try:
                result = await fn(first, *args, **kwargs)
                if trace.get("node_failed") or (session and session.last_error and session.last_error != previous_error):
                    status = "error"
                return result
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                emit("run.failure", status=status, **failure(exc))
                raise
            finally:
                emit("run.end", status=status, duration_ms=round((time.perf_counter()-started)*1000, 2),
                     tool_calls=dict(trace["calls"]), tool_attempts=dict(trace["attempts"]),
                     tool_failures=dict(trace["failures"]))
                _stage.reset(stage_token)
                _run.reset(token)
        return wrapped
    return decorate


def node(name: str, *, tool_event: str | None = None):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            token = None if tool_event else _stage.set(name)
            tool = args[1] if tool_event else None
            current = _run.get()
            key = f"{_stage.get()}/{tool}"
            if current and tool_event:
                current["attempts" if tool_event == "tool.attempt" else "calls"][key] += 1
            started = time.perf_counter()
            status, error = "ok", {}
            emit((tool_event or "node") + ".start", tool=tool, node=name)
            try:
                result = await fn(*args, **kwargs)
                if not tool_event and isinstance(result, dict) and result.get("error"):
                    status = "error"
                    error = {"error_type": "NodeResultError"}
                    if current:
                        current["node_failed"] = True
                return result
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                error = failure(exc)
                if current and tool_event == "tool.call" and status == "error":
                    current["failures"][key] += 1
                raise
            finally:
                emit((tool_event or "node") + ".end", tool=tool, node=name, status=status,
                     duration_ms=round((time.perf_counter()-started)*1000, 2), **error)
                if token is not None:
                    _stage.reset(token)
        return wrapped
    return decorate
