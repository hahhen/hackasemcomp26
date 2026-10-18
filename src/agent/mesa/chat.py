"""The conciliator agent: one shared group chat for the whole table.

Constraints that make the chat usable with a small local model:

* It gets **four table tools plus one read-only iFood search**, never the 23 raw
  MCP tools. The cart is built by the deterministic graphs instead.
* Every message carries its author (``author_id``), the model is told who is
  speaking, and ``update_my_order`` is bound to that person. Only the host can
  edit someone else's card or start the reconciliation.
* Audio arrives already transcribed (browser speech recognition); photos are
  passed to the model as ``image_url`` blocks when it supports vision.
"""

from __future__ import annotations

import logging
import os

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool

from agent.mesa import context, service
from agent.mesa.gateway import MCPError, get_gateway
from agent.mesa.models import ChatMessage, Member, Restriction, Session
from agent.mesa.store import log, say
from agent.providers import build_model
from agent.mesa.observability import operation

HISTORY = 16
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Voce e o Conciliador do iFood Mesa: ajuda um grupo de amigos a pedir comida junto, em portugues do Brasil, curto e simpatico.

Esta e uma conversa em grupo: a mesa toda ve as mensagens, e cada mensagem vem marcada com o nome de quem falou. Fale com a pessoa certa pelo nome.

Como funciona (nao precisa explicar tudo, so ajudar):
- Cada pessoa diz o que quer aqui no chat. O sistema registra o desejo na mesa dela.
- Assim que alguem registra um desejo, o sistema busca os restaurantes que atendem a mesa inteira, escolhe automaticamente um e mostra um seletor no topo do chat. O host pode trocar na lista.
- Cada pessoa recebe sua sugestao acima da caixa de mensagem, com foto, descricao, preco e um botao "+" para colocar no carrinho.
- Nao espere todos falarem: busque imediatamente com os desejos ja conhecidos. Novas mensagens reiniciam a busca considerando a mesa atualizada.
- Pedidos abertos como "algo bom ate 50 reais" ja sao suficientes: registre o desejo e o teto e deixe o sistema sugerir pratos variados. Nao exija que a pessoa escolha um tipo de comida antes de buscar.
- O carrinho e de todos. So o host fecha o carrinho; depois cada um paga a sua parte.

Ferramentas:
- update_my_order: registra o pedido de quem esta falando (desejo, restricoes, teto de gasto). Use para tudo que a pessoa falar sobre o proprio pedido; e o que faz a busca de restaurantes rodar.
- update_member / remove_member: so o host pode mexer no pedido de outra pessoa.
- search_menu: consulta o iFood de verdade (somente leitura) para responder "o que tem?", "quanto custa?" ou "tem sem lactose?". Nao coloca nada no carrinho.
- show_table: mostra o que cada um pediu.
- refresh_search: procura de novo restaurantes para a mesa.

Restricoes validas: VEGETARIAN, VEGAN, LAC_FREE (sem lactose), GLUTEN_FREE, SUGAR_FREE, ORGANIC.

Regras:
- Nunca invente pratos, lojas ou precos. Preco e loja so vem do search_menu ou do sistema.
- Quando alguem mandar uma foto, leia o que ela mostra e registre o pedido da pessoa (update_my_order).
- Nao prometa que o pedido sera feito: quem fecha o carrinho e o host.
- Depois das ferramentas, responda em 1 ou 2 frases.
- Sempre chame update_my_order quando alguem disser ou mudar o que quer comer; nao basta responder "anotado". Preserve desejos e restricoes anteriores quando a pessoa nao os alterar.
- Nao diga que o host precisa escolher um restaurante antes de ver sugestoes: a escolha e automatica.
- Nao afirme que uma busca terminou ou que existem cartoes sem isso constar no estado fornecido. O sistema publica os resultados reais separadamente.
- Escreva so a sua resposta. Nunca comece com o nome de quem falou nem com "Agente:" (o nome ja aparece na interface)."""


def _author_line(author: Member) -> str:
    role = "o host (dono da conta iFood)" if author.is_host else "um amigo da mesa"
    return f"\n\nQuem esta falando agora: {author.name} (id {author.id}, {role})."


def make_tools(session: Session, author: Member) -> list:
    """Tools bound to the person speaking: they can only edit their own card."""
    is_host = author.is_host

    @tool
    def update_my_order(
        desire: str = "",
        restrictions: list[Restriction] | None = None,
        max_budget: float | None = None,
    ) -> str:
        """Update what the person speaking wants (their own card only).

        desire: what they want to eat, in their own words. restrictions: dietary
        tags (VEGETARIAN, VEGAN, LAC_FREE, GLUTEN_FREE, SUGAR_FREE, ORGANIC).
        max_budget: their spending ceiling in BRL.
        """
        fields: dict = {}
        if desire:
            fields["desire"] = desire
        if restrictions is not None:
            fields["restrictions"] = restrictions
        if max_budget is not None:
            fields["max_budget"] = max_budget
        if not fields:
            return "nada para atualizar"
        try:
            member = service.update_member(session, author.id, fields)
        except service.TableError as exc:
            return f"erro: {exc}"
        return (
            f"ok: {member.name} | desejo='{member.desire}' | "
            f"restricoes={member.restrictions} | teto={member.max_budget}"
        )

    @tool
    def update_member(
        name: str,
        desire: str = "",
        restrictions: list[Restriction] | None = None,
        max_budget: float | None = None,
    ) -> str:
        """Add or update ANOTHER person on the table (host only)."""
        if not is_host:
            return f"erro: so o host pode editar outra pessoa; use update_my_order para {author.name}"
        try:
            member = service.upsert_member(
                session, name, desire or None, restrictions, max_budget
            )
        except service.TableError as exc:
            return f"erro: {exc}"
        return f"ok: {member.name} | desejo='{member.desire}' | restricoes={member.restrictions}"

    @tool
    def remove_member(name: str) -> str:
        """Remove a person from the table (host only)."""
        if not is_host:
            return "erro: so o host pode tirar alguem da mesa"
        try:
            service.remove_member(session, name)
        except service.TableError as exc:
            return f"erro: {exc}"
        return f"ok: {name} saiu da mesa"

    @tool
    def show_table() -> str:
        """Show who is on the table and what each person asked for."""
        return "\n".join(
            f"- {m.name}{' (host)' if m.is_host else ''}: desejo='{m.desire}', "
            f"restricoes={m.restrictions}, teto={m.max_budget}"
            for m in session.active_members
        )

    @tool
    async def search_menu(query: str) -> str:
        """Search the real iFood menu (read-only) for this group's address.

        Use for questions about what exists, prices, or dietary options. Returns
        a short list; it does not put anything in the cart.
        """
        try:
            gateway = get_gateway()
            await context.resolve_address(gateway, session)
            data = await gateway.call(
                "searchOffers",
                query=query,
                merchantType="RESTAURANT",
                resultKind="ITEM",
                pageSize=5,
                userLocation=context.location_of(session),
                refinements={"onlyOpen": True},
            )
        except (MCPError, context.NoAddress) as exc:
            return f"erro ao consultar o iFood: {exc}"
        except Exception as exc:  # noqa: BLE001 - never break the chat over a search
            return f"erro inesperado na busca: {type(exc).__name__}"
        rows = data.get("results", []) if isinstance(data, dict) else []
        if not rows:
            return f"nada encontrado para '{query}' perto do endereco da mesa"
        lines = ["achei no iFood (somente leitura):"]
        for row in rows[:5]:
            merchant = row.get("merchant") or {}
            price = row.get("promotionalUnitPrice") or row.get("unitPrice")
            lines.append(f"- {row.get('itemName')} - R$ {price} - {merchant.get('merchantName')}")
        return "\n".join(lines)

    @tool
    def refresh_search() -> str:
        """Search again for restaurants that can feed the whole table."""
        try:
            service.start_recommend(session)
        except service.TableError as exc:
            return f"erro: {exc}"
        return "ok: procurando restaurantes para a mesa"

    return [update_my_order, update_member, remove_member, show_table, search_menu, refresh_search]


def _to_message(message: ChatMessage, with_images: bool) -> BaseMessage:
    if message.kind == "agent":
        return AIMessage(message.text)
    if message.kind == "system":
        return HumanMessage(f"[sistema] {message.text}")

    text = f"{message.author_name}: {message.text or '(mandou uma foto)'}"
    if with_images and message.images:
        blocks: list[dict] = [{"type": "text", "text": text}]
        blocks += [{"type": "image_url", "image_url": {"url": url}} for url in message.images]
        return HumanMessage(content=blocks)
    if message.images:
        text += " (mandou uma foto)"
    return HumanMessage(text)


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return "".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()


def _record_usage(session: Session, messages: list[BaseMessage]) -> None:
    """Provider-reported counts, summed across this turn's model/tool loop."""
    inputs = outputs = calls = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = message.usage_metadata
        if usage:
            inputs += usage.get("input_tokens", 0)
            outputs += usage.get("output_tokens", 0)
            calls += 1
    if calls:
        log(session, f"LLM: {calls} chamada(s), {inputs} tokens de entrada e {outputs} de saida.", "info")




def chat_llm_enabled() -> bool:
    """``MESA_CHAT_LLM=false`` runs the chat with no model at all (fallback path).

    Useful offline (a demo with no provider configured) and it is what the test
    suite relies on to stay hermetic.
    """
    return os.getenv("MESA_CHAT_LLM", "true").strip().lower() not in {"0", "false", "no", "off"}


@operation("chat")
async def handle_chat(session: Session, author: Member, text: str, images: list[str] | None = None) -> None:
    """One turn: let the agent act on behalf of ``author`` and post its reply.

    The person's message is already in the chat (see ``service.chat_turn``), so
    it shows up instantly even while another turn is still running.
    """
    if not chat_llm_enabled():
        _fallback(session, author, text, images, RuntimeError("MESA_CHAT_LLM=false"))
        return

    # Pending turns may be replayed after cancellation. Keep the current author
    # aligned with their own message, even when newer messages are already visible.
    end = next((i + 1 for i in range(len(session.chat) - 1, -1, -1)
                if session.chat[i].kind == "user" and session.chat[i].author_id == author.id
                and session.chat[i].text == text), len(session.chat))
    messages = session.chat[:end]
    last = messages[-1]
    history = [_to_message(m, with_images=(m is last)) for m in messages[-HISTORY:]]
    table_state = "\n\nEstado atual da mesa:\n" + "\n".join(
        f"{m.id} ({m.name}): desejo={m.desire!r}, restricoes={m.restrictions}, teto={m.max_budget}"
        for m in session.active_members
    )
    selected = session.selected_option()
    table_state += f"\nRestaurante selecionado: {selected.name if selected else 'nenhum'}."

    try:
        agent = create_agent(
            build_model(),
            make_tools(session, author),
            system_prompt=SYSTEM_PROMPT + _author_line(author) + table_state,
        )
        result = await agent.ainvoke({"messages": history}, {"recursion_limit": 12})
        _record_usage(session, result["messages"])
        reply = _text(result["messages"][-1])
        if reply:
            say(session, reply)
    except service.TableError as exc:
        say(session, str(exc))
    except Exception as exc:
        logger.exception("Mesa chat failed during model or tool execution")
        _fallback(session, author, text, images, exc)


def _fallback(session: Session, author: Member, text: str, images: list[str] | None, exc: Exception) -> None:
    """No LLM: keep the demo moving instead of leaving the chat hanging.

    The message itself still grants the desire, which is enough to trigger a new
    restaurant search - the same path a successful tool call takes.
    """
    if images:
        say(session, "Esse modelo nao le imagens; escreva em texto que eu anoto.")
        return
    if len(text.strip()) >= 12 and not author.desire:
        try:
            service.update_member(session, author.id, {"desire": text.strip()})
        except service.TableError as table_error:
            say(session, str(table_error))
            return
        say(session, "Anotei aqui (o modelo estava fora do ar, usei suas palavras).")
        return
    say(session, f"Estou sem LLM no momento ({type(exc).__name__}). Tente de novo em instantes.")
