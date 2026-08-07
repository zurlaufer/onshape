"""FRC CAD Copilot — FastMCP server.

Step 2 of the walking skeleton: exactly one tool, to prove the wiring between
Claude Code, this server, and the Onshape API.

Launch from the project root:

    python -m src.mcp.server

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

from fastmcp import FastMCP

from src.api.onshape_client import (
    OnshapeApiClient,
    OnshapeAuthError,
    OnshapeConfigError,
    OnshapeError,
)
from src.context.cache import AssemblyCache
from src.context.graph import AssemblyGraph
from src.context.queries import assembly_overview, find_parts, fit_response
from src.skills.apply import ScratchPolicy, WriteRefused, apply_module, probe_body
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
    reachable = "at least one document visible" if count else "no documents visible"
    answer = f"Authenticated with Onshape at {client.base_url} ({reachable})."

    # Onshape meters each endpoint separately, so this speaks only for the one
    # we just called — but it is a real answer to "am I about to run out", and
    # it costs no extra request.
    remaining = client.quota.remaining("GET", client.build_path("/documents"))
    if remaining is not None:
        answer += f" Document-list quota remaining: {remaining}."
    return answer


async def _load_graph(document_id: str, workspace_id: str, element_id: str):
    """Fetch (or reuse) an assembly definition and index it.

    The cache means the 1.09 MB definition is fetched once per document edit
    rather than once per question; a warm call costs a single 49-byte request.
    """
    client = get_client()
    raw = await _cache.get_definition(client, document_id, workspace_id, element_id)
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
    document_id: str,
    workspace_id: str,
    element_id: str,
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
        document_id: Onshape document id (the `/documents/{id}` segment of a URL).
        workspace_id: Workspace id (the `/w/{id}` segment).
        element_id: The assembly tab's element id (the `/e/{id}` segment).
        path: Slash-separated component names from a previous result, e.g.
            "Robot <1>/Drivetrain <1>". Omit for the top level.
        depth: How many levels to show, 0-3. Defaults to 1.
    """
    try:
        graph = await _load_graph(document_id, workspace_id, element_id)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)
    return assembly_overview(graph, path=path, depth=depth)


@mcp.tool
async def onshape_find_part(
    document_id: str,
    workspace_id: str,
    element_id: str,
    query: str,
) -> str:
    """Find components in an assembly whose name contains some text.

    Returns each match's full path, which can be passed to
    `onshape_assembly_overview` to explore around it. Matches are deduplicated,
    so a component reused in several places is reported once with the number of
    places it appears.

    Args:
        document_id: Onshape document id.
        workspace_id: Workspace id.
        element_id: The assembly tab's element id.
        query: Text to look for in component names, case-insensitive. Shorter
            fragments match more; try "swerve" before "swerve module v3".
    """
    try:
        graph = await _load_graph(document_id, workspace_id, element_id)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)
    return find_parts(graph, query)


@mcp.tool
async def frc_list_templates() -> str:
    """List the FRC CAD templates this server can generate and apply.

    Call this before `frc_apply_template` to see each template's parameters,
    units and constraints. Templates are hand-written and pre-verified: you
    choose one and supply its parameters rather than writing FeatureScript.
    """
    payload = {
        "templates": [
            {
                "name": t.name,
                "summary": t.summary,
                "parameters": [s.to_dict() for s in t.parameters()],
            }
            for t in TEMPLATES.values()
        ],
        "note": (
            "Parameters are validated locally first (dimensional arithmetic, "
            "no API cost). Impossible designs are rejected before Onshape is "
            "contacted."
        ),
    }
    return fit_response(
        [lambda: payload],
        narrow_hint="Ask about one template by name if this is too long.",
    )


@mcp.tool
async def frc_apply_template(
    template: str,
    parameters: dict,
    document_id: str,
    workspace_id: str,
    part_studio_id: str,
    feature_studio_id: str | None = None,
) -> str:
    """Validate FRC CAD template parameters, then generate and check FeatureScript.

    Runs cheapest-first: dimensional checks happen locally and cost nothing, so
    an impossible design (a hole wider than its tube) is refused without
    contacting Onshape. Only if those pass is the generated code evaluated
    against Onshape, which reports syntax and query failures with line numbers.

    Writes are limited to scratch documents and are reverted afterwards; this
    verifies a template rather than committing it to a design.

    Args:
        template: Template name from `frc_list_templates`.
        parameters: Template parameters. Dimensions in millimetres.
        document_id: Onshape document id.
        workspace_id: Workspace id.
        part_studio_id: A Part Studio to evaluate against.
        feature_studio_id: Optional. When given, the full module is also
            compiled in that Feature Studio and the original source restored.
    """
    tpl = TEMPLATES.get(template)
    if tpl is None:
        return json.dumps(
            {"ok": False, "error": f"Unknown template {template!r}.",
             "available": sorted(TEMPLATES)}, indent=2)

    # Check the write policy up front. Probing is read-only and safe anywhere,
    # but if a write is being asked for, refusing before spending a request is
    # both cheaper and much clearer than failing three steps later.
    if feature_studio_id:
        try:
            ScratchPolicy().check(document_id)
        except WriteRefused as exc:
            return json.dumps({
                "ok": False, "stage": "write_safety",
                "api_requests_used": 0, "error": str(exc)}, indent=2)

    # --- Layers 3-4: parameters, free -----------------------------------
    problems = validate_params(template, parameters)
    if any(p.is_error for p in problems):
        return json.dumps({
            "ok": False,
            "stage": "parameters",
            "api_requests_used": 0,
            "problems": [p.to_dict() for p in problems],
        }, indent=2)

    module = tpl.render_module(parameters)
    probe = tpl.render_probe(parameters)

    # --- Layers 1-2: generated source, free ------------------------------
    source_problems = (validate_source(probe, is_module=False)
                       + validate_source(module, is_module=True))
    if any(p.is_error for p in source_problems):
        return json.dumps({
            "ok": False,
            "stage": "source",
            "api_requests_used": 0,
            "problems": [p.to_dict() for p in source_problems + problems],
        }, indent=2)

    warnings = [p.to_dict() for p in problems + source_problems]

    # --- Layer 5: Onshape probe, real diagnostics ------------------------
    # Request counts are measured from the client's own ledger rather than
    # written down here. The hardcoded numbers this replaces were guesses, and
    # they were wrong the moment a retry fired.
    try:
        client = get_client()
        spent_before = client.quota.requests_used
        result = await probe_body(
            client, probe,
            document_id=document_id, workspace_id=workspace_id,
            part_studio_id=part_studio_id,
        )
    except WriteRefused as exc:
        return json.dumps({"ok": False, "stage": "write_safety", "error": str(exc)}, indent=2)
    except Exception as exc:  # noqa: BLE001 - always answer the caller
        return _explain(exc)

    out: dict = {
        "ok": result.ok,
        "stage": "onshape_probe",
        "api_requests_used": client.quota.requests_used - spent_before,
        "problems": [p.to_dict() for p in result.problems] + warnings,
    }

    # --- Layer 6: compile the real module, optional ----------------------
    if result.ok and feature_studio_id:
        try:
            compiled, compile_problems = await apply_module(
                client, module,
                document_id=document_id, workspace_id=workspace_id,
                feature_studio_id=feature_studio_id,
            )
            out["stage"] = "onshape_compile"
            out["ok"] = compiled
            out["problems"] += [p.to_dict() for p in compile_problems]
            out["feature_studio_restored"] = True
        except WriteRefused as exc:
            out["ok"] = False
            out["stage"] = "write_safety"
            out["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            return _explain(exc)
        finally:
            out["api_requests_used"] = client.quota.requests_used - spent_before

    out.update(_quota_note(client))

    if out["ok"]:
        out["featurescript"] = module

    return fit_response(
        [lambda: out, lambda: {k: v for k, v in out.items() if k != "featurescript"}],
        narrow_hint="The generated source was omitted; re-run for it once the parameters settle.",
    )


def main() -> None:
    # show_banner=False: the banner is a startup write, and stdout is the
    # protocol channel under stdio transport.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
