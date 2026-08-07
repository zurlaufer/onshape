"""Onshape-side validation and application of a template.

Layer 5 — **probe**. Evaluate the template body as a bare lambda through
`POST /partstudios/.../featurescript`. Measured 2026-08-07, this returns a
`notices[]` array with real diagnostics: PARSE errors with line and column,
SEMANTIC errors naming an unknown function, and EXECUTION errors carrying the
thrown message — including "query matched nothing", the failure class spike 3b
warned about. It persists nothing.

Layer 6 — **apply**. Write the full module to a Feature Studio, confirm it
compiled via the `/featurespecs` count, and optionally insert it. This path is
silent about *why* anything failed (spike 3b), which is exactly why layer 5 runs
first and does the diagnosing.

Note: the evaluate endpoint returns **HTTP 200 even on failure**. The errors are
in `notices`. Status codes must never be used as the signal here.

WRITE SAFETY (FM2): every write refuses any document not on an explicit scratch
allowlist. A copilot that can write into a team's robot the week before a
competition is an incident waiting to happen, so the boundary is enforced in
code rather than documented as intent.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from src.api.onshape_client import OnshapeApiClient

from .templates import Problem, Severity

logger = logging.getLogger(__name__)

__all__ = [
    "ProbeResult",
    "ScratchPolicy",
    "WriteRefused",
    "probe_body",
    "apply_module",
]

LIBRARY_VERSION = 3029


class WriteRefused(PermissionError):
    """A write was aimed at a document outside the scratch allowlist."""


@dataclass
class ScratchPolicy:
    """Which documents may be written to.

    Defaults to the project's scratch document. Override with the
    `FRC_SCRATCH_DOCUMENT_IDS` environment variable (comma-separated) — never by
    widening this in code, so the allowlist stays visible in configuration.
    """

    allowed: frozenset[str] = field(
        default_factory=lambda: frozenset(
            filter(None, (
                os.environ.get("FRC_SCRATCH_DOCUMENT_IDS")
                or "e424515893d4f1ab8b23e5c4"  # asm-try1
            ).split(","))
        )
    )

    def check(self, document_id: str) -> None:
        if document_id not in self.allowed:
            raise WriteRefused(
                f"Refusing to write to document {document_id}. Writes are limited "
                f"to scratch documents. Set FRC_SCRATCH_DOCUMENT_IDS to allow "
                f"another, and never point this at a team's robot document."
            )


@dataclass
class ProbeResult:
    """Outcome of evaluating a body against Onshape."""

    ok: bool
    problems: list[Problem]
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "problems": [p.to_dict() for p in self.problems],
        }
        if self.ok and self.value is not None:
            out["result"] = self.value
        return out


def _notice_to_problem(notice: dict[str, Any]) -> Problem:
    """Translate one Onshape notice into our diagnostic type.

    Two message shapes exist. Most notices carry a plain `message`; unknown-symbol
    notices instead carry `expressionErrorInfo.errorMessageIdentifier` plus
    `messageArguments`, so those are reassembled into readable text.
    """
    message = (notice.get("message") or "").strip()
    info = notice.get("expressionErrorInfo") or {}
    identifier = info.get("errorMessageIdentifier")

    if not message and identifier:
        args = [
            a.get("value", {}).get("value")
            for a in (info.get("messageArguments") or [])
        ]
        named = [str(a) for a in args if isinstance(a, str)]
        if identifier == "PARAMETER_EXPRESSION_UNKNOWN_FUNCTION" and named:
            message = f"Unknown function {named[0]!r}."
        else:
            readable = identifier.replace("_", " ").lower()
            message = f"{readable}" + (f" ({', '.join(named)})" if named else "")

    location = (notice.get("stackTrace") or [{}])[0]
    line = location.get("line") or None
    column = location.get("column") or None

    kind = notice.get("type") or "ERROR"
    severity = (
        Severity.WARNING
        if str(notice.get("level", "")).upper() == "WARNING"
        else Severity.ERROR
    )

    suggestion = {
        "PARSE": "A syntax error. Check the position reported above.",
        "SEMANTIC": "The name or type is wrong. Check it against the standard library.",
        "EXECUTION": "The script ran but failed. Often a query matched nothing.",
    }.get(str(kind).upper())

    return Problem(
        severity=severity,
        message=f"[{kind}] {message}" if message else f"[{kind}] Onshape reported an error.",
        suggestion=suggestion,
        line=line,
        column=column,
    )


async def probe_body(
    client: OnshapeApiClient,
    body: str,
    *,
    document_id: str,
    workspace_id: str,
    part_studio_id: str,
) -> ProbeResult:
    """Layer 5: evaluate a bare lambda and translate Onshape's diagnostics.

    Read-only — nothing is persisted, so this deliberately does not consult the
    scratch allowlist. Costs one request.
    """
    response = await client.post(
        f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}/featurescript",
        json_body={"script": body, "libraryVersion": LIBRARY_VERSION},
    )
    payload = response.json()

    notices = payload.get("notices") or []
    problems = [_notice_to_problem(n) for n in notices]
    ok = not any(p.is_error for p in problems)

    value = None
    if ok:
        result = payload.get("result") or {}
        value = result.get("value")

    logger.debug("probe: ok=%s notices=%d", ok, len(notices))
    return ProbeResult(ok=ok, problems=problems, value=value)


async def apply_module(
    client: OnshapeApiClient,
    module: str,
    *,
    document_id: str,
    workspace_id: str,
    feature_studio_id: str,
    policy: ScratchPolicy | None = None,
    restore: bool = True,
) -> tuple[bool, list[Problem]]:
    """Layer 6: write the module to a Feature Studio and confirm it compiled.

    The compile signal is the `/featurespecs` count, because the write itself
    returns 200 regardless (spike 3b). With `restore=True` the original source is
    put back in a `finally`, which is what makes this safe to run as a check.

    Returns (compiled, problems). Costs 3-4 requests.
    """
    (policy or ScratchPolicy()).check(document_id)

    base = f"/featurestudios/d/{document_id}/w/{workspace_id}/e/{feature_studio_id}"
    original = (await client.get(base)).json()
    previous_source = original.get("contents", "")

    async def write(source: str) -> None:
        current = (await client.get(base)).json()
        await client.post(base, json_body={
            "contents": source,
            "serializationVersion": current.get("serializationVersion"),
            "sourceMicroversion": current.get("sourceMicroversion"),
        })

    try:
        await write(module)
        specs = (await client.get(f"{base}/featurespecs")).json()
        count = len(specs.get("featureSpecs") or [])
        if count:
            logger.info("module compiled: %d feature spec(s)", count)
            return True, []
        return False, [Problem(
            Severity.ERROR,
            "Onshape accepted the write but defined no feature, so the module "
            "did not compile. This path reports no reason - run the probe "
            "(layer 5) for a diagnostic with a line number.",
            suggestion="Call the probe on the template body first.")]
    finally:
        if restore and previous_source:
            try:
                await write(previous_source)
            except Exception:  # noqa: BLE001 - never mask the original outcome
                logger.exception("could not restore the Feature Studio source")
