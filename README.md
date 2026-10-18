# hackasemcomp26-agent

Graph and MCP traces: see [OBSERVABILITY.md](OBSERVABILITY.md) for JSONL logs,
per-stage/order counters, latency reports and MCP feedback evidence.

If `listAddresses` is unavailable, the host can confirm a delivery address saved
in an earlier table using **Usar este endereço** in the error banner. This reuses
the actual saved address ID and coordinates and restarts discovery without that
lookup. Confirm that the destination and iFood account match; checkout can still
reject a removed address or fail if another iFood service is unavailable.
Previous tables must be present in memory or loaded with `MESA_PERSIST`.

When no consulted restaurant covers every exact wish, discovery tries related
food families for the missing members, prioritizing the fewest substitutions.
Cards and restaurant choices label similar suggestions; a chat message names
the affected people, original wishes and proposed dishes. Wishes stay unchanged
until the person edits them, and adding an alternative requires the usual `+`.
Price caps, dietary tags and ingredient exclusions remain enforced. Related
families currently cover burgers, pizza, Japanese dishes, pasta, esfihas and açaí;
unknown categories are not replaced with arbitrary food. MCP errors remain
errors rather than evidence that the desired food does not exist.

A LangChain + LangGraph tool-calling agent with an `ENVIRONMENT` switch between
a local **Ollama** model (free) and a hosted **OpenCode Zen** model (paid), with
tools loaded from the iFood `sudowin-semcomp` **MCP** server.

On top of it sits **iFood Mesa**: group ordering with one restaurant, a different
dish per friend, an exact split, and a checkout locked until everyone pays.
FastAPI backend in `src/agent/mesa/`, Vite + React frontend in `frontend/`. See
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## iFood Mesa — run it

```powershell
# backend (mock iFood: no account, no real orders)
pip install -e ".[dev]"
uvicorn agent.mesa.api:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm.cmd install
npm.cmd run dev          # http://localhost:5173/?mesa=SEMCOMP-2026
```

The home page offers **Criar mesa** and **Entrar na mesa**. Creating generates a
unique room code and makes the creator the host. `Convidar` copies a `?mesa=`
invitation; a new browser opens a name form and joins as a member. People can also
paste the invitation or type its code. Unknown codes do not create rooms.
Each browser remembers its member ID per room, so refresh preserves its role.

The chat accepts text and microphone recordings. Press the microphone to record,
then stop to transcribe and send. Audio/image file attachment controls are removed.
The backend sends the audio to Gemini for transcription only, then the client
sends the returned text through the same author-bound chat flow. Configure
`GEMINI_API_KEY` in `.env`; `GEMINI_AUDIO_MODEL` defaults to `gemini-2.5-flash-lite`
and is independent of the chat model. Recordings stop at two minutes and files
are limited to 10 MB. Microphone recording requires a secure browser context
(HTTPS or localhost). Failed transcriptions
can be retried; failed chat sends leave the transcript in the text field.

There are two UI modes, independent of the mock/live MCP setting:

* **True mode** — a full-screen mobile conversation without a simulated phone
  border or repeated personal name, restaurant selector, personal suggestions,
  and the shared cart. The header retains the brand, viewer name/role, and a
  left-side menu button; the drawer holds invitations, room code, view switching,
  and navigation. Guests don't inherit the creator's identity
  from the invitation.
* **God mode** — the host's phone on the left and the other members' phones on
  the right. Send messages as any participant, inspect their individual cards,
  add simulated members, and follow the existing event logs. The old manual
  preference cards and separate demo view are no longer in the active UI.

This remains a prototype with client-supplied member IDs; God mode is an
inspection interface, not a production authentication or authorization system.

Then just talk. Say what each person wants ("a Bia é vegetariana, o João quer um
smash barato"). The agent remembers each person's wish, and the
restaurants that can serve the **whole table** appear by themselves in a dropdown
on top of the chat. From there:

1. the agent selects a restaurant automatically; the host can change it using
   the dropdown at the top of the chat;
2. each person sees **all matching suggestion cards above the message box**,
   with horizontal scrolling, title, picture (when supplied by iFood), description,
   price and a **"+"** that selects that specific dish;
   added dishes appear in the shared cart, labeled with their owner's name;
3. the host taps **Fechar carrinho e dividir** — this is what actually creates
   the iFood cart, applies the best voucher and splits the bill;
4. everyone pays their share from the shared cart (**the host can cover or drop
   someone who doesn't pay**);
5. the last payment sends the order. The verification code shows up for the group.

The chat is a chat: no order buttons live in it.

New messages cancel the current chat/search work and restart processing with the
pending messages and everyone's saved wishes. Searches only consider people who
have stated a wish or restriction, and publish suggestions as JSON keyed by
`memberId`. If no common restaurant is found, the chat explains that and removes
outdated suggestions. MCP errors are reported separately from empty results.

Adding a friend without a wish does not restart searches. Incomplete search
responses get up to three attempts; a failed scoped menu lookup can reuse valid
first-pass hits. A failed refresh preserves existing options only when the saved
wishes and budgets are unchanged. The UI distinguishes restaurant search from
the later dish-detail checks before publishing the selector.

The Mesa chat binds six local tools to the LLM. Python code calls the iFood MCP
behind those tools, so this flow does not send all 23 raw MCP schemas to the
model. When the provider reports usage, the event log records input/output token
counts across the chat turn's model calls; MCP HTTP calls are not LLM inferences.

Open-ended wishes such as "algo bom ate 50 reais" use `browseOffers` (no invented
search query), while named foods use `searchOffers`. Both paths apply the same
budgets and restrictions. Temporary server errors on these read-only discovery
calls get up to three attempts; authentication/validation errors are not blindly
retried.

Generic discovery requests merchant results first, then samples products inside
up to 12 distinct stores, so one chain's product volume cannot occupy the whole
first page. Specific-food discovery reads up to eight global result pages before
checking candidate menus. Missing global results for one person are retried in
the stores found for the others, with all preferences still enforced. A failed
later page preserves successfully retrieved earlier pages. An incomplete price-
filtered response can retry without that remote filter while keeping local caps.
These are bounded searches, not an exhaustive list of every open store.
`python scripts/probe_search_filters.py --coverage` verifies live store diversity
without creating carts or orders.

Price ceilings apply to the dish (including required extras), with delivery shown
separately. The client validates prices locally as well as sending the MCP filter;
an unfiltered retry never relaxes the user's ceiling. Explicit qualities such as
`artesanal` and exclusions such as `sem bacon` are checked against item data. The
server's broad search results alone are not treated as proof of a match. Scoped
restaurant searches follow pagination to collect the available matching options.
`python scripts/probe_search_filters.py` compares live price filters read-only,
using an existing token without launching a new sign-in.

Against the real iFood, set `MESA_MODE=live` in `.env` (after `python -m
agent.oauth`). Real orders stay **off** until `MESA_SUBMIT_ENABLED=true`.
`python scripts/probe_solver.py` runs the solver against the live MCP read-only.

```powershell
pytest                    # split math, solver, full flow on the mock catalog
npm.cmd run build         # in frontend/: typecheck + production build (served by the API)
```

After `npm run build`, `uvicorn` also serves the UI at `http://localhost:8000`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Then pick an environment — one line in `.env`:

```dotenv
ENVIRONMENT=remote   # OpenCode Zen (paid, fast, reliable)
# ENVIRONMENT=local  # Ollama (free, all 23 tools, ~30-90s per turn)
```

- **`remote`** (default) — the cheapest paid Zen model (`glm-5.3-flash`). Needs
  `OPENCODE_ZEN_API_KEY` (from [opencode.ai/zen](https://opencode.ai/zen)); a full
  ordering flow costs ~2–5 cents.
- **`local`** — Ollama, no key, no quota, and the only free way to give the agent
  all 23 iFood tools at once. See
  [Run it fully local](#run-it-fully-local-ollama).

The MCP settings in `.env` are pre-filled from `setup-sudowin-mcp.mjs`. Sign in
to iFood **once** (opens a browser; port 8080 must be free):

```powershell
python -m agent.oauth
```

That caches a token to `.mcp-token.json`, refreshes it automatically, and is
reused on every later run.

## Run it fully local (Ollama)

No API key and no rate limits — and because there is no tokens-per-minute
ceiling, this is the only *free* way to give the agent **all 23 iFood tools** at
once. The 10,508 tokens of schema are simply not a problem locally.

```powershell
winget install --id Ollama.Ollama
ollama pull qwen3:8b
ollama create ifood-agent -f Modelfile.ollama
```

`.env` then points at it:

```dotenv
ENVIRONMENT=local
```

> **`Modelfile.ollama` is not optional.** Ollama defaults to a **4096-token**
> context, and the tool schemas alone are **10,508**. At the default they are
> silently truncated and the model answers as if the tools do not exist — the
> same failure mode as the Gemini case below. The Modelfile sets
> `num_ctx=16384`.

`num_ctx` cannot be passed through the OpenAI-compatible endpoint, which is why
it is baked into a derived model rather than set from `.env`.

On an 8 GB GPU the KV cache is what decides whether the model fits in VRAM. If it
spills onto the CPU it still works, just slowly; quantise the cache
(`setx OLLAMA_KV_CACHE_TYPE q8_0`, then restart Ollama) or lower `num_ctx` to
12288 to bring it back.

Verified on a Ryzen 5 5500 + Radeon RX 6600 (8 GB): with all 23 iFood tools bound,
`qwen3:8b` runs **100% on the GPU** at a 16,384-token context, using **6.3 GB** of
VRAM, and a real `listAddresses` call returned the correct address in **69 s**.

Expect roughly 30–90 s per turn. qwen3 reasons before answering — Ollama returns
that in a separate `reasoning` field, so `content` stays clean — and every turn
re-processes ~10.5k tokens of tool schema. `think=false` is ignored by the
OpenAI-compatible endpoint, so thinking cannot currently be disabled from the
client.

## Run

```powershell
# verify provider + tool calling end to end
python -m agent.main --check

# list every tool the agent will have (local + MCP)
python -m agent.main --tools

# which model ids your key can actually call
python -m agent.probe

# interactive
python -m agent.main

# one-shot
python -m agent.main "what is 12 * 7?"
```

`agent.probe` tests each candidate id for your provider and prints OK / FAIL,
which settles which Groq models answer and exposes Zen ids on the free lane
(OpenCode client only) vs the paid lane. Pass your own ids to test them:
`python -m agent.probe some-model-id`.

## The MCP connection

The iFood server is a remote MCP endpoint that requires **OAuth 2.1**. Two
things are worth knowing:

- **We run the OAuth flow ourselves** (`src/agent/oauth.py`). FastMCP's built-in
  OAuth hardcodes its callback path to `/callback`, but iFood matches redirect
  URIs byte-for-byte and this client is registered for
  `http://localhost:8080/agent`. So the token is obtained separately and handed
  to the MCP client as a bearer token.
- **Tools stay callable after loading.** `MCPAdapter.list_tools()` returns
  LangChain tools that each hold the FastMCP client, so the agent can call them
  for the whole run.

Set `MCP_ENABLED=false` to run with local tools only, or `MCP_BEARER_TOKEN` to
skip the browser sign-in entirely.

> If your OAuth token expires mid-session, re-run `python -m agent.oauth`.

### Tool schemas cost tokens on every call

Tool schemas are re-sent with **every** model call. The full iFood set is 23 tools
and **10,508 tokens**, which on its own exceeds Groq's free **8,000 tokens/minute**
ceiling — producing `HTTP 413 Request too large` before the conversation even
starts. (A local model has no such ceiling at all — see
[Run it fully local](#run-it-fully-local-ollama).) Measured options:

| Tool set | Tools | Schema tokens | Fits Groq free |
|---|---|---|---|
| all | 23 | 10,508 | no |
| minus the 4 heaviest | 19 | 7,258 | barely |
| **order flow — verified working** | **12** | **6,207** | **yes** |
| minimal | 9 | 4,999 | yes |

Every tool, its cost, and all of its parameters are catalogued in
**[IFOOD-MCP-TOOLS.md](IFOOD-MCP-TOOLS.md)**. Regenerate with
`.\\.venv\\Scripts\\python.exe scripts\\dump_mcp_tools.py`.

Restrict the set with an allow or deny list:

```dotenv
MCP_TOOLS=listAddresses,getUser,searchOffers,getItem,getMerchant,createCart,addItemToCart,updateCartItem,getCart,setCartDelivery,setCartPayment,submitOrder
# MCP_EXCLUDE_TOOLS=submitOrder
```

Empty means "all tools".

> **A fallback is not a way out of this.** It only helps while it still has quota.
> Gemini 2.5 Flash's free tier is **20 requests per day**, so under agentic load it
> is exhausted almost immediately and starts replying `retry in 1h57m`. Either fit
> the primary provider's limit, [run locally](#run-it-fully-local-ollama) — which
> has no such limit — or pay for a higher tier.

Two related behaviours are handled for you:

- **Empty completions count as failures.** Gemini intermittently answers `HTTP 200`
  with no content and no tool call once a large tool set is bound. Left alone that
  silently ends the ReAct loop with a blank reply, so `GuardedChatOpenAI` retries
  once and then raises, letting the fallback layer take over.
- **`--check` reports the schema cost** and warns when it is high enough to trip a
  free-tier limit.

See **[MCP-FEEDBACK.md](MCP-FEEDBACK.md)** for the full write-up of these limits and
what the MCP server could do about them.

## Switching LLM providers

The primary is selected by `ENVIRONMENT` (`local` → Ollama, `remote` → Zen). The
presets live in `PRESETS` in `src/agent/providers.py`; edit them to point at a
different provider. Any OpenAI-compatible provider works:

| Provider | `LLM_BASE_URL` | Notes |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | local, no key, no quota |
| OpenCode Zen | `https://opencode.ai/zen/v1` | paid, uses your credits |
| Groq | `https://api.groq.com/openai/v1` | free tier, no card |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | free Flash models |
| Cerebras | `https://api.cerebras.ai/v1` | free tier |
| OpenRouter | `https://openrouter.ai/api/v1` | `:free` models |
| OpenAI | `https://api.openai.com/v1` | paid |

> On OpenCode Zen, **never use a model id ending in `-free`** (or `big-pickle`)
> from your own code — the free lane is gated to the OpenCode client and fails
> with `FreeTierError`. Use a paid id with your credits.

> Pick a model that supports **tool calling** — the agent relies on it.

## Retries and provider fallbacks

Free tiers rate-limit quickly, and an agent turn costs several calls (one per
tool-loop step). Two layers keep a run alive (`src/agent/providers.py`):

**1. Per-request retries.** Every client sets `max_retries`, so the OpenAI SDK
retries each request on `429` and `5xx` with exponential backoff and jitter:

```dotenv
LLM_MAX_RETRIES=5
LLM_TIMEOUT=60
```

**2. Provider fallback.** Once the primary exhausts its retries, the next
provider in the chain takes over — mid-run, without losing the conversation:

```dotenv
LLM_FALLBACKS=gemini,cerebras
GEMINI_API_KEY=...
CEREBRAS_API_KEY=...
```

Fallback keys use each provider's own variable, so the primary and fallbacks can
be different vendors. Names come from the built-in registry (`ollama`, `groq`,
`gemini`, `cerebras`, `openrouter`, `mistral`, `zen`, `openai`); override a
fallback's model with e.g. `GEMINI_MODEL=gemini-2.5-pro`. `ollama` is local, so it
needs no key and is always considered available.

`python -m agent.main --check` prints the resolved chain and marks any fallback
whose key is missing, so a typo never silently costs you the safety net.

> **Design note:** retries use `max_retries` rather than
> `Runnable.with_retry()`. In this LangChain version `with_retry()` returns a
> `RunnableRetry` that does **not** expose `bind_tools`, so wrapping the model in
> it silently breaks tool calling. The SDK-level retry avoids that entirely.

## Structure

- `src/agent/mesa/` — iFood Mesa backend: `api.py` (FastAPI), `recommend_graph.py`
  (find the restaurants) and `checkout_graph.py` (pay & send) (LangGraph), `chat.py`
  (ReAct agent), `solver.py` (the multi-constraint search), `cart.py`, `split.py`,
  `intents.py`, `context.py` (address), `gateway.py` (live/mock MCP), `store.py`,
  `models.py`.
- `frontend/` — Vite + React + TypeScript UI. `OrderDock` is the floating stack on
  top of the chat: `RestaurantBar` (the dropdown), `RecommendationStrip` (one card
  per person with a "+") and `CartBar` (the shared cart, payments and the host's
  finish button).
- `tests/` — pytest suite (runs on the mock catalog; no network).
- `src/agent/tools.py` — local tools (your own `@tool` functions).
- `src/agent/mcp.py` — loads tools from the iFood MCP server.
- `src/agent/oauth.py` — OAuth 2.1 sign-in and token caching for the MCP.
- `src/agent/providers.py` — provider registry, retries, and the fallback chain.
- `src/agent/graph.py` — builds the ReAct tool-calling graph.
- `src/agent/main.py` — CLI: interactive, one-shot, `--check`, `--tools`.
- `src/agent/probe.py` — checks which model ids your key can actually call.
- `Modelfile.ollama` — the local model, with `num_ctx=16384` baked in (see above).
- `scripts/dump_mcp_tools.py` — regenerates `IFOOD-MCP-TOOLS.md` from the live server.

## Adding a local tool

Define a function, decorate it with `@tool`, and add it to `TOOLS` in
`src/agent/tools.py`. The docstring becomes the tool description, so make it
clear about what the tool does and when to use it.
