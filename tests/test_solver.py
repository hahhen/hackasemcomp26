from agent.mesa.intents import heuristic_intent
from agent.mesa.models import Member
from agent.mesa.parsing import decode, find_first, to_float
from agent.mesa.solver import customer_note, pick_options, risk_warnings


def member(**kw) -> Member:
    return Member(id="m", name=kw.pop("name", "Bia"), **kw)


def test_customer_note_labels_the_owner_and_restriction():
    assert customer_note(member(restrictions=["VEGETARIAN"])) == "[Bia - VEGETARIANO]"
    assert customer_note(member(name="Joao")) == "[Joao]"
    assert customer_note(member(restrictions=["LAC_FREE", "GLUTEN_FREE"])) == "[Bia - SEM LACTOSE / SEM GLUTEN]"


def test_ingredient_check_flags_what_the_mcp_filter_missed():
    lac = member(restrictions=["LAC_FREE"])
    assert risk_warnings("Pão, burger, queijo prato e picles", lac)
    assert not risk_warnings("Frango empanado, molho da casa, sem laticinios", lac)
    assert not risk_warnings("Burger com maionese vegana", lac)

    veg = member(restrictions=["VEGETARIAN"])
    assert risk_warnings("Fit Frango grelhado com salada", veg)
    assert not risk_warnings("Burger de berinjela defumada e tomate", veg)


def test_unrestricted_members_never_get_flags():
    assert risk_warnings("Burger de carne com bacon e queijo", member()) == []


def test_required_option_groups_are_filled_with_the_dietary_variant():
    detail = {"optionGroups": [
        {"minSelections": 1, "options": [
            {"optionId": "a", "name": "Brioche", "additionalPrice": 0},
            {"optionId": "b", "name": "Pao sem gluten", "additionalPrice": 3},
        ]},
        {"minSelections": 0, "options": [{"optionId": "c", "name": "Bacon", "additionalPrice": 4}]},
    ]}
    options, extra, labels = pick_options(detail, member(restrictions=["GLUTEN_FREE"]))
    assert options == [{"optionId": "b", "quantity": 1}]
    assert extra == 3 and labels == ["Pao sem gluten"]

    options, extra, _ = pick_options(detail, member())
    assert options == [{"optionId": "a", "quantity": 1}] and extra == 0


def test_heuristic_intent_drops_filler_and_negations():
    intent = heuristic_intent(member(desire="Qualquer lanche bom, mas zero queijo/laticinios"))
    assert "queijo" not in " ".join(intent.queries)
    assert intent.queries[-1] == "prato"

    intent = heuristic_intent(member(desire="Smash duplo barato", max_budget=25))
    assert intent.queries[0] == "smash duplo"
    assert intent.max_price == 25 and intent.cheap


def test_parsing_is_tolerant_of_mcp_shapes():
    assert decode([{"type": "text", "text": '{"cartId": "abc"}'}]) == {"cartId": "abc"}
    assert decode("plain words") == {"text": "plain words"}
    assert find_first({"cart": {"items": [{"cartId": "x"}]}}, "cartId") == "x"
    assert to_float("R$ 1.234,50") == 1234.5
    assert to_float({"value": 12.5}) == 12.5
