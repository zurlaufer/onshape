"""Onshape REST API client with HMAC-SHA256 request signing.

Signing, then Step 4's hardening: a per-bucket quota governor, retries that are
classified rather than blanket, and per-endpoint timeouts.

Reference: https://onshape-public.github.io/docs/auth/apikeys/

NEVER write to stdout from this module. Under MCP stdio transport stdout carries
the JSON-RPC stream, and a stray write corrupts it — the server appears to
connect and then dies mid-call with no readable error. Use `logger`, which the
server entry point binds to stderr.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import string
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from .errors import (
    OnshapeAuthError,
    OnshapeClientError,
    OnshapeConfigError,
    OnshapeError,
    OnshapeQuotaExhausted,
    OnshapeRateLimitError,
    OnshapeServerError,
    OnshapeTimeoutError,
)
from .quota import QuotaLedger, bucket_key
from .write_policy import WritePolicy, WriteRefused

logger = logging.getLogger(__name__)

# Re-exported so `from src.api.onshape_client import OnshapeAuthError` keeps
# working now that the exception types live in errors.py.
__all__ = [
    "OnshapeApiClient",
    "OnshapeError",
    "OnshapeConfigError",
    "OnshapeAuthError",
    "OnshapeClientError",
    "OnshapeQuotaExhausted",
    "OnshapeRateLimitError",
    "OnshapeServerError",
    "OnshapeTimeoutError",
    "QuotaLedger",
    "WritePolicy",
    "WriteRefused",
    "build_auth_headers",
    "generate_nonce",
]

DEFAULT_BASE_URL = "https://cad.onshape.com"

# Probed against the live API on 2026-08-03: v0-v26 are accepted, v27 returns
# 400, and a malformed version returns 404. So Onshape does validate the number
# -- but every stale version keeps working silently, which is the trap: an old
# or absent version never errors, it just serves you an older contract.
# Onshape's guidance is to always call the newest. Re-probe when bumping.
DEFAULT_API_VERSION = "v26"

DEFAULT_TIMEOUT_SECONDS = 30.0

# Per-endpoint read timeouts, in seconds, keyed by the same normalised route the
# quota ledger uses. One notion of "which endpoint is this", two consumers.
#
# These are measured, not guessed (2026-08-07, against the real Citrus robot):
#
#   * assembly mass properties returns in 40.8 s with a 1,567-byte body. It was
#     never a hang — it was our flat 30 s timeout cutting off a slow-but-fine
#     endpoint, which is why STATUS.md recorded it as unusable for months.
#   * currentmicroversion returns 49 bytes in well under a second. Thirty
#     seconds of patience for that is thirty seconds of a hung tool call.
#
# A timeout is a configuration bug here, not a transient fault — see
# OnshapeTimeoutError for why that distinction decides the retry policy.
_TIMEOUT_OVERRIDES: dict[str, float] = {
    "GET /documents/d/{id}/w/{id}/currentmicroversion": 10.0,
    "GET /documents/d/{id}/w/{id}/elements": 20.0,
    "GET /assemblies/d/{id}/w/{id}/e/{id}": 90.0,
    "GET /assemblies/d/{id}/w/{id}/e/{id}/massproperties": 120.0,
    "GET /partstudios/d/{id}/w/{id}/e/{id}/massproperties": 120.0,
    "POST /partstudios/d/{id}/w/{id}/e/{id}/featurescript": 60.0,
}

#: Routes where a POST changes nothing on the server, so a retry is safe.
#: Everything not listed here is assumed to have side effects.
#:
#: The evaluate endpoint runs a lambda and returns a value; it persists nothing,
#: which is the whole reason layer 5 can be used freely. By contrast the Feature
#: Studio write carries a `sourceMicroversion` that goes stale the moment a
#: write partially succeeds, so a blind retry would either clobber or conflict.
#: That one is retried by re-reading first, in `src/skills/apply.py`, which is
#: the only layer that knows what it was trying to write.
_IDEMPOTENT_POSTS = frozenset({
    "POST /partstudios/d/{id}/w/{id}/e/{id}/featurescript",
})

DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 8.0

# Onshape requires at least 16 alphanumeric characters, unique per request.
_NONCE_ALPHABET = string.ascii_letters + string.digits
_NONCE_LENGTH = 25

# Onshape rejects a Date more than 5 minutes from its own clock. We warn a
# little earlier so the diagnostic fires before the hard failure does.
_CLOCK_SKEW_WARN_SECONDS = 120


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
            `/api/v6/documents`. Must be exactly what goes on the wire.
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
            docs = (await client.get("/documents", query={"limit": 1})).json()

    Prefer endpoints covered by the OAuth2Read scope. Anything under /users
    returns profile data and needs OAuth2ReadPII, which project keys do not
    carry — it fails with 403 even when the signature is correct.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        quota: QuotaLedger | None = None,
        write_policy: WritePolicy | None = None,
    ) -> None:
        if not access_key or not secret_key:
            raise OnshapeConfigError(
                "Both ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY are required."
            )
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self.default_timeout = timeout
        self.max_retries = max(0, max_retries)
        #: Reports what calls actually cost, so tools stop hardcoding guesses.
        self.quota = quota if quota is not None else QuotaLedger()
        #: Enforced here rather than in a caller, because a guard you have to
        #: remember to call is one that eventually is not called.
        self.write_policy = (
            write_policy if write_policy is not None else WritePolicy.from_manifest()
        )
        # Connect and pool waits stay short regardless of the per-endpoint read
        # timeout: a slow *endpoint* is normal here, a slow *handshake* is not.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0, pool=10.0)
        )

    def timeout_for(self, method: str, api_path: str) -> httpx.Timeout:
        """The timeout for one route, from the measured override table."""
        read = _TIMEOUT_OVERRIDES.get(
            bucket_key(method, api_path), self.default_timeout
        )
        return httpx.Timeout(read, connect=10.0, pool=10.0)

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
                f"Copy .env.template to .env and fill them in (generate keys in "
                f"Onshape under My account -> Developer, or Enterprise settings "
                f"-> Developer -> API keys)."
            )

        api_version = os.environ.get("ONSHAPE_API_VERSION", DEFAULT_API_VERSION).strip()
        if not api_version:
            # An empty version yields /api//path, which Onshape resolves to v0
            # (the oldest API) without complaining. Refuse instead.
            raise OnshapeConfigError(
                f"ONSHAPE_API_VERSION is empty. Omitting the version selects the "
                f"oldest API (v0) silently. Set it to '{DEFAULT_API_VERSION}'."
            )

        timeout_raw = os.environ.get("ONSHAPE_TIMEOUT_SECONDS", "")
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError as exc:
            raise OnshapeConfigError(
                f"ONSHAPE_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}."
            ) from exc

        retries_raw = os.environ.get("ONSHAPE_MAX_RETRIES", "")
        try:
            max_retries = int(retries_raw) if retries_raw else DEFAULT_MAX_RETRIES
        except ValueError as exc:
            raise OnshapeConfigError(
                f"ONSHAPE_MAX_RETRIES must be an integer, got {retries_raw!r}."
            ) from exc

        return cls(
            access_key=os.environ["ONSHAPE_ACCESS_KEY"],
            secret_key=os.environ["ONSHAPE_SECRET_KEY"],
            base_url=os.environ.get("ONSHAPE_BASE_URL", DEFAULT_BASE_URL),
            api_version=api_version,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def __aenter__(self) -> "OnshapeApiClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def build_path(self, path: str) -> str:
        """Prefix an API-relative path with `/api/{version}`.

        Callers pass `/documents`; the version prefix is added here so it can
        never be forgotten (omitting it silently selects v0).
        """
        return f"/api/{self.api_version}/{path.lstrip('/')}"

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """How long to wait before attempt N+1.

        Onshape's own `Retry-After` wins when present — it knows when the bucket
        refills and we do not. Otherwise exponential with full jitter, which
        matters because several tools may be retrying at once and a fixed
        schedule would march them in lockstep.
        """
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    return max(0.0, float(raw))
                except (TypeError, ValueError):
                    pass  # a date-formatted Retry-After; fall through
        return random.uniform(
            0.0, min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2 ** attempt)
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        """Send one signed request, retrying only what is worth retrying.

        Retry policy, by outcome — deliberately not "retry on failure", because
        some of our calls are POSTs and a 404 never becomes a 200:

            401 / 403     never       an auth failure is not transient
            429           retry       honour Retry-After, else backoff
            5xx           retry       GETs and idempotent POSTs only
            ConnectError  retry       never reached the server, so it is free
            ReadTimeout   never       measured: it still spends quota (see
                                      OnshapeTimeoutError)
            other 4xx     never

        Every attempt re-signs. `Date` expires after 300 s and nonces must be
        unique per request, so signing lives inside this loop and never above it.

        Args:
            path: API-relative, e.g. `/documents`. The `/api/{version}` prefix
                is added for you.
            query: Encoded here, once. Never passed to httpx as `params=` —
                httpx would re-encode it and the signature would stop matching.
        """
        method = method.upper()
        # Encode exactly once: this same string is both signed and sent.
        query_string = urlencode(query, doseq=True) if query else ""
        api_path = self.build_path(path)

        # Before anything is signed or sent. The document is read out of the
        # path, so no caller can reach Onshape without passing this.
        self.write_policy.check(method, api_path)

        url = f"{self.base_url}{api_path}"
        if query_string:
            url = f"{url}?{query_string}"

        content = (
            json.dumps(json_body).encode("utf-8") if json_body is not None else None
        )
        timeout = self.timeout_for(method, api_path)
        retryable_method = method == "GET" or (
            bucket_key(method, api_path) in _IDEMPOTENT_POSTS
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            # Refuse before spending, not after. Raises OnshapeQuotaExhausted.
            self.quota.check(method, api_path)

            # Re-signed every attempt: a reused nonce or a stale Date is a 401.
            headers = build_auth_headers(
                method=method,
                path=api_path,
                query=query_string,
                access_key=self.access_key,
                secret_key=self.secret_key,
            )

            logger.debug("Onshape %s %s (attempt %d)", method, api_path, attempt + 1)
            try:
                response = await self._client.request(
                    method, url, headers=headers, content=content, timeout=timeout
                )
            except httpx.ReadTimeout as exc:
                # Not retried: the request reached Onshape and already cost a
                # quota slot, so a retry pays twice to duplicate work in flight.
                raise OnshapeTimeoutError(
                    f"{method} {api_path} exceeded {timeout.read:.0f}s."
                ) from exc
            except httpx.TransportError as exc:
                # Connect/pool failures never reached the server, so they cost
                # nothing and are the one genuinely free thing to retry.
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self._retry_delay(attempt, None))
                continue

            self.quota.record(method, api_path, response.headers)

            if response.status_code in (401, 403):
                raise OnshapeAuthError(
                    f"Onshape rejected the request ({response.status_code}) for "
                    f"{method} {api_path}. Check ONSHAPE_ACCESS_KEY / "
                    f"ONSHAPE_SECRET_KEY."
                    f"{_describe_clock_skew(response)}"
                )

            if response.status_code == 429 or (
                response.status_code >= 500 and retryable_method
            ):
                if attempt < self.max_retries:
                    delay = self._retry_delay(attempt, response)
                    logger.warning(
                        "Onshape %s on %s %s; retrying in %.1fs (%d/%d)",
                        response.status_code, method, api_path, delay,
                        attempt + 1, self.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

            return self._raise_for_status(method, api_path, response)

        raise OnshapeServerError(
            f"{method} {api_path} failed after {self.max_retries + 1} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    @staticmethod
    def _raise_for_status(
        method: str, api_path: str, response: httpx.Response
    ) -> httpx.Response:
        """Turn a final non-2xx into a typed error carrying advice."""
        code = response.status_code
        if code < 400:
            return response

        where = f"{method} {api_path}"
        if code == 429:
            raise OnshapeRateLimitError(f"Onshape rate-limited {where} (429).")
        if code >= 500:
            raise OnshapeServerError(f"Onshape returned {code} for {where}.")
        raise OnshapeClientError(f"Onshape returned {code} for {where}.")

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
