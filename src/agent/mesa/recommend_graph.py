"""LangGraph: as people chat, find restaurants that can feed the whole table.

    load_context -> extract_intents -> find_merchants -> publish -> END
                                                     (any node -> fail -> END)

It publishes two things to the table:

* ``merchant_options`` + ``selected_merchant_id`` - the restaurants that have
  something for everyone, so the host can switch between them;
* ``recommendations`` - each person's suggested dish **at the selected
  restaurant**, shown above their composer with a "+" for the shared cart.

Every candidate's full per-person dish lists are kept in the in-memory
``_CANDIDATES`` cache (never serialised), which makes switching restaurants
instant instead of a new round of searches.

The run is triggered automatically whenever somebody's desire changes, and is
debounced through ``session.recommend_dirty``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.mesa import context, solver
from agent.mesa.gateway import MCPError, get_gateway
from agent.mesa.intents import extract_intents
from agent.mesa.models import McpCart, MerchantOption, Recommendation, Session
from agent.mesa.parsing import to_float
from agent.mesa.store import log, say, store
from agent.mesa.observability import node as trace_node, operation

Node = Callable[[dict], Awaitable[dict]]

#: room -> ranked candidates with everyone's dish lists (memory only, never sent).
_CANDIDATES: dict[str, list[solver.Candidate]] = {}
_CANDIDATE_SIGNATURES: dict[str, tuple] = {}


class RecommendState(TypedDict, total=False):
    room_id: str
    intents: dict[str, Any]
    candidates: list[solver.Candidate]
    error: str
    unavailable: bool


def _session(state: dict) -> Session:
    return store.sessions[state["room_id"]]


def _participants(session: Session) -> list:
    return [m for m in session.active_members if m.desire or m.restrictions]


def preference_signature(session: Session) -> tuple:
    return tuple((m.id, m.desire, tuple(m.restrictions), m.max_budget) for m in _participants(session))


def _logger(session: Session) -> solver.Log:
    return lambda text, kind="info": log(session, text, kind)


def recommend_uses_llm() -> bool:
    """Intent extraction for searches: keywords by default, so re-search is fast.

    The chat agent already rewrites each desire into clean text, which the
    keyword extractor handles well; an LLM call per search would add ~30s on a
    local model for little gain.
    """
    return os.getenv("MESA_RECOMMEND_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}


def guarded(node: Node) -> Node:
    async def wrapper(state: dict) -> dict[str, Any]:
        try:
            return await node(state)
        except (solver.NoMatch, context.NoAddress) as exc:
            return {"error": str(exc)}
        except MCPError as exc:
            return {"error": f"Nao consegui atualizar as sugestoes do iFood: {exc.message}",
                    "unavailable": True}

    wrapper.__name__ = node.__name__
    return wrapper


@guarded
async def load_context(state: RecommendState) -> dict[str, Any]:
    """``listAddresses`` first; the coordinates feed every search."""
    session = _session(state)
    session.recommend_phase = "Consultando o endereco de entrega…"
    if session.host_address is None:
        log(session, "listAddresses -> consultando enderecos da conta autenticada", "tool")
    address = await context.resolve_address(get_gateway(), session)
    log(session, f"listAddresses -> entregando em '{address.alias}'", "tool")
    return {}


@guarded
async def extract(state: RecommendState) -> dict[str, Any]:
    session = _session(state)
    session.recommend_phase = "Buscando restaurantes para os pedidos da mesa…"
    members = _participants(session)
    intents, note = await extract_intents(members, use_llm=recommend_uses_llm())
    if note:
        log(session, note, "warn")
    for member in members:
        intent = intents[member.id]
        ceiling = f" | teto por prato R$ {member.max_budget:.2f}" if member.max_budget is not None else ""
        exclusions = f" | sem {', '.join(intent.excluded_terms)}" if intent.excluded_terms else ""
        log(session, f"{member.name}: buscar {intent.queries[:2]}{ceiling}{exclusions}", "info")
    return {"intents": intents}


@guarded
async def find(state: RecommendState) -> dict[str, Any]:
    session = _session(state)
    assert session.host_address
    searcher = solver.Searcher(get_gateway(), context.location_of(session))
    candidates = await solver.find_merchant(
        searcher, _participants(session), state["intents"], _logger(session)
    )
    # Show the table promptly. Details are needed for the restaurant that will
    # be displayed now; alternative restaurants remain selectable from their
    # search results and an item is always validated again before the cart.
    selected_id = session.selected_merchant_id
    displayed = next((c for c in candidates if c.merchant.merchant_id == selected_id), candidates[0])
    hits = { (h.merchant.merchant_id, h.item_id): h
             for options in displayed.options.values() for h in options }
    session.recommend_phase = f"Conferindo {len(hits)} pratos antes de mostrar as opcoes…"
    log(session, f"Conferindo detalhes de {len(hits)} pratos no restaurante sugerido.", "info")
    await asyncio.gather(*(searcher.describe(h) for h in hits.values()))
    members = {m.id: m for m in _participants(session)}
    for candidate in candidates:
        for member_id, options in candidate.options.items():
            member = members[member_id]
            budget = member.max_budget if member.max_budget is not None else state["intents"][member_id].max_price
            for hit in options:
                enriched = hits.get((hit.merchant.merchant_id, hit.item_id))
                if enriched is None:
                    continue
                hit.description = enriched.description
                hit.image = enriched.image
                hit.detail = enriched.detail
                if hit.detail:
                    base_price = to_float(hit.detail.get("promotionalUnitPrice")
                                          or hit.detail.get("unitPrice"), hit.price)
                    _, extras, _ = solver.pick_options(hit.detail, member)
                    hit.price = round(base_price + extras, 2)
            candidate.options[member_id] = [
                hit for hit in options
                if (budget is None or hit.price <= budget)
                and solver.matches_exact(hit, state["intents"][member_id].model_copy(
                    update={"required_terms": [], "queries": [hit.alternative_query]} if hit.alternative_query else {}))
                and (not hit.alternative_query or not solver.risk_warnings(hit.description, member))
            ]
    candidates = [c for c in candidates if any(c.options.get(mid) for mid in members)]
    if not candidates:
        raise solver.NoMatch("Nenhum dos pratos encontrados passou pela conferencia dos desejos, precos e restricoes. "
                             "Nao tenho uma sugestao segura para mostrar com esses limites.")
    candidates.sort(key=lambda c: (-sum(bool(c.options.get(mid)) for mid in members),
                                   sum(bool(hits and hits[0].alternative_query) for hits in c.options.values())))
    return {"candidates": candidates}


def merchant_option(candidate: solver.Candidate, members: list) -> MerchantOption:
    merchant = candidate.merchant
    return MerchantOption(
        merchant_id=merchant.merchant_id,
        name=merchant.name,
        logo_url=merchant.logo,
        fee=merchant.fee,
        minimum_order_amount=merchant.minimum,
        rating=round(merchant.rating, 1),
        distance_km=merchant.distance_km,
        eta=merchant.eta,
        covers=sum(1 for m in members if candidate.options.get(m.id)),
        alternative_members=[m.name for m in members if any(h.alternative_query for h in candidate.options.get(m.id, []))],
        missing_members=[m.name for m in members if not candidate.options.get(m.id)],
        requested_count=len(members),
        search_incomplete=candidate.search_incomplete,
    )


def clear_cart(session: Session) -> int:
    """Drop every dish in the cart (items belong to one restaurant)."""
    cleared = [m for m in session.active_members if m.assigned_item]
    for member in cleared:
        member.assigned_item = None
    if cleared:
        session.mcp_cart = McpCart()
    return len(cleared)


def _hit_note(hit: solver.Hit, member) -> str | None:
    if hit.alternative_query:
        return f'Alternativa similar a "{member.desire}"; o pedido exato nao foi encontrado neste restaurante.'
    if hit.over_budget:
        return f"passa do teto de R$ {member.max_budget:.0f}" if member.max_budget else "acima do teto"
    return None


def publish_recommendations(session: Session, merchant_id: str) -> bool:
    """Point ``session.recommendations`` at one restaurant from the cache."""
    candidates = _CANDIDATES.get(session.room_id, [])
    candidate = next((c for c in candidates if c.merchant.merchant_id == merchant_id), None)
    if candidate is None:
        return False

    session.selected_merchant_id = merchant_id
    session.recommendations = [
        Recommendation(
            member_id=member.id,
            item_id=hit.item_id,
            name=hit.name,
            price=hit.price,
            description=hit.description,
            image_url=hit.image,
            over_budget=hit.over_budget,
            note=_hit_note(hit, member),
            alternative_query=hit.alternative_query,
        )
        for member in session.active_members
        for hit in candidate.options.get(member.id, [])
    ]
    alternatives = [(member, candidate.options.get(member.id, [])) for member in session.active_members
                    if any(h.alternative_query for h in candidate.options.get(member.id, []))]
    missing = [member for member in _participants(session) if not candidate.options.get(member.id)]
    signature = repr((merchant_id, [(member.id, member.desire, [h.item_id for h in hits])
                                    for member, hits in alternatives],
                      [(member.id, member.desire) for member in missing], candidate.search_incomplete)) if alternatives or missing else ""
    if signature and signature != session.alternative_signature:
        summary = "; ".join(f'{member.name} pediu "{member.desire}" → {hits[0].name}'
                            for member, hits in alternatives)
        text = ("Parte das consultas ao iFood falhou; nao consegui confirmar uma opcao exata para todos. "
                if candidate.search_incomplete else
                "Nas lojas consultadas, nao encontrei um restaurante que atendesse todos os desejos exatos. ")
        text += f"Em **{candidate.merchant.name}**, "
        if alternatives:
            text += f"mostrei alternativas similares para: {summary}. "
        if missing:
            pending = "; ".join(f'{member.name}: "{member.desire}"' for member in missing)
            text += f"Ainda sem opcao compativel neste restaurante, mesmo entre os similares: {pending}. "
            text += "Mostrei os pratos disponiveis para os demais; o host pode trocar de restaurante ou voces podem ajustar os desejos. "
        text += "Os pedidos originais continuam registrados; aceitar uma alternativa exige adicionar pelo +. Mantive os tetos e restricoes."
        say(session, text)
    session.alternative_signature = signature
    return True


def _announce(session: Session) -> None:
    """Tell the group once per result set - not on every keystroke."""
    signature = repr((preference_signature(session), [(o.merchant_id, o.alternative_members, o.missing_members) for o in session.merchant_options]))
    if signature == session.recommend_signature:
        return
    session.recommend_signature = signature

    names = [o.name for o in session.merchant_options if o.merchant_id != session.selected_merchant_id]
    chosen = session.selected_option()
    if chosen is None:
        return
    total = len(session.merchant_options)
    description = "com opcoes parciais para a mesa" if chosen.missing_members else "com opcoes e alternativas para a mesa" if chosen.alternative_members else "que atende a mesa"
    text = f"Achei {total} restaurante{'s' if total > 1 else ''} {description} e escolhi **{chosen.name}**"
    if names:
        text += f". O host pode trocar por {', '.join(names[:3])} no topo do chat."
    else:
        text += "."
    text += " Confira as sugestoes acima da caixa de mensagem; o carrinho so pode fechar quando todos tiverem um prato."
    say(session, text)


@guarded
async def publish(state: RecommendState) -> dict[str, Any]:
    session = _session(state)
    members = _participants(session)
    candidates: list[solver.Candidate] = state["candidates"]
    _CANDIDATES[session.room_id] = candidates
    _CANDIDATE_SIGNATURES[session.room_id] = preference_signature(session)

    previously_selected = session.selected_merchant_id
    options = [merchant_option(candidate, members) for candidate in candidates]
    session.merchant_options = options
    if session.last_error == session.recommend_error:
        session.last_error = None
    session.recommend_error = None

    best_coverage = max((o.covers for o in options), default=0)
    keep = previously_selected if any(o.merchant_id == previously_selected and o.covers == best_coverage for o in options) else None
    chosen = keep or (options[0].merchant_id if options else None)
    if chosen:
        publish_recommendations(session, chosen)
    if chosen != previously_selected:
        if previously_selected is not None:
            # The table switched restaurant on its own: old dishes are invalid.
            cleared = clear_cart(session)
            if cleared:
                say(session, "Mudei o restaurante e recomecei o carrinho.")
        option = session.selected_option()
        log(session, f"Restaurante sugerido: {option.name if option else '-'}", "ok")
    _announce(session)
    return {}


async def fail(state: RecommendState) -> dict[str, Any]:
    session = _session(state)
    keep = (state.get("unavailable") and session.merchant_options
            and _CANDIDATE_SIGNATURES.get(session.room_id) == preference_signature(session))
    if not keep:
        session.merchant_options = []
        session.recommendations = []
        session.selected_merchant_id = None
        _CANDIDATES.pop(session.room_id, None)
        _CANDIDATE_SIGNATURES.pop(session.room_id, None)
    session.last_error = state.get("error")
    session.recommend_error = state.get("error")
    log(session, state.get("error", "erro na busca"), "warn")
    if state.get("error") and state["error"] != session.recommend_signature:
        session.recommend_signature = state["error"]
        say(session, state["error"])
    return {}


def _route(state: RecommendState) -> str:
    return "fail" if state.get("error") else "next"


def build_recommend_graph():
    graph = StateGraph(RecommendState)
    steps: list[tuple[str, Node]] = [
        ("load_context", load_context),
        ("extract_intents", extract),
        ("find_merchants", find),
        ("publish", publish),
    ]
    for name, node in steps:
        graph.add_node(name, trace_node(name)(node))
    graph.add_node("fail", trace_node("fail")(fail))
    graph.set_entry_point(steps[0][0])
    names = [n for n, _ in steps]
    for current, following in pairwise(names):
        graph.add_conditional_edges(current, _route, {"next": following, "fail": "fail"})
    graph.add_edge("publish", END)
    graph.add_edge("fail", END)
    return graph.compile()


_graph = None


@operation("recommend")
async def run_recommend(room_id: str) -> None:
    """Search once; if a desire changed meanwhile, search again. Never raises."""
    global _graph
    _graph = _graph or build_recommend_graph()

    session = store.sessions[room_id]
    # Bounded loop instead of recursion: a burst of messages coalesces into
    # "search, and search again at most twice more".
    for _ in range(3):
        session.recommend_dirty = False
        session.searching_recommendations = True
        try:
            await _graph.ainvoke({"room_id": room_id})
        except Exception as exc:  # noqa: BLE001 - keep the table usable
            await fail({"room_id": room_id, "error": f"Falha na busca: {type(exc).__name__}: {exc}"})
        finally:
            session.searching_recommendations = False
            session.recommend_phase = None
            store.save()
        if not session.recommend_dirty:
            break
