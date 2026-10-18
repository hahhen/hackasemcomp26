"""Exact bill splitting.

    quota_i = item_i + delivery_fee / N - discount * item_i / subtotal

Everything is computed in integer cents with largest-remainder rounding, so the
quotas always add up to exactly ``subtotal + delivery_fee - discount``. A float
implementation drifts by a cent and the host ends up paying for it.
"""

from __future__ import annotations

from agent.mesa.models import Quota


def cents(value: float) -> int:
    return round(value * 100)


def _spread(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` cents proportionally to ``weights`` without losing a cent."""
    weight_sum = sum(weights)
    if not weights:
        return []
    if weight_sum <= 0:
        weights, weight_sum = [1] * len(weights), len(weights)

    raw = [total * w / weight_sum for w in weights]
    shares = [int(r) for r in raw]
    leftover = total - sum(shares)
    by_remainder = sorted(range(len(raw)), key=lambda i: raw[i] - shares[i], reverse=True)
    for i in by_remainder[:leftover]:
        shares[i] += 1
    return shares


def compute_quotas(
    item_costs: dict[str, float], delivery_fee: float, discount: float
) -> dict[str, Quota]:
    """Per-member quota. ``item_costs`` maps member id -> price of their dish."""
    ids = list(item_costs)
    if not ids:
        return {}

    item_cents = [cents(item_costs[i]) for i in ids]
    subtotal = sum(item_cents)
    discount_cents = min(max(cents(discount), 0), subtotal)

    fee_shares = _spread(cents(delivery_fee), [1] * len(ids))
    discount_shares = _spread(discount_cents, item_cents)

    quotas: dict[str, Quota] = {}
    for member_id, item, fee, disc in zip(ids, item_cents, fee_shares, discount_shares):
        quotas[member_id] = Quota(
            item=item / 100,
            delivery_share=fee / 100,
            discount_share=disc / 100,
            total=(item + fee - disc) / 100,
        )
    return quotas
