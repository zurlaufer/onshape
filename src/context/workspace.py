"""Resolve "where" — a name, a URL, or raw ids — into one Onshape target.

WHY THIS EXISTS. Every tool used to take `document_id`, `workspace_id` and
`element_id`: three opaque hex strings, three parameter descriptions costing
tokens on every request, and three chances to get it wrong. In the first real
session the model pasted a Part Studio's element id into an assembly tool and
got a 400 — the only API error of the session, and entirely avoidable.

One `where` replaces all three, and the manifest records each target's element
*type*, so a Part Studio can never reach an assembly tool in the first place.

THREE INPUT FORMS, deliberately:

    "Intake"                          a name from robot.toml
    "https://cad.onshape.com/..."     a pasted URL, for anything not listed
    "d/w/e"                           raw ids, for scripts and tests

The manifest is also the **write boundary**. `FRC_SCRATCH_DOCUMENT_IDS` was a
comma-separated string of hex that nobody could audit; `writable = true` beside
a readable name can be checked at a glance by a mentor. That matters: a raw
DELETE returned 200 in the first real session, guarded only by the model
voluntarily checking a policy it could have skipped.

Named lookup also sidesteps a trap. `GET /documents?q=` searches **all public
Onshape**, not your documents — `q=asm` returns strangers' files unless scoped
with `filter=0`, and `filter=0` means *owned*, which a student with a shared
team document is not. Written-down ids need no search at all.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

__all__ = [
    "Target",
    "Workspace",
    "WorkspaceError",
    "UnknownTarget",
    "parse_onshape_url",
    "DEFAULT_MANIFEST",
]

DEFAULT_MANIFEST = Path("robot.toml")

#: Onshape ids are long hex strings.
_ID = r"[0-9a-f]{16,}"

#: `/documents/{did}/w|v|m/{id}[/e/{eid}]`, tolerant of trailing paths and
#: query strings. Onshape also writes `/documents/d/{did}/...` in API-style
#: links, so both spellings are accepted.
_URL = re.compile(
    rf"/documents/(?:d/)?(?P<did>{_ID})"
    rf"(?:/(?P<wvm>[wvm])/(?P<wvmid>{_ID}))?"
    rf"(?:/e/(?P<eid>{_ID}))?",
    re.IGNORECASE,
)

#: Bare `did/wid/eid`, for scripts and tests.
_TRIPLE = re.compile(rf"^(?P<did>{_ID})/(?P<wvmid>{_ID})(?:/(?P<eid>{_ID}))?$",
                     re.IGNORECASE)


class WorkspaceError(RuntimeError):
    """The manifest is missing or malformed."""


class UnknownTarget(WorkspaceError):
    """`where` matched no name, URL or id triple."""


@dataclass(frozen=True)
class Target:
    """One addressable place in Onshape."""

    document_id: str
    #: Workspace, version or microversion id — whichever `wvm` selects.
    wvm_id: str
    #: "w" (workspace), "v" (version) or "m" (microversion).
    wvm: str = "w"
    element_id: str | None = None
    #: "partstudio", "assembly", "featurestudio", or None when unknown.
    kind: str | None = None
    name: str | None = None
    writable: bool = False

    @property
    def path(self) -> str:
        """The `/d/{}/w/{}/e/{}` fragment every Onshape URL is built from."""
        out = f"/d/{self.document_id}/{self.wvm}/{self.wvm_id}"
        if self.element_id:
            out += f"/e/{self.element_id}"
        return out

    def describe(self) -> str:
        label = self.name or self.document_id[:8]
        kind = f" ({self.kind})" if self.kind else ""
        return f"{label}{kind}{'' if self.writable else ' [read-only]'}"


def parse_onshape_url(text: str) -> Target | None:
    """Pull a target out of a pasted Onshape URL, or return None.

    Handles workspace, version and microversion links, a missing element, query
    strings, and enterprise hosts (`https://company.onshape.com/...`) — the host
    is not checked, only the path shape, because enterprise domains vary.
    """
    if not text:
        return None
    candidate = text.strip()
    if "://" in candidate:
        candidate = urlparse(candidate).path
    match = _URL.search(candidate)
    if not match:
        return None
    return Target(
        document_id=match.group("did"),
        wvm_id=match.group("wvmid") or "",
        wvm=(match.group("wvm") or "w").lower(),
        element_id=match.group("eid"),
    )


class Workspace:
    """The documents this copilot knows about, loaded from `robot.toml`."""

    def __init__(self, targets: list[Target]) -> None:
        self.targets = targets
        self._by_name = {t.name.strip().lower(): t for t in targets if t.name}

        # A document is writable or it is not; two entries disagreeing is a
        # configuration mistake, and the safe reading of a mistake is unclear.
        # Refuse at load rather than pick one silently.
        flags: dict[str, bool] = {}
        for target in targets:
            previous = flags.setdefault(target.document_id, target.writable)
            if previous != target.writable:
                raise WorkspaceError(
                    f"Document {target.document_id} is marked both writable and "
                    f"read-only in the manifest. Fix it — a write boundary that "
                    f"depends on which entry was read is not a boundary."
                )
        self.writable_documents = frozenset(
            did for did, ok in flags.items() if ok
        )

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Workspace":
        """Read the manifest. An absent one is empty, not an error.

        Without a manifest the tools still work with URLs and raw ids; only
        names and the write allowlist are unavailable. That degradation is on
        purpose — a fresh clone should not fail to start.
        """
        location = Path(path or os.environ.get("FRC_ROBOT_MANIFEST")
                        or DEFAULT_MANIFEST)
        if not location.is_file():
            logger.info("no manifest at %s; names unavailable", location)
            return cls([])

        try:
            raw = tomllib.loads(location.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise WorkspaceError(f"Could not read {location}: {exc}") from exc

        targets: list[Target] = []
        for entry in raw.get("target", []):
            name = entry.get("name")
            url = entry.get("url", "")
            parsed = parse_onshape_url(url)
            if parsed is None or not parsed.wvm_id:
                raise WorkspaceError(
                    f"Target {name!r} in {location} has no usable Onshape URL. "
                    f"Copy it from the browser address bar while the tab is open."
                )
            targets.append(Target(
                document_id=parsed.document_id,
                wvm_id=parsed.wvm_id,
                wvm=parsed.wvm,
                element_id=parsed.element_id,
                kind=(entry.get("kind") or None),
                name=name,
                writable=bool(entry.get("writable", False)),
            ))
        logger.info("manifest: %d target(s), %d writable document(s)",
                    len(targets), len({t.document_id for t in targets if t.writable}))
        return cls(targets)

    # -- resolving -------------------------------------------------------

    def resolve(self, where: str, *, expect: str | None = None) -> Target:
        """Turn `where` into a target: a manifest name, a URL, or `d/w/e`.

        Args:
            expect: Element kind the caller needs, e.g. "assembly". Checked
                only when the manifest records a kind — a URL alone does not
                say what it points at.
        """
        if not where or not where.strip():
            raise UnknownTarget(
                f"No target given. {self._suggestions()}")

        text = where.strip()
        target = self._by_name.get(text.lower())

        if target is None:
            triple = _TRIPLE.match(text)
            if triple:
                target = Target(
                    document_id=triple.group("did"),
                    wvm_id=triple.group("wvmid"),
                    element_id=triple.group("eid"),
                    writable=triple.group("did") in self.writable_documents,
                )
            else:
                parsed = parse_onshape_url(text)
                if parsed is None or not parsed.wvm_id:
                    raise UnknownTarget(
                        f"Cannot resolve {where!r}. Give a name, an Onshape URL, "
                        f"or document/workspace/element ids. {self._suggestions()}")
                # A pasted URL inherits the manifest's writability, so pasting
                # a link is never a way around the boundary.
                target = Target(
                    document_id=parsed.document_id,
                    wvm_id=parsed.wvm_id,
                    wvm=parsed.wvm,
                    element_id=parsed.element_id,
                    writable=parsed.document_id in self.writable_documents,
                )

        if expect and target.kind and target.kind != expect:
            article = "an" if expect[0] in "aeiou" else "a"
            raise UnknownTarget(
                f"{target.describe()} is a {target.kind}, but this needs "
                f"{article} {expect}. In Onshape every tab has its own URL, so "
                f"open the {expect} tab and copy the address bar again."
            )
        return target

    def _suggestions(self) -> str:
        if not self._by_name:
            return "No robot.toml was found, so no names are available."
        return "Known names: " + ", ".join(
            sorted(t.name for t in self.targets if t.name))

    def summary(self) -> list[dict[str, object]]:
        """Compact listing for `onshape_check_connection`.

        Carries the ids, not just the names, so a pasted URL can be matched to
        an entry. Without them a workspace session could not tell whether a
        link was writable, and guessed "robot parts" (2026-11-26). Writability
        is per DOCUMENT, so any tab of a writable document is writable, listed
        or not.
        """
        return [
            {"name": t.name, "kind": t.kind, "writable": t.writable,
             "document_id": t.document_id, "element_id": t.element_id}
            for t in self.targets if t.name
        ]
