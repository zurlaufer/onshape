"""Step 0 of the FeatureScript skill: does the evaluate endpoint return diagnostics?

Spike 3b tested the *Feature Studio write* path and found it silent — broken
source POSTs return 200 with no error field, and a failing feature reports
`featureStatus: "ERROR"` with the thrown message nowhere in the response.

This tests a different path:

    POST /partstudios/d/{did}/w/{wid}/e/{eid}/featurescript
    {"script": "<lambda>", "libraryVersion": NNNN}

It evaluates a lambda against a Part Studio and returns a value. It persists
nothing. Forum reports describe real error text coming back from it, which would
be a genuine feedback channel the Feature Studio path does not offer.

If it yields messages, the skill's validation layer 5 upgrades from binary to
informative. If not, the constrained-template design stands as planned.

    python scripts/spike_fs_evaluate.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402

# Scratch document only (FM2). Never the Citrus robot.
DID = "e424515893d4f1ab8b23e5c4"
WID = "65294e9343b8524f94ad5093"
PS = "4fa46792842ff31b21179dad"
LIBRARY_VERSION = 3029

CASES: list[tuple[str, str]] = [
    (
        "valid: arithmetic",
        "function(context is Context, queries) { return 1 + 1; }",
    ),
    (
        "valid: query the geometry",
        "function(context is Context, queries) {"
        " return size(evaluateQuery(context, qEverything(EntityType.BODY))); }",
    ),
    (
        "SYNTAX ERROR: unbalanced brace",
        "function(context is Context, queries) { return 1 + ; }",
    ),
    (
        "SYNTAX ERROR: unknown function",
        "function(context is Context, queries) { return thisFunctionDoesNotExist(context); }",
    ),
    (
        "RUNTIME: explicit throw",
        'function(context is Context, queries) { throw regenError("DELIBERATE-SPIKE-MESSAGE"); }',
    ),
    (
        "RUNTIME: type error",
        'function(context is Context, queries) { var x is number = "not a number"; return x; }',
    ),
]


async def run_case(client: OnshapeApiClient, label: str, script: str) -> None:
    print(f"\n{'=' * 72}\n{label}\n{'-' * 72}")
    try:
        response = await client.post(
            f"/partstudios/d/{DID}/w/{WID}/e/{PS}/featurescript",
            json_body={"script": script, "libraryVersion": LIBRARY_VERSION},
        )
        body = response.json()
        print(f"  HTTP {response.status_code}   {len(response.content)} bytes")
        print(f"  keys: {list(body.keys())}")

        # The whole question: is there human-readable diagnostic text anywhere?
        blob = json.dumps(body)
        for key in ("notices", "errors", "messages", "message", "console"):
            if key in body:
                print(f"  '{key}': {json.dumps(body[key])[:600]}")
        if "DELIBERATE-SPIKE-MESSAGE" in blob:
            print("  >>> the thrown message IS returned")
        print(f"  body (first 700): {blob[:700]}")

    except Exception as exc:  # noqa: BLE001 - a spike reports whatever happens
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", type(exc).__name__)
        print(f"  HTTP {code}")
        if resp is not None:
            text = resp.text
            print(f"  body ({len(text)} bytes):")
            print("    " + text[:900].replace("\n", "\n    "))
            hints = [
                w
                for w in ("line", "column", "expected", "unexpected", "undefined")
                if w in text.lower()
            ]
            if hints:
                print(f"  >>> diagnostic vocabulary present: {hints}")


async def main() -> int:
    async with OnshapeApiClient.from_env() as client:
        for label, script in CASES:
            await run_case(client, label, script)
    print(f"\n{'=' * 72}")
    print("Question answered: does a failing script yield actionable text?")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
