"""OAuth 2.1 (authorization code + PKCE) for the iFood MCP server.

Why we don't use FastMCP's built-in OAuth: it hardcodes the callback path to
``/callback`` (see ``fastmcp/client/auth/oauth.py``), but iFood matches redirect
URIs byte-for-byte and this client is registered for
``http://localhost:8080/agent``. So we run the flow ourselves and hand the
resulting bearer token to the MCP client.

The token is cached to disk and refreshed automatically. Sign in once with::

    python -m agent.oauth
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

DEFAULT_AUTHORIZE_ENDPOINT = "https://id.ifood.com.br/authorize"
DEFAULT_TOKEN_ENDPOINT = "https://id.ifood.com.br/token"
DEFAULT_REDIRECT_URI = "http://localhost:8080/agent"


@dataclass(frozen=True)
class OAuthConfig:
    """Everything the flow needs, resolved from the environment."""

    client_id: str
    client_secret: str
    authorize_endpoint: str
    token_endpoint: str
    redirect_uri: str
    scope: str

    @property
    def callback_host(self) -> str:
        return urlparse(self.redirect_uri).hostname or "localhost"

    @property
    def callback_port(self) -> int:
        return urlparse(self.redirect_uri).port or 80

    @property
    def callback_path(self) -> str:
        return urlparse(self.redirect_uri).path or "/"


def load_config() -> OAuthConfig:
    """Read OAuth settings from the environment."""
    client_id = os.getenv("MCP_CLIENT_ID")
    client_secret = os.getenv("MCP_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "MCP_CLIENT_ID and MCP_CLIENT_SECRET must be set to sign in.\n"
            "See .env.example (values are in setup-sudowin-mcp.mjs)."
        )
    return OAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        authorize_endpoint=os.getenv("MCP_AUTHORIZE_ENDPOINT", DEFAULT_AUTHORIZE_ENDPOINT),
        token_endpoint=os.getenv("MCP_TOKEN_ENDPOINT", DEFAULT_TOKEN_ENDPOINT),
        redirect_uri=os.getenv("MCP_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        scope=os.getenv("MCP_SCOPE", ""),
    )


def token_cache_path() -> Path:
    """Where the token is cached between runs."""
    return Path(os.getenv("MCP_TOKEN_CACHE", ".mcp-token.json")).expanduser()


def _pkce_pair() -> tuple[str, str]:
    """Return a PKCE (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Answers the single OAuth redirect that lands on the callback path."""

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _CallbackServer)

        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != server.callback_path.rstrip("/"):
            print(f"  ignored request for path {parsed.path!r}", flush=True)
            self._respond(404, "Not found")
            return

        params = parse_qs(parsed.query)
        got_state = (params.get("state") or [""])[0]

        # Ignore redirects from an earlier or abandoned sign-in attempt, so a
        # stale browser tab cannot abort the flow that is actually in progress.
        if got_state != server.expected_state:
            print(
                f"  ignored redirect: state {got_state[:12]!r} does not match "
                f"expected {server.expected_state[:12]!r} "
                f"(params: {sorted(params)})",
                flush=True,
            )
            self._respond(
                400,
                "<h1>Stale sign-in</h1><p>This link belongs to an earlier "
                "attempt. Return to the terminal and open the current "
                "sign-in URL.</p>",
            )
            return

        print(f"  accepted redirect (params: {sorted(params)})", flush=True)
        server.result = params
        self._respond(200, "<h1>Signed in</h1><p>You can close this tab.</p>")

    def _respond(self, status: int, html: str) -> None:
        body = f"<!doctype html><html><body>{html}</body></html>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default request logging."""


class _CallbackServer(HTTPServer):
    """Local listener that captures the authorization code."""

    callback_path: str
    expected_state: str
    result: dict[str, list[str]] | None


def _wait_for_result(server: _CallbackServer, timeout: float) -> dict[str, list[str]] | None:
    """Block until the callback arrives or the deadline passes."""
    deadline = time.monotonic() + timeout
    while server.result is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        server.timeout = remaining
        server.handle_request()
    return server.result


def _token_request(cfg: OAuthConfig, data: dict[str, str]) -> dict:
    """POST to the token endpoint using HTTP basic client auth."""
    response = httpx.post(
        cfg.token_endpoint,
        data=data,
        auth=(cfg.client_id, cfg.client_secret),
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"token endpoint returned {response.status_code}: {response.text}"
        )
    return response.json()


async def _authorize(cfg: OAuthConfig, timeout: float) -> dict:
    """Run the full authorization-code + PKCE flow in the browser."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    try:
        server = _CallbackServer((cfg.callback_host, cfg.callback_port), _CallbackHandler)
    except OSError as err:
        raise RuntimeError(
            f"could not listen on {cfg.callback_host}:{cfg.callback_port} ({err}).\n"
            "Free that port before signing in - it must stay free until the "
            "redirect arrives."
        ) from err

    server.callback_path = cfg.callback_path
    server.expected_state = state
    server.result = None

    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.scope:
        params["scope"] = cfg.scope
    auth_url = f"{cfg.authorize_endpoint}?{urlencode(params)}"

    print(f"Listening for the redirect on {cfg.redirect_uri}")
    print("Opening your browser for iFood sign-in...")
    print(f"If it does not open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _wait_for_result, server, timeout)
    finally:
        server.server_close()

    if result is None:
        raise TimeoutError(
            f"timed out after {timeout:.0f}s waiting for the redirect on "
            f"{cfg.redirect_uri}.\n"
            "Complete the sign-in at the URL printed above, then retry."
        )
    if error := result.get("error"):
        description = (result.get("error_description") or [""])[0]
        raise RuntimeError(f"authorization failed: {error[0]} {description}".strip())

    # The callback handler only accepts a matching state, so reaching here means
    # the response is for this attempt. Guard anyway, since the code is a secret.
    if (result.get("state") or [""])[0] != state:
        raise RuntimeError("OAuth state mismatch; refusing the response.")

    return _token_request(
        cfg,
        {
            "grant_type": "authorization_code",
            "code": result["code"][0],
            "redirect_uri": cfg.redirect_uri,
            "code_verifier": verifier,
        },
    )


def _load_cached() -> dict | None:
    path = token_cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cached(token: dict) -> None:
    token = dict(token)
    # Refresh a minute early so a token never expires mid-request.
    token["expires_at"] = time.time() + float(token.get("expires_in", 3600)) - 60
    path = token_cache_path()
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best effort on platforms without POSIX permissions


async def get_access_token(*, force: bool = False) -> str:
    """Return a valid bearer token, signing in or refreshing as needed.

    A token set directly in ``MCP_BEARER_TOKEN`` short-circuits the whole flow,
    which is useful for a pre-provisioned deployment.
    """
    if static_token := os.getenv("MCP_BEARER_TOKEN"):
        return static_token

    cfg = load_config()
    cached = None if force else _load_cached()

    if cached:
        if cached.get("expires_at", 0) > time.time():
            return cached["access_token"]
        if refresh_token := cached.get("refresh_token"):
            try:
                token = _token_request(
                    cfg,
                    {"grant_type": "refresh_token", "refresh_token": refresh_token},
                )
                _save_cached(token)
                return token["access_token"]
            except RuntimeError as err:
                print(f"  refresh failed ({err}); signing in again.", flush=True)

    token = await _authorize(cfg, timeout=float(os.getenv("MCP_OAUTH_TIMEOUT", "300")))
    _save_cached(token)
    return token["access_token"]


def main() -> None:
    force = "--force" in sys.argv
    token = asyncio.run(get_access_token(force=force))
    print(f"\nGot an access token ({len(token)} chars), cached at {token_cache_path()}")


if __name__ == "__main__":
    main()
