"""Quota accounting from Onshape's own `x-rate-limit-remaining` header.

WHY THIS IS OBSERVATIONAL AND NEVER PREDICTIVE
----------------------------------------------
The obvious design — count our requests, subtract from a known budget — is
wrong here, and it took measuring to find out. Onshape does not have one budget.
It has many, with different ceilings, and the mapping from endpoint to bucket is
neither documented nor inferable. Measured live 2026-08-07:

    POST /partstudios/d/{id}/w/{id}/e/{id}/featurescript      ~84   <-- layer 5
    GET  /partstudios/d/{id}/w/{id}/e/{id}/features           100
    GET  /assemblies/d/{id}/w/{id}/e/{id}/massproperties      250
    GET  /documents/d/{id}/w/{id}/currentmicroversion         400   <-- the cache
    GET  /documents                                          1000
    GET  /assemblies/d/{id}/w/{id}/e/{id}                    1000
    GET  /documents/d/{id}/w/{id}/elements                   1500
    GET  /documents/{id}, /featurestudios/..., .../boundingboxes  3000

Two facts fall out of that table, and both hurt:

  * The evaluate endpoint that gives us line-numbered FeatureScript diagnostics
    is the *scarcest* thing we call, at roughly 84 per window. It is also the one
    a correction loop would hammer hardest.
  * `currentmicroversion` costs 49 bytes and is our cheapest call by far, but it
    sits on a 400 bucket and `AssemblyCache` used to spend one on every single
    query. Cheapest by bytes, near-scarcest by quota.

Within one endpoint the behaviour is clean: exactly 1 per request, and no refill
observed over 45 s of idling — so this is a daily-ish quota, not a throttle.
Across endpoints it is not: requests to different paths decrement in patterns
that only make sense if some buckets are shared, and we could not determine
which. So this module never simulates. It reads the header, remembers what it
said, and acts on that. Anything else would be a guess presented as a number.

Ceilings are *learned* (the largest value ever seen for a key), never configured,
so the table above is evidence in a docstring rather than a constant that rots.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Mapping

from .errors import OnshapeQuotaExhausted

logger = logging.getLogger(__name__)

__all__ = ["QuotaLedger", "BucketState", "bucket_key", "RATE_LIMIT_HEADER"]

RATE_LIMIT_HEADER = "x-rate-limit-remaining"

#: Onshape ids are long hex strings. Anything that looks like one is an
#: identifier, not part of the route, so it collapses to `{id}`.
_ID_SEGMENT = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)

#: Strip the `/api/vNN` prefix so a version bump does not orphan every bucket.
_API_PREFIX = re.compile(r"^/api/v\d+", re.IGNORECASE)

#: Refuse below this many remaining, so a retry storm cannot spend the last of a
#: shared budget. Small on purpose: this is a reserve, not a policy.
DEFAULT_FLOOR = 5

#: Warn (to stderr) below this fraction of a bucket's learned ceiling.
DEFAULT_WARN_FRACTION = 0.20


def bucket_key(method: str, api_path: str) -> str:
    """Collapse a concrete request into the route it belongs to.

    `GET /api/v26/assemblies/d/abc.../w/def.../e/ghi...` becomes
    `GET /assemblies/d/{id}/w/{id}/e/{id}`.

    This is deliberately the same normalisation the timeout table uses — one
    notion of "which endpoint is this", two consumers.
    """
    path = _API_PREFIX.sub("", api_path or "")
    segments = [
        "{id}" if _ID_SEGMENT.match(seg) else seg
        for seg in path.split("/")
    ]
    return f"{method.upper()} {'/'.join(segments)}"


@dataclass
class BucketState:
    """What the last response told us about one bucket."""

    remaining: int
    #: Largest value ever seen for this key — our best estimate of the ceiling.
    ceiling: int
    #: Monotonic clock, for staleness rather than wall time.
    observed_at: float = field(default_factory=time.monotonic)
    #: Requests we have sent against this key since the process started.
    requests: int = 0

    @property
    def fraction(self) -> float:
        return self.remaining / self.ceiling if self.ceiling else 1.0


class QuotaLedger:
    """Remembers the last reported quota per route, and refuses near empty.

    Not thread-safe and does not need to be: the MCP server is one process
    serving one user over stdio, and all access happens on one event loop.
    """

    def __init__(
        self,
        *,
        floor: int | None = None,
        warn_fraction: float = DEFAULT_WARN_FRACTION,
    ) -> None:
        self.buckets: dict[str, BucketState] = {}
        self.warn_fraction = warn_fraction
        #: Total requests sent since construction. This is what lets tools
        #: report what a call actually cost instead of hardcoding a guess.
        self.requests_used = 0
        self._warned: set[str] = set()

        if floor is None:
            raw = os.environ.get("FRC_QUOTA_FLOOR", "")
            try:
                floor = int(raw) if raw else DEFAULT_FLOOR
            except ValueError:
                logger.warning(
                    "FRC_QUOTA_FLOOR=%r is not an integer; using %d",
                    raw, DEFAULT_FLOOR,
                )
                floor = DEFAULT_FLOOR
        self.floor = max(0, floor)

    # -- reading ---------------------------------------------------------

    def check(self, method: str, api_path: str) -> None:
        """Refuse a request whose bucket is nearly empty.

        Silent when the bucket has never been seen — a first request must always
        be allowed through, since the header is the only way to learn anything.
        """
        state = self.buckets.get(bucket_key(method, api_path))
        if state is None or state.remaining > self.floor:
            return
        key = bucket_key(method, api_path)
        raise OnshapeQuotaExhausted(
            f"Refusing to call {key}: only {state.remaining} request(s) remain "
            f"of about {state.ceiling} for that endpoint, and the reserve is "
            f"{self.floor}."
        )

    def remaining(self, method: str, api_path: str) -> int | None:
        state = self.buckets.get(bucket_key(method, api_path))
        return None if state is None else state.remaining

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Current state, cheapest-bucket-first — the order worth reading."""
        return {
            key: {"remaining": s.remaining, "ceiling": s.ceiling,
                  "requests": s.requests}
            for key, s in sorted(
                self.buckets.items(), key=lambda kv: kv[1].fraction
            )
        }

    # -- writing ---------------------------------------------------------

    def record(
        self, method: str, api_path: str, headers: Mapping[str, str]
    ) -> None:
        """Absorb one response's quota header.

        Called for every response including error responses — verified live that
        4xx still carries the header, which is exactly when the number matters
        most.
        """
        self.requests_used += 1
        key = bucket_key(method, api_path)

        raw = headers.get(RATE_LIMIT_HEADER) or headers.get(
            RATE_LIMIT_HEADER.title()
        )
        if raw is None:
            # Not every response is guaranteed to carry it. Silence beats a
            # fabricated number: an absent header means we learn nothing, not
            # that the budget is full.
            logger.debug("no %s on %s", RATE_LIMIT_HEADER, key)
            return
        try:
            remaining = int(raw)
        except (TypeError, ValueError):
            logger.debug("unparseable %s=%r on %s", RATE_LIMIT_HEADER, raw, key)
            return

        previous = self.buckets.get(key)
        state = BucketState(
            remaining=remaining,
            ceiling=max(remaining, previous.ceiling if previous else remaining),
            requests=(previous.requests if previous else 0) + 1,
        )
        self.buckets[key] = state
        self._maybe_warn(key, state)

    def _maybe_warn(self, key: str, state: BucketState) -> None:
        """Warn once per bucket per crossing, not once per request.

        A warning repeated on every call is noise a student learns to ignore.
        """
        low = state.fraction < self.warn_fraction
        if low and key not in self._warned:
            self._warned.add(key)
            logger.warning(
                "quota low: %s has %d of about %d left (refills at least daily)",
                key, state.remaining, state.ceiling,
            )
        elif not low:
            self._warned.discard(key)
