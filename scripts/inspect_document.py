"""Show what is actually in an Onshape document: tabs, features, parts.

    python scripts/inspect_document.py <documentId> <workspaceId>
    python scripts/inspect_document.py                    # defaults to asm-try1

Read-only. Cheap — document reads come from a bucket of roughly 3000 a day, not
the ~84 the FeatureScript evaluate endpoint gets.

Written because `onshape_assembly_overview` answers a 400 when handed a Part
Studio's element id, which is what an Onshape URL usually contains, and because
a feature sitting in ERROR is invisible until something asks. It found exactly
that: a custom feature stranded when its Feature Studio was restored out from
under it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient, OnshapeConfigError  # noqa: E402

DEFAULT_DOCUMENT = "e424515893d4f1ab8b23e5c4"  # asm-try1
DEFAULT_WORKSPACE = "65294e9343b8524f94ad5093"

logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if "-v" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)


async def show_part_studio(client, document_id, workspace_id, element) -> int:
    """Print one Part Studio's features and parts. Returns the error count."""
    base = f"/partstudios/d/{document_id}/w/{workspace_id}/e/{element['id']}"
    features = (await client.get(f"{base}/features")).json()
    states = features.get("featureStates", {})

    errors = 0
    for feature in features.get("features", []):
        status = states.get(feature.get("featureId"), {}).get("featureStatus")
        marker = "  " if status == "OK" else "!!"
        print(f"    {marker} {feature.get('name')} "
              f"({feature.get('featureType')}): {status}")
        if status != "OK":
            errors += 1
            namespace = feature.get("namespace")
            if namespace:
                print(f"         defined in {namespace}")

    parts = (await client.get(
        f"/parts/d/{document_id}/w/{workspace_id}/e/{element['id']}")).json()
    if parts:
        names = ", ".join(p.get("name", "?") for p in parts)
        print(f"       parts: {names}")
    else:
        print("       parts: none — nothing in this tab is a solid body")
    return errors


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    document_id = args[0] if args else DEFAULT_DOCUMENT
    workspace_id = args[1] if len(args) > 1 else DEFAULT_WORKSPACE

    try:
        client = OnshapeApiClient.from_env()
    except OnshapeConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 2

    errors = 0
    async with client:
        document = (await client.get(f"/documents/{document_id}")).json()
        print(f"{document.get('name')}  ({document_id})\n")

        elements = (await client.get(
            f"/documents/d/{document_id}/w/{workspace_id}/elements")).json()
        for element in elements:
            print(f"  {element.get('name')}  [{element.get('elementType')}]  "
                  f"{element.get('id')}")
            if element.get("elementType") == "PARTSTUDIO":
                errors += await show_part_studio(
                    client, document_id, workspace_id, element)
            elif element.get("elementType") == "FEATURESTUDIO":
                studio = (await client.get(
                    f"/featurestudios/d/{document_id}/w/{workspace_id}"
                    f"/e/{element['id']}")).json()
                contents = studio.get("contents", "")
                lines = [ln for ln in contents.splitlines() if ln.strip()]
                print(f"       {len(contents):,} characters, {len(lines)} lines")
                if len(lines) <= 2:
                    print("       (header only — this studio defines nothing. Any "
                          "custom feature inserted from it is now stranded.)")
            print()

    if errors:
        print(f"{errors} feature(s) in error.")
        return 1
    print("No features in error.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
