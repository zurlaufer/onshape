"""Which documents may be written to — enforced where every write passes.

WHY THIS MOVED. The allowlist used to live in `src/skills/apply.py`, which is
the polite door. In the first real design session the model went through the
window: it used `OnshapeApiClient` directly and issued a raw

    DELETE /partstudios/d/{did}/w/{wid}/e/{eid}/features/featureid/{fid}

which returned **200**. It did call the policy first, in its own script, of its
own accord. That is courtesy, not enforcement — nothing would have stopped the
same call aimed at a team's robot.

So the check sits in `OnshapeApiClient.request()` now. Every mutating verb, one
place, and the document id is parsed out of the URL path rather than passed in,
because a caller that has to remember to declare the document is a caller that
can forget.

The allowlist itself comes from `robot.toml` (`writable = true`), so a mentor
can read it. The old `FRC_SCRATCH_DOCUMENT_IDS` was a comma-separated string of
hex that nobody could audit, and it is still honoured as an override for
environments with no manifest.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from .errors import OnshapeError

logger = logging.getLogger(__name__)

__all__ = ["WritePolicy", "WriteRefused", "MUTATING_METHODS", "document_id_in"]

#: Verbs that can change something. GET and HEAD are always allowed.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: `/d/{documentId}/` appears in every document-scoped Onshape path.
_DOCUMENT_IN_PATH = re.compile(r"/d(?:ocuments)?/([0-9a-f]{16,})", re.IGNORECASE)


class WriteRefused(OnshapeError, PermissionError):
    """A write was aimed at a document that is not marked writable.

    Subclasses `PermissionError` as well as `OnshapeError` so existing
    `except PermissionError` handlers keep working.
    """

    advice = (
        "Mark the document `writable = true` in robot.toml if that is really "
        "intended. Never do that for a team's robot."
    )


def document_id_in(path: str) -> str | None:
    """The document a request targets, read from its own URL.

    Both `/documents/{id}` and `/d/{id}` spellings appear in Onshape's API.
    """
    match = _DOCUMENT_IN_PATH.search(path or "")
    return match.group(1) if match else None


@dataclass
class WritePolicy:
    """The set of documents that may be written to."""

    allowed: frozenset[str] = field(default_factory=frozenset)
    #: Set False only in tests that must exercise the transport itself.
    enforce: bool = True

    @classmethod
    def from_manifest(cls, workspace=None) -> "WritePolicy":
        """Build from `robot.toml`, falling back to the environment variable.

        Imported lazily so `src.api` keeps no dependency on `src.context`; the
        client must remain usable on its own.
        """
        if workspace is None:
            try:
                from src.context.workspace import Workspace

                workspace = Workspace.load()
            except Exception:  # noqa: BLE001 - a bad manifest must not block reads
                logger.exception("could not load robot.toml; writes will be refused")
                workspace = None

        allowed = set(getattr(workspace, "writable_documents", ()) or ())

        override = os.environ.get("FRC_SCRATCH_DOCUMENT_IDS", "")
        extra = {piece.strip() for piece in override.split(",") if piece.strip()}
        if extra:
            logger.info("FRC_SCRATCH_DOCUMENT_IDS adds %d document(s)", len(extra))
        return cls(allowed=frozenset(allowed | extra))

    def check(self, method: str, path: str) -> None:
        """Refuse a mutating request outside the allowlist.

        A path with no document id is allowed: it cannot modify a document.
        """
        if not self.enforce or method.upper() not in MUTATING_METHODS:
            return

        document = document_id_in(path)
        if document is None or document in self.allowed:
            return

        listed = ", ".join(sorted(self.allowed)) or "(none)"
        raise WriteRefused(
            f"Refusing {method.upper()} on document {document}: it is not marked "
            f"writable. Writable documents: {listed}."
        )
