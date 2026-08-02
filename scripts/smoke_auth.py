"""Gate 1: prove we can authenticate against Onshape.

The smallest possible signed call — GET /users/sessioninfo. If your name prints,
the HMAC signing, header formatting, clock, and API version prefix are all good.

Run from the project root:

    python scripts/smoke_auth.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow running as a plain script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import (  # noqa: E402
    OnshapeApiClient,
    OnshapeAuthError,
    OnshapeConfigError,
)

# This is a standalone CLI, not the MCP server, so stdout is safe here.
# Inside src/ it never is — see the note in onshape_client.py.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if "-v" in sys.argv else logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)


async def main() -> int:
    try:
        client = OnshapeApiClient.from_env()
    except OnshapeConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 2

    print(f"Base URL : {client.base_url}")
    print(f"API path : {client.build_path('/users/sessioninfo')}")

    async with client:
        try:
            response = await client.get("/users/sessioninfo")
        except OnshapeAuthError as exc:
            print(f"\nAuthentication failed:\n  {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - smoke script, report anything
            print(f"\nRequest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        data = response.json()

    name = data.get("name") or data.get("email") or "(no name field)"
    print(f"\nAuthenticated as: {name}")
    print(f"Response size   : {len(response.content)} bytes")
    print("\nGate 1 passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
