"""OpenAI-compatible providers, plus the retry/fallback model chain.

Every provider here speaks the OpenAI protocol, so one client class covers all
of them. Two independent layers keep a run alive when a provider struggles:

* ``max_retries`` on each client retries a single request on 429s and 5xx
  responses with exponential backoff and jitter (handled by the OpenAI SDK).
* ``with_fallbacks`` moves to the next provider once the primary has exhausted
  those retries, so a rate-limited free tier does not end the run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

load_dotenv()


@dataclass(frozen=True)
class Provider:
    """A known OpenAI-compatible endpoint."""

    name: str
    base_url: str
    model: str
    api_key_env: str
    free: bool
    #: Local servers need no real key and have no quota (see ``api_key_for``).
    local: bool = False


REGISTRY: dict[str, Provider] = {
    # Local inference: no key, no quota, and the only free option that can hold
    # all 23 iFood tool schemas at once. Its context window must be raised past
    # Ollama's 4096 default or those schemas are silently truncated - see
    # Modelfile.ollama.
    "ollama": Provider(
        "ollama",
        "http://localhost:11434/v1",
        "qwen3:8b",
        "OLLAMA_API_KEY",
        True,
        local=True,
    ),
    "groq": Provider(
        "groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY", True
    ),
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-2.5-flash",
        "GEMINI_API_KEY",
        True,
    ),
    "cerebras": Provider(
        "cerebras", "https://api.cerebras.ai/v1", "llama-3.3-70b", "CEREBRAS_API_KEY", True
    ),
    "openrouter": Provider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
        "OPENROUTER_API_KEY",
        True,
    ),
    "mistral": Provider(
        "mistral", "https://api.mistral.ai/v1", "mistral-small-latest", "MISTRAL_API_KEY", False
    ),
    "zen": Provider(
        "zen", "https://opencode.ai/zen/v1", "glm-5.3-flash", "OPENCODE_ZEN_API_KEY", False
    ),
    "openai": Provider(
        "openai", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY", False
    ),
}

# Used when the explicit LLM_BASE_URL / MODEL_NAME variables are unset.
DEFAULT_BASE_URL = REGISTRY["groq"].base_url
DEFAULT_MODEL = REGISTRY["groq"].model

#: Presets selected by ``ENVIRONMENT``. ``local`` is free (Ollama); ``remote``
#: is the cheapest paid Zen model that still handles tool calling well.
PRESETS: dict[str, tuple[str, str]] = {
    "local": ("ollama", "ifood-agent"),
    "remote": ("zen", "glm-5.3-flash"),
}

DEFAULT_ENVIRONMENT = "remote"


def environment() -> str:
    """The active environment name (``local`` or ``remote``)."""
    return os.getenv("ENVIRONMENT", DEFAULT_ENVIRONMENT).strip().lower()


def max_retries() -> int:
    """How many times the SDK retries one request before giving up."""
    return int(os.getenv("LLM_MAX_RETRIES", "5"))


def request_timeout() -> float:
    """Per-request timeout in seconds."""
    return float(os.getenv("LLM_TIMEOUT", "60"))


def api_key_for(provider: Provider) -> str | None:
    """The key to use for a provider, or ``None`` if it cannot be built.

    Local servers have no key, but the OpenAI client rejects an empty one, so
    any non-empty placeholder will do.
    """
    if provider.local:
        return os.getenv(provider.api_key_env) or "ollama"
    return os.getenv(provider.api_key_env) or None


def is_local_endpoint() -> bool:
    """Whether the primary points at a local server (no quota, no TPM limit)."""
    return environment() == "local"


class EmptyCompletionError(RuntimeError):
    """A provider answered HTTP 200 with neither content nor a tool call."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"{model} returned an empty completion (no content, no tool call). "
            "Free tiers do this intermittently with very large tool sets."
        )


class GuardedChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` that refuses to accept an empty completion.

    Gemini 2.5 Flash intermittently answers HTTP 200 with an empty candidate
    (zero completion tokens, ``finish_reason="stop"``) once the full iFood tool
    set is bound. Treated as success, that silently ends a ReAct loop with a
    blank reply that looks like a crash. Raising instead hands control back to
    the retry/fallback layer.

    Subclassing (rather than wrapping) matters: ``bind_tools`` returns a binding
    that still delegates to this instance, so the guard survives tool binding.
    """

    #: Extra attempts against the same provider before giving up.
    empty_retries: int = 1

    @staticmethod
    def _is_empty(message: BaseMessage) -> bool:
        return not (message.content or "").strip() and not getattr(message, "tool_calls", None)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = None
        for _ in range(self.empty_retries + 1):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if not self._is_empty(result.generations[0].message):
                return result
        raise EmptyCompletionError(self.model_name)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = None
        for _ in range(self.empty_retries + 1):
            result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if not self._is_empty(result.generations[0].message):
                return result
        raise EmptyCompletionError(self.model_name)


def make_client(base_url: str, model: str, api_key: str, *, temperature: float = 0) -> GuardedChatOpenAI:
    """Build a client with per-request retries and the empty-completion guard."""
    return GuardedChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries(),
        timeout=request_timeout(),
    )


def build_primary() -> ChatOpenAI:
    """The primary model, selected by ``ENVIRONMENT``.

    ``ENVIRONMENT=local``  -> Ollama (free, no key, all 23 tools fit).
    ``ENVIRONMENT=remote`` -> the cheapest paid Zen model (needs a key).
    """
    env = environment()
    provider_name, model = PRESETS.get(env, PRESETS[DEFAULT_ENVIRONMENT])
    provider = REGISTRY[provider_name]
    api_key = api_key_for(provider)
    if api_key is None:
        raise RuntimeError(
            f"ENVIRONMENT={env} needs {provider.api_key_env} to be set.\n"
            "Copy .env.example to .env and paste your key, or set "
            "ENVIRONMENT=local to run on Ollama instead."
        )
    return make_client(provider.base_url, model, api_key)


def fallback_names() -> list[str]:
    """Provider names listed in ``LLM_FALLBACKS``, in order."""
    raw = os.getenv("LLM_FALLBACKS", "")
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def build_fallbacks() -> tuple[list[ChatOpenAI], list[str]]:
    """Build the fallback clients.

    Returns ``(clients, skipped)``. ``skipped`` records entries that could not
    be built (unknown provider, or its API key is unset) so callers can report
    them instead of silently running without the fallback they configured.
    """
    clients: list[ChatOpenAI] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for name in fallback_names():
        if name in seen:
            continue
        seen.add(name)

        provider = REGISTRY.get(name)
        if provider is None:
            skipped.append(f"{name} (unknown provider)")
            continue

        api_key = api_key_for(provider)
        if api_key is None:
            skipped.append(f"{name} ({provider.api_key_env} not set)")
            continue

        # Allow per-provider model overrides, e.g. GEMINI_MODEL=gemini-2.5-pro.
        prefix = provider.api_key_env.removesuffix("_API_KEY")
        model = os.getenv(f"{prefix}_MODEL", provider.model)
        clients.append(make_client(provider.base_url, model, api_key))

    return clients, skipped


def build_model():
    """The model the agent runs on: primary, plus any configured fallbacks."""
    primary = build_primary()
    fallbacks, _ = build_fallbacks()
    if fallbacks:
        return primary.with_fallbacks(fallbacks)
    return primary


def primary_model_name() -> str:
    """The resolved primary model id (for display in health endpoints)."""
    env = environment()
    _, model = PRESETS.get(env, PRESETS[DEFAULT_ENVIRONMENT])
    return model


def describe_chain() -> list[tuple[str, str, str]]:
    """Summarise the chain for ``--check``: (role, model, base_url).

    Reads configuration only, so it works before an API key is set.
    """
    env = environment()
    provider_name, model = PRESETS.get(env, PRESETS[DEFAULT_ENVIRONMENT])
    provider = REGISTRY[provider_name]
    rows = [
        (f"primary[{env}]", model, provider.base_url)
    ]

    for name in fallback_names():
        provider = REGISTRY.get(name)
        if provider is None:
            rows.append((f"fallback:{name}", "unknown provider", "-"))
            continue
        if api_key_for(provider) is None:
            note = f"skipped - {provider.api_key_env} not set"
            rows.append((f"fallback:{name}", note, provider.base_url))
            continue
        prefix = provider.api_key_env.removesuffix("_API_KEY")
        model = os.getenv(f"{prefix}_MODEL", provider.model)
        rows.append((f"fallback:{name}", model, provider.base_url))

    return rows
