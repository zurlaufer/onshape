"""Gate 1: prove we can authenticate against Onshape.

The smallest possible signed call — GET /documents?limit=1. A 200 means the HMAC
signing, header formatting, clock, and API version prefix are all good.

Deliberately *not* /users/sessioninfo: that endpoint returns profile data and so
requires the OAuth2ReadPII scope. Keys for this project are issued with
OAuth2Read only (least privilege), which would make the smoke test 403 on a
perfectly good signature — a false alarm at the exact moment you least want one.
/documents needs nothing beyond OAuth2Read.

It also exercises query-string signing, which sessioninfo does not: the `limit`
parameter has to be encoded once and be byte-identical in both the signature and
the URL. Signing an empty query would leave that path untested until Step 2.

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
    print(f"API path : {client.build_path('/documents')}?limit=1")

    async with client:
        try:
            response = await client.get("/documents", query={"limit": 1})
        except OnshapeAuthError as exc:
            print(f"\nAuthentication failed:\n  {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - smoke script, report anything
            print(f"\nRequest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        data = response.json()

    # An empty document list is still a passing signature — a brand-new account
    # simply has nothing to list. Only the 200 proves or disproves the auth.
    items = data.get("items") or []
    if items:
        first = items[0].get("name") or items[0].get("id") or "(unnamed)"
        print(f"\nDocuments visible: {len(items)} (first: {first})")
    else:
        print("\nDocuments visible: 0 — signature is still valid.")
    print(f"Response size   : {len(response.content)} bytes")
    print("\nGate 1 passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
