"""Turn what each friend typed into iFood search terms.

The LLM does the language work (one call for the whole table); a deterministic
heuristic takes over if the model is unreachable, slow or returns garbage, so a
dead LLM never blocks the demo. Dietary restrictions are *never* searched as
text: they go to ``refinements.itemRestrictions`` instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata

from pydantic import BaseModel, Field

from agent.mesa.models import Member

BROAD_FALLBACKS = ["lanche", "prato"]

SYSTEM_PROMPT = """You convert food wishes into iFood search terms (Brazilian Portuguese).
Return ONLY a JSON object, no prose:
{"members": [{"id": "<member id>", "queries": ["...", "..."], "max_price": null}]}

Rules:
- "queries": 1 to 3 short search terms (1-3 words each), most specific first, the last one broad
  (for example ["smash duplo", "hamburguer"]).
- Never put dietary words in queries (vegetariano, vegano, sem lactose, sem gluten): they are
  applied as filters elsewhere.
- Ignore what the person does NOT want (e.g. "zero queijo" must not produce "queijo").
- "max_price": a number in BRL only if the person states a price ceiling in the text, else null.
- Output one entry per member, using the exact ids you were given."""

_STOPWORDS = {
    "um", "uma", "uns", "umas", "o", "a", "os", "as", "de", "do", "da", "dos", "das", "e", "ou",
    "com", "para", "pra", "que", "quero", "qualquer", "algo", "tipo", "bom", "boa", "gostoso",
    "gostosa", "barato", "barata", "mas", "por", "favor", "no", "na", "em", "meu", "minha",
    "vegetariano", "vegetariana", "vegano", "vegana", "lactose", "gluten", "lanche", "prato",
}
_NEGATORS = {"sem", "zero", "nao", "nunca"}


class Intent(BaseModel):
    member_id: str
    queries: list[str] = Field(default_factory=list)
    max_price: float | None = None
    #: They asked for something cheap: price breaks ties instead of relevance.
    cheap: bool = False
    required_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    browse: bool = False


def wish_constraints(desire: str) -> tuple[list[str], list[str]]:
    """Explicit qualities and ingredient exclusions must survive broad search."""
    text = _plain(desire)
    required = [word for word in ("artesanal", "caseiro", "integral")
                if re.search(rf"\b{word}\b", text)]
    excluded = re.findall(
        r"\b(?:sem|zero|nao quero|nao pode ter)\s+(?:nada de\s+)?([a-z]+)", text
    )
    # Dietary tags have their own server filter; these aren't ingredients.
    excluded = [word for word in excluded if word not in {"lactose", "gluten", "acucar"}]
    return required, _dedupe(excluded)


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


_CHEAP = re.compile(r"\b(barat\w*|economic\w*|em conta)\b")


def wants_cheap(member: Member) -> bool:
    return bool(_CHEAP.search(_plain(member.desire)))


def heuristic_intent(member: Member) -> Intent:
    """Keyword extraction: drop filler words and anything after a negation."""
    text = _plain(member.desire)
    text = re.sub(r"\bx[\s-]*(?=salada\b|bacon\b|tudo\b|egg\b|frango\b|burguer\b|burger\b|calabresa\b)", "hamburguer ", text)
    price_pattern = r"\b(?:ate|no maximo|teto(?: de)?|limite(?: de)?)\s*(?:r\$\s*)?(\d+(?:[.,]\d+)?)\s*(?:reais)?"
    ceiling = re.search(price_pattern, text)
    text = re.sub(price_pattern, "", text)
    words = re.findall(r"[a-zA-Z0-9]+", text)
    kept: list[str] = []
    skip = 0
    for word in words:
        if word in _NEGATORS:
            skip = 2
            continue
        if skip:
            skip -= 1
            continue
        if word not in _STOPWORDS and len(word) > 2:
            kept.append(word)

    queries: list[str] = []
    required, excluded = wish_constraints(member.desire)
    if kept:
        queries.append(" ".join(kept[:3]))
        if len(kept) > 1 and not required:
            queries.append(kept[0])
    # A sushi request must not silently become any sandwich/dish just to make
    # the restaurant intersection succeed.
    if not queries:
        queries.extend(BROAD_FALLBACKS)
    return Intent(
        member_id=member.id,
        queries=_dedupe(queries),
        max_price=(member.max_budget if member.max_budget is not None
                   else float(ceiling[1].replace(",", ".")) if ceiling else None),
        cheap=wants_cheap(member),
        required_terms=required,
        excluded_terms=excluded,
        browse=not kept,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [i for i in items if not (i in seen or seen.add(i))]


def _parse_llm_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


async def llm_intents(members: list[Member]) -> dict[str, Intent]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.providers import build_model

    payload = [
        {"id": m.id, "name": m.name, "wish": m.desire or "qualquer lanche",
         "restrictions": m.restrictions}
        for m in members
    ]
    timeout = float(os.getenv("MESA_LLM_TIMEOUT", "90"))
    reply = await asyncio.wait_for(
        build_model().ainvoke(
            [SystemMessage(SYSTEM_PROMPT), HumanMessage(json.dumps(payload, ensure_ascii=False))]
        ),
        timeout=timeout,
    )
    data = _parse_llm_json(_message_text(reply.content))

    known = {m.id for m in members}
    out: dict[str, Intent] = {}
    for row in data.get("members", []):
        if row.get("id") not in known:
            continue
        queries = [q.strip() for q in row.get("queries", []) if isinstance(q, str) and q.strip()]
        price = row.get("max_price")
        out[row["id"]] = Intent(
            member_id=row["id"],
            queries=queries[:3],
            max_price=float(price) if isinstance(price, (int, float)) else None,
        )
    return out


def use_llm_default() -> bool:
    return os.getenv("MESA_USE_LLM", "true").strip().lower() not in {"0", "false", "no", "off"}


async def extract_intents(
    members: list[Member], use_llm: bool | None = None
) -> tuple[dict[str, Intent], str | None]:
    """Intents for every member plus a note if the heuristic had to step in.

    ``use_llm=False`` keeps the periodic re-search cheap (see
    ``recommend_graph.recommend_uses_llm``): it is the default there, because the
    chat agent already writes each desire as clean text.
    """
    heuristics = {m.id: heuristic_intent(m) for m in members}
    if not (use_llm_default() if use_llm is None else use_llm):
        return heuristics, None

    note = None
    try:
        from_llm = await llm_intents(members)
    except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to keywords
        from_llm = {}
        note = f"LLM indisponivel ({type(exc).__name__}); usei extracao por palavras-chave."

    result: dict[str, Intent] = {}
    for member in members:
        intent = from_llm.get(member.id)
        if intent and intent.queries:
            intent.browse = heuristics[member.id].browse
            intent.queries = _dedupe(intent.queries)
            intent.required_terms, intent.excluded_terms = wish_constraints(member.desire)
            if intent.max_price is None:
                intent.max_price = heuristics[member.id].max_price
            intent.cheap = wants_cheap(member)
            result[member.id] = intent
        else:
            result[member.id] = heuristics[member.id]
    return result, note
