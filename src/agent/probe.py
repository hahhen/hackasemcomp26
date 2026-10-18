"""Probe which model ids your provider key can actually call.

Useful for two reasons:
  * Groq: check which models your free tier exposes and that they respond.
  * Zen: its free lane is gated to the OpenCode client, so some ids that look
    available fail with a 403 ``FreeTierError`` when called from your own code.

The candidate list is chosen from ``LLM_BASE_URL``. Usage:
    python -m agent.probe                    # built-in candidates for your provider
    python -m agent.probe model-a model-b    # test explicit ids
"""

import os
import sys

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

from agent.providers import DEFAULT_BASE_URL

load_dotenv()

# Candidate ids per provider (matched as a substring of the base URL).
CANDIDATES_BY_PROVIDER = {
    "groq.com": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "llama-3.3-70b-versatile",
    ],
    "opencode.ai": [
        # free lane (expected to be OpenCode-client only)
        "big-pickle",
        "space-bunny-free",
        "longcat-2.5-preview-free",
        "mimo-v2.6-flash-free",
        "deepseek-v4.1-flash-free",
        # paid lane (expected to bill your credits)
        "deepseek-v4.1-flash",
        "glm-5.3-flash",
        "minimax-m3",
    ],
}

# Human-readable verdicts for common error types.
VERDICTS = {
    "FreeTierError": "BLOCKED - free lane (OpenCode client only)",
    "insufficient_quota": "FAILED - out of credits",
    "invalid_request_error": "FAILED - unknown model id?",
    "authentication_error": "FAILED - bad API key",
    "rate_limit_exceeded": "FAILED - rate limited (try again later)",
}


def candidates_for(base_url: str) -> list[str]:
    for fragment, models in CANDIDATES_BY_PROVIDER.items():
        if fragment in base_url:
            return models
    return []


def error_type(err: APIStatusError) -> tuple[str, str]:
    """Pull the error type and message out of an API error body."""
    body = err.body if isinstance(err.body, dict) else {}
    inner = body.get("error", body)
    if not isinstance(inner, dict):
        return "unknown", ""
    return inner.get("type", "unknown"), inner.get("message", "")


def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise SystemExit(
            "LLM_API_KEY is not set. Put your key in .env first (see .env.example)."
        )

    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    client = OpenAI(api_key=api_key, base_url=base_url)
    models = sys.argv[1:] or candidates_for(base_url)

    if not models:
        raise SystemExit(
            f"No built-in candidates for {base_url}.\n"
            "Pass model ids explicitly: python -m agent.probe <id> [<id> ...]"
        )

    print(f"base URL: {base_url}\n")
    for model_id in models:
        try:
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            print(f"OK      {model_id}")
        except APIStatusError as err:
            etype, message = error_type(err)
            verdict = VERDICTS.get(etype, etype)
            print(f"FAIL    {model_id:<32} [{err.status_code}] {verdict}")
            if message:
                print(f"        {message}")
        except Exception as err:  # noqa: BLE001 - report anything unexpected
            print(f"ERROR   {model_id:<32} {type(err).__name__}: {err}")


if __name__ == "__main__":
    main()
