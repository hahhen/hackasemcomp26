"""FastAPI backend for iFood Mesa.

Run:  uvicorn agent.mesa.api:app --reload --port 8000

The frontend polls ``GET /api/mesas/{room}`` every ~1.5 s; there are no
websockets and no database - see :mod:`agent.mesa.store`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load before application imports: the project's .env owns server configuration.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from agent.mesa import service
from agent.mesa.chat import chat_llm_enabled
from agent.mesa.checkout_graph import track
from agent.mesa.gateway import MCPError, mesa_mode, real_orders_enabled
from agent.mesa.models import CamelModel, Restriction, Session
from agent.mesa.store import DEFAULT_ROOM, log, store
from agent.providers import environment, primary_model_name

app = FastAPI(title="iFood Mesa", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MESA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(service.TableError)
async def table_error(_: Request, exc: service.TableError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# --- request bodies -----------------------------------------------------------


class MemberIn(CamelModel):
    name: str = Field(min_length=1, max_length=40)
    desire: str = Field(default="", max_length=400)
    restrictions: list[Restriction] = Field(default_factory=list)
    max_budget: float | None = Field(default=None, ge=0)


class MemberPatch(CamelModel):
    name: str | None = Field(default=None, max_length=40)
    desire: str | None = Field(default=None, max_length=400)
    restrictions: list[Restriction] | None = None
    max_budget: float | None = Field(default=None, ge=0)


class AuthorIn(CamelModel):
    """Who is acting. The id is a member id; omitted means "the host UI".

    A real device will send a join token here instead; until then this is what
    keeps one mock device from pressing another's buttons.
    """

    author_id: str | None = None


class JoinIn(CamelModel):
    name: str = Field(min_length=1, max_length=40)


class RoomEntry(CamelModel):
    session: Session
    member_id: str


class MerchantIn(AuthorIn):
    merchant_id: str = Field(min_length=1)


class ReuseAddressIn(CamelModel):
    author_id: str = Field(min_length=1)
    source_room: str = Field(min_length=1)
    address_id: str = Field(min_length=1)


class CartItemIn(AuthorIn):
    item_id: str | None = Field(default=None, min_length=1)


class SubmitIn(AuthorIn):
    #: CPF/CNPJ/passport, only if the iFood checkout asks for it.
    document: str | None = None


class ChatIn(AuthorIn):
    message: str = Field(default="", max_length=2000)
    #: data: URLs (jpeg/png) - at most a few, the model may not support vision.
    images: list[str] = Field(default_factory=list, max_length=4)


class AudioIn(CamelModel):
    audio: str = Field(min_length=1, max_length=14_000_000)


@app.post("/api/transcribe")
async def transcribe_audio(body: AudioIn) -> dict[str, str]:
    from agent.mesa.audio import TranscriptionError, transcribe

    try:
        return {"text": await transcribe(body.audio)}
    except TranscriptionError as exc:
        raise HTTPException(422, str(exc)) from exc


def table(room: str) -> Session:
    return store.get_or_create(room)


# --- meta ------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "mode": mesa_mode(),
        "realOrdersEnabled": real_orders_enabled(),
        "defaultRoom": DEFAULT_ROOM,
        # ENVIRONMENT picks the provider preset (local Ollama vs paid remote).
        "environment": environment(),
        "model": primary_model_name(),
        "chatLlm": chat_llm_enabled(),
    }


# --- table ----------------------------------------------------------------------------


@app.post("/api/rooms", response_model=RoomEntry, status_code=201)
async def create_room(body: JoinIn) -> RoomEntry:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Informe seu nome.")
    room = uuid.uuid4().hex[:8].upper()
    while store.get(room) is not None:
        room = uuid.uuid4().hex[:8].upper()
    session = store.get_or_create(room)
    host = session.host()
    assert host is not None
    host.name = name
    store.save()
    return RoomEntry(session=session, member_id=host.id)


@app.get("/api/rooms/{room}", response_model=Session)
async def lookup_room(room: str) -> Session:
    session = store.get(room)
    if session is None:
        raise HTTPException(404, "Mesa nao encontrada. Confira o codigo ou o convite.")
    return session


@app.post("/api/rooms/{room}/join", response_model=RoomEntry, status_code=201)
async def enter_room(room: str, body: JoinIn) -> RoomEntry:
    session = store.get(room)
    if session is None:
        raise HTTPException(404, "Mesa nao encontrada. Confira o codigo ou o convite.")
    member = service.join(session, body.name)
    return RoomEntry(session=session, member_id=member.id)


@app.get("/api/mesas/{room}", response_model=Session)
async def get_table(room: str) -> Session:
    return table(room)


@app.post("/api/mesas/{room}/reset", response_model=Session)
async def reset_table(room: str) -> Session:
    if session := store.get(room):
        await service.cancel_processing(session)
    return store.reset(room)


@app.get("/api/mesas/{room}/addresses/previous")
async def previous_addresses(room: str, authorId: str) -> list[dict]:
    return service.previous_addresses(table(room), authorId)


@app.post("/api/mesas/{room}/addresses/reuse", response_model=Session)
async def reuse_address(room: str, body: ReuseAddressIn) -> Session:
    session = table(room)
    service.reuse_address(session, body.source_room, body.address_id, body.author_id)
    return session


@app.post("/api/mesas/{room}/demo", response_model=Session)
async def load_demo(room: str) -> Session:
    session = table(room)
    service.seed_demo(session)
    return session


# --- members ----------------------------------------------------------------------


@app.post("/api/mesas/{room}/members", response_model=Session, status_code=201)
async def add_member(room: str, body: MemberIn) -> Session:
    session = table(room)
    if service.find_member(session, body.name):
        raise HTTPException(409, "Ja existe alguem com esse nome na mesa.")
    service.upsert_member(session, body.name, body.desire, body.restrictions, body.max_budget)
    return session


@app.patch("/api/mesas/{room}/members/{member_id}", response_model=Session)
async def patch_member(room: str, member_id: str, body: MemberPatch) -> Session:
    session = table(room)
    service.update_member(session, member_id, body.model_dump(exclude_unset=True))
    return session


@app.delete("/api/mesas/{room}/members/{member_id}", response_model=Session)
async def delete_member(room: str, member_id: str, body: AuthorIn) -> Session:
    session = table(room)
    if not body.author_id:
        raise service.TableError("Identifique o host para remover um membro.")
    service.require_host(session, body.author_id)
    service.remove_member(session, member_id)
    return session


# --- the agent --------------------------------------------------------------------------


@app.post("/api/mesas/{room}/join", response_model=Session, status_code=201)
async def join(room: str, body: JoinIn) -> Session:
    """Someone opened the group link on their phone and picked a name."""
    session = table(room)
    service.join(session, body.name)
    return session


@app.post("/api/mesas/{room}/recommend", response_model=Session, status_code=202)
async def recommend(room: str) -> Session:
    """Search again for restaurants that can feed the whole table.

    Normally not needed: a search runs by itself whenever a desire changes.
    """
    session = table(room)
    service.start_recommend(session)
    return session


@app.patch("/api/mesas/{room}/merchant", response_model=Session)
async def select_merchant(room: str, body: MerchantIn) -> Session:
    """The host switches which restaurant the table orders from."""
    session = table(room)
    service.select_merchant(session, body.merchant_id, body.author_id)
    return session


@app.post("/api/mesas/{room}/members/{member_id}/cart", response_model=Session)
async def add_to_cart(room: str, member_id: str, body: CartItemIn | None = None) -> Session:
    """The "+" on someone's recommendation card."""
    session = table(room)
    await service.add_to_cart(session, member_id, body.author_id if body else None,
                              item_id=body.item_id if body else None)
    return session


@app.delete("/api/mesas/{room}/members/{member_id}/cart", response_model=Session)
async def remove_from_cart(room: str, member_id: str, body: AuthorIn | None = None) -> Session:
    session = table(room)
    service.remove_from_cart(session, member_id, body.author_id if body else None)
    return session


@app.post("/api/mesas/{room}/cart/finish", response_model=Session, status_code=202)
async def finish_cart(room: str, body: AuthorIn | None = None) -> Session:
    """Host closes the cart: this is what creates the real iFood cart."""
    session = table(room)
    service.start_finish_cart(session, body.author_id if body else None)
    return session


@app.post("/api/mesas/{room}/chat", response_model=Session, status_code=202)
async def chat_message(room: str, body: ChatIn) -> Session:
    """Send one group-chat message. Answers asynchronously; poll the table.

    The turn runs in the background so a slow model never holds this request
    open. The reply appears in ``chat`` and ``chatBusy`` drives the typing dots.
    """
    session = table(room)
    if not body.message.strip() and not body.images:
        raise HTTPException(422, "Mensagem vazia.")
    service.start_chat_turn(session, body.author_id, body.message.strip(), body.images)
    return session


# --- payments and checkout --------------------------------------------------------------


@app.post("/api/mesas/{room}/members/{member_id}/pay", response_model=Session)
async def pay(room: str, member_id: str, body: AuthorIn | None = None) -> Session:
    session = table(room)
    author_id = body.author_id if body else None
    if author_id and author_id != member_id:
        raise HTTPException(409, "Cada um confirma o proprio Pix.")
    service.mark_paid(session, member_id)
    return session


@app.post("/api/mesas/{room}/members/{member_id}/cover", response_model=Session)
async def cover(room: str, member_id: str, body: AuthorIn | None = None) -> Session:
    session = table(room)
    service.cover_member(session, member_id, body.author_id if body else None)
    return session


@app.post("/api/mesas/{room}/members/{member_id}/drop", response_model=Session)
async def drop(room: str, member_id: str, body: AuthorIn | None = None) -> Session:
    session = table(room)
    await service.drop_member(session, member_id, body.author_id if body else None)
    return session


@app.post("/api/mesas/{room}/submit", response_model=Session, status_code=202)
async def submit(room: str, body: SubmitIn | None = None) -> Session:
    session = table(room)
    service.require_host(session, body.author_id if body else None)
    if not session.all_paid:
        raise HTTPException(409, f"Pedido travado: {session.paid_count}/{session.active_count} pagos.")
    service.start_checkout(session, body.document if body else None)
    return session


@app.get("/api/mesas/{room}/track", response_model=Session)
async def track_order(room: str) -> Session:
    session = table(room)
    try:
        await track(session)
    except MCPError as exc:
        log(session, f"trackOrder falhou: {exc.message}", "warn")
    return session


# --- built frontend (optional: one process for the demo) --------------------------------

_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
