"""LangGraph: the deterministic gatekeeper in front of ``submitOrder``.

    gate -> prepare_payment -> submit -> fetch_order -> END        (gate fails -> END)

``userConsent`` is only ever set to ``true`` inside ``submit``, and ``submit`` is
only reachable through ``gate``: N/N paid (or covered/removed), a cart that meets
the merchant's minimum, and an explicit host click. No LLM is in this path.

``submitOrder`` places a REAL order with REAL money on the live server, so in
``MESA_MODE=live`` it is skipped (dry run) unless ``MESA_SUBMIT_ENABLED=true``.
"""

from __future__ import annotations

import time
import uuid
from itertools import pairwise
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.mesa import cart as cart_ops
from agent.mesa.gateway import MCPError, get_gateway, real_orders_enabled
from agent.mesa.models import Session, Status
from agent.mesa.parsing import find_first
from agent.mesa.store import log, say, store
from agent.mesa.observability import node as trace_node, operation

#: Stages the dry run walks through so the tracker still has something to show.
SIMULATED_STAGES = ["PLACED", "CONFIRMED", "PREPARING", "OUT_FOR_DELIVERY", "DELIVERED"]
SIMULATED_STEP_SECONDS = 20


class CheckoutState(TypedDict, total=False):
    room_id: str
    document: str | None
    error: str
    order_id: str


def _session(state: CheckoutState) -> Session:
    return store.sessions[state["room_id"]]


async def gate(state: CheckoutState) -> dict[str, Any]:
    session = _session(state)
    mcp = session.mcp_cart
    if session.status != Status.WAITING_PAYMENTS or not mcp.cart_id:
        return {"error": "Nao ha um carrinho aguardando pagamento."}
    if not session.all_paid:
        pending = [m.name for m in session.active_members if not m.has_paid]
        return {
            "error": f"Pedido travado: {session.paid_count}/{session.active_count} pagos "
            f"(faltam {', '.join(pending)})."
        }
    if mcp.subtotal < mcp.minimum_order_amount:
        return {
            "error": f"Itens somam R$ {mcp.subtotal:.2f}, abaixo do pedido minimo "
            f"de R$ {mcp.minimum_order_amount:.2f}."
        }
    session.status = Status.SUBMITTING
    log(session, f"Gatekeeper liberado: {session.paid_count}/{session.active_count} pagos", "ok")
    return {}


async def prepare_payment(state: CheckoutState) -> dict[str, Any]:
    session = _session(state)
    session.busy_phase = "Conferindo o pagamento e o total…"
    gateway = get_gateway()
    await cart_ops.set_payment(gateway, session, lambda t, k="info": log(session, t, k))  # type: ignore[arg-type]
    try:
        snapshot = await gateway.call("getCart", cartId=session.mcp_cart.cart_id)
        cart_ops.apply_cart_snapshot(session, snapshot)
        log(session, f"getCart final: total R$ {session.mcp_cart.total_amount:.2f}", "tool")
    except MCPError as exc:
        return {"error": f"Nao consegui reler o carrinho: {exc.message}"}
    return {}


async def submit(state: CheckoutState) -> dict[str, Any]:
    session = _session(state)
    session.busy_phase = "Finalizando o pedido…"
    gateway = get_gateway()
    mcp = session.mcp_cart

    if gateway.mode == "live" and not real_orders_enabled():
        mcp.order_id = f"SIMULADO-{uuid.uuid4().hex[:8].upper()}"
        mcp.simulated = True
        session.tracking = [{"status": "PLACED", "ts": time.time()}]
        log(session, "submitOrder PULADO (MESA_SUBMIT_ENABLED=false): pedido simulado", "warn")
        return {"order_id": mcp.order_id}

    session.busy_phase = "Enviando o pedido ao iFood…"
    try:
        placed = await gateway.call(
            "submitOrder", cartId=mcp.cart_id, userConsent=True, document=state.get("document")
        )
    except MCPError as exc:
        return {"error": f"submitOrder falhou: {exc.message}"}

    order_id = find_first(placed, "orderId", "id")
    if not order_id:
        return {"error": "submitOrder respondeu sem orderId."}
    mcp.order_id = str(order_id)
    log(session, f"submitOrder(userConsent=true) -> {mcp.order_id}", "ok")
    return {"order_id": mcp.order_id}


async def fetch_order(state: CheckoutState) -> dict[str, Any]:
    session = _session(state)
    session.busy_phase = "Buscando a confirmação do pedido…"
    mcp = session.mcp_cart
    session.status = Status.ORDER_SUBMITTED

    if mcp.simulated:
        mcp.verification_code = f"{uuid.uuid4().int % 10000:04d}"
        mcp.order_status = "PLACED"
        say(session, f"Pedido simulado! Codigo de verificacao: {mcp.verification_code}")
        return {}

    try:
        order = await get_gateway().call("getOrder", orderId=mcp.order_id)
        code = find_first(order, "verificationCode", "verification_code", "deliveryCode")
        mcp.verification_code = str(code) if code is not None else None
        log(session, "getOrder -> codigo de verificacao lido", "tool")
    except MCPError as exc:
        log(session, f"getOrder falhou ({exc.message})", "warn")
    mcp.order_status = "PLACED"
    say(session, f"Pedido enviado! Codigo para a portaria: {mcp.verification_code or 'veja no app'}")
    return {}


async def fail(state: CheckoutState) -> dict[str, Any]:
    session = _session(state)
    if session.status == Status.SUBMITTING:
        session.status = Status.WAITING_PAYMENTS
    session.last_error = state.get("error")
    log(session, state.get("error", "erro"), "error")
    say(session, state.get("error", "Nao foi possivel enviar o pedido."))
    return {}


def _route(state: CheckoutState) -> str:
    return "fail" if state.get("error") else "next"


def build_checkout_graph():
    graph = StateGraph(CheckoutState)
    order = [("gate", gate), ("prepare_payment", prepare_payment), ("submit", submit),
             ("fetch_order", fetch_order)]
    for name, node in order:
        graph.add_node(name, trace_node(name)(node))
    graph.add_node("fail", trace_node("fail")(fail))
    graph.set_entry_point("gate")
    names = [n for n, _ in order]
    for current, following in pairwise(names):
        graph.add_conditional_edges(current, _route, {"next": following, "fail": "fail"})
    graph.add_edge("fetch_order", END)
    graph.add_edge("fail", END)
    return graph.compile()


_graph = None


@operation("checkout")
async def run_checkout(room_id: str, document: str | None = None) -> None:
    global _graph
    _graph = _graph or build_checkout_graph()
    session = store.sessions[room_id]
    session.last_error = None
    try:
        await _graph.ainvoke({"room_id": room_id, "document": document})
    finally:
        store.save()


@operation("tracking")
async def track(session: Session) -> dict[str, Any]:
    """Current delivery status (``trackOrder``, or a timed walk for a dry run)."""
    mcp = session.mcp_cart
    if not mcp.order_id:
        return {}
    if mcp.simulated:
        started = session.tracking[0]["ts"] if session.tracking else time.time()
        step = min(int((time.time() - started) // SIMULATED_STEP_SECONDS), len(SIMULATED_STAGES) - 1)
        mcp.order_status = SIMULATED_STAGES[step]
        session.tracking = [{"status": s, "ts": started} for s in SIMULATED_STAGES[: step + 1]]
        return {"status": mcp.order_status}

    data = await get_gateway().call("trackOrder", orderId=mcp.order_id)
    status = find_first(data, "status", "currentStatus", "orderStatus")
    if status:
        mcp.order_status = str(status)
    events = find_first(data, "events", "history", "statusEvents")
    if isinstance(events, list):
        session.tracking = [e for e in events if isinstance(e, dict)]
    return {"status": mcp.order_status}
