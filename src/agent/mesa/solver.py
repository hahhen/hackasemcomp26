"""Multi-constraint solver: one restaurant, one *different* dish per friend.

This is the part Ailo gets wrong (4 identical dishes, or a combo that ignores
restrictions). It is deterministic code on purpose - an LLM guessing a cart is
how you end up with a vegetarian eating bacon.

    1. Search in parallel, one ``searchOffers`` per member with ``resultKind=ITEM``
       and that member's ``itemRestrictions`` (the MCP only accepts them for items).
    2. Intersect merchant ids across the *restricted* members: those are hard
       constraints, so the winner must serve all of them.
    3. For the best merchants, complete coverage with merchant-scoped searches
       (``refinements.merchantId``) for everyone the first pass did not reach.
    4. Rank by (over-budget members, minimum-order shortfall, total cost), then
       confirm with ``getMerchant`` and ``getItem`` and resolve required options.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from agent.mesa.gateway import Gateway, MCPError
from agent.mesa.intents import Intent, heuristic_intent, wish_constraints
from agent.mesa.models import RESTRICTION_LABELS, AssignedItem, Member
from agent.mesa.parsing import decode, to_float

#: Restaurants offered in the dropdown (the host can switch between them).
MAX_CANDIDATE_MERCHANTS = 12
DISCOVERY_PAGES = 8
PAGE_SIZE = 25
CONCURRENCY = 4
# A restaurant can have hundreds of matching menu items. The UI is useful with
# a short horizontal set; checking every item before showing any card is not.
MAX_RECOMMENDATIONS_PER_MEMBER = 8
DETAIL_TIMEOUT_SECONDS = 12

Log = Callable[[str, str], None]
logger = logging.getLogger(__name__)


def recoverable_search_error(exc: Exception) -> bool:
    return isinstance(exc, MCPError) and any(marker in exc.message.lower() for marker in (
        "incompleta", "temporar", "server returned an error response", "upstream", "timeout", "timed out",
        "429", "500", "502", "503", "504",
    ))


class NoMatch(RuntimeError):
    """No restaurant can serve everyone; the message says why, in Portuguese."""


@dataclass
class MerchantInfo:
    merchant_id: str
    name: str
    fee: float = 0.0
    minimum: float = 0.0
    rating: float = 0.0
    logo: str | None = None
    eta: str | None = None
    distance_km: float | None = None
    open: bool = True


@dataclass
class Hit:
    item_id: str
    name: str
    price: float
    merchant: MerchantInfo
    image: str | None
    query_rank: int
    #: Position in the server's relevance-ordered results for that query.
    position: int = 0
    over_budget: bool = False
    description: str = ""
    detail: dict[str, Any] | None = None
    alternative_query: str | None = None


@dataclass
class Candidate:
    """A restaurant that covers every member, with each member's options best-first."""

    merchant: MerchantInfo
    options: dict[str, list[Hit]]
    search_incomplete: bool = False


# --- parsing ----------------------------------------------------------------


def _merchant_from(row: dict[str, Any]) -> MerchantInfo:
    delivery = row.get("deliveryMethod") or {}
    minutes = delivery.get("estimatedMinutes") or {}
    fee = delivery.get("promotionalFee")
    return MerchantInfo(
        merchant_id=row["merchantId"],
        name=row.get("merchantName", "Restaurante"),
        fee=to_float(fee if fee is not None else delivery.get("fee")),
        minimum=to_float(row.get("minimumOrderAmount")),
        rating=to_float(row.get("reviewRating")),
        logo=row.get("merchantLogoUrl"),
        eta=f"{minutes['min']}-{minutes['max']} min" if minutes.get("max") else None,
        distance_km=to_float(row.get("straightLineDistanceKm")) or None,
        open=row.get("isOpen", True) is not False,
    )


def _hit_from(row: dict[str, Any], rank: int, position: int, budget: float | None) -> Hit | None:
    merchant = row.get("merchant") or {}
    if not row.get("itemId") or not merchant.get("merchantId"):
        return None
    price = to_float(row.get("promotionalUnitPrice") or row.get("unitPrice"))
    return Hit(
        item_id=row["itemId"],
        name=row.get("itemName", "Item"),
        price=price,
        merchant=_merchant_from(merchant),
        image=row.get("itemImageUrl"),
        query_rank=rank,
        position=position,
        over_budget=budget is not None and price > budget,
        description=row.get("itemDescription") or "",
    )


def rank_key(hit: Hit, cheap: bool) -> tuple:
    """Best first: within budget, then the most specific query, then relevance.

    Someone who asked for something cheap gets price as the tie-breaker instead.
    """
    return (hit.over_budget, hit.query_rank, hit.price if cheap else hit.position)


def matches_wish(hit: Hit, intent: Intent) -> bool:
    text = _plain(f"{hit.name} {hit.description}")
    # A generic burger match is not evidence of an artisanal burger.
    if any(not re.search(rf"\b{re.escape(term)}\b", text)
           for term in intent.required_terms):
        return False
    for term in intent.excluded_terms:
        # "sem bacon" is evidence of absence, while "bacon crocante" is not.
        positive = re.sub(rf"\b(?:sem|zero)\s+{re.escape(term)}\b", "", text)
        if re.search(rf"\b{re.escape(term)}\b", positive):
            return False
    return True


def food_words(text: str) -> set[str]:
    text = _plain(text)
    text = re.sub(r"\bx[\s-]*(?=salada\b|bacon\b|tudo\b|egg\b|frango\b|burguer\b|burger\b|calabresa\b)", "hamburguer ", text)
    text = re.sub(r"\b(?:hamburger|hamburguer|burger|burguer|burgers)\b", "hamburguer", text)
    words = set(re.findall(r"[a-z]+", text)) - {"com", "de", "do", "da", "e", "um", "uma", "comida"}
    for category, related in FOOD_FAMILIES.items():
        if words & related:
            words.add(category)
    return words


FOOD_FAMILIES = {
    "hamburguer": {"hamburguer", "smash"},
    "pizza": {"pizza", "pizzas"},
    "japonesa": {"sushi", "sashimi", "temaki", "uramaki", "hossomaki", "niguiri", "nigiri"},
    "massa": {"massa", "macarrao", "lasanha", "nhoque", "ravioli", "espaguete"},
    "esfiha": {"esfiha", "esfirra"},
    "salgado": {"salgado", "salgados", "coxinha", "pastel", "empada", "risole", "kibe", "quibe"},
    "acai": {"acai"},
}


def matches_exact(hit: Hit, intent: Intent) -> bool:
    if not matches_wish(hit, intent):
        return False
    positive = re.sub(r"\b(?:sem|zero)\s+[a-z]+", "", _plain(f"{hit.name} {hit.description}"))
    return intent.browse or not intent.queries or food_words(intent.queries[0]) <= food_words(positive)


def similar_query(intent: Intent) -> str | None:
    """Conservative food families; never turn sushi into an arbitrary sandwich."""
    words = food_words(" ".join(intent.queries))
    for family in FOOD_FAMILIES:
        if family in words:
            return family
    return None


def alternative_request(member: Member, intent: Intent) -> tuple[Member, Intent] | None:
    query = similar_query(intent)
    if not query:
        return None
    exclusions = " ".join(f"sem {term}" for term in intent.excluded_terms)
    alternative = member.model_copy(update={"desire": f"{query} {exclusions}"})
    return alternative, heuristic_intent(alternative).model_copy(update={"max_price": intent.max_price})


# --- searching ---------------------------------------------------------------


class Searcher:
    def __init__(self, gateway: Gateway, location: dict[str, float]) -> None:
        self.gw = gateway
        self.location = location
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._details: dict[tuple[str, str], asyncio.Task] = {}
        self._searches: dict[str, asyncio.Task] = {}

    async def _search(self, query: str, refinements: dict[str, Any], *, browse: bool = False,
                      result_kind: str = "ITEM", max_pages: int | None = None) -> list[dict[str, Any]]:
        # Room-local only: identical requests from different participants share
        # a read, while budgets, restrictions, merchant and pagination remain
        # part of the key. No results survive a new recommendation run.
        key = json.dumps([query, refinements, browse, result_kind, max_pages], sort_keys=True)
        task = self._searches.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_search(
                query, refinements, browse=browse, result_kind=result_kind, max_pages=max_pages,
            ))
            self._searches[key] = task
        try:
            return await task
        except BaseException:
            if self._searches.get(key) is task:
                self._searches.pop(key, None)
            raise

    async def _fetch_search(self, query: str, refinements: dict[str, Any], *, browse: bool = False,
                            result_kind: str = "ITEM", max_pages: int | None = None) -> list[dict[str, Any]]:
        tool = "browseOffers" if browse else "searchOffers"
        rows: list[dict[str, Any]] = []
        token = None
        seen_tokens: set[str] = set()
        pages = 0
        while True:
            for attempt in range(3):
                try:
                    async with self._sem:
                        data = await self.gw.call(
                            tool, merchantType="RESTAURANT", resultKind=result_kind,
                            pageSize=PAGE_SIZE, userLocation=self.location, refinements=refinements,
                            **({"query": query} if not browse else {}),
                            **({"nextPageToken": token} if token else {}),
                        )
                except MCPError as exc:
                    message = exc.message.lower()
                    transient = any(marker in message for marker in (
                        "server returned an error response", "upstream", "timeout", "timed out", "temporar",
                        "429", "500", "502", "503", "504",
                    ))
                    if not transient:
                        raise
                    if attempt == 2:
                        if rows:
                            logger.warning("%s pagination stopped after a server error; keeping %s rows", tool, len(rows))
                            return rows
                        raise MCPError(tool, "O iFood esta temporariamente indisponivel apos 3 tentativas. Tente novamente.") from exc
                    logger.warning("%s transient failure (attempt %s/3)", tool, attempt + 1)
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                data = decode(data)
                if isinstance(data, dict) and isinstance(data.get("results"), list):
                    break
                # Log shape only, not private result contents or coordinates.
                shape = sorted(data) if isinstance(data, dict) else type(data).__name__
                logger.warning("%s incomplete response (attempt %s/3), shape=%s", tool, attempt + 1, shape)
                if attempt == 2:
                    if rows:
                        logger.warning("%s pagination incomplete; keeping %s rows", tool, len(rows))
                        return rows
                    raise MCPError(tool, "A busca retornou uma resposta incompleta apos 3 tentativas. Tente novamente.")
                await asyncio.sleep(0.2 * (attempt + 1))
            rows.extend(data["results"])
            pages += 1
            token = data.get("nextPageToken")
            # Discover restaurants from the first page; exhaust their scoped
            # menus so a cheaper or more relevant dish isn't hidden on page 2.
            limit = max_pages if max_pages is not None else (None if refinements.get("merchantId") else DISCOVERY_PAGES)
            if not token or token in seen_tokens or (limit is not None and pages >= limit):
                return rows
            seen_tokens.add(token)

    async def describe(self, hit: Hit) -> Hit:
        key = (hit.merchant.merchant_id, hit.item_id)

        async def fetch():
            async with self._sem:
                return await asyncio.wait_for(
                    self.gw.call("getItem", itemId=hit.item_id,
                                 merchantId=hit.merchant.merchant_id,
                                 userLocation=self.location),
                    timeout=DETAIL_TIMEOUT_SECONDS,
                )

        if key not in self._details:
            self._details[key] = asyncio.create_task(fetch())
        try:
            detail = await self._details[key]
        except (MCPError, TimeoutError):
            return hit
        hit.description = detail.get("itemDescription") or hit.description
        hit.image = detail.get("itemImageUrl") or hit.image
        hit.detail = detail
        return hit

    async def hits(
        self, member: Member, intent: Intent, merchant_id: str | None = None,
        max_pages: int | None = None,
    ) -> list[Hit]:
        """All matching dishes; enforce the item's BRL ceiling locally."""
        budget = member.max_budget if member.max_budget is not None else intent.max_price
        required, excluded = wish_constraints(member.desire)
        intent = intent.model_copy(update={"required_terms": required, "excluded_terms": excluded})
        base: dict[str, Any] = {"onlyOpen": True}
        if intent.browse:
            base["sort"] = "RATING"
        if member.restrictions:
            base["itemRestrictions"] = list(member.restrictions)
        if merchant_id:
            base["merchantId"] = merchant_id

        async def run(with_budget: bool) -> list[Hit]:
            refinements = dict(base)
            if with_budget and budget is not None:
                refinements["maxItemPrice"] = budget
            merchant_ids: list[str] = []
            if intent.browse and merchant_id is None:
                try:
                    discovery_filters = {key: value for key, value in refinements.items()
                                         if key != "itemRestrictions"}
                    merchants = await self._search("", discovery_filters, browse=True,
                                                   result_kind="MERCHANT", max_pages=2)
                    merchant_ids = list(dict.fromkeys(
                        row["merchantId"] for row in merchants if row.get("merchantId")
                        and row.get("isOpen", True) is not False
                    ))[:MAX_CANDIDATE_MERCHANTS]
                except MCPError as exc:
                    if "incompleta" not in exc.message.lower():
                        raise
            batches = await asyncio.gather(
                *(self._search("", {**refinements, "merchantId": mid}, browse=True, max_pages=1)
                  for mid in merchant_ids), return_exceptions=True,
            ) if merchant_ids else await asyncio.gather(
                *(self._search(q, refinements, browse=intent.browse, max_pages=max_pages)
                  for q in (intent.queries[:1] if intent.browse else intent.queries)),
                return_exceptions=True,
            )
            seen: set[str] = set()
            found: list[Hit] = []
            failures = [b for b in batches if isinstance(b, Exception)]
            if failures and len(failures) == len(batches):
                raise failures[0]
            for rank, batch in enumerate(batches):
                if isinstance(batch, Exception):
                    continue
                parsed = [_hit_from(row, 0 if merchant_ids else rank, position, budget)
                          for position, row in enumerate(batch)]
                eligible = [hit for hit in parsed if hit and hit.merchant.open
                            and not hit.over_budget and hit.item_id not in seen
                            and (merchant_id is None or hit.merchant.merchant_id == merchant_id)]
                if required or excluded:
                    await asyncio.gather(*(self.describe(hit) for hit in eligible if not hit.description))
                for hit in eligible:
                    if hit.item_id in seen or not matches_wish(hit, intent):
                        continue
                    seen.add(hit.item_id)
                    found.append(hit)
            if not found and failures:
                raise failures[0]
            return found

        try:
            found = await run(with_budget=True)
        except MCPError as exc:
            if budget is None or "incompleta" not in exc.message.lower():
                raise
            # A broken filtered response must not prevent trying the same
            # retrieval without the remote price filter. Local caps still apply.
            return await run(with_budget=False)
        if not found and budget is not None:
            # Fallback broadens upstream retrieval, never the user's price cap.
            found = await run(with_budget=False)
        return found


def _group_by_merchant(hits: list[Hit]) -> dict[str, list[Hit]]:
    grouped: dict[str, list[Hit]] = {}
    for hit in hits:
        grouped.setdefault(hit.merchant.merchant_id, []).append(hit)
    return grouped


# --- the algorithm -----------------------------------------------------------


def _describe(member: Member) -> str:
    tags = ", ".join(RESTRICTION_LABELS[r].lower() for r in member.restrictions)
    return f"{member.name} ({tags})" if tags else member.name


async def find_merchant(
    searcher: Searcher,
    members: list[Member],
    intents: dict[str, Intent],
    log: Log,
) -> list[Candidate]:
    """Restaurants that can feed the whole table, best first, with everyone's options."""
    first_pass = await asyncio.gather(*(searcher.hits(m, intents[m.id]) for m in members), return_exceptions=True)
    first_errors = [hits for hits in first_pass if isinstance(hits, Exception)]
    for error in first_errors:
        if not recoverable_search_error(error):
            raise error
    for member, hits in zip(members, first_pass):
        if isinstance(hits, Exception):
            log(f"{member.name}: busca inicial indisponivel; vou conferir nas lojas encontradas para a mesa.", "warn")
    first_pass = [hits if isinstance(hits, list) else [] for hits in first_pass]
    by_member = {m.id: _group_by_merchant(h) for m, h in zip(members, first_pass)}
    for member, hits in zip(members, first_pass):
        log(f"{_describe(member)}: {len(hits)} prato(s) em {len(by_member[member.id])} loja(s)", "tool")

    # A missing global hit does not prove a store cannot serve a person. Verify
    # every person's constraints in each candidate's scoped menu below.
    candidate_ids = set().union(*(set(v) for v in by_member.values()))
    if not candidate_ids and not first_errors:
        # Qualities like "artesanal" can eliminate all exact discovery hits.
        # Find related stores without claiming that a changed wish was met.
        for member in members:
            request = alternative_request(member, intents[member.id])
            if request:
                related, related_intent = request
                hits = await searcher.hits(related, related_intent, max_pages=1)
                by_member[member.id] = _group_by_merchant(hits)
        candidate_ids = set().union(*(set(v) for v in by_member.values()))
    if not candidate_ids:
        if first_errors:
            raise first_errors[0]
        names = ", ".join(_describe(m) for m in members)
        raise NoMatch(f"Nao encontrei pratos compativeis para {names} nos restaurantes consultados.")

    info: dict[str, MerchantInfo] = {}
    for grouped in by_member.values():
        for mid, hits in grouped.items():
            info.setdefault(mid, hits[0].merchant)

    def reach(mid: str) -> int:
        return sum(1 for m in members if mid in by_member[m.id])

    def specific(mid: str) -> int:
        """People whose *most specific* query matched here (not just a broad one)."""
        return sum(
            1
            for m in members
            if any(h.query_rank == 0 for h in by_member[m.id].get(mid, []))
        )

    ranked = sorted(
        candidate_ids,
        key=lambda mid: (-reach(mid), -specific(mid), -info[mid].rating, info[mid].fee),
    )[:MAX_CANDIDATE_MERCHANTS]
    log(
        f"Descoberta: {len(candidate_ids)} loja(s) encontradas; "
        f"avaliando {len(ranked)}",
        "info",
    )
    incomplete: list[Exception] = []

    async def complete(mid: str) -> dict[str, list[Hit]]:
        """Everyone's options at ``mid``: first-pass hits, topped up by a scoped search.

        A first-pass hit only proves the store is reachable; the global search
        returns a handful of items per store, so anyone with a thin list gets a
        search inside the store (``refinements.merchantId``) before ranking.
        """
        thin = [m for m in members if sum(
            matches_exact(hit, intents[m.id]) for hit in by_member[m.id].get(mid, [])
        ) < MAX_RECOMMENDATIONS_PER_MEMBER]
        scoped = await asyncio.gather(
            # One page has enough candidates for the recommendation strip. It
            # also prevents a broad request from downloading an entire menu for
            # every restaurant before the first suggestion is visible.
            *(searcher.hits(m, intents[m.id], merchant_id=mid, max_pages=1) for m in thin),
            return_exceptions=True,
        )
        extra = {}
        for member, hits in zip(thin, scoped):
            if isinstance(hits, Exception):
                if not recoverable_search_error(hits):
                    raise hits
                incomplete.append(hits)
                log(f"{info[mid].name}: nao consegui ampliar o cardapio; usando os pratos ja encontrados.", "warn")
            else:
                extra[member.id] = hits

        options: dict[str, list[Hit]] = {}
        for m in members:
            merged: dict[str, Hit] = {}
            for hit in [*by_member[m.id].get(mid, []), *extra.get(m.id, [])]:
                known = merged.get(hit.item_id)
                if hit.merchant.merchant_id != mid:
                    continue
                if known is None or (hit.query_rank, hit.position) < (known.query_rank, known.position):
                    merged[hit.item_id] = hit
            options[m.id] = sorted(
                (h for h in merged.values() if matches_exact(h, intents[m.id])),
                key=lambda h, c=intents[m.id].cheap: rank_key(h, c)
            )[:MAX_RECOMMENDATIONS_PER_MEMBER]
        return options

    completed = await asyncio.gather(*(complete(mid) for mid in ranked))

    # Only propose substitutions after an exact shared restaurant was not found.
    # Infrastructure errors must not be described as a lack of food options.
    if not any(all(options.get(m.id) for m in members) for options in completed):
        for error in incomplete:
            if not recoverable_search_error(error):
                raise error
        if incomplete or first_errors:
            log("Parte das consultas falhou; vou aproveitar resultados confirmados e tentar similares sem afirmar ausencia de pratos.", "warn")
        log("Nenhuma das lojas consultadas atende todos os desejos exatos; buscando alternativas similares.", "info")

        async def alternatives(mid, options):
            async def fill(member):
                if options.get(member.id):
                    return
                intent = intents[member.id]
                request = alternative_request(member, intent)
                if not request:
                    return
                alternative_member, alternative_intent = request
                query = alternative_intent.queries[0]
                try:
                    hits = await searcher.hits(alternative_member, alternative_intent, merchant_id=mid, max_pages=1)
                except MCPError as exc:
                    if not recoverable_search_error(exc):
                        raise
                    incomplete.append(exc)
                    return
                safe = [replace(h, alternative_query=query) for h in hits if matches_exact(h, alternative_intent)
                        and not risk_warnings(h.description, member)]
                options[member.id] = sorted(safe, key=lambda h: rank_key(h, intent.cheap))[:MAX_RECOMMENDATIONS_PER_MEMBER]
            await asyncio.gather(*(fill(m) for m in members))
        await asyncio.gather(*(alternatives(mid, options) for mid, options in zip(ranked, completed)))

    scored: list[tuple[tuple, MerchantInfo, dict[str, list[Hit]]]] = []
    n = len(members)
    for mid, options in zip(ranked, completed):
        merchant = info[mid]
        covered = [m for m in members if options.get(m.id)]
        if not covered:
            continue
        picks = {m.id: options[m.id][0] for m in covered}
        subtotal = sum(p.price for p in picks.values())
        over = sum(
            1
            for m in covered
            if m.max_budget is not None and picks[m.id].price > m.max_budget
        )
        shortfall = max(0.0, merchant.minimum - subtotal)
        total = subtotal + merchant.fee
        log(
            f"{merchant.name}: cobre {len(covered)}/{n} · itens R$ {subtotal:.2f} + entrega R$ {merchant.fee:.2f}",
            "ok",
        )
        substitutions = sum(bool(options[m.id][0].alternative_query) for m in covered)
        scored.append(((-len(covered), substitutions, over, shortfall > 0, round(total, 2)), merchant, options))

    if not scored:
        if incomplete:
            raise incomplete[0]
        raise NoMatch(
            "Nao encontrei um restaurante que atenda aos desejos de todos ao mesmo tempo. "
            "Tentem mudar algum pedido ou buscar novamente."
        )
    scored.sort(key=lambda s: s[0])
    complete_scores = [entry for entry in scored if -entry[0][0] == len(members)]
    if complete_scores:
        scored = complete_scores
    return [Candidate(merchant, options, bool(incomplete or first_errors)) for _, merchant, options in scored]


# --- validation: getMerchant + getItem + required options -----------------------

_RISKY: dict[str, list[str]] = {
    "LAC_FREE": ["queijo", "leite", "cheddar", "catupiry", "mussarela", "manteiga", "requeijao",
                 "laticinio", "cream cheese"],
    "VEGETARIAN": ["carne", "bacon", "frango", "presunto", "linguica", "peixe", "atum",
                   "camarao", "calabresa", "costela", "salsicha", "hamburguer de"],
    "VEGAN": ["carne", "bacon", "frango", "presunto", "queijo", "leite", "ovo", "mel",
              "maionese", "catupiry", "cheddar"],
    "GLUTEN_FREE": ["trigo", "centeio", "cevada"],
}

_OPTION_PREFERENCE: dict[str, list[str]] = {
    "GLUTEN_FREE": ["sem gluten", "gluten free", "sem glutem"],
    "LAC_FREE": ["sem lactose", "zero lactose", "sem queijo"],
    "VEGAN": ["vegano", "vegan"],
    "VEGETARIAN": ["vegetariano"],
}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def risk_warnings(description: str, member: Member) -> list[str]:
    """Cheap second opinion on top of the MCP's own filter: flag, never exclude."""
    text = _plain(description)
    warnings = []
    for restriction in member.restrictions:
        for word in _RISKY.get(restriction, []):
            if re.search(rf"\b{word}", text) and not re.search(rf"\bsem\s+(\w+\s+)?{word}", text):
                warnings.append(
                    f"descricao cita '{word}': confirmar {RESTRICTION_LABELS[restriction].lower()}"
                )
                break
    return warnings


def pick_options(detail: dict[str, Any], member: Member) -> tuple[list[dict[str, Any]], float, list[str]]:
    """Fill every required option group, preferring the member's dietary variant."""
    prefer = [w for r in member.restrictions for w in _OPTION_PREFERENCE.get(r, [])]
    chosen: list[dict[str, Any]] = []
    extra = 0.0
    labels: list[str] = []

    for group in detail.get("optionGroups") or []:
        needed = int(group.get("minSelections") or 0)
        if needed <= 0:
            continue
        options = group.get("options") or []
        options = sorted(
            options,
            key=lambda o: (
                not any(p in _plain(o.get("name", "")) for p in prefer),
                to_float(o.get("additionalPrice")),
            ),
        )
        for option in options[:needed]:
            chosen.append({"optionId": option["optionId"], "quantity": 1})
            extra += to_float(option.get("additionalPrice"))
            labels.append(option.get("name", ""))
    return chosen, round(extra, 2), labels


def customer_note(member: Member) -> str:
    tags = " / ".join(RESTRICTION_LABELS[r] for r in member.restrictions)
    return f"[{member.name} - {tags}]" if tags else f"[{member.name}]"


async def validate_one(
    gateway: Gateway,
    location: dict[str, float],
    merchant_id: str,
    member: Member,
    item_id: str,
    *,
    fallback_name: str = "Item",
    fallback_price: float = 0.0,
    fallback_image: str | None = None,
    alternative_query: str | None = None,
) -> AssignedItem:
    """Read one dish with ``getItem`` and turn it into a cart line for ``member``.

    Called when somebody presses "+" on their recommendation: this is where the
    required option groups, the real price and the ingredient warnings are
    resolved. Raises :class:`NoMatch` when the item is gone or matches nothing.
    """
    try:
        detail = await gateway.call(
            "getItem", itemId=item_id, merchantId=merchant_id, userLocation=location
        )
    except MCPError as exc:
        raise NoMatch(f"Nao consegui abrir esse prato no cardapio ({exc.message}).") from exc

    chosen, extra, labels = pick_options(detail, member)
    warnings = risk_warnings(detail.get("itemDescription", ""), member)
    item = AssignedItem(
        item_id=item_id,
        name=detail.get("itemName", fallback_name),
        merchant_id=merchant_id,
        unit_price=to_float(
            detail.get("promotionalUnitPrice") or detail.get("unitPrice"), fallback_price
        ),
        options_price=extra,
        options=chosen,
        option_labels=labels,
        customer_note=customer_note(member),
        image_url=detail.get("itemImageUrl") or fallback_image,
        warnings=warnings,
    )
    intent = Intent(member_id=member.id)
    intent.required_terms, intent.excluded_terms = wish_constraints(member.desire)
    if alternative_query:
        expected = similar_query(heuristic_intent(member))
        if alternative_query != expected:
            raise NoMatch("Essa alternativa nao corresponde ao seu pedido atual.")
        intent.required_terms = []
        intent.queries = [alternative_query]
    current = Hit(item_id=item.item_id, name=item.name, price=item.price,
                  merchant=MerchantInfo(merchant_id, ""), image=item.image_url, query_rank=0,
                  description=detail.get("itemDescription") or "")
    if not matches_wish(current, intent) or (alternative_query and (
            not matches_exact(current, intent) or risk_warnings(current.description, member))):
        raise NoMatch("Esse prato nao atende mais ao seu pedido. Escolha outra sugestao.")
    budget = member.max_budget if member.max_budget is not None else heuristic_intent(member).max_price
    if budget is not None and item.price > budget:
        raise NoMatch(
            f"Com os complementos obrigatorios, esse prato custa R$ {item.price:.2f} "
            f"e passa do seu teto de R$ {budget:.2f}."
        )
    return item


async def confirm_merchant(
    gateway: Gateway, location: dict[str, float], merchant: MerchantInfo
) -> MerchantInfo:
    """Refresh fee/minimum from ``getMerchant``; the listing may be stale."""
    try:
        detail = await gateway.call(
            "getMerchant", merchantId=merchant.merchant_id, userLocation=location
        )
    except MCPError:
        return merchant

    if detail.get("isOpen") is False:
        raise NoMatch(f"{merchant.name} fechou enquanto eu conferia. Tente de novo.")
    if detail.get("minimumOrderAmount") is not None:
        merchant.minimum = to_float(detail["minimumOrderAmount"])
    for method in detail.get("deliveryMethods") or []:
        if method.get("deliveryMethodMode") == "DELIVERY":
            fee = method.get("promotionalFee")
            merchant.fee = to_float(fee if fee is not None else method.get("fee"), merchant.fee)
            break
    return merchant
