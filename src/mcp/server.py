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
import re
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


def _source_stamp() -> float:
    """Newest modification time of any source file the server runs.

    Mtimes, not the git commit, so an edit that has not been committed yet
    counts too. About 40 files; cheap enough to re-read on every check.
    """
    return max((p.stat().st_mtime for p in (PROJECT_ROOT / "src").rglob("*.py")),
               default=0.0)


#: What the code on disk looked like when this process started. Measured
#: 2026-11-26: the VS Code extension keeps an MCP server running across
#: conversations, so a session started half an hour after a fix still ran
#: the old code. It read a Part Studio through the assembly endpoint (400) and
#: had no `values` option, while the freshly re-read CLAUDE.md described both.
_LOADED_STAMP = _source_stamp()

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
    COPILOT_STUDIO_NAME,
    PREVIEW_STUDIO_NAME,
    ProbeCache,
    apply_module,
    ensure_feature_studio,
    persist_feature,
    probe_body,
    studio_name_for,
)
from src.skills import standards
from src.skills.assemble import (
    DEFAULT_ASSEMBLY_NAME,
    AssembleError,
    assemble,
    part_studio_tabs,
    pick_studios,
)
from src.skills.selections import (
    SELECTORS,
    SelectionError,
    declared_query_filters,
    declared_query_parameters,
    declared_value_parameters,
    query_parameter,
    resolve_body,
    resolve_face,
    value_parameter,
)
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

    # Is this process running the code on disk? A reconnect alone is not
    # enough: the server picks up new code, but a conversation keeps the tool
    # list (descriptions, parameters) it started with. Measured 2026-11-26,
    # after a reconnect the reads worked but `values` was still not
    # accepted.
    if _source_stamp() > _LOADED_STAMP + 1.0:
        payload["STALE_SERVER"] = (
            "This copilot server is running older code than is on disk. "
            "Restart it (/mcp -> frc-cad-copilot -> Reconnect), then START A "
            "NEW CONVERSATION: a conversation keeps the tool list it began "
            "with. Until then, tools may be missing options the instructions "
            "describe.")

    # What `where` accepts. Without this the model has to guess a name or ask
    # for a URL, which is how the first real session ended up pasting one.
    known = _workspace.summary()
    # Stated, not implied: a session saw a URL for an unnamed tab of a writable
    # document and warned the write might be refused (2026-11-26).
    writable = sorted(getattr(_workspace, "writable_documents", ()) or ())
    if writable:
        payload["writable_documents"] = {
            "ids": writable,
            "rule": "Every tab of these documents is writable, named here or not.",
        }
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
    """Show what is in an assembly or a Part Studio.

    ASSEMBLY: its structure, or one subassembly's. Start with no `path` to see
    the top level, then drill in by passing the `path` of a child from the
    previous result. Navigating down is much cheaper and clearer than raising
    `depth`. Identical components collapse into one entry with a count.

    PART STUDIO: its features in order, each with its status and its parameter
    values (e.g. `rib: 8 mm`), and its parts. A feature the copilot applied
    names the studio it came from. Read this before changing an existing
    feature, rather than assuming its values. Costs one read from a ~94/day
    bucket.

    Args:
        where: An assembly or Part Studio. A name from robot.toml (see
            `onshape_check_connection`), or a pasted Onshape URL. Copy the URL
            with that tab open — every tab has its own address.
        path: Assemblies only. Slash-separated component names from a previous
            result, e.g. "Robot <1>/Drivetrain <1>". Omit for the top level.
        depth: Assemblies only. How many levels to show, 0-3. Defaults to 1.
    """
    try:
        target = _resolve(where)
        if not target.element_id:
            raise WorkspaceError(
                f"{where!r} names a document but not a tab. Open the assembly "
                f"or Part Studio tab and copy its URL, or add it to robot.toml.")
        client = get_client()
        kind, studios = target.kind, {}
        if kind in (None, "partstudio"):
            # One cheap read (~9000/day) tells us what a pasted URL points at,
            # and which Feature Studios are the copilot's.
            elements = (await client.get(
                f"/documents/d/{target.document_id}/{target.wvm}/{target.wvm_id}"
                f"/elements")).json()
            types = {e.get("id"): e.get("elementType") for e in elements}
            studios = {e.get("id"): e.get("name") for e in elements
                       if e.get("elementType") == "FEATURESTUDIO"}
            if types.get(target.element_id) == "PARTSTUDIO":
                kind = "partstudio"
        if kind == "partstudio":
            return await _partstudio_overview(client, target, studios)
        graph = await _load_graph(target)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)
    return assembly_overview(graph, path=path, depth=depth)


def _keep_and_require(declared_queries: set[str], declared_values: set[str],
                      selections: dict, values: dict) -> dict[str, set[str]]:
    """Which parameters an update keeps, and which an insert must carry.

    An update KEEPS:
      - a selection named by geometry ("top"): re-resolving it would see this
        feature's own cut (2026-11-25);
      - a selection NOT GIVEN at all. Measured 2026-11-26: re-applying with
        only values={"rib": "2 mm"} kept border and depth but dropped the
        face, and the feature sat in ERROR in the document until the session
        re-applied with the face;
      - a value not given.
    An explicit face id, or a value that IS given, replaces what was there.

    An insert must carry every declared selection and value: Onshape fills no
    defaults for an API insert, and an empty selection is ERROR.
    """
    keep = ({pid for pid, sel in selections.items() if sel in SELECTORS}
            | (declared_queries - set(selections))
            | (declared_values - set(values)))
    return {"keep_existing": keep,
            "required_on_insert": declared_queries | declared_values}


def _parameter_value(param: dict) -> object:
    """One parameter, as a person would read it in Onshape's dialog."""
    kind = param.get("btType", "")
    if kind.startswith("BTMParameterQuantity"):
        return param.get("expression") or param.get("value")
    if kind.startswith("BTMParameterQueryList"):
        count = len(param.get("queries") or [])
        return f"{count} selection{'s' if count != 1 else ''}"
    if kind.startswith(("BTMParameterBoolean", "BTMParameterEnum", "BTMParameterString")):
        return param.get("value")
    return kind.split("-")[0].replace("BTMParameter", "").lower() or None


def _part_sizes(bodydetails: dict) -> dict[str, list[float]]:
    """{part id: [x, y, z] extent in mm} from the vertices of each body.

    World-axis extents. Exact for a plate drawn square to the axes; for an
    angled one, `face_dimensions` measures the true size.
    """
    out: dict[str, list[float]] = {}
    for body in (bodydetails.get("bodies") or []) if isinstance(bodydetails, dict) else []:
        points = [v.get("point") or {} for v in body.get("vertices") or []]
        if not points:
            continue
        out[str(body.get("id"))] = [
            round((max(p.get(k, 0.0) for p in points)
                   - min(p.get(k, 0.0) for p in points)) * 1000.0, 2)
            for k in ("x", "y", "z")]
    return out


_DEFAULTS_LINE = re.compile(r"^// Defaults: (.+)$", re.MULTILINE)


def _generated_with(source: str) -> dict[str, float] | None:
    """The template parameters a copilot module was generated from."""
    match = _DEFAULTS_LINE.search(source or "")
    if not match:
        return None
    out: dict[str, float] = {}
    for pair in match.group(1).split(","):
        key, _, value = pair.strip().partition("=")
        try:
            out[key] = float(value)
        except ValueError:
            continue
    return out or None


async def _partstudio_overview(client, target: Target, studios: dict) -> str:
    """Features, their status and values, and the parts of one Part Studio.

    Exists because a session asked to "change the ribs to 2 mm" could not see
    the feature it was changing, ran the check on an example's numbers instead,
    and reported 66 % removed where the truth was 71 % (2026-11-26).
    """
    base = (f"/partstudios/d/{target.document_id}/{target.wvm}/{target.wvm_id}"
            f"/e/{target.element_id}")
    data = (await client.get(f"{base}/features")).json()
    states = data.get("featureStates") or {}
    features = []
    for feature in data.get("features", []):
        row: dict[str, object] = {
            "name": feature.get("name"),
            "type": feature.get("featureType"),
            "status": (states.get(feature.get("featureId")) or {}).get("featureStatus"),
        }
        values = {p.get("parameterId"): _parameter_value(p)
                  for p in feature.get("parameters") or [] if p.get("parameterId")}
        if values:
            row["values"] = values
        studio = str(feature.get("namespace", "")).split("::")[0]
        if studio.startswith("e") and studios.get(studio[1:], "").startswith(
                COPILOT_STUDIO_NAME):
            row["applied_by_copilot_from"] = studios[studio[1:]]
        features.append(row)
    parts = (await client.get(
        f"/parts/d/{target.document_id}/{target.wvm}/{target.wvm_id}"
        f"/e/{target.element_id}")).json()
    # Part sizes, because a feature's values alone did not let a session run
    # the check: it had rib and border but not the plate, so it could not say
    # how much area 2 mm ribs remove (2026-11-26). One read from ~500/day.
    details = (await client.get(f"{base}/bodydetails")).json()
    sizes = _part_sizes(details)
    requests = 4

    # For a feature the copilot generated, the values it was generated with,
    # including what is baked in rather than exposed (the pocket grid). Read
    # from the "// Defaults:" line of its module. One cheap read per studio.
    for row, feature in zip(features, data.get("features", [])):
        studio = str(feature.get("namespace", "")).split("::")[0][1:]
        if "applied_by_copilot_from" in row and studio:
            source = (await client.get(
                f"/featurestudios/d/{target.document_id}/{target.wvm}/"
                f"{target.wvm_id}/e/{studio}")).json().get("contents", "")
            requests += 1
            generated = _generated_with(source)
            if generated:
                row["generated_with"] = generated

    payload = {
        "part_studio": target.describe(),
        "features": features,
        "parts": [{"name": p.get("name"), **({"size_mm": sizes[p.get("partId")]}
                                              if p.get("partId") in sizes else {})}
                  for p in parts],
        "api_requests_used": requests,
    }
    # A team's real Part Studio can hold hundreds of features. Degrade to
    # names and status, then to the last 40, rather than overflow.
    return fit_response(
        [
            lambda: payload,
            lambda: {**payload, "features": [
                {k: f[k] for k in ("name", "status")} for f in features]},
            lambda: {**payload, "features": [
                {k: f[k] for k in ("name", "status")} for f in features[-40:]],
                "features_omitted": max(0, len(features) - 40)},
        ],
        narrow_hint="Parameter values were trimmed to fit. Ask about one "
                    "feature by name, or use a smaller Part Studio.",
    )


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
    selections: dict[str, str] | None = None,
    values: dict[str, str | int | float | bool] | None = None,
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
        selections: Faces for the module's `Query` parameters, keyed by
            parameter id, e.g. {"face": "top"}. A feature whose required
            selection is empty regenerates as ERROR and is rolled back, so
            set every one. Values: "top", "bottom", "largest_planar" (planar
            faces of solid bodies), or a face id. The chosen face is reported.
        values: Values for the module's length/angle/integer parameters, as
            Onshape expressions, e.g. {"rib": "8 mm"}. Onshape does not fill
            defaults for an API insert, so an insert needs every one. On an
            update, give only what changes; the rest is kept. frc_check_design
            returns the right `selections` and `values` under `apply_with`.
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

    # Free check first: a selection for a parameter the module does not
    # declare would be silently ignored by Onshape.
    selections = selections or {}
    declared = declared_query_parameters(module)
    unknown = sorted(set(selections) - declared)
    if unknown:
        return json.dumps({
            "ok": False, "stage": "selections", "api_requests_used": 0,
            "error": (f"{unknown} not declared as `definition.<id> is Query` in "
                      f"the module. Declared: {sorted(declared) or 'none'}.")},
            indent=2)
    values = values or {}
    declared_values = declared_value_parameters(module)
    unknown = sorted(set(values) - declared_values)
    if unknown:
        return json.dumps({
            "ok": False, "stage": "values", "api_requests_used": 0,
            "error": (f"{unknown} not declared as a length, angle, integer or "
                      f"real parameter in the module. Declared: "
                      f"{sorted(declared_values) or 'none'}.")}, indent=2)

    spent = client.quota.requests_used
    chosen: dict[str, str] = {}
    parameters: list[dict] = [value_parameter(pid, v) for pid, v in values.items()]
    try:
        if selections and not preview:
            details = (await client.get(
                f"/partstudios/d/{target.document_id}/w/{target.wvm_id}"
                f"/e/{target.element_id}/bodydetails")).json()
            filters = declared_query_filters(module)
            for parameter_id, selector in selections.items():
                if filters.get(parameter_id) == "BODY":
                    body = resolve_body(details, selector)
                    parameters.append(query_parameter(
                        parameter_id, [body.body_id], entity="BODY"))
                    chosen[parameter_id] = (f"body {body.body_id}: "
                                            f"{body.area_mm2:,.0f} mm2 of surface")
                else:
                    face = resolve_face(details, selector)
                    parameters.append(query_parameter(parameter_id, [face.face_id]))
                    chosen[parameter_id] = face.describe()

        # One studio per feature, and a separate one for dry runs, which are
        # restored afterwards. See COPILOT_STUDIO_NAME for the failure.
        studio_id = await ensure_feature_studio(
            client, document_id=target.document_id, workspace_id=target.wvm_id,
            name=(PREVIEW_STUDIO_NAME if preview
                  else studio_name_for(feature_name, target.element_id or "")))

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
                parameters=parameters,
                **_keep_and_require(declared, declared_values, selections, values),
            )
            out = {"stage": "applied", **result.to_dict()}
            for pid in result.kept_selections:
                if pid in declared:
                    chosen[pid] = ("kept the face chosen when this feature was "
                                   "first inserted; pass a face id to change it")
            if chosen:
                out["selected"] = chosen
            missing = sorted(declared - set(selections))
            if missing and not result.ok:
                out["hint"] = (f"{missing} declared but not selected. An empty "
                               f"required selection regenerates as ERROR; pass "
                               f"selections={{...}}.")
            if result.ok:
                out["note"] = (
                    f"{feature_name!r} is now in {target.describe()}. Run this "
                    f"again after changing the design and it updates in place.")
    except WriteRefused as exc:
        return json.dumps({"ok": False, "stage": "write_safety",
                           "error": str(exc)}, indent=2)
    except SelectionError as exc:
        return json.dumps({"ok": False, "stage": "selections", "error": str(exc),
                           "api_requests_used": client.quota.requests_used - spent},
                          indent=2)
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    out["problems"] = out.get("problems", []) + [p.to_dict() for p in local]
    out["api_requests_used"] = client.quota.requests_used - spent
    out.update(_quota_note(client))
    return json.dumps(out, indent=2)


@mcp.tool
async def frc_assemble(
    where: str,
    part_studios: list[str] | None = None,
    preview: bool = False,
) -> str:
    """Put Part Studios into an assembly at their designed positions, no mates.

    For a robot drawn top-down, every subsystem's Part Studio already in robot
    coordinates. Each part is inserted where its Part Studio has it, and all of
    them are held by one Group, "Robot (copilot)". Measured: parts land exactly
    in place. Re-running inserts only what is missing and updates the group.

    It does not mate, move or rotate anything. A part that must sit somewhere
    other than its Part Studio position (a COTS module at four corners, an arm
    at another angle) is not something this tool can place.

    Args:
        where: The assembly tab, or the document's URL without `/e/...` to use
            (or create) a tab named "Robot". Writable documents only.
        part_studios: Tab names (as shown in Onshape), ids or URLs, from the
            same document. Leave out to get the list of Part Studios there.
        preview: Report what would be inserted, writing nothing.
    """
    try:
        target = _resolve(where)
        client = get_client()
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    if not client.write_policy.allows(target.document_id):
        return json.dumps({
            "ok": False, "stage": "write_safety", "api_requests_used": 0,
            "error": str(client.write_policy.refuse(target.document_id))}, indent=2)
    if target.wvm != "w":
        return json.dumps({
            "ok": False, "stage": "where", "api_requests_used": 0,
            "error": "That link is a version, which cannot change. Copy the URL "
                     "of the document's workspace (it has /w/ in it)."}, indent=2)

    spent = client.quota.requests_used
    base = f"d/{target.document_id}/w/{target.wvm_id}"
    try:
        elements = (await client.get(f"/documents/{base}/elements")).json()
        tabs = part_studio_tabs(elements)
        if not part_studios:
            return json.dumps({
                "ok": False, "stage": "choose",
                "error": "Name the Part Studios to assemble (part_studios=[...]).",
                "part_studios_here": sorted(tabs.values()),
                "api_requests_used": client.quota.requests_used - spent}, indent=2)
        studios = pick_studios(part_studios, tabs, target.document_id)

        kinds = {e["id"]: (e.get("elementType"), e.get("name")) for e in elements}
        created = False
        if target.element_id:
            kind, assembly_name = kinds.get(target.element_id, (None, None))
            if kind != "ASSEMBLY":
                raise AssembleError(
                    f"{where!r} is a {kind or 'tab that does not exist'}, not an "
                    f"assembly. Give the assembly tab, or the document URL without "
                    f"/e/... to use a tab named {DEFAULT_ASSEMBLY_NAME!r}.")
            assembly_id = target.element_id
        else:
            assembly_name = DEFAULT_ASSEMBLY_NAME
            assembly_id = next((eid for eid, (kind, name) in kinds.items()
                                if kind == "ASSEMBLY" and name == DEFAULT_ASSEMBLY_NAME),
                               None)
            if assembly_id is None and preview:
                return json.dumps({
                    "ok": True, "stage": "preview", "changed": False,
                    "would_create_tab": DEFAULT_ASSEMBLY_NAME,
                    "would_insert": {name: "every solid part" for _, name in studios},
                    "api_requests_used": client.quota.requests_used - spent}, indent=2)
            if assembly_id is None:
                assembly_id = (await client.post(
                    f"/assemblies/{base}",
                    json_body={"name": DEFAULT_ASSEMBLY_NAME})).json()["id"]
                created = True

        out = await assemble(client, document_id=target.document_id,
                             workspace_id=target.wvm_id, assembly_id=assembly_id,
                             studios=studios, preview=preview)
    except AssembleError as exc:
        return json.dumps({"ok": False, "stage": "refused", "error": str(exc),
                           "api_requests_used": client.quota.requests_used - spent},
                          indent=2)
    except WriteRefused as exc:
        return json.dumps({"ok": False, "stage": "write_safety",
                           "error": str(exc)}, indent=2)
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)

    out["assembly"] = {
        "name": assembly_name, "created": created,
        "url": (f"{client.base_url}/documents/{target.document_id}"
                f"/w/{target.wvm_id}/e/{assembly_id}")}
    left_out = sorted(set(tabs.values()) - {name for _, name in studios})
    if left_out:
        out["other_part_studios"] = left_out
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
    # Where each of this design's limits comes from. A session in the team
    # workspace cannot read standards.toml, and on 2026-11-25 one said "the
    # check doesn't say whether the 3 mm limit is your team's own rule or a
    # built-in default". Messages stay short (team_note is empty for a
    # default, on purpose), so the source is stated once, here.
    section = design.rsplit("_", 1)[0]
    limits = {
        f"{row['standard']}": (f"{row['value']:g} {row['unit']}, "
                               + ("set by your team in standards.toml"
                                  if row["team_set"] else
                                  "a default; your team can set it in standards.toml"))
        for row in standards.STANDARDS.summary()
        if row["standard"].startswith(section + ".")
    }
    if limits:
        out["limits"] = limits
    if ok:
        out["derived"] = _derived_numbers(template, parameters)
        module = template.render_module(parameters)
        out["featurescript"] = {
            "probe_body": template.render_probe(parameters),
            "module": module,
        }
        # The arguments apply needs, so nobody has to work them out: Onshape
        # fills no defaults for an API insert (measured 2026-11-26), and a
        # selection left empty regenerates as ERROR.
        out["apply_with"] = {
            "selections": {pid: ("largest_solid" if entity == "BODY" else "top")
                           for pid, entity in declared_query_filters(module).items()},
            "values": getattr(template, "apply_values", lambda _p: {})(parameters),
        }
        out["note"] = ("Probe `probe_body` with frc_probe_featurescript, then "
                       "apply `module` with frc_apply_featurescript and the "
                       "`apply_with` arguments. Check the selections fit.")

    # Degrade the probe first. The module is what gets applied, and
    # frc_apply_featurescript compiles it and rolls back a failed insert, so the
    # probe is a pre-check while the module is the deliverable. Measured
    # 2026-11-25: boxing the face along its own edges took the pocket
    # template's probe + module from 6.9 KB to 10.4 KB, over the 8 KB ceiling,
    # and the old fallback then dropped both.
    def module_only(strip_comments: bool = False) -> dict:
        module = out["featurescript"]["module"]
        if strip_comments:
            # The comments explain the code to a reader of the Feature Studio;
            # Onshape does not need them. Measured 2026-11-26: with feature
            # parameters and in-feature rule warnings the pocket module was
            # 8.3 KB encoded, over the ceiling on its own, and 6.4 KB without
            # its comments.
            module = "\n".join(line for line in module.splitlines()
                               if not line.strip().startswith("//"))
        trimmed = dict(out)
        trimmed["featurescript"] = {"module": module}
        return trimmed

    return fit_response(
        [
            lambda: out,
            *([module_only, lambda: module_only(strip_comments=True)] if ok else []),
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
