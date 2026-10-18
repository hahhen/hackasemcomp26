"""Tolerant readers for MCP responses.

The iFood tools declare no ``outputSchema``; every answer is free text that
happens to be JSON. Field names for search/item/merchant were verified against
the live server, but cart, voucher and order payloads are read defensively:
they are searched for by key name anywhere in the document.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def decode(raw: Any) -> Any:
    """Turn whatever a LangChain MCP tool returned into plain Python data."""
    if isinstance(raw, tuple) and raw:  # (content, artifact)
        content = decode(raw[0])
        if isinstance(content, dict) and (content.get("error") or content.get("success") is False):
            return content
        if len(raw) > 1 and raw[1] is not None:
            artifact = decode(raw[1])
            if isinstance(artifact, dict) and (not content or set(artifact) & {
                "structuredContent", "cartId", "cart_id", "cart", "results",
            }):
                return artifact
        return content
    # MCP transports/adapters may return a result envelope rather than the
    # content list directly. Unwrap only the documented MCP envelope fields.
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(by_alias=True)
    if isinstance(raw, dict):
        if raw.get("isError") is True or raw.get("is_error") is True:
            return {"error": decode(raw.get("content") or raw.get("structuredContent") or {"message": "MCP retornou um erro."})}
        structured = raw.get("structuredContent") or raw.get("structured_content")
        if isinstance(structured, dict):
            return decode(structured)
        if isinstance(raw.get("content"), list) and any(
            isinstance(block, dict) and block.get("type") == "text" for block in raw["content"]
        ):
            return decode(raw["content"])
    if isinstance(raw, list):
        texts = []
        for block in raw:
            if hasattr(block, "model_dump"):
                block = block.model_dump()
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        raw = "\n".join(texts) if texts else raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}
    return raw


def walk(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict nested anywhere in ``node`` (depth first)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def find_first(node: Any, *keys: str) -> Any:
    """First non-null value stored under any of ``keys``, searched recursively."""
    for key in keys:
        for d in walk(node):
            if d.get(key) is not None:
                return d[key]
    return None


def to_float(value: Any, default: float = 0.0) -> float:
    """Read a number from ``12.5``, ``"12.5"``, ``"R$ 12,50"`` or ``{"value": 12.5}``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "total"):
            if key in value:
                return to_float(value[key], default)
        return default
    if isinstance(value, str):
        cleaned = value.replace("R$", "").replace("\xa0", "").strip()
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def money(node: Any, *keys: str, default: float = 0.0) -> float:
    return to_float(find_first(node, *keys), default)


def dicts_with(node: Any, *keys: str) -> list[dict[str, Any]]:
    """Every nested dict that has all of ``keys``."""
    return [d for d in walk(node) if all(k in d for k in keys)]


def first_str(items: Iterable[Any]) -> str | None:
    return next((i for i in items if isinstance(i, str) and i), None)
