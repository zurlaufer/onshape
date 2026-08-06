"""Spike 3a: measure the real cost of an Onshape "spatial context graph".

The project brief asserts that lightweight context (bounding boxes, mass
properties, transforms, configuration variables) runs ~20 KB, versus 50 MB+ for
raw geometry, and the entire data-reduction architecture rests on that number.
It was never measured. This measures it.

Throwaway by design — the point is the number, not the code.

    python scripts/spike_3a_payload.py [documentName]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402

TARGET = sys.argv[1] if len(sys.argv) > 1 else "asm-try1"


async def measure(client, label, path, query=None):
    """Fetch one endpoint and report status and byte count."""
    try:
        r = await client.get(path, query=query)
    except Exception as exc:  # noqa: BLE001 - a spike reports whatever happens
        detail = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"  {label:<34} {detail or type(exc).__name__}")
        return 0, None
    size = len(r.content)
    print(f"  {label:<34} 200   {size:>9,} bytes")
    return size, r.json()


async def main() -> int:
    async with OnshapeApiClient.from_env() as c:
        docs = (await c.get("/documents", query={"limit": 20})).json().get("items") or []
        doc = next((d for d in docs if d.get("name") == TARGET), None)
        if doc is None:
            print(f"No document named {TARGET!r}. Available: "
                  f"{[d.get('name') for d in docs]}")
            return 1

        did = doc["id"]
        wid = (doc.get("defaultWorkspace") or {}).get("id")
        els = (await c.get(f"/documents/d/{did}/w/{wid}/elements")).json()
        asm = next((e for e in els if e["elementType"] == "ASSEMBLY"), None)
        ps = next((e for e in els if e["elementType"] == "PARTSTUDIO"), None)

        print(f"document : {TARGET}  ({did})")
        print(f"assembly : {asm['name'] if asm else '(none)'}")
        print(f"partstudio: {ps['name'] if ps else '(none)'}\n")

        total = 0
        print("--- lightweight context (what we intend to fetch) ---")

        if asm:
            base = f"/assemblies/d/{did}/w/{wid}/e/{asm['id']}"
            size, definition = await measure(c, "assembly definition", base)
            total += size
            size, _ = await measure(c, "assembly boundingboxes", f"{base}/boundingboxes")
            total += size
            size, _ = await measure(c, "assembly massproperties", f"{base}/massproperties")
            total += size

            # A near-empty assembly would make this measurement meaningless, so
            # report how much is actually in it.
            if definition:
                root = definition.get("rootAssembly") or {}
                print(f"\n  [assembly contains {len(root.get('instances') or [])} instances, "
                      f"{len(root.get('occurrences') or [])} occurrences, "
                      f"{len(definition.get('subAssemblies') or [])} subassemblies]")

        if ps:
            pbase = f"/partstudios/d/{did}/w/{wid}/e/{ps['id']}"
            print()
            size, _ = await measure(c, "partstudio massproperties", f"{pbase}/massproperties")
            total += size
            size, _ = await measure(c, "partstudio boundingboxes", f"{pbase}/boundingboxes")
            total += size
            size, _ = await measure(c, "partstudio configuration", f"{pbase}/configuration")
            total += size

        print(f"\n  TOTAL lightweight context: {total:,} bytes "
              f"({total / 1024:.1f} KB)  vs ~20 KB claimed")

        if ps:
            print("\n--- raw geometry, for contrast (what we must NOT fetch) ---")
            pbase = f"/partstudios/d/{did}/w/{wid}/e/{ps['id']}"
            await measure(c, "tessellated faces", f"{pbase}/tessellatedfaces")
            await measure(c, "feature list", f"{pbase}/features")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
