"""Typed failures for the Onshape client.

Every one of these carries an `advice` line: one sentence naming what the person
in front of the screen should actually do. That matters more here than in a
normal library, because the immediate consumer is a language model relaying the
failure to a student. Before this module existed, `src/mcp/server.py` handed the
model `type(exc).__name__` straight from httpx — "ReadTimeout" tells a student
nothing about whether to wait, retry, or change something.

`OnshapeConfigError` and `OnshapeAuthError` keep their names and are re-exported
from `onshape_client`, so existing imports are unaffected.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

__all__ = [
    "OnshapeError",
    "OnshapeConfigError",
    "OnshapeAuthError",
    "OnshapeRateLimitError",
    "OnshapeQuotaExhausted",
    "OnshapeTimeoutError",
    "OnshapeServerError",
    "OnshapeClientError",
]


class OnshapeError(RuntimeError):
    """Base for every failure this client raises deliberately.

    Catching this catches our failures without also swallowing bugs.
    """

    #: One actionable sentence, appended when the failure is shown to a caller.
    advice: str = ""

    def explain(self) -> str:
        return f"{self}{' ' + self.advice if self.advice else ''}"


class OnshapeConfigError(OnshapeError):
    """Required configuration is missing or malformed."""

    advice = "Copy .env.template to .env and fill in both keys."


class OnshapeAuthError(OnshapeError):
    """Onshape rejected our credentials or signature (401/403)."""

    advice = (
        "Check ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY for stray whitespace. "
        "If the endpoint is under /users it needs the OAuth2ReadPII scope, "
        "which these keys do not carry by design."
    )


class OnshapeRateLimitError(OnshapeError):
    """Onshape returned 429. Retries were exhausted."""

    advice = "Wait for the quota to refill — it refills at least daily — and try again."


class OnshapeQuotaExhausted(OnshapeError):
    """Our own governor refused the request before Onshape could 429.

    Deliberately distinct from `OnshapeRateLimitError`: this one means we stopped
    ourselves with requests still available, to keep a retry storm from spending
    the last of a shared budget. It is a safety margin, not a hard limit.
    """

    advice = (
        "This is our own reserve, not Onshape's limit — a few requests remain. "
        "Lower FRC_QUOTA_FLOOR to use them, or wait for the daily refill."
    )


class OnshapeTimeoutError(OnshapeError):
    """A request exceeded its timeout.

    Not retried by default, and the reason is measured rather than assumed: a
    timed-out request still decrements the quota bucket (2026-08-07), so the
    server received it and is very likely still computing. Retrying spends a
    second scarce request to duplicate work already in flight. A timeout here
    means the timeout is set wrong, not that the network flickered.
    """

    advice = (
        "The request reached Onshape and was still running when we gave up. "
        "This endpoint may need a longer timeout rather than a retry."
    )


class OnshapeServerError(OnshapeError):
    """Onshape returned 5xx and retries did not clear it."""

    advice = "This is Onshape's side, not ours. Check status.onshape.com."


class OnshapeClientError(OnshapeError):
    """Onshape returned a 4xx we cannot fix by retrying.

    Usually a wrong id or a document that is not visible to these keys.
    """

    advice = "Check the document, workspace and element ids in the Onshape URL."
