"""Mesa text models: local Ollama or remote OpenCode Zen.

Both expose the OpenAI protocol, so they share a client with bounded retries
and empty-response protection. Gemini transcription lives in mesa.audio.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

DEFAULT_ENVIRONMENT = "remote"
DEFAULT_LOCAL_MODEL = "ifood-agent"
DEFAULT_REMOTE_MODEL = "glm-5.3-flash"


def environment() -> str:
    """The selected text-model environment; keep remote as the default."""
    value = os.getenv("ENVIRONMENT", DEFAULT_ENVIRONMENT).strip().lower()
    return value if value in {"local", "remote"} else DEFAULT_ENVIRONMENT


def primary_model_name() -> str:
    if environment() == "local":
        return os.getenv("OLLAMA_MODEL", "").strip() or DEFAULT_LOCAL_MODEL
    return os.getenv("OPENCODE_ZEN_MODEL", "").strip() or DEFAULT_REMOTE_MODEL


class EmptyCompletionError(RuntimeError):
    """A model answered with neither content nor a tool call."""

    def __init__(self, model: str) -> None:
        super().__init__(f"{model} returned an empty completion (no content, no tool call).")


class GuardedChatOpenAI(ChatOpenAI):
    """Reject empty completions, including when tools are bound to the model."""

    empty_retries: int = 1

    @staticmethod
    def _is_empty(message: BaseMessage) -> bool:
        return not (message.content or "").strip() and not getattr(message, "tool_calls", None)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for _ in range(self.empty_retries + 1):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if not self._is_empty(result.generations[0].message):
                return result
        raise EmptyCompletionError(self.model_name)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        for _ in range(self.empty_retries + 1):
            result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if not self._is_empty(result.generations[0].message):
                return result
        raise EmptyCompletionError(self.model_name)


def build_model() -> GuardedChatOpenAI:
    """Build the configured text model; never switch providers implicitly."""
    if environment() == "local":
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434/v1"
        api_key = "ollama"  # The compatible client requires a nonempty placeholder.
    else:
        base_url = "https://opencode.ai/zen/v1"
        api_key = os.getenv("OPENCODE_ZEN_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ENVIRONMENT=remote needs OPENCODE_ZEN_API_KEY in .env. "
                "Use ENVIRONMENT=local to run on Ollama instead."
            )
    return GuardedChatOpenAI(
        model=primary_model_name(),
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "5")),
        timeout=float(os.getenv("LLM_TIMEOUT", "60")),
    )
