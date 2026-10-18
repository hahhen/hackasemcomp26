"""An in-memory iFood that mimics the shapes of the real ``sudowin-semcomp`` server.

Search/item/merchant payloads copy the live responses field for field. The
catalog is built so the merchant-intersection step has something to prove:
only *Burger Garden* feeds a vegetarian, a lactose-free eater and a smash fan.
"""

from __future__ import annotations

import itertools
import re
import uuid
from typing import Any

from agent.mesa.gateway import MCPError
from agent.mesa.observability import node

LOCATION = {"latitude": -22.00461, "longitude": -47.89372}


def _id(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mesa-mock/{seed}"))


def _merchant(slug: str, name: str, *, fee: float, minimum: float, rating: float, km: float,
              open_: bool = True) -> dict[str, Any]:
    return {
        "merchantId": _id(slug), "merchantName": name, "isOpen": open_,
        "minimumOrderAmount": minimum, "reviewRating": rating, "ratingCount": 400,
        "straightLineDistanceKm": km, "merchantLogoUrl": None,
        "deliveryMethod": {
            "deliveryMethodId": "DEFAULT", "deliveryMethodMode": "DELIVERY",
            "estimatedMinutes": {"min": 25, "max": 40}, "fee": fee, "promotionalFee": fee,
        },
    }


MERCHANTS = {
    m["merchantId"]: m
    for m in [
        _merchant("garden", "Burger Garden", fee=6.0, minimum=25, rating=4.8, km=1.1),
        _merchant("ze", "Lanche do Ze", fee=4.0, minimum=15, rating=4.5, km=0.8),
        _merchant("veggie", "Veggie Place", fee=7.5, minimum=20, rating=4.9, km=2.4),
        _merchant("closed", "Hamburgueria Fechada", fee=3.0, minimum=10, rating=5.0, km=0.5, open_=False),
    ]
}


def _item(slug: str, merchant: str, name: str, price: float, desc: str, tags: list[str],
          options: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "itemId": _id(f"{merchant}/{slug}"), "merchantId": _id(merchant), "itemName": name,
        "unitPrice": price, "promotionalUnitPrice": price, "itemDescription": desc,
        "tags": tags, "itemImageUrl": None, "itemEan": "",
        "optionGroups": options or [],
    }


_BREAD = [{
    "optionGroupId": _id("grp/bread"), "name": "Escolha o pao", "minSelections": 1, "maxSelections": 1,
    "options": [
        {"optionId": _id("opt/brioche"), "name": "Brioche", "additionalPrice": 0},
        {"optionId": _id("opt/sem-gluten"), "name": "Pao sem gluten", "additionalPrice": 3},
    ],
}]

ITEMS = {
    i["itemId"]: i
    for i in [
        _item("smash-duplo", "garden", "Smash Duplo", 24.9,
              "Dois smashes de carne, queijo prato, picles e maionese", ["MEAT"], _BREAD),
        _item("smash-simples", "garden", "Smash Simples", 18.9,
              "Um smash de carne, queijo prato e picles", ["MEAT"], _BREAD),
        _item("bacon", "garden", "Bacon Artesanal", 34.9,
              "Burger artesanal 180g com bacon crocante e cheddar", ["MEAT"], _BREAD),
        _item("veggie", "garden", "Garden Veggie", 26.9,
              "Burger de berinjela defumada, queijo prato, alface e tomate", ["VEGETARIAN"], _BREAD),
        _item("vegano", "garden", "Burger de Grao-de-bico", 29.9,
              "Burger de grao-de-bico, rucula, tomate seco e maionese verde vegana",
              ["VEGETARIAN", "VEGAN", "LAC_FREE"], _BREAD),
        _item("frango-sl", "garden", "Frango Crocante sem Queijo", 27.9,
              "Frango empanado, alface, tomate e molho da casa, sem laticinios",
              ["LAC_FREE"], _BREAD),
        _item("x-ze", "ze", "X-Burger do Ze", 15.0, "Hamburguer, queijo, presunto e ovo", ["MEAT"]),
        _item("x-bacon-ze", "ze", "X-Bacon do Ze", 19.0, "Hamburguer, queijo e bacon", ["MEAT"]),
        _item("salada", "veggie", "Bowl Vegetariano", 32.0,
              "Bowl de quinoa, legumes assados e homus", ["VEGETARIAN", "VEGAN", "LAC_FREE"]),
        _item("burger-veggie", "veggie", "Burger Vegetariano", 31.0,
              "Burger de lentilha com queijo", ["VEGETARIAN"]),
        _item("fechado", "closed", "Smash Fantasma", 10.0, "Smash de carne", ["MEAT"]),
    ]
}

_GENERIC = {"lanche", "prato", "comida", "hamburguer", "burger", "refeicao"}


def _score(item: dict[str, Any], query: str) -> int:
    """Crude relevance: specific words count double, generic ones just match."""
    haystack = f"{item['itemName']} {item['itemDescription']}".lower()
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return 1
    return sum(2 if w in haystack else 1 if w in _GENERIC else 0 for w in words)


def _listing(item: dict[str, Any]) -> dict[str, Any]:
    merchant = {k: v for k, v in MERCHANTS[item["merchantId"]].items()}
    return {
        "itemId": item["itemId"], "itemName": item["itemName"], "itemImageUrl": item["itemImageUrl"],
        "itemEan": "", "unitPrice": item["unitPrice"],
        "promotionalUnitPrice": item["promotionalUnitPrice"], "merchant": merchant,
    }


class MockGateway:
    mode = "mock"

    def __init__(self) -> None:
        self.carts: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tracks: dict[str, itertools.count] = {}

    @node("mock_gateway", tool_event="tool.call")
    async def call(self, tool: str, **args: Any) -> Any:
        self.calls.append((tool, args))
        handler = getattr(self, f"_{tool}", None)
        if handler is None:
            raise MCPError(tool, "not implemented in the mock catalog")
        return handler(**args)

    # --- account ---------------------------------------------------------
    def _listAddresses(self) -> dict[str, Any]:
        return {"results": [{
            "addressId": _id("address/casa"), "alias": "Casa",
            "formattedAddress": "Rua Salomao Dibbo, 618, Jardim Lutfalla, Sao Carlos, SP",
            "coordinates": LOCATION, "isLastUsedAddress": True, "isMostUsedAddress30d": True,
        }], "agentHints": {"addressCount": 1}}

    def _getUser(self, includeProfileData: bool = False) -> dict[str, Any]:
        return {"name": "Arthur", "email": "arthur@example.com"}

    # --- discovery -------------------------------------------------------
    def _browseOffers(self, **args: Any) -> dict[str, Any]:
        if args.get("resultKind", "MERCHANT") == "MERCHANT":
            size = args.get("pageSize", 10)
            data = self._searchOffers(query="", **{**args, "pageSize": len(ITEMS)})
            merchants = {r["merchant"]["merchantId"]: r["merchant"] for r in data["results"]}
            return {"results": list(merchants.values())[:size], "hasMore": False, "nextPageToken": None}
        return self._searchOffers(query="", **args)

    def _searchOffers(self, query: str, userLocation: dict[str, Any], resultKind: str = "MERCHANT",
                      pageSize: int = 10, refinements: dict[str, Any] | None = None,
                      **_: Any) -> dict[str, Any]:
        ref = refinements or {}
        wanted = set(ref.get("itemRestrictions") or [])
        found = []
        for item in ITEMS.values():
            merchant = MERCHANTS[item["merchantId"]]
            if ref.get("onlyOpen") and not merchant["isOpen"]:
                continue
            if ref.get("merchantId") and ref["merchantId"] != item["merchantId"]:
                continue
            if wanted and not wanted <= set(item["tags"]):
                continue
            if ref.get("maxItemPrice") is not None and item["unitPrice"] > ref["maxItemPrice"]:
                continue
            if score := _score(item, query):
                found.append((score, _listing(item)))
        found.sort(key=lambda s: (-s[0], -s[1]["merchant"]["reviewRating"]))
        found = [row for _, row in found]
        return {"resultKind": "ITEM", "results": found[:pageSize], "hasMore": False,
                "nextPageToken": None, "interpretedQuery": query}

    def _getMerchant(self, merchantId: str, userLocation: dict[str, Any]) -> dict[str, Any]:
        merchant = MERCHANTS.get(merchantId)
        if not merchant:
            raise MCPError("getMerchant", "merchant not found")
        return {
            "merchantId": merchantId, "merchantName": merchant["merchantName"],
            "minimumOrderAmount": merchant["minimumOrderAmount"], "isOpen": merchant["isOpen"],
            "deliveryMethods": [merchant["deliveryMethod"]], "address": "Sao Carlos - SP",
        }

    def _getItem(self, itemId: str, merchantId: str, userLocation: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS.get(itemId)
        if not item or item["merchantId"] != merchantId:
            raise MCPError("getItem", f"item {itemId} was not found or is unavailable")
        return {
            "itemId": itemId, "itemName": item["itemName"], "itemDescription": item["itemDescription"],
            "merchantId": merchantId, "merchantName": MERCHANTS[merchantId]["merchantName"],
            "merchantMinimumOrderAmount": MERCHANTS[merchantId]["minimumOrderAmount"],
            "unitPrice": item["unitPrice"], "promotionalUnitPrice": item["promotionalUnitPrice"],
            "optionGroups": item["optionGroups"],
        }

    # --- cart ------------------------------------------------------------
    def _createCart(self, merchantId: str, addressId: str, items: list[dict[str, Any]],
                    **_: Any) -> dict[str, Any]:
        cart_id = str(uuid.uuid4())
        cart_items = []
        for entry in items:
            item = ITEMS[entry["itemId"]]
            extra = 0.0
            for opt in entry.get("options", []):
                for group in item["optionGroups"]:
                    for option in group["options"]:
                        if option["optionId"] == opt["optionId"]:
                            extra += option["additionalPrice"] * opt.get("quantity", 1)
            qty = entry.get("quantity", 1)
            cart_items.append({
                "cartItemId": str(uuid.uuid4()), "itemId": item["itemId"], "name": item["itemName"],
                "quantity": qty, "unitPrice": round(item["unitPrice"] + extra, 2),
                "totalPrice": round((item["unitPrice"] + extra) * qty, 2),
                "customerNote": entry.get("customerNote"),
            })
        self.carts[cart_id] = {"cartId": cart_id, "merchantId": merchantId, "items": cart_items,
                               "voucher": None, "delivery": None, "payment": None}
        return {"cartId": cart_id, "cart": self._render(cart_id)}

    def _render(self, cart_id: str) -> dict[str, Any]:
        cart = self.carts[cart_id]
        merchant = MERCHANTS[cart["merchantId"]]
        subtotal = round(sum(i["totalPrice"] for i in cart["items"]), 2)
        fee = merchant["deliveryMethod"]["fee"] if cart["items"] else 0
        discount = 0.0
        if cart["voucher"]:
            discount = min(cart["voucher"]["discount"], subtotal)
        return {
            "cartId": cart_id,
            "merchant": {"merchantId": cart["merchantId"], "merchantName": merchant["merchantName"]},
            "items": cart["items"],
            "delivery": {"deliveryMethodId": cart["delivery"], "fee": fee},
            "payment": {"paymentMethodId": cart["payment"]},
            "vouchers": [cart["voucher"]] if cart["voucher"] else [],
            "totals": {"subtotal": subtotal, "deliveryFee": fee, "discount": discount,
                       "total": round(subtotal + fee - discount, 2)},
        }

    def _getCart(self, cartId: str) -> dict[str, Any]:
        if cartId not in self.carts:
            raise MCPError("getCart", "cart not found")
        return self._render(cartId)

    def _removeItemFromCart(self, cartId: str, cartItemId: str) -> dict[str, Any]:
        cart = self.carts[cartId]
        cart["items"] = [i for i in cart["items"] if i["cartItemId"] != cartItemId]
        return self._render(cartId)

    def _getCartDeliveryMethods(self, cartId: str) -> dict[str, Any]:
        return {"deliveryMethods": [
            {"deliveryMethodId": "DEFAULT", "deliveryMethodMode": "DELIVERY"},
            {"deliveryMethodId": "TAKEOUT", "deliveryMethodMode": "TAKEOUT"},
        ]}

    def _setCartDelivery(self, cartId: str, deliveryMethodId: str, **_: Any) -> dict[str, Any]:
        self.carts[cartId]["delivery"] = deliveryMethodId
        return self._render(cartId)

    def _listPaymentMethods(self, merchantId: str) -> dict[str, Any]:
        return {"paymentMethods": [
            {"paymentMethodId": "PIX", "name": "Pix", "type": "PIX"},
            {"paymentMethodId": "CREDIT_CARD", "name": "Cartao", "type": "CREDIT_CARD"},
        ]}

    def _setCartPayment(self, cartId: str, paymentMethodId: str, **_: Any) -> dict[str, Any]:
        self.carts[cartId]["payment"] = paymentMethodId
        return self._render(cartId)

    def _listVouchers(self, cartId: str) -> dict[str, Any]:
        subtotal = self._render(cartId)["totals"]["subtotal"]
        vouchers = [
            {"code": "SEMCOMP10", "campaignId": _id("v/semcomp10"), "description": "R$ 10 off",
             "discountValue": 10.0, "minimumOrderValue": 40.0},
            {"code": "FRETEGRATIS", "campaignId": _id("v/frete"), "description": "R$ 6 off",
             "discountValue": 6.0, "minimumOrderValue": 0.0},
        ]
        return {"vouchers": [v for v in vouchers if subtotal >= v["minimumOrderValue"]]}

    def _setVoucher(self, cartId: str, code: str, campaignId: str) -> dict[str, Any]:
        value = {"SEMCOMP10": 10.0, "FRETEGRATIS": 6.0}.get(code)
        if value is None:
            raise MCPError("setVoucher", "voucher not applicable")
        self.carts[cartId]["voucher"] = {"code": code, "campaignId": campaignId, "discount": value}
        return self._render(cartId)

    def _removeVoucher(self, cartId: str) -> dict[str, Any]:
        self.carts[cartId]["voucher"] = None
        return self._render(cartId)

    # --- orders ----------------------------------------------------------
    def _submitOrder(self, cartId: str, userConsent: bool = False, **_: Any) -> dict[str, Any]:
        if not userConsent:
            raise MCPError("submitOrder", "userConsent must be true")
        order_id = str(uuid.uuid4())
        self.orders[order_id] = {"orderId": order_id, "cartId": cartId,
                                 "verificationCode": f"{abs(hash(order_id)) % 10000:04d}"}
        self._tracks[order_id] = itertools.count()
        return {"orderId": order_id, "status": "PLACED"}

    def _getOrder(self, orderId: str) -> dict[str, Any]:
        order = self.orders.get(orderId)
        if not order:
            raise MCPError("getOrder", "order not found")
        return {"orderId": orderId, "verificationCode": order["verificationCode"],
                "cart": self._render(order["cartId"])}

    def _trackOrder(self, orderId: str, **_: Any) -> dict[str, Any]:
        steps = ["PLACED", "CONFIRMED", "PREPARING", "OUT_FOR_DELIVERY", "DELIVERED"]
        step = min(next(self._tracks[orderId]), len(steps) - 1)
        return {"orderId": orderId, "status": steps[step],
                "events": [{"status": s} for s in steps[: step + 1]], "agentHints": {}}
