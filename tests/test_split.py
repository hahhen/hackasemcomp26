from agent.mesa.split import compute_quotas


def test_quotas_add_up_to_the_exact_bill():
    items = {"a": 24.90, "b": 26.90, "c": 27.90, "d": 18.90}
    quotas = compute_quotas(items, delivery_fee=6.00, discount=10.00)

    expected = round(sum(items.values()) + 6.00 - 10.00, 2)
    assert round(sum(q.total for q in quotas.values()), 2) == expected


def test_no_cent_is_lost_on_awkward_splits():
    quotas = compute_quotas({"a": 10, "b": 10, "c": 10}, delivery_fee=5.00, discount=1.00)
    assert round(sum(q.total for q in quotas.values()), 2) == 34.00


def test_discount_is_proportional_and_fee_is_even():
    quotas = compute_quotas({"a": 30, "b": 10}, delivery_fee=4.00, discount=8.00)
    assert quotas["a"].delivery_share == quotas["b"].delivery_share == 2.00
    assert quotas["a"].discount_share == 6.00
    assert quotas["b"].discount_share == 2.00
    assert quotas["a"].total == 26.00


def test_discount_never_exceeds_subtotal():
    quotas = compute_quotas({"a": 5}, delivery_fee=0, discount=50)
    assert quotas["a"].total == 0
