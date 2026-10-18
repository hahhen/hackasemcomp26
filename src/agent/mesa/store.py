"""The whole "database": a dict of tables in memory, optionally mirrored to JSON.

``MESA_PERSIST=path/to/sessions.json`` keeps tables across server restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from agent.mesa.gateway import get_gateway
from agent.mesa.models import ChatMessage, Event, Member, Session

DEFAULT_ROOM = "SEMCOMP-2026"
MAX_EVENTS = 200


def _persist_path() -> Path | None:
    raw = os.getenv("MESA_PERSIST", "").strip()
    return Path(raw) if raw else None


class Store:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self._load()

    # --- persistence -----------------------------------------------------
    def _load(self) -> None:
        path = _persist_path()
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for room, raw in data.items():
                session = Session.model_validate(raw)
                session.busy = False
                self.sessions[room] = session
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        path = _persist_path()
        if not path:
            return
        data = {room: s.model_dump(mode="json", by_alias=True) for room, s in self.sessions.items()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- sessions --------------------------------------------------------
    def get(self, room_id: str) -> Session | None:
        return self.sessions.get(normalize_room(room_id))

    def get_or_create(self, room_id: str) -> Session:
        room = normalize_room(room_id)
        if room not in self.sessions:
            self.sessions[room] = new_session(room)
            self.save()
        return self.sessions[room]

    def reset(self, room_id: str) -> Session:
        room = normalize_room(room_id)
        self.sessions[room] = new_session(room)
        self.save()
        return self.sessions[room]

    def lock(self, room_id: str) -> asyncio.Lock:
        return self.locks.setdefault(normalize_room(room_id), asyncio.Lock())


def normalize_room(room_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", room_id.strip().upper()) or DEFAULT_ROOM


def new_session(room_id: str) -> Session:
    return Session(
        room_id=room_id,
        mode=get_gateway().mode,  # type: ignore[arg-type]
        members=[Member(id="m1", name="Voce (host)", is_host=True)],
    )


def log(session: Session, text: str, kind: str = "info") -> None:
    session.events.append(Event(kind=kind, text=text))  # type: ignore[arg-type]
    del session.events[:-MAX_EVENTS]


def say(session: Session, text: str, kind: str = "agent") -> None:
    """Post in the shared group chat as the agent (or as the system)."""
    session.chat.append(ChatMessage(kind=kind, author_name="Agente", text=text))  # type: ignore[arg-type]


def heard(session: Session, author: Member, text: str, images: list[str] | None = None) -> None:
    """Record what a person said, tagged with their member id."""
    session.chat.append(
        ChatMessage(
            kind="user",
            author_id=author.id,
            author_name=author.name,
            text=text,
            images=images or [],
        )
    )


store = Store()
