"""Microversion-keyed disk cache for assembly definitions.

The idea is borrowed from git, rsync and HTTP ETags: never move the big payload
when a cheap content hash can tell you nothing changed. Onshape hands us exactly
that primitive — microversions are immutable and directly addressable.

Measured on the real Citrus robot, 2026-08-05:

    GET /assemblies/d/{did}/w/{wid}/e/{eid}        1,088,965 bytes
    GET /documents/d/{did}/w/{wid}/currentmicroversion      49 bytes

A 22,000:1 validator, and the reason a warm assembly question is one tiny
request instead of 1.09 MB.

Disk rather than memory because the MCP server is a subprocess of Claude Code
and restarts often; an in-memory cache would re-pay the 1.09 MB every restart.

THE VALIDATOR IS THE SCARCE ONE (measured 2026-08-07)
-----------------------------------------------------
Onshape meters per endpoint, not globally, and the buckets differ by an order of
magnitude. `currentmicroversion` sits on a **400** bucket while the 1.09 MB
assembly fetch it protects sits on **1000**. So the call we made cheap in bytes
is the one we are poorest in — 400 assembly questions per window would exhaust
the context layer while the expensive endpoint still had headroom.

Hence the TTL below. Inside one conversation a student asks several questions
about the same assembly in quick succession; without a TTL each spends a slot
from the tightest budget we have, to re-learn something that almost certainly
did not change in the last few seconds.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from src.api.onshape_client import OnshapeApiClient

logger = logging.getLogger(__name__)

__all__ = ["AssemblyCache", "DEFAULT_CACHE_DIR", "DEFAULT_MICROVERSION_TTL"]

DEFAULT_CACHE_DIR = Path(".cache/onshape")

#: Seconds a microversion reading stays trusted without re-checking.
#:
#: The trade is explicit: at worst we answer from an assembly edited within the
#: last minute. Against that, a hard 400/window ceiling that previously had no
#: guard at all. Sixty seconds is short enough that a student who edits and then
#: asks will normally still see the change, and `force_refresh` always bypasses
#: it for the case where they need certainty.
DEFAULT_MICROVERSION_TTL = 60.0

# Onshape ids are hex; anything else has no business becoming a filename.
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def _safe(value: str) -> str:
    return _SAFE_ID.sub("", value or "")[:64]


class AssemblyCache:
    """Caches raw assembly definitions on disk, keyed by microversion.

    The cache holds real document content, so its directory is gitignored.
    """

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        *,
        microversion_ttl: float = DEFAULT_MICROVERSION_TTL,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.microversion_ttl = max(0.0, microversion_ttl)
        #: Requests issued since construction, split by kind. The whole point of
        #: this class is reducing the second number, so it is worth counting.
        self.validator_requests = 0
        self.definition_requests = 0
        #: Requests the TTL avoided — the number that justifies it existing.
        self.validator_requests_saved = 0
        #: (did, wid) -> (microversion, monotonic time it was read)
        self._microversion_seen: dict[tuple[str, str], tuple[str, float]] = {}

    def _path_for(self, did: str, eid: str, microversion: str) -> Path:
        return self.cache_dir / f"{_safe(did)}_{_safe(eid)}_{_safe(microversion)}.json"

    async def current_microversion(
        self,
        client: OnshapeApiClient,
        did: str,
        wid: str,
        *,
        force_refresh: bool = False,
    ) -> str:
        """The document's current microversion — a 49-byte staleness check.

        Note this is per *document*, not per element, so editing any tab
        invalidates the whole document's cache. That is conservative on purpose:
        an occasional wasted refetch is much cheaper than serving stale geometry.

        Reuses a reading younger than `microversion_ttl` — see the module
        docstring for why this endpoint's quota is the one worth protecting.
        """
        key = (did, wid)
        if not force_refresh and self.microversion_ttl:
            seen = self._microversion_seen.get(key)
            if seen and (time.monotonic() - seen[1]) < self.microversion_ttl:
                self.validator_requests_saved += 1
                logger.debug("microversion within TTL, not re-checking %s", did)
                return seen[0]

        self.validator_requests += 1
        response = await client.get(f"/documents/d/{did}/w/{wid}/currentmicroversion")
        microversion = (response.json() or {}).get("microversion", "")
        if microversion:
            self._microversion_seen[key] = (microversion, time.monotonic())
        return microversion

    async def get_definition(
        self,
        client: OnshapeApiClient,
        did: str,
        wid: str,
        eid: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return the assembly definition, fetching only if it changed.

        Cold: two requests (validator + definition). Warm: one, the validator —
        or zero, when a previous call read the microversion within the TTL.
        """
        microversion = await self.current_microversion(
            client, did, wid, force_refresh=force_refresh
        )
        cached = self._path_for(did, eid, microversion)

        if not force_refresh and microversion and cached.is_file():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                logger.debug("assembly cache hit %s", cached.name)
                return data
            except (OSError, json.JSONDecodeError):
                # A corrupt or half-written file must never be fatal — the
                # authoritative copy is one request away.
                logger.warning("discarding unreadable cache entry %s", cached.name)
                cached.unlink(missing_ok=True)

        logger.info("fetching assembly definition %s/%s (microversion %s)",
                    did, eid, microversion or "unknown")
        self.definition_requests += 1
        response = await client.get(f"/assemblies/d/{did}/w/{wid}/e/{eid}")
        data = response.json()

        if microversion:
            self._write(cached, response.text)
            self._prune(did, eid, keep=cached.name)
        return data

    def _write(self, target: Path, text: str) -> None:
        """Write atomically so a crash cannot leave a torn cache entry."""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            # A cache that cannot write is slow, not broken.
            logger.warning("could not write cache entry %s: %s", target.name, exc)

    def _prune(self, did: str, eid: str, *, keep: str) -> None:
        """Drop superseded microversions for this element."""
        pattern = f"{_safe(did)}_{_safe(eid)}_*.json"
        try:
            for old in self.cache_dir.glob(pattern):
                if old.name != keep:
                    old.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("prune skipped: %s", exc)
