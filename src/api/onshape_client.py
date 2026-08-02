"""Onshape REST API client with HMAC-SHA256 request signing.

Step 1 of the walking skeleton: signing + a single request path. Retry/backoff
and the full error hierarchy arrive in Step 4, once signing is proven live.

Reference: https://onshape-public.github.io/docs/auth/apikeys/

NEVER write to stdout from this module. Under MCP stdio transport stdout carries
the JSON-RPC stream, and a stray write corrupts it — the server appears to
connect and then dies mid-call with no readable error. Use `logger`, which the
server entry point binds to stderr.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cad.onshape.com"
DEFAULT_API_VERSION = "v6"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Onshape requires at least 16 alphanumeric characters, unique per request.
_NONCE_ALPHABET = string.ascii_letters + string.digits
_NONCE_LENGTH = 25

# Onshape rejects a Date more than 5 minutes from its own clock. We warn a
# little earlier so the diagnostic fires before the hard failure does.
_CLOCK_SKEW_WARN_SECONDS = 120


class OnshapeConfigError(RuntimeError):
    """Required configuration is missing or malformed."""


class OnshapeAuthError(RuntimeError):
    """Onshape rejected our credentials or signature (401/403)."""


def generate_nonce(length: int = _NONCE_LENGTH) -> str:
    """A fresh alphanumeric nonce. Must be unique per request, including retries."""
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(length))


def build_auth_headers(
    *,
    method: str,
    path: str,
    access_key: str,
    secret_key: str,
    query: str = "",
    content_type: str = "application/json",
    date: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Build the signed request headers for one Onshape call.

    Pure function — no I/O, no clock reads unless `date` is omitted. `date` and
    `nonce` are injectable so the signature can be tested against a frozen
    golden vector without a network or a stable clock.

    Args:
        method: HTTP verb. Case-insensitive; normalised to upper case.
        path: URL pathname *including* the `/api/{version}` prefix, e.g.
            `/api/v6/users/sessioninfo`. Must be exactly what goes on the wire.
        query: Already-encoded query string with no leading `?`, or `""`.
            Must be byte-identical to what is appended to the URL.
        content_type: Signed even on GET requests, per Onshape's scheme.

    Returns:
        Headers dict ready to hand to httpx.
    """
    date = formatdate(usegmt=True) if date is None else date
    nonce = generate_nonce() if nonce is None else nonce
    method = method.upper()

    # The string-to-sign is lowercased in its entirety. The *headers* below keep
    # their original casing — only this copy is folded.
    string_to_sign = (
        f"{method}\n{nonce}\n{date}\n{content_type}\n{path}\n{query}\n"
    ).lower()

    digest = hmac.new(
        secret_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")

    return {
        "Date": date,
        "On-Nonce": nonce,
        "Content-Type": content_type,
        "Accept": "application/json",
        "Authorization": f"On {access_key}:HmacSHA256:{signature}",
    }


def _describe_clock_skew(response: httpx.Response) -> str:
    """A hint for the 401-that-is-really-a-wrong-clock case (FM7).

    Onshape rejects requests whose Date is more than 5 minutes from its own,
    and the resulting 401 is indistinguishable from a bad key. Comparing our
    clock to the server's Date header turns a mystery into a one-line fix.
    """
    server_date = response.headers.get("Date")
    if not server_date:
        return ""
    try:
        server_time = parsedate_to_datetime(server_date)
    except (TypeError, ValueError):
        return ""
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)

    skew = abs((datetime.now(timezone.utc) - server_time).total_seconds())
    if skew < _CLOCK_SKEW_WARN_SECONDS:
        return ""
    return (
        f" Your system clock is {skew:.0f}s away from Onshape's "
        f"(signatures expire after 300s) — this is very likely the cause. "
        f"Sync your clock and retry."
    )


class OnshapeApiClient:
    """Async Onshape REST client.

    Use as an async context manager so the connection pool is closed:

        async with OnshapeApiClient.from_env() as client:
            me = (await client.get("/users/sessioninfo")).json()
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not access_key or not secret_key:
            raise OnshapeConfigError(
                "Both ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY are required."
            )
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_env(cls) -> "OnshapeApiClient":
        """Build a client from environment variables, loading `.env` if present."""
        try:
            from dotenv import load_dotenv
        except ImportError:  # pragma: no cover - dependency is declared
            pass
        else:
            load_dotenv()

        missing = [
            name
            for name in ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY")
            if not os.environ.get(name)
        ]
        if missing:
            raise OnshapeConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Copy .env.template to .env and fill them in "
                f"(generate keys at https://dev-portal.onshape.com/keys)."
            )

        api_version = os.environ.get("ONSHAPE_API_VERSION", DEFAULT_API_VERSION).strip()
        if not api_version:
            # An empty version yields /api//path, which Onshape resolves to v0
            # (the oldest API) without complaining. Refuse instead.
            raise OnshapeConfigError(
                "ONSHAPE_API_VERSION is empty. Omitting the version selects the "
                "oldest API (v0) silently. Set it to e.g. 'v6'."
            )

        timeout_raw = os.environ.get("ONSHAPE_TIMEOUT_SECONDS", "")
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError as exc:
            raise OnshapeConfigError(
                f"ONSHAPE_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}."
            ) from exc

        return cls(
            access_key=os.environ["ONSHAPE_ACCESS_KEY"],
            secret_key=os.environ["ONSHAPE_SECRET_KEY"],
            base_url=os.environ.get("ONSHAPE_BASE_URL", DEFAULT_BASE_URL),
            api_version=api_version,
            timeout=timeout,
        )

    async def __aenter__(self) -> "OnshapeApiClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def build_path(self, path: str) -> str:
        """Prefix an API-relative path with `/api/{version}`.

        Callers pass `/users/sessioninfo`; the version prefix is added here so
        it can never be forgotten (omitting it silently selects v0).
        """
        return f"/api/{self.api_version}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        """Send one signed request. Raises on any non-2xx response.

        Args:
            path: API-relative, e.g. `/users/sessioninfo`. The `/api/{version}`
                prefix is added for you.
            query: Encoded here, once. Never passed to httpx as `params=` —
                httpx would re-encode it and the signature would stop matching.
        """
        # Encode exactly once: this same string is both signed and sent.
        query_string = urlencode(query, doseq=True) if query else ""
        api_path = self.build_path(path)
        url = f"{self.base_url}{api_path}"
        if query_string:
            url = f"{url}?{query_string}"

        headers = build_auth_headers(
            method=method,
            path=api_path,
            query=query_string,
            access_key=self.access_key,
            secret_key=self.secret_key,
        )

        content = None
        if json_body is not None:
            content = json.dumps(json_body).encode("utf-8")

        logger.debug("Onshape %s %s", method.upper(), api_path)
        response = await self._client.request(
            method.upper(), url, headers=headers, content=content
        )

        if response.status_code in (401, 403):
            raise OnshapeAuthError(
                f"Onshape rejected the request ({response.status_code}) for "
                f"{method.upper()} {api_path}. Check ONSHAPE_ACCESS_KEY / "
                f"ONSHAPE_SECRET_KEY."
                f"{_describe_clock_skew(response)}"
            )

        response.raise_for_status()
        return response

    async def get(
        self, path: str, *, query: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        return await self.request("GET", path, query=query)

    async def post(
        self,
        path: str,
        *,
        json_body: Any | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        return await self.request("POST", path, query=query, json_body=json_body)
