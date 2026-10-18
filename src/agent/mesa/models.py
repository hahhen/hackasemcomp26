"""State of a table (a "mesa"): the whole in-memory database.

Field names are snake_case in Python and camelCase on the wire, so the JSON the
frontend sees matches the shape sketched in the project docs (``roomId``,
``hostAddress``, ``assignedItem``, ``mcpCart``...).
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field
from pydantic.alias_generators import to_camel

#: Values accepted by ``searchOffers.refinements.itemRestrictions``.
Restriction = Literal["VEGETARIAN", "VEGAN", "LAC_FREE", "GLUTEN_FREE", "SUGAR_FREE", "ORGANIC"]

RESTRICTION_LABELS: dict[str, str] = {
    "VEGETARIAN": "VEGETARIANO",
    "VEGAN": "VEGANO",
    "LAC_FREE": "SEM LACTOSE",
    "GLUTEN_FREE": "SEM GLUTEN",
    "SUGAR_FREE": "SEM ACUCAR",
    "ORGANIC": "ORGANICO",
}


class Status(StrEnum):
    """COLLECTING covers everything up to the host closing the cart."""

    COLLECTING_PREFERENCES = "COLLECTING_PREFERENCES"
    WAITING_PAYMENTS = "WAITING_PAYMENTS"
    SUBMITTING = "SUBMITTING"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Quota(CamelModel):
    """Exact share of the bill: item + delivery/N - proportional voucher."""

    item: float = 0
    delivery_share: float = 0
    discount_share: float = 0
    total: float = 0


class AssignedItem(CamelModel):
    item_id: str
    name: str
    merchant_id: str
    unit_price: float
    options_price: float = 0
    options: list[dict[str, Any]] = Field(default_factory=list)
    option_labels: list[str] = Field(default_factory=list)
    customer_note: str = ""
    image_url: str | None = None
    cart_item_id: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def price(self) -> float:
        """Dish + chosen options, and part of the JSON the UI reads."""
        return round(self.unit_price + self.options_price, 2)


class Member(CamelModel):
    id: str
    name: str
    is_host: bool = False
    desire: str = ""
    restrictions: list[Restriction] = Field(default_factory=list)
    max_budget: float | None = None
    assigned_item: AssignedItem | None = None
    amount_to_pay: float = 0
    quota: Quota | None = None
    has_paid: bool = False
    #: The host agreed to pay this member's share.
    covered_by_host: bool = False
    #: Dropped from the cart (gave up / did not pay in time).
    removed: bool = False

    @property
    def active(self) -> bool:
        return not self.removed


class HostAddress(CamelModel):
    address_id: str
    alias: str
    label: str = ""
    latitude: float
    longitude: float


class McpCart(CamelModel):
    cart_id: str | None = None
    merchant_id: str | None = None
    merchant_name: str | None = None
    merchant_logo_url: str | None = None
    minimum_order_amount: float = 0
    subtotal: float = 0
    delivery_fee: float = 0
    voucher_code: str | None = None
    voucher_discount: float = 0
    total_amount: float = 0
    delivery_method_id: str | None = None
    payment_method_id: str | None = None
    estimated_minutes: str | None = None
    order_id: str | None = None
    verification_code: str | None = None
    order_status: str | None = None
    #: True when ``submitOrder`` was skipped because real orders are disabled.
    simulated: bool = False


class Recommendation(CamelModel):
    """One person's suggested dish from the currently selected restaurant."""

    member_id: str
    item_id: str
    name: str
    price: float
    description: str = ""
    image_url: str | None = None
    #: Above that person's stated ceiling: shown with a warning, never hidden.
    over_budget: bool = False
    note: str | None = None
    alternative_query: str | None = None


class MerchantOption(CamelModel):
    """A restaurant that can feed the whole table (metadata only).

    The per-person dishes for each option live in the in-memory candidate cache
    so the host can switch instantly; only the selected one is published.
    """

    merchant_id: str
    name: str
    logo_url: str | None = None
    fee: float = 0
    minimum_order_amount: float = 0
    rating: float = 0
    distance_km: float | None = None
    eta: str | None = None
    #: How many people this restaurant has something for.
    covers: int = 0
    alternative_members: list[str] = Field(default_factory=list)


class Event(CamelModel):
    ts: float = Field(default_factory=time.time)
    kind: Literal["info", "tool", "warn", "error", "ok"] = "info"
    text: str


class ChatMessage(CamelModel):
    """One message of the shared group chat.

    Every message carries its author, because the chat is one room for the whole
    table and the agent must know who is speaking (``author_id`` is the member
    id; ``None`` means the agent or the system).
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: Literal["user", "agent", "system"] = "user"
    author_id: str | None = None
    author_name: str = "Agente"
    text: str
    ts: float = Field(default_factory=time.time)
    images: list[str] = Field(default_factory=list)


class Session(CamelModel):
    room_id: str
    observability_order_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: Status = Status.COLLECTING_PREFERENCES
    mode: Literal["mock", "live"] = "mock"
    host_address: HostAddress | None = None
    members: list[Member] = Field(default_factory=list)
    mcp_cart: McpCart = Field(default_factory=McpCart)
    events: list[Event] = Field(default_factory=list)
    chat: list[ChatMessage] = Field(default_factory=list)
    last_error: str | None = None
    recommend_error: str | None = None
    #: True while the reconcile graph is running.
    busy: bool = False
    #: True while the chat agent is answering (so other devices can show dots).
    chat_busy: bool = False
    tracking: list[dict[str, Any]] = Field(default_factory=list)

    # --- restaurant search (runs automatically as people chat) ---------------
    #: True while restaurants are being searched, so the UI can say so.
    searching_recommendations: bool = False
    recommend_phase: str | None = None
    #: Restaurants that can feed everyone, best first (the host switches between them).
    merchant_options: list[MerchantOption] = Field(default_factory=list)
    selected_merchant_id: str | None = None
    #: Each person's suggested dish at the selected restaurant, with a "+" to add.
    recommendations: list[Recommendation] = Field(default_factory=list)
    #: Signature of the last published result, so the chat is not spammed.
    recommend_signature: str = ""
    alternative_signature: str = ""
    #: Set when a desire changes while a search is already running.
    recommend_dirty: bool = False

    def recommendation(self, member_id: str, item_id: str | None = None) -> Recommendation | None:
        return next((r for r in self.recommendations
                     if r.member_id == member_id and (item_id is None or r.item_id == item_id)), None)

    def selected_option(self) -> MerchantOption | None:
        return next(
            (o for o in self.merchant_options if o.merchant_id == self.selected_merchant_id), None
        )

    @computed_field
    @property
    def cart_members(self) -> list[Member]:
        """Everyone who already added their dish (this *is* the shared cart)."""
        return [m for m in self.active_members if m.assigned_item]

    @computed_field
    @property
    def cart_subtotal(self) -> float:
        return round(sum(m.assigned_item.price for m in self.cart_members), 2)  # type: ignore[union-attr]

    @computed_field
    @property
    def cart_is_full(self) -> bool:
        """Everyone still at the table has added something."""
        active = self.active_members
        return bool(active) and all(m.assigned_item is not None for m in active)

    def member(self, member_id: str) -> Member | None:
        return next((m for m in self.members if m.id == member_id), None)

    def host(self) -> Member | None:
        return next((m for m in self.active_members if m.is_host), None)

    @computed_field
    @property
    def host_member_id(self) -> str | None:
        host = self.host()
        return host.id if host else None

    @property
    def active_members(self) -> list[Member]:
        return [m for m in self.members if m.active]

    @computed_field
    @property
    def paid_count(self) -> int:
        return sum(1 for m in self.active_members if m.has_paid)

    @computed_field
    @property
    def active_count(self) -> int:
        return len(self.active_members)

    @computed_field
    @property
    def all_paid(self) -> bool:
        active = self.active_members
        return bool(active) and all(m.has_paid for m in active)
