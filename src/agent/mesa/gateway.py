"""Single entry point for every MCP tool call the table makes.

Two implementations share one interface:

* ``LiveGateway`` talks to the real ``sudowin-semcomp`` server through the
  LangChain tools that :mod:`agent.mcp` already loads (OAuth included).
* ``MockGateway`` (see :mod:`agent.mesa.mock_data`) is an in-memory iFood, so the
  whole flow - UI included - runs offline and never touches a real account.

``MESA_MODE=mock|live`` picks one (default ``mock``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Protocol

from dotenv import load_dotenv

from agent.mesa.parsing import decode
from agent.mesa.observability import node

load_dotenv()


class MCPError(RuntimeError):
    """A tool answered with an error payload (or could not be reached)."""

    def __init__(self, tool: str, message: str, *, code: str | None = None) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.message = message
        self.code = code


class Gateway(Protocol):
    mode: str

    async def call(self, tool: str, **args: Any) -> Any: ...


def _clean(args: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None``/empty values: the server's ``##default`` marker is not a default."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if value is None or value == "":
            continue
        out[key] = _clean(value) if isinstance(value, dict) else value
    return out


def raise_if_error(tool: str, payload: Any) -> Any:
    """MCP failures arrive as ``{"error": ...}`` inside a successful response."""
    if isinstance(payload, dict) and str(payload.get("code", "")).upper() in {
        "INTERNAL", "INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND",
        "FAILED_PRECONDITION", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED",
    }:
        raise MCPError(tool, str(payload.get("message") or payload["code"]), code=str(payload["code"]).upper())
    if isinstance(payload, dict) and (payload.get("success") is False
            or str(payload.get("status", "")).upper() in {"ERROR", "FAILED", "FAILURE"}):
        raise MCPError(tool, str(payload.get("userFriendlyMessage") or payload.get("message")
                                  or payload.get("error") or "Operacao recusada pelo iFood."))
    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            error = error.get("userFriendlyMessage") or error.get("message") or error.get("text") or str(error)
        raise MCPError(tool, str(error))
    if isinstance(payload, dict) and set(payload) == {"text"}:
        raise MCPError(tool, str(payload["text"])[:300])
    return payload


class LiveGateway:
    mode = "live"

    def __init__(self) -> None:
        self._tools: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._token_fingerprint: str | None = None
        self._auth_recovery_lock = asyncio.Lock()
        self._last_auth_recovery = float("-inf")
        self._calls = asyncio.Semaphore(max(1, int(os.getenv("MCP_MAX_CONCURRENT_CALLS", "4"))))

    async def _load(self, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            from agent.oauth import get_access_token
            try:
                token = await get_access_token(refresh=force, interactive=False)
            except RuntimeError as exc:
                raise MCPError("oauth", "Nao consegui autenticar ou renovar a sessao do iFood. "
                               "Execute python -m agent.oauth no terminal.", code="UNAUTHENTICATED") from exc
            fingerprint = hashlib.sha256(token.encode()).hexdigest()
            if self._tools is None or force or fingerprint != self._token_fingerprint:
                from agent.mcp import load_mcp_tools

                loaded = await load_mcp_tools(access_token=token)
                self._tools = {t.name: t for t in loaded}
                self._token_fingerprint = fingerprint
                logging.getLogger(__name__).info("MCP tool client loaded with current credentials")
            return self._tools

    @node("gateway", tool_event="tool.call")
    async def call(self, tool: str, **args: Any) -> Any:
        # Search pagination already owns its retries. Never retry mutations:
        # an upstream failure may occur after a cart/order was created.
        attempts = 3 if tool in {"listAddresses", "getMerchant", "getItem"} else 1
        failed_token: str | None = None
        first_internal = False
        for attempt in range(attempts):
            try:
                return await self._call_once(tool, **args)
            except MCPError as exc:
                if attempt == 0:
                    failed_token = self._token_fingerprint
                    first_internal = exc.code == "INTERNAL"
                transient = exc.code in {"INTERNAL", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED"} or any(marker in exc.message.lower() for marker in (
                    "upstream", "internal", "unavailable", "server returned an error response",
                    "timeout", "timed out", "429", "502", "503", "504",
                ))
                if not transient or attempts == 1:
                    raise
                if attempt == attempts - 1:
                    raise MCPError(tool, "O iFood esta temporariamente indisponivel "
                                   f"na consulta {tool} apos {attempts} tentativas. Tente novamente.", code=exc.code) from exc
                # Cached addresses bypass listAddresses, so every retried read
                # needs the same bounded recovery. INTERNAL can also be a true
                # outage; refresh once, not on each failure or on mutations.
                if attempt == 1 and first_internal and exc.code == "INTERNAL":
                    await self._recover_read_auth(failed_token, tool)
                logging.getLogger(__name__).warning("%s transient failure (attempt %s/%s)",
                                                    tool, attempt + 1, attempts)
                await asyncio.sleep(0.5 * (2 ** attempt))

    async def _recover_read_auth(self, failed_token: str | None, tool: str) -> None:
        if not failed_token or os.getenv("MCP_BEARER_TOKEN"):
            return
        async with self._auth_recovery_lock:
            # Other rooms share this gateway. Do not rotate the token again
            # when another request has recovered it, or on persistent outages.
            if failed_token != self._token_fingerprint:
                return
            now = time.monotonic()
            if now - self._last_auth_recovery < 300:
                return
            self._last_auth_recovery = now
            logging.getLogger(__name__).warning("Repeated %s INTERNAL; trying one OAuth refresh and client rebuild", tool)
            await self._load(force=True)

    async def _call_once(self, tool: str, **args: Any) -> Any:
        tools = await self._load()
        if tool not in tools:
            raise MCPError(tool, "tool not exposed by the MCP server")
        payload = _clean(args)
        try:
            return await self._invoke(tool, tools[tool], payload)
        except Exception as exc:
            text = str(exc).lower()
            auth_failure = getattr(exc, "code", None) == "UNAUTHENTICATED" or "401" in text or "unauthor" in text or "expired" in text
            if auth_failure:
                tools = await self._load(force=True)
                return await self._invoke(tool, tools[tool], payload)
            else:
                if isinstance(exc, MCPError):
                    raise
                raise MCPError(tool, str(exc)[:300]) from exc

    @node("gateway", tool_event="tool.attempt")
    async def _invoke(self, tool: str, adapter: Any, payload: dict) -> Any:
        async with self._calls:
            return raise_if_error(tool, decode(await adapter.ainvoke(payload)))


_gateway: Gateway | None = None


def mesa_mode() -> str:
    mode = os.getenv("MESA_MODE", "mock").strip().lower()
    return mode if mode in {"mock", "live"} else "mock"


def real_orders_enabled() -> bool:
    """``submitOrder`` spends real money; it is off unless explicitly enabled."""
    return os.getenv("MESA_SUBMIT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        if mesa_mode() == "live":
            _gateway = LiveGateway()
        else:
            from agent.mesa.mock_data import MockGateway

            _gateway = MockGateway()
    return _gateway


def set_gateway(gateway: Gateway | None) -> None:
    """Swap the gateway (used by tests)."""
    global _gateway
    _gateway = gateway
