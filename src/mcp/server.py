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

import logging
import os
import sys

from fastmcp import FastMCP

from src.api.onshape_client import (
    OnshapeApiClient,
    OnshapeAuthError,
    OnshapeConfigError,
)

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("FRC_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("frc_cad_copilot")

mcp = FastMCP("frc-cad-copilot")

_client: OnshapeApiClient | None = None


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
async def onshape_whoami() -> str:
    """Report which Onshape account this server is authenticated as.

    Use this to confirm the Onshape connection is working before running other
    tools, or to check which account's documents are reachable.
    """
    try:
        client = get_client()
    except OnshapeConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return f"Not configured: {exc}"

    try:
        data = (await client.get("/users/sessioninfo")).json()
    except OnshapeAuthError as exc:
        logger.error("Auth error: %s", exc)
        return f"Authentication failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface anything to the caller
        logger.exception("Onshape request failed")
        return f"Onshape request failed: {type(exc).__name__}: {exc}"

    name = data.get("name") or "(unknown)"
    email = data.get("email") or "(no email)"
    return f"Authenticated with Onshape as {name} <{email}> at {client.base_url}"


# Temporary, Gate 2 only. Registered solely when FRC_MCP_STDOUT_EXPERIMENT=1 so
# the production tool surface stays at one tool. Delete once the README records
# how this server behaves when something writes to stdout.
if os.environ.get("FRC_MCP_STDOUT_EXPERIMENT") == "1":

    @mcp.tool
    async def debug_write_to_stdout() -> str:
        """Deliberately write to stdout to observe whether it breaks the session."""
        logger.warning("stdout experiment: writing junk to stdout on purpose")
        print("BOOM-THIS-SHOULD-NOT-BE-HERE", flush=True)  # allow-print: the whole point
        return "Wrote to stdout. If you can read this, the session survived it."

    logger.warning("stdout experiment tool is REGISTERED (Gate 2 only)")


def main() -> None:
    # show_banner=False: the banner is a startup write, and stdout is the
    # protocol channel under stdio transport.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
