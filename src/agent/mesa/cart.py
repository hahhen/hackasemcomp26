"""Cart orchestration on the MCP: createCart -> vouchers -> delivery -> payment -> split.

Cart, voucher and delivery payloads are not typed by the server, so everything
read here goes through :mod:`agent.mesa.parsing` and degrades to a safe default
(no voucher, the merchant's quoted fee) instead of guessing.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.mesa.gateway import Gateway, MCPError, raise_if_error
from agent.mesa.models import McpCart, Member, Session
from agent.mesa.parsing import decode, dicts_with, find_first, money, to_float, walk
from agent.mesa.solver import Log, MerchantInfo
from agent.mesa.split import compute_quotas

MAX_VOUCHER_TRIES = 4
logger = logging.getLogger(__name__)


def created_cart_id(response: Any) -> str:
    """Read only cart identifiers, never an item's or merchant's generic id."""
    response = raise_if_error("createCart", decode(response))
    for node in walk(response):
        for key in ("cartId", "cart_id"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(response, dict):
        if isinstance(response.get("id"), str) and response["id"].strip():
            return response["id"]
        for node in walk(response):
            cart = node.get("cart")
            if isinstance(cart, dict) and isinstance(cart.get("id"), str) and cart["id"].strip():
                return cart["id"]
        message = find_first(response, "userFriendlyMessage", "error_description", "message")
        if isinstance(message, str) and message.strip():
            raise MCPError("createCart", message)
    shape = sorted(response) if isinstance(response, dict) else type(response).__name__
    logger.error("createCart response has no confirmed cart identifier; shape=%s", shape)
    raise MCPError("createCart", "O iFood nao confirmou o identificador do carrinho. A criacao nao foi repetida automaticamente.")

_TOTAL_KEYS = ("total", "totalAmount", "totalPrice", "orderTotal", "amountToPay")
_FEE_KEYS = ("deliveryFee", "deliveryFeeAmount", "fee")
_DISCOUNT_KEYS = ("discount", "discountAmount", "voucherDiscount", "totalDiscount")


def cart_total(cart: Any) -> float:
    return money(cart, *_TOTAL_KEYS)


def apply_cart_snapshot(session: Session, cart: Any) -> None:
    """Copy fee/discount from a ``getCart`` payload and recompute subtotal and total."""
    mcp = session.mcp_cart
    subtotal = round(sum(m.assigned_item.price for m in session.active_members if m.assigned_item), 2)

    fee = find_first(cart, *_FEE_KEYS)
    if fee is not None:
        mcp.delivery_fee = to_float(fee, mcp.delivery_fee)
    mcp.voucher_discount = round(money(cart, *_DISCOUNT_KEYS), 2)
    mcp.subtotal = subtotal
    mcp.total_amount = round(subtotal + mcp.delivery_fee - mcp.voucher_discount, 2)

    link_cart_items(session, cart)
    recompute_quotas(session)


def link_cart_items(session: Session, cart: Any) -> None:
    """Attach each MCP ``cartItemId`` to the member who owns it (via ``customerNote``)."""
    rows = [r for r in dicts_with(cart, "cartItemId") if r.get("itemId")]
    taken: set[str] = set()
    for member in session.active_members:
        item = member.assigned_item
        if not item:
            continue
        match = next(
            (
                r for r in rows
                if r["cartItemId"] not in taken
                and r.get("itemId") == item.item_id
                and (item.customer_note in str(r.get("customerNote") or ""))
            ),
            None,
        ) or next(
            (r for r in rows if r["cartItemId"] not in taken and r.get("itemId") == item.item_id),
            None,
        )
        if match:
            item.cart_item_id = match["cartItemId"]
            taken.add(match["cartItemId"])


def recompute_quotas(session: Session) -> None:
    """quota_i = item_i + fee/N - discount * item_i/subtotal, and who owes whom."""
    mcp = session.mcp_cart
    active = [m for m in session.active_members if m.assigned_item]
    quotas = compute_quotas(
        {m.id: m.assigned_item.price for m in active},  # type: ignore[union-attr]
        mcp.delivery_fee,
        mcp.voucher_discount,
    )
    for member in active:
        member.quota = quotas[member.id]
        member.amount_to_pay = quotas[member.id].total

    # A covered friend owes nothing; the host absorbs the share.
    host = next((m for m in active if m.is_host), None)
    for member in active:
        if member.covered_by_host and host and member is not host:
            host.amount_to_pay = round(host.amount_to_pay + member.amount_to_pay, 2)
            member.amount_to_pay = 0.0


def _pix_id(methods: Any) -> str | None:
    for row in dicts_with(methods, "paymentMethodId"):
        label = f"{row.get('paymentMethodId')} {row.get('name', '')} {row.get('type', '')}".upper()
        if "PIX" in label:
            return str(row["paymentMethodId"])
    return None


async def pick_voucher(gateway: Gateway, cart_id: str, baseline: Any, log: Log) -> Any:
    """Apply whichever voucher yields the lowest cart total (or none).

    The payload does not say how much each voucher saves, so each candidate is
    tried against the real cart and the totals are compared.
    """
    try:
        listing = await gateway.call("listVouchers", cartId=cart_id)
    except MCPError as exc:
        log(f"listVouchers falhou ({exc.message}); sigo sem cupom", "warn")
        return baseline

    vouchers = dicts_with(listing, "code", "campaignId")[:MAX_VOUCHER_TRIES]
    if not vouchers:
        log("Nenhum cupom elegivel para este carrinho", "info")
        return baseline

    best_cart, best_total, best_voucher = baseline, cart_total(baseline), None
    for voucher in vouchers:
        try:
            tried = await gateway.call(
                "setVoucher", cartId=cart_id, code=voucher["code"], campaignId=voucher["campaignId"]
            )
            tried = await gateway.call("getCart", cartId=cart_id) if not cart_total(tried) else tried
        except MCPError:
            continue
        total = cart_total(tried)
        if 0 < total < best_total or (best_total == 0 and total > 0):
            best_cart, best_total, best_voucher = tried, total, voucher

    if best_voucher is None:
        await _safe(gateway, "removeVoucher", cartId=cart_id)
        log("Cupons testados, nenhum reduz o total", "info")
        return baseline

    await gateway.call(
        "setVoucher", cartId=cart_id, code=best_voucher["code"], campaignId=best_voucher["campaignId"]
    )
    log(f"Cupom {best_voucher['code']} aplicado (maior desconto liquido)", "ok")
    return best_cart


async def _safe(gateway: Gateway, tool: str, **args: Any) -> Any:
    try:
        return await gateway.call(tool, **args)
    except MCPError:
        return None


async def build_cart(
    gateway: Gateway, session: Session, merchant: MerchantInfo, log: Log
) -> None:
    """Create the single shared cart and fill ``session.mcp_cart`` + every quota."""
    assert session.host_address, "host address must be resolved first"
    members = [m for m in session.active_members if m.assigned_item]

    session.busy_phase = "Criando o carrinho no iFood…"
    created = await gateway.call(
        "createCart",
        merchantId=merchant.merchant_id,
        addressId=session.host_address.address_id,
        items=[
            {
                "itemId": m.assigned_item.item_id,  # type: ignore[union-attr]
                "quantity": 1,
                "customerNote": m.assigned_item.customer_note,  # type: ignore[union-attr]
                "options": m.assigned_item.options,  # type: ignore[union-attr]
            }
            for m in members
        ],
    )
    cart_id = created_cart_id(created)

    session.mcp_cart = McpCart(
        cart_id=str(cart_id),
        merchant_id=merchant.merchant_id,
        merchant_name=merchant.name,
        merchant_logo_url=merchant.logo,
        minimum_order_amount=merchant.minimum,
        delivery_fee=merchant.fee,
        estimated_minutes=merchant.eta,
    )
    log(f"createCart -> {len(members)} itens com customerNote por amigo", "tool")

    session.busy_phase = "Conferindo os valores do carrinho…"
    cart = await gateway.call("getCart", cartId=cart_id)
    session.busy_phase = "Procurando o melhor cupom…"
    cart = await pick_voucher(gateway, str(cart_id), cart, log)

    session.busy_phase = "Conferindo as opções de entrega…"
    try:
        methods = await gateway.call("getCartDeliveryMethods", cartId=cart_id)
        rows = dicts_with(methods, "deliveryMethodId")
        chosen = next((r for r in rows if r.get("deliveryMethodMode") == "DELIVERY"), None) or (
            rows[0] if rows else None
        )
        if chosen:
            await gateway.call(
                "setCartDelivery", cartId=cart_id, deliveryMethodId=chosen["deliveryMethodId"]
            )
            session.mcp_cart.delivery_method_id = chosen["deliveryMethodId"]
            log(f"Entrega: {chosen['deliveryMethodId']}", "tool")
    except MCPError as exc:
        log(f"Entrega nao configurada ({exc.message})", "warn")

    session.busy_phase = "Preparando o pagamento…"
    await set_payment(gateway, session, log)

    session.busy_phase = "Calculando a parte de cada pessoa…"
    cart = await gateway.call("getCart", cartId=cart_id)
    apply_cart_snapshot(session, cart)
    session.mcp_cart.voucher_code = _applied_voucher(cart)

    mcp = session.mcp_cart
    if mcp.subtotal < mcp.minimum_order_amount:
        log(
            f"Atencao: itens somam R$ {mcp.subtotal:.2f}, abaixo do pedido minimo "
            f"R$ {mcp.minimum_order_amount:.2f}",
            "warn",
        )
    for member in members:
        if member.max_budget and member.quota and member.quota.total > member.max_budget:
            log(
                f"{member.name}: cota R$ {member.quota.total:.2f} passa do teto "
                f"R$ {member.max_budget:.2f}",
                "warn",
            )


def _applied_voucher(cart: Any) -> str | None:
    for key in ("appliedVoucher", "voucher", "vouchers", "appliedVouchers"):
        value = find_first(cart, key)
        if isinstance(value, dict) and value.get("code"):
            return str(value["code"])
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0].get("code")
    return None


async def set_payment(gateway: Gateway, session: Session, log: Log) -> None:
    """Point the cart at PIX (the host pays the MCP; friends settle with the host)."""
    cart_id = session.mcp_cart.cart_id
    merchant_id = session.mcp_cart.merchant_id
    try:
        methods = await gateway.call("listPaymentMethods", merchantId=merchant_id)
        pix = _pix_id(methods)
        if pix:
            await gateway.call("setCartPayment", cartId=cart_id, paymentMethodId=pix)
            session.mcp_cart.payment_method_id = pix
            log("Pagamento do carrinho: PIX", "tool")
        else:
            log("PIX nao disponivel nesta loja; escolha o pagamento no app", "warn")
    except MCPError as exc:
        log(f"Pagamento nao configurado ({exc.message})", "warn")


async def drop_member_from_cart(
    gateway: Gateway, session: Session, member: Member, log: Log
) -> None:
    """Timeout exception: remove one friend's dish and re-split among the rest."""
    mcp = session.mcp_cart
    item = member.assigned_item
    if mcp.cart_id and item and item.cart_item_id:
        await gateway.call("removeItemFromCart", cartId=mcp.cart_id, cartItemId=item.cart_item_id)
        log(f"removeItemFromCart -> {member.name}", "tool")
    member.removed = True
    if mcp.cart_id:
        cart = await gateway.call("getCart", cartId=mcp.cart_id)
        apply_cart_snapshot(session, cart)
