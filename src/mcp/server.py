"""FRC CAD Copilot — FastMCP server.

Step 2 of the walking skeleton: exactly one tool, to prove the wiring between
Claude Code, this server, and the Onshape API.

Launch it from anywhere, as long as the project root is importable:

    python -m src.mcp.server                       # from the project root
    PYTHONPATH=<project root> python -m src.mcp.server   # from a workspace

Users run it from a separate workspace (`scripts/make_workspace.py`), never
from this repo, so a session cannot read or edit the copilot's own source.

STDOUT DISCIPLINE  (measured on fastmcp 3.4.5, 2026-08-02 — do not re-litigate)
------------------------------------------------------------------------------
Under stdio transport, stdout carries the JSON-RPC stream. Two things were
verified empirically rather than assumed:

  1. A single stray `print()` writes a raw line into the stream. The in-flight
     tool reply never reaches the client — the call is lost, with no error.

  2. The obvious blanket guard, `sys.stdout = sys.stderr` before run(), DOES
     NOT WORK. The transport writes to the `sys.stdout` *object*, not to fd 1,
     so repointing it silences stray prints and the protocol together: zero
     replies reach the client. Making that guard viable would mean dup'ing
     fd 1 and handing the copy to the transport, which FastMCP's run() does
     not expose. Not worth it.

So the mitigation is discipline, not a safety net:

  * never `print()` anywhere under src/ — use `logger`, which is bound to stderr
  * `show_banner=False` on run(), so FastMCP's startup banner cannot land there
  * be wary of third-party libraries that print; there is no net to catch them

TOOL BUDGET
-----------
Ceiling of 8 tools, all FRC-specific, and no generic `onshape_api_call`
passthrough — a wide surface costs context on every request and dissolves the
scope boundary against Onshape's native AI.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Every file the server reads (`robot.toml`, `standards.toml`, `.env`, the
# assembly and probe caches, `featurescript/`) is a path relative to the
# working directory, and several are loaded at import time, just below. A
# session started in a workspace folder would otherwise find none of them and
# silently run with no targets and no write permission. So the server pins
# its own working directory to the project root before anything is loaded.
# Measured 2026-11-25, when the FS MCP evaluation showed that sessions run
# inside this repo read and edit its source; the fix for that is a separate
# workspace, which needs this.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.chdir(PROJECT_ROOT)

from fastmcp import FastMCP

from src.api.onshape_client import (
    OnshapeApiClient,
    OnshapeAuthError,
    OnshapeConfigError,
    OnshapeError,
)
from src.api.write_policy import WriteRefused
from src.context.cache import AssemblyCache
from src.context.graph import AssemblyGraph
from src.context.queries import assembly_overview, find_parts, fit_response
from src.context.workspace import Target, Workspace, WorkspaceError
from src.skills.apply import (
    PREVIEW_STUDIO_NAME,
    ProbeCache,
    apply_module,
    ensure_feature_studio,
    persist_feature,
    probe_body,
    studio_name_for,
)
from src.skills import standards
from src.skills.templates import TEMPLATES
from src.skills.validate import validate_params, validate_source

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("FRC_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("frc_cad_copilot")

# httpx logs every request at INFO, full URL included. That is what you want
# while debugging and noise the rest of the time, so let it follow our level
# instead of its own: visible at FRC_MCP_LOG_LEVEL=DEBUG, quiet otherwise.
if logger.getEffectiveLevel() > logging.DEBUG:
    logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = FastMCP("frc-cad-copilot")

_client: OnshapeApiClient | None = None
_cache = AssemblyCache()
_workspace = Workspace.load()
# The FeatureScript bucket is ~100/day and shared. Iteration re-probes
# near-identical bodies constantly, so an identical one costs nothing.
_probes = ProbeCache()


def _resolve(where: str, *, expect: str | None = None) -> Target:
    """`where` into a target, or a message saying what would have worked.

    Replaced three opaque hex parameters on every tool. `expect` catches the
    session-observed mistake of handing a Part Studio id to an assembly tool
    before a request is spent on it.
    """
    target = _workspace.resolve(where, expect=expect)
    if expect in ("assembly", "partstudio", "featurestudio") and not target.element_id:
        raise WorkspaceError(
            f"{where!r} names a document but not a tab. Open the {expect} tab "
            f"and copy its URL, or add it to robot.toml.")
    return target


def get_client() -> OnshapeApiClient:
    """Lazily build the shared client so one connection pool is reused.

    Safe without a lock: construction is synchronous, so it cannot be
    interleaved by the event loop.
    """
    global _client
    if _client is None:
        _client = OnshapeApiClient.from_env()
        logger.info(
            "Onshape client ready (%s, api %s)", _client.base_url, _client.api_version
        )
    return _client


@mcp.tool
async def onshape_check_connection() -> str:
    """Verify that this server can reach and authenticate against Onshape.

    Use this to confirm the connection is working before running other tools, or
    to diagnose whether a failure is credentials, clock skew, or the account.

    Reports document visibility rather than the signed-in identity: identity
    lives behind /users, which needs the OAuth2ReadPII scope. Project keys carry
    OAuth2Read only, so asking for identity would 403 on a valid signature.
    """
    try:
        client = get_client()
    except OnshapeConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return f"Not configured: {exc}"

    try:
        data = (await client.get("/documents", query={"limit": 1})).json()
    except OnshapeAuthError as exc:
        logger.error("Auth error: %s", exc)
        return f"Authentication failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface anything to the caller
        logger.exception("Onshape request failed")
        return f"Onshape request failed: {type(exc).__name__}: {exc}"

    # Zero documents is a pass, not a failure — the 200 is what proves the auth.
    count = len(data.get("items") or [])
    payload: dict[str, object] = {
        "authenticated": True,
        "base_url": client.base_url,
        "documents_visible": count,
    }

    # What `where` accepts. Without this the model has to guess a name or ask
    # for a URL, which is how the first real session ended up pasting one.
    known = _workspace.summary()
    payload["known_places"] = known or (
        "No robot.toml found - pass Onshape URLs directly, and note that "
        "nothing will be writable until documents are listed there.")

    # Onshape meters each endpoint separately. The FeatureScript bucket is the
    # scarce one, so it is worth naming before a loop starts rather than after.
    buckets = {k: v["remaining"] for k, v in client.quota.snapshot().items()}
    if buckets:
        payload["quota_remaining"] = buckets

    # Both of those grow: the manifest as a team adds subsystems, the quota as
    # more endpoints get called. Degrade to names rather than overflow.
    return fit_response(
        [
            lambda: payload,
            lambda: {**payload, "known_places": [
                p["name"] for p in known] if known else known},
        ],
        narrow_hint="Place details were trimmed; ask for one by name.",
    )


async def _load_graph(target: Target):
    """Fetch (or reuse) an assembly definition and index it.

    The cache means the 1.09 MB definition is fetched once per document edit
    rather than once per question; a warm call costs a single 49-byte request,
    and a repeat inside the TTL costs none.
    """
    raw = await _cache.get_definition(
        get_client(), target.document_id, target.wvm_id, target.element_id or "")
    return AssemblyGraph.from_definition(raw)


def _explain(exc: Exception) -> str:
    """Turn a failure into something a student can act on.

    Every `OnshapeError` carries an `advice` line for exactly this moment. The
    fallback still exposes the exception type, because an unrecognised failure
    is a bug in us and hiding it would only make it harder to find.
    """
    if isinstance(exc, OnshapeError):
        if not isinstance(exc, (OnshapeConfigError, OnshapeAuthError)):
            logger.warning("Onshape request failed: %s", exc)
        return exc.explain()
    logger.exception("Onshape request failed")
    return f"Onshape request failed: {type(exc).__name__}: {exc}"


def _quota_note(client: OnshapeApiClient) -> dict[str, object]:
    """Surface the quota only when it is worth a student's attention.

    Reporting healthy numbers on every call would be payload spent on nothing.
    The endpoints that run out are specific and few — the FeatureScript evaluate
    bucket is roughly 84 — so the warning is worth its bytes when it appears.
    """
    tight = {
        key: state["remaining"]
        for key, state in client.quota.snapshot().items()
        if state["ceiling"] and state["remaining"] / state["ceiling"] < 0.2
    }
    if not tight:
        return {}
    return {"quota_warning": {
        "low_endpoints": tight,
        "note": "Onshape meters each endpoint separately; these refill at least daily.",
    }}


@mcp.tool
async def onshape_assembly_overview(
    where: str,
    path: str | None = None,
    depth: int = 1,
) -> str:
    """Show the structure of an Onshape assembly, or of one subassembly in it.

    Start with no `path` to see the top level, then drill in by passing the
    `path` of a child from the previous result. Navigating down is much cheaper
    and clearer than raising `depth`, which grows the result very fast.

    Identical components are collapsed into one entry with a count, so four
    matching swerve modules appear once as `count: 4` rather than four times.

    Args:
        where: Which assembly. A name from robot.toml (see
            `onshape_check_connection`), or a pasted Onshape URL. Copy the URL
            with the *assembly* tab open — every tab has its own address.
        path: Slash-separated component names from a previous result, e.g.
            "Robot <1>/Drivetrain <1>". Omit for the top level.
        depth: How many levels to show, 0-3. Defaults to 1.
    """
    try:
        target = _resolve(where, expect="assembly")
        graph = await _load_graph(target)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)
    return assembly_overview(graph, path=path, depth=depth)


@mcp.tool
async def onshape_find_part(where: str, query: str) -> str:
    """Find components in an assembly whose name contains some text.

    Returns each match's full path, which can be passed to
    `onshape_assembly_overview` to explore around it. Matches are deduplicated,
    so a component reused in several places is reported once with the number of
    places it appears.

    Args:
        where: Which assembly — a name from robot.toml or a pasted URL.
        query: Text to look for in component names, case-insensitive. Shorter
            fragments match more; try "swerve" before "swerve module v3".
    """
    try:
        target = _resolve(where, expect="assembly")
        graph = await _load_graph(target)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)
    return find_parts(graph, query)


@mcp.tool
async def frc_probe_featurescript(where: str, body: str) -> str:
    """Run FeatureScript against real geometry and get diagnostics back.

    This is the fastest way to find out whether FeatureScript works. Onshape
    evaluates the body and reports syntax errors with a line and column, unknown
    function names, and runtime failures including "query matched nothing" —
    the failure that matters most when selecting faces or edges.

    **Nothing is persisted.** It can also run real modelling operations, so a
    probe can build a test body, cut it and measure the result, all inside one
    call that leaves the document untouched. Use `makeId("name")` where a
    feature would normally receive an `id`.

    Send a bare lambda. `import`, `annotation` and `export` are rejected by this
    endpoint; the standard library is available without importing.

        function(context is Context, queries) { ... }

    Costs one request from a bucket of roughly 100 per day, shared by everyone
    on this key — the scarcest thing here. Check the parameters locally with
    `frc_check_design` before spending one.

    Args:
        where: A Part Studio to evaluate against — a name from robot.toml or a
            pasted URL. Its geometry is what queries will see.
        body: The lambda source.
    """
    try:
        target = _resolve(where, expect="partstudio")
        client = get_client()
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    local = validate_source(body, is_module=False)
    if any(p.is_error for p in local):
        return json.dumps({
            "ok": False, "stage": "source", "api_requests_used": 0,
            "problems": [p.to_dict() for p in local]}, indent=2)

    spent = client.quota.requests_used
    try:
        result = await probe_body(
            client, body,
            document_id=target.document_id,
            workspace_id=target.wvm_id,
            part_studio_id=target.element_id or "",
            cache=_probes,
        )
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    out: dict = {
        "ok": result.ok,
        "api_requests_used": client.quota.requests_used - spent,
        "problems": [p.to_dict() for p in result.problems]
                    + [p.to_dict() for p in local],
    }
    if result.ok and result.value is not None:
        out["result"] = result.value
    out.update(_quota_note(client))
    return fit_response(
        [lambda: out, lambda: {k: v for k, v in out.items() if k != "result"}],
        narrow_hint="The returned value was omitted; return less from the lambda.",
    )


@mcp.tool
async def frc_apply_featurescript(
    where: str,
    module: str,
    feature_name: str,
    preview: bool = False,
) -> str:
    """Add a custom feature to a Part Studio, and leave it there.

    Give it a complete Feature Studio module — a `FeatureScript` header, an
    import, and one `defineFeature`. It is written to a Feature Studio in the
    same document, compiled, and inserted into the Part Studio. Run it again
    after changing the design and it updates the same feature rather than
    stacking duplicates.

    Prefer an existing community FeatureScript over new geometry where one fits:
    the FRC community maintains dozens (Tube Converter, CheeseIt!, Spur Gear).

    Probe the body first with `frc_probe_featurescript` — that is where line
    numbers come from. This tool reports *that* something failed, rarely why.

    Only documents marked writable in robot.toml can be written to, and that is
    enforced inside the API client rather than here.

    Args:
        where: The Part Studio the feature should appear in.
        module: Full Feature Studio source.
        feature_name: What the feature is called in the tree, e.g.
            "Climber Gearbox Plate".
        preview: Check that it compiles and then put everything back, changing
            nothing. Use for a dry run; the default is to keep the result.
    """
    try:
        target = _resolve(where, expect="partstudio")
        client = get_client()
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    if not client.write_policy.allows(target.document_id):
        return json.dumps({
            "ok": False, "stage": "write_safety", "api_requests_used": 0,
            "error": str(client.write_policy.refuse(target.document_id))}, indent=2)

    local = validate_source(module, is_module=True)
    if any(p.is_error for p in local):
        return json.dumps({
            "ok": False, "stage": "source", "api_requests_used": 0,
            "problems": [p.to_dict() for p in local]}, indent=2)

    spent = client.quota.requests_used
    try:
        # One studio per feature, and a separate one for dry runs, which are
        # restored afterwards. See COPILOT_STUDIO_NAME for the failure.
        studio_id = await ensure_feature_studio(
            client, document_id=target.document_id, workspace_id=target.wvm_id,
            name=PREVIEW_STUDIO_NAME if preview else studio_name_for(feature_name))

        if preview:
            compiled, problems = await apply_module(
                client, module,
                document_id=target.document_id,
                workspace_id=target.wvm_id,
                feature_studio_id=studio_id,
                restore=True,
            )
            out: dict = {
                "ok": compiled, "stage": "preview", "reverted": True,
                "problems": [p.to_dict() for p in problems],
            }
        else:
            result = await persist_feature(
                client, module,
                document_id=target.document_id,
                workspace_id=target.wvm_id,
                feature_studio_id=studio_id,
                part_studio_id=target.element_id or "",
                feature_name=feature_name,
            )
            out = {"stage": "applied", **result.to_dict()}
            if result.ok:
                out["note"] = (
                    f"{feature_name!r} is now in {target.describe()}. Run this "
                    f"again after changing the design and it updates in place.")
    except WriteRefused as exc:
        return json.dumps({"ok": False, "stage": "write_safety",
                           "error": str(exc)}, indent=2)
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    out["problems"] = out.get("problems", []) + [p.to_dict() for p in local]
    out["api_requests_used"] = client.quota.requests_used - spent
    out.update(_quota_note(client))
    return json.dumps(out, indent=2)


@mcp.tool
async def frc_list_checks() -> str:
    """List the FRC design checks available, and the parameters each needs.

    Call this before `frc_check_design`. These are engineering rules a CAD tool
    does not know — minimum tooth counts, end mill radii, wall thicknesses,
    material density — plus the geometry they imply.
    """
    payload: dict[str, object] = {
        "checks": [_template_detail(t) for t in TEMPLATES.values()],
        "note": ("All of these run locally and cost nothing. Use them before "
                 "spending a FeatureScript probe."),
    }

    # Only the numbers this team actually changed. A team with no standards.toml
    # sees nothing here and pays nothing for it; the defaults already show up in
    # each check's own message. What matters is that a session designing to
    # "3 mm ribs" when the team decided 6 finds that out before proposing it.
    # Read through the module, not `from ... import STANDARDS`: that would bind
    # the object at import and quietly ignore any later reload.
    team_set = [s for s in standards.STANDARDS.summary() if s["team_set"]]
    if team_set:
        payload["team_standards"] = team_set

    return fit_response(
        [
            lambda: payload,
            lambda: {"checks": [{"name": t.name, "summary": t.summary}
                                for t in TEMPLATES.values()],
                     "note": payload["note"],
                     **({"team_standards": team_set} if team_set else {})},
            lambda: {"checks": sorted(TEMPLATES)},
        ],
        narrow_hint="Call frc_check_design with one name for its full schema.",
    )


@mcp.tool
async def frc_check_design(design: str, parameters: dict) -> str:
    """Check a design against FRC engineering rules, and get the numbers out.

    Free and instant — no Onshape request. Catches what is impossible on paper
    (a hole wider than its tube, a pocket narrower than the cutter, a gear pair
    whose bores would intersect) and returns the dimensions those rules imply:
    a gear pair's centre distance, a pocket grid's size, the grams a lightening
    pattern removes.

    Run this before generating any geometry. The numbers it returns are what the
    FeatureScript should be built around, whether that is a community feature or
    one you write.

    Args:
        design: A name from `frc_list_checks`.
        parameters: Its parameters. Dimensions in millimetres.
    """
    template = TEMPLATES.get(design)
    if template is None:
        return json.dumps({"ok": False, "error": f"Unknown design {design!r}.",
                           "available": sorted(TEMPLATES)}, indent=2)

    problems = validate_params(design, parameters)
    ok = not any(p.is_error for p in problems)

    out: dict = {
        "ok": ok,
        "design": design,
        "api_requests_used": 0,
        "problems": [p.to_dict() for p in problems],
    }
    if ok:
        out["derived"] = _derived_numbers(template, parameters)
        out["featurescript"] = {
            "probe_body": template.render_probe(parameters),
            "module": template.render_module(parameters),
        }
        out["note"] = ("Probe `probe_body` with frc_probe_featurescript, then "
                       "apply `module` with frc_apply_featurescript.")

    # Degrade the probe first. The module is what gets applied, and
    # frc_apply_featurescript compiles it and rolls back a failed insert, so the
    # probe is a pre-check while the module is the deliverable. Measured
    # 2026-11-25: boxing the face along its own edges took the pocket
    # template's probe + module from 6.9 KB to 10.4 KB, over the 8 KB ceiling,
    # and the old fallback then dropped both.
    def module_only() -> dict:
        trimmed = dict(out)
        trimmed["featurescript"] = {"module": out["featurescript"]["module"]}
        return trimmed

    return fit_response(
        [
            lambda: out,
            *([module_only] if ok else []),
            lambda: {k: v for k, v in out.items() if k != "featurescript"},
        ],
        # fit_response replaces `note` with this whenever it degrades.
        narrow_hint="If `probe_body` is missing, apply `module` directly with "
                    "frc_apply_featurescript: it compiles the module and rolls "
                    "back a failed insert. If all source is missing, re-run "
                    "once the parameters settle.",
    )


def _template_detail(template) -> dict:
    return {
        "name": template.name,
        "summary": template.summary,
        "parameters": [s.to_dict() for s in template.parameters()],
    }


def _derived_numbers(template, parameters: dict) -> dict:
    """Whatever a design computes that a student would otherwise work out.

    Each template exposes its own derivations; this collects the ones present
    rather than requiring every template to implement a common interface it
    would only partly mean.
    """
    derived: dict[str, object] = {}
    try:
        if hasattr(template, "centre_distance_mm"):
            derived["centre_distance_mm"] = round(
                template.centre_distance_mm(parameters), 4)
        if hasattr(template, "reduction"):
            derived["reduction"] = round(template.reduction(parameters), 4)
        if hasattr(template, "pitch_diameters_mm"):
            driving, driven = template.pitch_diameters_mm(parameters)
            derived["pitch_diameter_mm"] = {
                "driving": round(driving, 4), "driven": round(driven, 4)}
        if hasattr(template, "pocket_size"):
            width, length = template.pocket_size(parameters)
            derived["pocket_mm"] = {"width": round(width, 4),
                                    "length": round(length, 4)}
        if hasattr(template, "mass_removed_g"):
            derived["mass_removed_g"] = round(template.mass_removed_g(parameters), 2)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        # Derivations are a bonus. A parameter set that survived validation but
        # trips one of these must not turn a useful answer into an error.
        logger.debug("could not derive numbers for %s", template.name)
    return derived


def main() -> None:
    # show_banner=False: the banner is a startup write, and stdout is the
    # protocol channel under stdio transport.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
