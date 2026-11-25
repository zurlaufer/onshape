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

WRITE SAFETY (FM2) is no longer enforced here. It moved into
`OnshapeApiClient.request()`, because a guard that a caller has to remember to
invoke is one that eventually is not invoked -- proven when a real session used
the client directly and issued a DELETE that returned 200. `robot.toml` decides
which documents are writable; this module just writes.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.api.onshape_client import OnshapeApiClient

# Re-exported: the allowlist used to live here, but a guard in the polite door
# is not a guard. It moved into `OnshapeApiClient.request()`, where every write
# passes whether or not the caller remembers to ask. Keeping the name importable
# from here means nothing that used it had to change; keeping two *classes*
# called WriteRefused would have meant `except WriteRefused` silently missing
# the one actually raised.
from src.api.write_policy import WritePolicy, WriteRefused

from .probe_cache import ProbeCache, body_digest
from .symbols import observe_compiled, observe_rejected
from .templates import Problem, Severity

logger = logging.getLogger(__name__)

__all__ = [
    "PersistResult",
    "ProbeCache",
    "ProbeResult",
    "WritePolicy",
    "WriteRefused",
    "COPILOT_STUDIO_NAME",
    "PREVIEW_STUDIO_NAME",
    "studio_name_for",
    "decode_fsvalue",
    "ensure_feature_studio",
    "probe_body",
    "apply_module",
    "persist_feature",
]

LIBRARY_VERSION = 3029

#: Where generated modules live: ONE STUDIO PER FEATURE, named after it, so a
#: student can see which tab holds which feature's source.
#:
#: It used to be one studio per document, and that broke the second feature
#: ever applied (measured 2026-11-25, workspace trial). Writing a module
#: replaces the studio's whole source, so applying "Pocket Grid" erased the
#: code "Plate" had been inserted from. The plate went to ERROR and its body
#: vanished. Separate studios make features independent; re-applying the same
#: feature name rewrites only its own studio, and the feature is repointed.
COPILOT_STUDIO_NAME = "FRC Copilot"

#: The studio a dry run compiles in and then restores. Nothing is ever inserted
#: from it. Restoring a studio strands any feature inserted from it, so
#: previews must never share a studio with a real feature.
PREVIEW_STUDIO_NAME = f"{COPILOT_STUDIO_NAME} - preview"


def studio_name_for(feature_name: str) -> str:
    """The Feature Studio that holds one feature's source."""
    name = f"{COPILOT_STUDIO_NAME} - {feature_name.strip()}"
    if name == PREVIEW_STUDIO_NAME:
        raise ValueError("'preview' is reserved for dry runs; name the feature "
                         "something else")
    return name


async def ensure_feature_studio(
    client: OnshapeApiClient,
    *,
    document_id: str,
    workspace_id: str,
    name: str = COPILOT_STUDIO_NAME,
) -> str:
    """The element id of a Feature Studio with this name, creating it if absent.

    Lets callers name a Part Studio and nothing else: the studio that holds the
    generated source is an implementation detail, and asking a student for a
    second element id is asking for the two to be mismatched.
    """
    base = f"/documents/d/{document_id}/w/{workspace_id}/elements"
    for element in (await client.get(base)).json():
        if element.get("name") == name and element.get("elementType") == "FEATURESTUDIO":
            return element["id"]

    created = (await client.post(
        f"/featurestudios/d/{document_id}/w/{workspace_id}",
        json_body={"name": name},
    )).json()
    logger.info("created Feature Studio %r: %s", name, created.get("id"))
    return created["id"]


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


def decode_fsvalue(node: Any) -> Any:
    """Onshape's FSValue wire format into plain Python.

    A lambda returning `{ "pocketU_mm" : 36.7 }` comes back as roughly 400 bytes
    of `btType` wrappers and nested `value` keys. Handing that to a model is
    payload spent on noise, and reading it by hand is how three separate
    debugging sessions started.

    Maps arrive as a list of entries carrying `key` and `value`, so a list whose
    every element decodes to a pair becomes a dict; anything else stays a list.
    """
    if isinstance(node, list):
        items = [decode_fsvalue(n) for n in node]
        if items and all(isinstance(i, tuple) and len(i) == 2 for i in items):
            return dict(items)
        return items
    if not isinstance(node, dict):
        return node
    if "key" in node and "value" in node:
        return (decode_fsvalue(node["key"]), decode_fsvalue(node["value"]))
    inner = node.get("value")
    return decode_fsvalue(inner) if isinstance(inner, (list, dict)) else inner


#: Identifiers in call position — the same crude pattern `validate.py` uses.
#: Good enough to feed an observation, which is all it does.
_CALLED = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")


async def probe_body(
    client: OnshapeApiClient,
    body: str,
    *,
    document_id: str,
    workspace_id: str,
    part_studio_id: str,
    cache: ProbeCache | None = None,
) -> ProbeResult:
    """Layer 5: evaluate a bare lambda and translate Onshape's diagnostics.

    Read-only — nothing is persisted, so no write check applies. Costs one
    request from the scarcest bucket we have (~100/day, shared), which is why a
    cache is consulted first and why every answer teaches `symbols.py`
    something.
    """
    digest = body_digest(
        body, target=f"{document_id}/{workspace_id}/{part_studio_id}",
        library_version=LIBRARY_VERSION)

    if cache is not None:
        cached = cache.get(digest)
        if cached is not None:
            return ProbeResult(
                ok=cached["ok"],
                problems=[Problem(**{**p, "severity": Severity(p["severity"])})
                          for p in cached.get("problems", [])],
                value=cached.get("value"),
            )

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
        value = decode_fsvalue(result.get("value"))

    # Every answer is evidence about the language, and it cost a scarce request
    # either way. A body that compiled proves its names exist; a rejection names
    # one that does not.
    if ok:
        observe_compiled(set(_CALLED.findall(body)))
    for problem in problems:
        match = re.search(r"Unknown function '([^']+)'", problem.message)
        if match:
            observe_rejected(match.group(1))

    if cache is not None:
        cache.put(digest, {
            "ok": ok, "value": value,
            "problems": [{**p.to_dict(), "severity": p.severity.value}
                         for p in problems],
        })

    logger.debug("probe: ok=%s notices=%d", ok, len(notices))
    return ProbeResult(ok=ok, problems=problems, value=value)


async def apply_module(
    client: OnshapeApiClient,
    module: str,
    *,
    document_id: str,
    workspace_id: str,
    feature_studio_id: str,
    restore: bool = True,
) -> tuple[bool, list[Problem]]:
    """Layer 6: write the module to a Feature Studio and confirm it compiled.

    The compile signal is the `/featurespecs` count, because the write itself
    returns 200 regardless (spike 3b). With `restore=True` the original source is
    put back in a `finally`, which is what makes this safe to run as a check.

    **Never use `restore=True` once a feature has been inserted from this
    studio.** Restoring moves the microversion the inserted feature is pinned
    to, and strands it in ERROR permanently — `persist_feature` exists because
    of that, and passes `restore=False`.

    Returns (compiled, problems). Costs 3-4 requests. Write safety is enforced
    inside the client, so there is nothing to check here.
    """
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


@dataclass
class PersistResult:
    """Outcome of leaving a feature behind in a Part Studio."""

    ok: bool
    problems: list[Problem] = field(default_factory=list)
    feature_id: str | None = None
    feature_type: str | None = None
    namespace: str | None = None
    #: True when an existing feature was repointed rather than a new one added.
    updated: bool = False
    #: True when a failed insert was removed again.
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "problems": [p.to_dict() for p in self.problems],
        }
        for key in ("feature_id", "feature_type", "namespace"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.updated:
            out["updated_existing"] = True
        if self.rolled_back:
            out["rolled_back"] = True
        return out


async def persist_feature(
    client: OnshapeApiClient,
    module: str,
    *,
    document_id: str,
    workspace_id: str,
    feature_studio_id: str,
    part_studio_id: str,
    feature_name: str,
    parameters: list[dict[str, Any]] | None = None,
) -> PersistResult:
    """Write a module and leave the feature in the Part Studio.

    The difference from `apply_module` is the whole point of this function: the
    source is **not** restored, and the inserted feature is repointed at the
    microversion just written.

    WHY THAT MATTERS, learned by breaking a document. A custom feature is pinned
    to the microversion that defined it. Restoring the Feature Studio moves that
    microversion out from under it and the feature is stranded in ERROR forever
    — that is how a dead `Boom 1` ended up in a scratch Part Studio. Rewriting
    the source moves it too, so an already-inserted feature must be updated with
    the fresh namespace every time.

    Idempotent: run it again after changing the design and it edits the feature
    that is there rather than stacking duplicates.

    ROLLBACK, and its one deliberate asymmetry:

      * An **insert** that fails to regenerate is deleted again. We created it;
        leaving something broken behind is the failure this function exists to
        prevent.
      * An **update** that fails is left in place. It is someone's feature, and
        silently deleting a student's work to tidy up an error is worse than the
        error. The failure is reported instead.

    Costs about 11 requests, measured 2026-11-25. Two of them are
    `GET .../features`, a bucket that reported only ~94 remaining, so this is
    not free to loop. It uses no evaluate requests. Write safety is enforced
    in the client.

    A feature with a REQUIRED query parameter (a face to pocket) cannot go in
    yet: inserted with the selection empty, even a feature whose body does
    nothing regenerates as ERROR and is rolled back (measured 2026-11-25).
    Setting the selection needs `deterministicIds`; see
    scripts/spike_tube_converter_query.py.
    """
    compiled, problems = await apply_module(
        client, module,
        document_id=document_id,
        workspace_id=workspace_id,
        feature_studio_id=feature_studio_id,
        restore=False,  # deliberate: see above
    )
    if not compiled:
        return PersistResult(ok=False, problems=problems)

    specs = (await client.get(
        f"/featurestudios/d/{document_id}/w/{workspace_id}"
        f"/e/{feature_studio_id}/featurespecs")).json()
    spec = (specs.get("featureSpecs") or [{}])[0]
    feature_type = spec.get("featureType")
    namespace = spec.get("namespace")
    if not feature_type or not namespace:
        return PersistResult(ok=False, problems=[Problem(
            Severity.ERROR,
            "The module compiled but Onshape reported no feature type to "
            "insert.",
            suggestion="Check that the module exports a defineFeature.")])

    part_studio = f"/partstudios/d/{document_id}/w/{workspace_id}/e/{part_studio_id}"
    existing = (await client.get(f"{part_studio}/features")).json()
    # Ours only if it comes from THIS studio. The namespace is
    # `e{studio}::m{microversion}`, and the part before `::` names the studio.
    # A feature type alone is not enough: two studios can both define
    # `frcPlate`, and updating the other one would repoint it at code it was
    # never written from.
    studio_prefix = namespace.split("::")[0]
    already = next(
        (f for f in existing.get("features", [])
         if f.get("featureType") == feature_type
         and str(f.get("namespace", "")).split("::")[0] == studio_prefix), None)

    payload: dict[str, Any] = {
        "btType": "BTMFeature-134",
        "namespace": namespace,
        "name": feature_name,
        "featureType": feature_type,
        "suppressed": False,
        "parameters": parameters or [],
        "returnAfterSubfeatures": False,
        "subFeatures": [],
    }

    if already is None:
        created = await client.post(
            f"{part_studio}/features", json_body={"feature": payload})
        feature_id = created.json().get("feature", {}).get("featureId")
        updated = False
    else:
        feature_id = already["featureId"]
        payload["featureId"] = feature_id
        await client.post(f"{part_studio}/features/featureid/{feature_id}",
                          json_body={"feature": payload})
        updated = True

    states = (await client.get(f"{part_studio}/features")).json()
    status = (states.get("featureStates") or {}).get(
        feature_id, {}).get("featureStatus")

    if status == "OK":
        logger.info("feature %s %s in %s", feature_id,
                    "updated" if updated else "inserted", part_studio_id)
        return PersistResult(ok=True, feature_id=feature_id,
                             feature_type=feature_type, namespace=namespace,
                             updated=updated)

    trouble = [Problem(
        Severity.ERROR,
        f"The feature was accepted but regenerated with status {status!r}. "
        f"The module compiles; the failure is in what it was asked to operate "
        f"on.",
        suggestion="Run the probe on the body for a diagnostic with a line "
                   "number, or check that the geometry it selects exists.")]

    if updated:
        trouble.append(Problem(
            Severity.WARNING,
            f"Feature {feature_id} was already in the Part Studio, so it has "
            f"been left in place rather than deleted. It is currently in "
            f"{status!r}.",
            suggestion="Fix the design and run this again — it updates in "
                       "place."))
        return PersistResult(ok=False, problems=trouble, feature_id=feature_id,
                             feature_type=feature_type, namespace=namespace,
                             updated=True)

    rolled_back = False
    try:
        await client.request(
            "DELETE", f"{part_studio}/features/featureid/{feature_id}")
        rolled_back = True
        logger.info("rolled back failed insert %s", feature_id)
    except Exception:  # noqa: BLE001 - report the original failure regardless
        logger.exception("could not roll back the inserted feature")
        trouble.append(Problem(
            Severity.ERROR,
            f"Feature {feature_id} failed to regenerate and could not be "
            f"removed. It is still in the Part Studio and will not work.",
            suggestion="Delete it in Onshape."))

    return PersistResult(ok=False, problems=trouble, feature_type=feature_type,
                         namespace=namespace, rolled_back=rolled_back)
