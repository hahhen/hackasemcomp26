"""Generate a catalogue of every tool the iFood MCP server exposes.

Reads the live ``tools/list`` (using the cached OAuth token), then writes:

* ``IFOOD-MCP-TOOLS.md``  - human-readable reference: purpose, parameters, cost
* ``ifood-mcp-tools.json`` - the raw schemas, for further processing

Run from the project root:

    .\\.venv\\Scripts\\python.exe scripts\\dump_mcp_tools.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import tiktoken
from fastmcp.client import Client

from agent.mcp import mcp_url
from agent.oauth import get_access_token

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = ROOT / "IFOOD-MCP-TOOLS.md"
RAW_JSON = ROOT / "ifood-mcp-tools.json"

# Tool name -> (group, order within group). Groups keep the doc readable and
# follow the order of a real ordering flow.
GROUPS: dict[str, list[str]] = {
    "Conta e endereços": ["getUser", "listAddresses", "createAddress"],
    "Descoberta de lojas e itens": [
        "searchOffers",
        "browseOffers",
        "getRecommendations",
        "getMerchant",
        "getItem",
    ],
    "Carrinho": [
        "createCart",
        "getCart",
        "addItemToCart",
        "updateCartItem",
        "removeItemFromCart",
        "getCartDeliveryMethods",
        "setCartDelivery",
    ],
    "Pagamento e vouchers": [
        "listPaymentMethods",
        "setCartPayment",
        "listVouchers",
        "setVoucher",
        "removeVoucher",
    ],
    "Pedidos": ["submitOrder", "getOrder", "trackOrder"],
}

# Fields whose "default" is the server's placeholder rather than a real value.
PLACEHOLDER_DEFAULT = "##default"

ENCODER = tiktoken.get_encoding("o200k_base")


def one_line(text: str | None) -> str:
    """Collapse whitespace and escape pipes so text survives a table cell."""
    return " ".join((text or "").split()).replace("|", "\\|")


def type_of(node: dict) -> str:
    """Render a JSON-schema node's type, including unions and anyOf."""
    declared = node.get("type")
    if isinstance(declared, list):
        return " \\| ".join(declared)
    if declared:
        return declared
    if "anyOf" in node:
        return " \\| ".join(type_of(part) for part in node["anyOf"])
    return "?"


def constraints_of(node: dict) -> str:
    """Format, enum, and placeholder-default notes for one field."""
    notes = []
    if node.get("format"):
        notes.append(f"formato `{node['format']}`")
    if node.get("enum"):
        notes.append("valores: " + ", ".join(f"`{v}`" for v in node["enum"]))
    if node.get("default") == PLACEHOLDER_DEFAULT:
        notes.append("sem default real (o servidor envia `##default`)")
    return "; ".join(notes)


def collect_rows(
    properties: dict,
    required: set[str],
    prefix: str = "",
    depth: int = 0,
) -> list[tuple[str, str, str, str]]:
    """Flatten a (possibly nested) parameter schema into table rows."""
    rows: list[tuple[str, str, str, str]] = []
    for name, node in (properties or {}).items():
        path = f"{prefix}{name}"
        label = ("&nbsp;&nbsp;&nbsp;&nbsp;" * depth) + f"`{path}`"
        description = one_line(node.get("description"))
        extra = constraints_of(node)
        if extra:
            description = f"{description} _({extra})_" if description else f"_{extra}_"
        rows.append((label, type_of(node), "sim" if name in required else "", description))

        if node.get("type") == "object" and node.get("properties"):
            rows += collect_rows(
                node["properties"], set(node.get("required") or []), f"{path}.", depth + 1
            )
        items = node.get("items")
        if isinstance(items, dict) and items.get("properties"):
            rows += collect_rows(
                items["properties"], set(items.get("required") or []), f"{path}[].", depth + 1
            )
    return rows


def cost_of(tool) -> int:
    """Schema cost in tokens, exactly as an OpenAI tool spec sends it.

    Built from the raw schema rather than a LangChain conversion, so it works on
    the MCP client's own ``Tool`` objects.
    """
    spec = {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }
    return len(ENCODER.encode(json.dumps(spec, ensure_ascii=False)))


def annotations_of(tool) -> dict:
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {}
    return {
        "read_only": getattr(ann, "read_only_hint", None),
        "destructive": getattr(ann, "destructive_hint", None),
        "idempotent": getattr(ann, "idempotent_hint", None),
        "open_world": getattr(ann, "open_world_hint", None),
    }


def render_tool(tool) -> str:
    schema = tool.input_schema or {}
    ann = annotations_of(tool)
    write = "escrita" if ann.get("read_only") is False else "leitura"
    cost = cost_of(tool)
    rows = collect_rows(schema.get("properties") or {}, set(schema.get("required") or []))

    out = [
        f"### `{tool.name}`",
        "",
        (
            f"- **{write}** · {cost:,} tokens de schema · "
            f"`readOnly={ann.get('read_only')}` `destructive={ann.get('destructive')}` "
            f"`idempotent={ann.get('idempotent')}` `openWorld={ann.get('open_world')}`"
        ),
        "",
        one_line(tool.description),
        "",
    ]

    if rows:
        out += [
            "| Parâmetro | Tipo | Obrigatório | Descrição |",
            "|---|---|---|---|",
        ]
        out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
        out.append("")
    else:
        out += ["_Sem parâmetros._", ""]
    return "\n".join(out)


async def main() -> None:
    token = await get_access_token()
    async with Client(mcp_url(), auth=token) as client:
        tools = await client.list_tools()

    by_name = {t.name: t for t in tools}
    known = {name for names in GROUPS.values() for name in names}
    ungrouped = [t.name for t in tools if t.name not in known]

    RAW_JSON.write_text(
        json.dumps(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                    "annotations": annotations_of(t),
                    "schema_tokens": cost_of(t),
                }
                for t in tools
            ],
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    total = sum(cost_of(t) for t in tools)
    parts = [
        "# Ferramentas do MCP do iFood (`sudowin-semcomp`)",
        "",
        f"Catálogo gerado a partir de uma chamada real a `tools/list` em `{mcp_url()}`.",
        (
            f"Este inventário contém **{len(tools)} ferramentas**, com **{total:,} tokens** "
            "de schema no formato de função OpenAI. Esse seria o custo por chamada se "
            "todas fossem vinculadas ao modelo; **o Mesa mantém o catálogo no gateway "
            "Python e envia apenas suas seis ferramentas de aplicação ao LLM**. "
            "O total exato varia com a serialização."
        ),
        "",
        "- `ifood-mcp-tools.json` contém os schemas brutos, se precisar reprocessar.",
        "- Nenhuma ferramenta declara `outputSchema`: todas devolvem texto livre.",
        (
            f"- Campos marcados com `\"default\": \"{PLACEHOLDER_DEFAULT}\"` no schema não "
            "têm default real — é apenas o marcador que o servidor envia. **Não envie "
            "essa string.**"
        ),
        "- Regenerar: `.\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\dump_mcp_tools.py`",
        "",
        "## Índice",
        "",
        "| Ferramenta | Grupo | Tipo | Params | Tokens |",
        "|---|---|---|---|---|",
    ]

    for group, names in GROUPS.items():
        for name in names:
            tool = by_name.get(name)
            if tool is None:
                continue
            ann = annotations_of(tool)
            kind = "escrita" if ann.get("read_only") is False else "leitura"
            n_params = len((tool.input_schema or {}).get("properties") or {})
            parts.append(f"| `{name}` | {group} | {kind} | {n_params} | {cost_of(tool):,} |")
    for name in ungrouped:
        parts.append(f"| `{name}` | (não classificado) | | | {cost_of(by_name[name]):,} |")

    for group, names in GROUPS.items():
        parts += ["", f"## {group}", ""]
        for name in names:
            if name in by_name:
                parts.append(render_tool(by_name[name]))

    MARKDOWN.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {MARKDOWN.name} ({len(tools)} tools, {total:,} schema tokens)")
    print(f"wrote {RAW_JSON.name}")
    if ungrouped:
        print("WARNING ungrouped tools:", ungrouped)


if __name__ == "__main__":
    asyncio.run(main())
