"""Remember what each FeatureScript body did, so we do not ask twice.

THE BUDGET THIS PROTECTS. Onshape meters per endpoint, and the FeatureScript
evaluate bucket is roughly **100 a day, shared by everyone on one key** — the
scarcest thing this project calls, and the one a correction loop hammers
hardest. Making the probe a first-class tool makes that worse before it makes it
better.

Iteration re-probes near-identical source constantly: change one number, run
again, change it back. Identical source gives an identical answer, so keying on
a hash of the body turns the second ask into no request at all.

TWO ARTIFACTS, DELIBERATELY DIFFERENT

    .cache/probe/     hash -> result. Ephemeral, gitignored, never read by a
                      model. Everything goes here, including failures.
    featurescript/    worked examples, committed, **grepped and never loaded
                      whole**. A curated slice, promoted by hand.

The token budget dictates that split. One example is ~500 tokens, which is
affordable; twenty is not. So `CLAUDE.md` tells a session to *grep* the corpus,
and `promote()` is what puts something there worth finding.

Failures earn their place in the corpus as much as successes. `box` being a
reserved word was hit twice — by this project and again by a later session that
had read the fact in STATUS.md and not retained it. One worked example with the
diagnostic attached would have prevented the second.

STALENESS. The key includes the FeatureScript library version, so a version bump
invalidates everything rather than serving an answer from a different language.
Geometry is the one thing it cannot see: the same body probed against a
different Part Studio can legitimately differ, so the target is part of the key
too.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ProbeCache", "DEFAULT_CACHE_DIR", "CORPUS_DIR", "body_digest"]

DEFAULT_CACHE_DIR = Path(".cache/probe")
CORPUS_DIR = Path("featurescript")

#: Entries older than this are re-probed. Onshape's standard library and the
#: document's geometry both move underneath us eventually, and a stale "this
#: compiles" is a worse answer than spending one request.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def body_digest(body: str, *, target: str, library_version: int) -> str:
    """A stable key for one body evaluated against one target.

    Whitespace is *not* normalised: FeatureScript reports errors by line and
    column, so two bodies differing only in layout have genuinely different
    diagnostics and must not share an entry.
    """
    material = f"{library_version}\n{target}\n{body}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class ProbeCache:
    """Hash-keyed probe results on disk.

    Not thread-safe and does not need to be: one process, one event loop.
    """

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = max(0.0, ttl_seconds)
        #: Requests this cache has avoided. The number that justifies it.
        self.hits = 0
        self.misses = 0

    def _path_for(self, digest: str) -> Path:
        return self.cache_dir / f"{_UNSAFE.sub('', digest)}.json"

    def get(self, digest: str) -> dict[str, Any] | None:
        """A previous result for this exact body, or None."""
        if not self.ttl_seconds:
            return None
        path = self._path_for(digest)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        age = time.time() - float(entry.get("stored_at", 0.0))
        if age > self.ttl_seconds:
            logger.debug("probe cache entry expired (%.0f days)", age / 86400)
            path.unlink(missing_ok=True)
            return None

        self.hits += 1
        logger.info("probe cache hit - one request not spent (%d saved)", self.hits)
        return entry.get("result")

    def put(self, digest: str, result: dict[str, Any]) -> None:
        """Record a result. Failures are cached too — re-asking Onshape the same
        broken question spends the same scarce request for the same answer."""
        self.misses += 1
        entry = {"stored_at": time.time(), "result": result}
        target = self._path_for(digest)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            # A cache that cannot write is slow, not broken.
            logger.warning("could not write probe cache entry: %s", exc)

    # -- the corpus ------------------------------------------------------

    @staticmethod
    def promote(
        name: str,
        body: str,
        *,
        note: str,
        ok: bool,
        diagnostics: str = "",
        corpus_dir: Path | str = CORPUS_DIR,
    ) -> Path:
        """Save a body as a worked example, for grepping later.

        Committed, unlike the cache, and written as FeatureScript with the
        explanation in a comment header so that a `grep` for an operation lands
        on something readable rather than on JSON.
        """
        directory = Path(corpus_dir) / ("verified" if ok else "failures")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_UNSAFE.sub('_', name)}.fs"

        header = [f"// {note}"]
        if diagnostics:
            header += [f"// Onshape said: {line}"
                       for line in diagnostics.splitlines() if line.strip()]
        header.append("// Verified against Onshape." if ok
                      else "// KEPT AS A FAILURE. Do not copy this shape.")
        path.write_text("\n".join(header) + "\n\n" + body.rstrip() + "\n",
                        encoding="utf-8")
        logger.info("corpus: wrote %s", path)
        return path
