"""Operations on a table. Used by both the REST API and the chat agent's tools.

The shape of the flow: people chat -> each desire lands on their member card ->
restaurants that can feed the whole table are searched in the background ->
one is selected automatically (the host can switch) -> everyone adds their dish -> the host closes the
cart -> everyone pays -> the order goes out.
"""

from __future__ import annotations

import asyncio
import os
import unicodedata

from agent.mesa import cart as cart_ops
from agent.mesa import context, recommend_graph, solver
from agent.mesa.checkout_graph import run_checkout
from agent.mesa.gateway import MCPError, get_gateway
from agent.mesa.models import Member, Restriction, Session, Status
from agent.mesa.store import heard, log, say, store
from agent.mesa.observability import operation

_tasks: set[asyncio.Task] = set()
_chat_tasks: dict[str, asyncio.Task] = {}
_recommend_tasks: dict[str, asyncio.Task] = {}
_pending_turns: dict[str, list[tuple[Member, str, list[str] | None]]] = {}


class TableError(ValueError):
    """A user-facing refusal (maps to HTTP 409/404)."""


def chat_timeout() -> float:
    """Hard ceiling on one chat turn, so a slow model cannot hang the UI."""
    return float(os.getenv("MESA_CHAT_TIMEOUT", "90"))


def require_host(session: Session, author_id: str | None) -> None:
    """Host-only actions. ``author_id=None`` means "the host UI", so it passes.

    Real per-device tokens belong here later; today the id comes from the client
    so the mock devices cannot press the host-only buttons of one another.
    """
    if not author_id:
        return
    host = session.host()
    if host is None or host.id != author_id:
        raise TableError("So o host (dono da conta iFood) pode fazer isso.")


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def find_member(session: Session, name_or_id: str) -> Member | None:
    needle = _plain(name_or_id)
    for member in session.members:
        if member.id == name_or_id or _plain(member.name) == needle:
            return member
    return next((m for m in session.members if needle and needle in _plain(m.name)), None)


def _editable(session: Session) -> None:
    if session.busy:
        raise TableError("O agente esta trabalhando; aguarde.")
    if session.status not in (Status.COLLECTING_PREFERENCES,):
        raise TableError("A mesa ja esta fechada. Use 'Recomecar' para montar outro pedido.")


def _cart_open(session: Session) -> None:
    """The cart is open until the host closes it (that is what "finish" means)."""
    _editable(session)


def upsert_member(
    session: Session,
    name: str,
    desire: str | None = None,
    restrictions: list[Restriction] | None = None,
    max_budget: float | None = None,
) -> Member:
    _editable(session)
    member = find_member(session, name)
    if member is None:
        member = Member(id=f"m{len(session.members) + 1}", name=name.strip())
        while session.member(member.id):
            member.id = f"{member.id}x"
        session.members.append(member)
    changed = ((desire is not None and desire != member.desire)
               or (restrictions is not None and restrictions != member.restrictions)
               or (max_budget is not None and max_budget != member.max_budget))
    if changed:
        member.assigned_item = None
    if desire is not None:
        member.desire = desire
    if restrictions is not None:
        member.restrictions = list(dict.fromkeys(restrictions))
    if max_budget is not None:
        member.max_budget = max_budget if max_budget > 0 else None
    if changed:
        _refresh_preview(session)
        _schedule_recommend(session)
    store.save()
    return member


def update_member(session: Session, member_id: str, fields: dict) -> Member:
    """Apply exactly the given fields (so ``max_budget: None`` really clears it)."""
    _editable(session)
    member = session.member(member_id)
    if member is None:
        raise TableError("Pessoa nao encontrada.")
    changed = (("desire" in fields and (fields["desire"] or "") != member.desire)
               or ("restrictions" in fields and (fields["restrictions"] or []) != member.restrictions)
               or ("max_budget" in fields and fields["max_budget"] != member.max_budget))
    if changed:
        member.assigned_item = None
    if fields.get("name"):
        member.name = fields["name"].strip()
    if "desire" in fields:
        member.desire = fields["desire"] or ""
    if "restrictions" in fields:
        member.restrictions = list(dict.fromkeys(fields["restrictions"] or []))
    if "max_budget" in fields:
        budget = fields["max_budget"]
        member.max_budget = budget if budget and budget > 0 else None
    # A new wish changes which restaurants can feed the table.
    if changed:
        _refresh_preview(session)
        _schedule_recommend(session)
    store.save()
    return member


def remove_member(session: Session, name_or_id: str) -> None:
    _editable(session)
    member = find_member(session, name_or_id)
    if member is None:
        raise TableError("Nao achei essa pessoa na mesa.")
    if member.is_host:
        raise TableError("O host nao pode sair da mesa.")
    session.members.remove(member)
    _refresh_preview(session)
    _schedule_recommend(session)
    store.save()


def join(session: Session, name: str) -> Member:
    """Add someone who joined from their own phone.

    The row exists today; the join *token* (so a real device can only edit itself)
    is the next step and will replace the client-supplied ``authorId``.
    """
    _editable(session)
    clean = name.strip()
    if not clean:
        raise TableError("Escolha um nome.")
    if find_member(session, clean):
        raise TableError("Ja existe alguem com esse nome na mesa.")
    member = Member(id=f"m{len(session.members) + 1}", name=clean)
    while session.member(member.id):
        member.id = f"{member.id}x"
    session.members.append(member)
    say(session, f"{member.name} entrou na mesa 👋", kind="system")
    store.save()
    return member


def resolve_author(session: Session, author_id: str | None) -> Member | None:
    """Who is speaking: the given member, or the host when the id is unknown."""
    member = session.member(author_id) if author_id else None
    if member is not None and member.active:
        return member
    return session.host()


def _prepare_turn(
    session: Session, author_id: str | None, text: str, images: list[str] | None
) -> Member | None:
    """Resolve the speaker and record the message right away (synchronously)."""
    author = resolve_author(session, author_id)
    if author is None:
        say(session, "Nao reconheci quem falou. Adicione a pessoa na mesa.", kind="system")
        store.save()
        return None
    heard(session, author, text, images)
    store.save()
    return author


async def answer_turn(
    session: Session, author: Member, text: str, images: list[str] | None = None
) -> None:
    """Let the agent answer, under the room lock and a hard timeout.

    The lock keeps two devices from running agent turns at the same time;
    ``chat_busy`` lets every other open screen show "digitando".
    """
    from agent.mesa.chat import handle_chat  # local import: avoids a cycle

    async with store.lock(session.room_id):
        session.chat_busy = True
        completed = False
        try:
            await asyncio.wait_for(
                handle_chat(session, author, text, images), timeout=chat_timeout()
            )
            completed = True
        except TimeoutError:
            completed = True
            say(
                session,
                f"O modelo demorou mais de {int(chat_timeout())}s. Tente uma mensagem mais "
                "curta, ou edite seu pedido direto no card da mesa.",
                kind="system",
            )
        finally:
            # Sync tools run without an event loop. Launch one search after the
            # final pending turn, including wishes saved before a model timeout.
            if (completed and _has_wishes(session)
                    and len(_pending_turns.get(session.room_id, [])) <= 1):
                _schedule_recommend(session)
            session.chat_busy = False
            store.save()


async def chat_turn(
    session: Session, author_id: str | None, text: str, images: list[str] | None = None
) -> None:
    """One full turn, awaited (tests and any caller that wants the answer)."""
    author = _prepare_turn(session, author_id, text, images)
    if author is not None:
        await answer_turn(session, author, text, images)


def start_chat_turn(
    session: Session, author_id: str | None, text: str, images: list[str] | None = None
) -> None:
    """Fire-and-forget the answer so no device holds a long HTTP request open."""
    author = _prepare_turn(session, author_id, text, images)
    if author is None:
        return
    previous = _chat_tasks.get(session.room_id)
    if previous is not None and not previous.done():
        previous.cancel()
    search = _recommend_tasks.get(session.room_id)
    if search is not None and not search.done():
        search.cancel()
        session.recommend_dirty = True
    pending = _pending_turns.setdefault(session.room_id, [])
    pending.append((author, text, images))
    session.chat_busy = True

    async def process_pending() -> None:
        if previous is not None:
            try:
                await previous
            except asyncio.CancelledError:
                pass
        # An interrupted person's message stays pending, so their wish is not
        # lost when a second device sends a message during model execution.
        while pending:
            who, message, attachments = pending[0]
            await answer_turn(session, who, message, attachments)
            pending.pop(0)

    task = asyncio.create_task(process_pending())
    _chat_tasks[session.room_id] = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def cancel_processing(session: Session) -> None:
    """Drain room work before replacing the session (the Restart action)."""
    jobs = [task for tasks in (_chat_tasks, _recommend_tasks)
            if (task := tasks.pop(session.room_id, None)) is not None and not task.done()]
    for task in jobs:
        task.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)
    _pending_turns.pop(session.room_id, None)
    recommend_graph._CANDIDATES.pop(session.room_id, None)
    recommend_graph._CANDIDATE_SIGNATURES.pop(session.room_id, None)


def seed_demo(session: Session) -> None:
    """The four friends from the pitch, ready for the demo."""
    _editable(session)
    recommend_graph.clear_cart(session)
    session.merchant_options = []
    session.recommendations = []
    session.selected_merchant_id = None
    session.recommend_signature = ""
    host = next((m for m in session.members if m.is_host), None) or Member(id="m1", name="Arthur", is_host=True)
    # Only the placeholder name is replaced; a real one (from getUser) is kept.
    if host.name.lower().startswith("voce"):
        host.name = "Arthur (host)"
    host.desire = "Hamburguer artesanal com bacon"
    host.restrictions = []
    host.max_budget = 40
    session.members = [
        host,
        Member(id="m2", name="Bia", desire="Lanche ou prato vegetariano gostoso",
               restrictions=["VEGETARIAN"], max_budget=30),
        Member(id="m3", name="Lucas", desire="Qualquer lanche bom, mas zero queijo/laticinios",
               restrictions=["LAC_FREE"], max_budget=35),
        Member(id="m4", name="Joao", desire="Smash duplo barato", restrictions=[], max_budget=25),
    ]
    store.save()
    _schedule_recommend(session)  # the demo table starts searching right away


# --- restaurant search: automatic, debounced -------------------------------------


def _has_wishes(session: Session) -> bool:
    return any(m.desire or m.restrictions for m in session.active_members)


def _schedule_recommend(session: Session) -> None:
    """Cancel the previous search and restart with the updated table.

    Called whenever a desire changes (a chat message the agent understood, or the
    host editing a card), so the restaurants on top of the chat stay current
    without anybody pressing a button.
    """
    if session.status != Status.COLLECTING_PREFERENCES or session.busy:
        return
    if not _has_wishes(session):
        return  # nothing to search for yet
    session.recommend_dirty = True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Called from sync code with no event loop (a script, a sync tool):
        # stay dirty so the next call - which will have a loop - runs it.
        return
    # Flag it here (not inside the task) so the UI - and tests - never see a gap
    # between "a search was asked for" and "a search is running".
    session.searching_recommendations = True
    previous = _recommend_tasks.get(session.room_id)
    if previous is not None and not previous.done():
        previous.cancel()

    async def restart() -> None:
        if previous is not None:
            try:
                await previous
            except asyncio.CancelledError:
                pass
        await recommend_graph.run_recommend(session.room_id)

    task = asyncio.create_task(restart())
    _recommend_tasks[session.room_id] = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def start_recommend(session: Session) -> None:
    """Force a search now (the "buscar de novo" button)."""
    if session.status != Status.COLLECTING_PREFERENCES:
        raise TableError("A mesa ja esta fechada. Use 'Recomecar' para montar outro pedido.")
    if not session.active_members:
        raise TableError("Adicione pelo menos uma pessoa na mesa.")
    if not _has_wishes(session):
        raise TableError("Diga o que alguem quer comer primeiro.")
    _schedule_recommend(session)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --- the restaurant the table is ordering from -------------------------------------


def previous_addresses(session: Session, author_id: str) -> list[dict]:
    require_host(session, author_id)
    if not author_id:
        raise TableError("Identifique o host para consultar enderecos anteriores.")
    seen = set()
    choices = []
    for previous in reversed(list(store.sessions.values())):
        address = previous.host_address
        if previous.room_id == session.room_id or previous.mode != session.mode or not address:
            continue
        if address.address_id in seen:
            continue
        seen.add(address.address_id)
        choices.append({"sourceRoom": previous.room_id, "alias": address.alias,
                        "label": address.label, "addressId": address.address_id})
    return choices


def reuse_address(session: Session, source_room: str, address_id: str, author_id: str) -> None:
    _cart_open(session)
    choices = previous_addresses(session, author_id)
    if session.host_address or session.searching_recommendations or session.chat_busy or session.cart_members:
        raise TableError("Aguarde a busca terminar. O endereco so pode ser recuperado antes de montar o carrinho.")
    if not any(choice["sourceRoom"] == source_room and choice["addressId"] == address_id for choice in choices):
        raise TableError("Endereco anterior nao encontrado. Atualize a lista.")
    source = store.sessions[source_room]
    session.host_address = source.host_address.model_copy(deep=True)
    log(session, "Host confirmou o endereco salvo de uma mesa anterior; usando cache local.", "info")
    say(session, "Endereco anterior confirmado pelo host. Vou tentar a busca novamente.")
    store.save()
    start_recommend(session)


def _refresh_preview(session: Session) -> None:
    """Pre-finish estimate: merchant, fee and everyone's share as items come in."""
    option = session.selected_option()
    if option is None:
        return
    cart = session.mcp_cart
    cart.merchant_id = option.merchant_id
    cart.merchant_name = option.name
    cart.merchant_logo_url = option.logo_url
    cart.minimum_order_amount = option.minimum_order_amount
    cart.delivery_fee = option.fee
    cart.estimated_minutes = option.eta
    cart.subtotal = session.cart_subtotal
    cart.voucher_discount = 0
    cart.total_amount = round(cart.subtotal + cart.delivery_fee, 2)
    cart_ops.recompute_quotas(session)


def select_merchant(session: Session, merchant_id: str, author_id: str | None = None) -> None:
    """The host switches restaurant; items from the previous one are dropped."""
    require_host(session, author_id)
    _cart_open(session)
    if session.searching_recommendations or session.chat_busy:
        raise TableError("Estou atualizando os restaurantes. Aguarde a busca terminar.")
    if session.selected_merchant_id == merchant_id:
        return
    if not recommend_graph.publish_recommendations(session, merchant_id):
        raise TableError("Esse restaurante nao esta na lista de opcoes.")
    cleared = recommend_graph.clear_cart(session)
    _refresh_preview(session)
    option = session.selected_option()
    name = option.name if option else merchant_id
    log(session, f"Restaurante da mesa: {name}", "ok")
    if cleared:
        say(session, f"O host trocou para **{name}**. O carrinho comecou de novo.")
    store.save()


# --- the shared cart ----------------------------------------------------------------


def _may_edit_member(session: Session, member_id: str, author_id: str | None) -> None:
    if not author_id:
        return  # the host UI
    host = session.host()
    if author_id == member_id or (host is not None and author_id == host.id):
        return
    raise TableError("Cada um adiciona o proprio prato (ou o host por voce).")


@operation("add_to_cart")
async def add_to_cart(session: Session, member_id: str, author_id: str | None = None,
                      *, item_id: str | None = None) -> Member:
    """Someone pressed "+" on their recommendation: validate the dish and add it."""
    _cart_open(session)
    _may_edit_member(session, member_id, author_id)
    member = session.member(member_id)
    if member is None or not member.active:
        raise TableError("Pessoa nao encontrada.")
    option = session.selected_option()
    if option is None:
        raise TableError("Escolha o restaurante primeiro.")
    recommendation = session.recommendation(member_id, item_id)
    if recommendation is None:
        raise TableError("Ainda nao tenho uma sugestao para essa pessoa.")
    if session.searching_recommendations or session.chat_busy:
        raise TableError("Estou atualizando as sugestoes. Aguarde a busca terminar.")
    preference = (member.desire, tuple(member.restrictions), member.max_budget)

    assert session.host_address
    try:
        item = await solver.validate_one(
            get_gateway(),
            context.location_of(session),
            option.merchant_id,
            member,
            recommendation.item_id,
            fallback_name=recommendation.name,
            fallback_price=recommendation.price,
            fallback_image=recommendation.image_url,
            alternative_query=recommendation.alternative_query,
        )
    except solver.NoMatch as exc:
        raise TableError(str(exc)) from exc
    if (session.selected_merchant_id != option.merchant_id
            or session.recommendation(member_id, recommendation.item_id) != recommendation
            or session.searching_recommendations or session.chat_busy
            or preference != (member.desire, tuple(member.restrictions), member.max_budget)):
        raise TableError("A sugestao mudou durante a validacao. Confira o novo prato.")
    if recommendation.over_budget:
        item.warnings.append("acima do teto informado")
    member.assigned_item = item
    _refresh_preview(session)
    log(session, f"{member.name} adicionou {item.name} (R$ {item.price:.2f})", "ok")
    store.save()
    return member


def remove_from_cart(session: Session, member_id: str, author_id: str | None = None) -> Member:
    _cart_open(session)
    _may_edit_member(session, member_id, author_id)
    member = session.member(member_id)
    if member is None or not member.active:
        raise TableError("Pessoa nao encontrada.")
    if member.assigned_item:
        log(session, f"{member.name} tirou {member.assigned_item.name} do carrinho", "info")
    member.assigned_item = None
    _refresh_preview(session)
    store.save()
    return member


def start_finish_cart(session: Session, author_id: str | None = None) -> None:
    """Host closes the cart, which is what creates the real iFood cart."""
    require_host(session, author_id)
    _cart_open(session)
    if session.searching_recommendations or session.chat_busy:
        raise TableError("Estou atualizando a mesa. Aguarde a busca terminar para fechar o carrinho.")
    option = session.selected_option()
    if option is None:
        raise TableError("Nenhum restaurante selecionado ainda.")
    if not session.cart_members:
        raise TableError("Ninguem adicionou prato ainda.")
    missing = [m.name for m in session.active_members if not m.assigned_item]
    if missing:
        raise TableError(f"Falta o prato de {', '.join(missing)}.")
    subtotal = session.cart_subtotal
    if subtotal < option.minimum_order_amount:
        falta = option.minimum_order_amount - subtotal
        raise TableError(
            f"Faltam R$ {falta:.2f} para o pedido minimo de {option.name} "
            f"(R$ {option.minimum_order_amount:.2f})."
        )

    session.busy = True
    _spawn(_finish_cart(session))


@operation("finish_cart")
async def _finish_cart(session: Session) -> None:
    option = session.selected_option()
    assert option is not None and session.host_address
    merchant = solver.MerchantInfo(
        merchant_id=option.merchant_id,
        name=option.name,
        fee=option.fee,
        minimum=option.minimum_order_amount,
        rating=option.rating,
        logo=option.logo_url,
        eta=option.eta,
    )
    try:
        gateway = get_gateway()
        location = context.location_of(session)
        merchant = await solver.confirm_merchant(gateway, location, merchant)
        await cart_ops.build_cart(
            gateway, session, merchant, lambda t, k="info": log(session, t, k)  # type: ignore[arg-type]
        )
    except (MCPError, solver.NoMatch, context.NoAddress) as exc:
        session.last_error = str(exc)
        log(session, session.last_error, "error")
        if isinstance(exc, MCPError) and exc.tool == "createCart" and "upstream" in exc.message.lower():
            say(session, "O servico do iFood falhou internamente ao criar o carrinho. "
                "Nao consegui confirmar a criacao e nao repeti a operacao automaticamente.")
        else:
            say(session, f"Nao consegui fechar o carrinho: {exc}")
        return
    finally:
        session.busy = False
        store.save()

    for member in session.active_members:
        if member.is_host:
            member.has_paid = True  # the host is the one paying the iFood

    session.status = Status.WAITING_PAYMENTS
    cart = session.mcp_cart
    lines = [f"- {m.name}: {m.assigned_item.name} — R$ {m.quota.total:.2f}" for m in session.cart_members if m.quota]
    extras = f"entrega R$ {cart.delivery_fee:.2f}"
    if cart.voucher_discount:
        extras += f", cupom {cart.voucher_code or ''} -R$ {cart.voucher_discount:.2f}"
    say(
        session,
        f"Carrinho fechado em **{cart.merchant_name}** (total R$ {cart.total_amount:.2f}, {extras}).\n"
        + "\n".join(lines)
        + "\nAgora cada um paga a sua parte.",
    )
    log(session, f"Carrinho fechado: R$ {cart.total_amount:.2f}", "ok")
    store.save()


def start_checkout(
    session: Session, document: str | None = None, author_id: str | None = None
) -> None:
    require_host(session, author_id)
    if session.busy:
        raise TableError("O agente esta trabalhando; aguarde.")
    session.busy = True

    async def run() -> None:
        try:
            await run_checkout(session.room_id, document)
        finally:
            session.busy = False

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _waiting(session: Session) -> None:
    if session.status != Status.WAITING_PAYMENTS:
        raise TableError("Nao ha pagamentos pendentes agora.")


def mark_paid(session: Session, member_id: str) -> Member:
    """'Simular Pix Pago' for one friend."""
    _waiting(session)
    member = session.member(member_id)
    if member is None or not member.active:
        raise TableError("Pessoa nao encontrada.")
    member.has_paid = True
    log(session, f"Pix de {member.name} confirmado ({session.paid_count}/{session.active_count})", "ok")
    store.save()
    _maybe_auto_submit(session)
    return member


def auto_submit_enabled() -> bool:
    """When the last person pays, send the order (the host already consented)."""
    return os.getenv("MESA_AUTO_SUBMIT", "true").strip().lower() in {"1", "true", "yes", "on"}


def _maybe_auto_submit(session: Session) -> None:
    if not auto_submit_enabled() or session.busy:
        return
    if session.status != Status.WAITING_PAYMENTS or not session.all_paid:
        return
    say(session, "Todo mundo pagou 🎉 Enviando o pedido para o iFood...")
    start_checkout(session)


def cover_member(session: Session, member_id: str, author_id: str | None = None) -> Member:
    """Host pays a friend's share ("Cobrir amigo")."""
    require_host(session, author_id)
    _waiting(session)
    member = session.member(member_id)
    if member is None or not member.active or member.is_host:
        raise TableError("So da para cobrir um amigo da mesa.")
    member.covered_by_host = True
    member.has_paid = True
    cart_ops.recompute_quotas(session)
    log(session, f"Host cobriu a parte de {member.name}", "ok")
    store.save()
    _maybe_auto_submit(session)
    return member


async def drop_member(session: Session, member_id: str, author_id: str | None = None) -> Member:
    """Remove a late payer from the cart and re-split among the rest."""
    require_host(session, author_id)
    _waiting(session)
    member = session.member(member_id)
    if member is None or not member.active or member.is_host:
        raise TableError("So da para remover um amigo da mesa.")
    if len(session.active_members) <= 1:
        raise TableError("Precisa sobrar alguem no pedido.")
    try:
        await cart_ops.drop_member_from_cart(
            get_gateway(), session, member, lambda t, k="info": log(session, t, k)  # type: ignore[arg-type]
        )
    except MCPError as exc:
        raise TableError(f"O iFood recusou a remocao: {exc.message}") from exc
    say(session, f"{member.name} saiu do pedido. Novo total R$ {session.mcp_cart.total_amount:.2f}.")
    store.save()
    return member
