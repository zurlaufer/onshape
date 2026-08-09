"""Can we drive the FRC community's FeatureScripts instead of writing our own?

The question that decides whether this project should generate geometry at all.
Web research on 2026-08-09 found **37 maintained FRC FeatureScripts** covering
exactly what our three hand-written templates do, and better — `Tube Converter`
punches tube, `CheeseIt!` does isogrid and hex lightening, `Spur Gear` makes
gears. If those can be driven from the API, our templates are dead weight.

ANSWER: YES. The namespace for a feature living in someone else's document is

    d{documentId}::v{versionId}::e{elementId}::m{microversionId}

The inner `e...::m...` half comes back from `featurespecs`; the `d...::v...`
half has to be prepended by hand. Read the spec through a **version**, not a
workspace — the workspace microversion is not reachable from another document
and the insert 404s. Pinning to a version is also the safer choice: the library
cannot change under a design that already shipped.

    ./.venv/Scripts/python.exe scripts/spike_community_featurescript.py

Costs about 6 requests. Inserts nothing permanently: the feature is removed
again, because a feature whose definition we cannot reach is exactly the
stranded-`Boom 1` failure.

WHAT THIS DOES NOT YET PROVE: setting parameters. Tube Converter has 20, and its
spec is 30 KB of BTParameterSpec types that each need mapping to a value shape.
The cheap way in, from the Onshape forum, is to add the feature once by hand in
the UI, `GET /features`, and copy the parameter block that comes back — the read
shape and the write shape are the same.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402
from src.api.write_policy import WritePolicy  # noqa: E402

# Scratch only (FM2). Never a team's robot.
DID = "e424515893d4f1ab8b23e5c4"
WID = "65294e9343b8524f94ad5093"
PART_STUDIO = "4fa46792842ff31b21179dad"

#: A public community library. Tube Converter and Gusset Generator share it.
LIB_DOCUMENT = "95c00401c440b44ad8799ef5"
LIB_ELEMENT = "7f62400703032fab9a7a3320"

FEATURE_NAME = "SPIKE community feature"


async def list_features(client, label: str) -> list[dict]:
    data = (await client.get(
        f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/features")).json()
    states = data.get("featureStates", {})
    rows = data.get("features", [])
    print(f"  [{label}] {len(rows)} feature(s)")
    for f in rows:
        status = states.get(f.get("featureId"), {}).get("featureStatus")
        print(f"      {f.get('name'):<26} {status}")
    return rows


async def main() -> int:
    policy = WritePolicy.from_manifest()
    if not policy.allows(DID):
        raise policy.refuse(DID)

    async with OnshapeApiClient.from_env() as client:
        versions = (await client.get(
            f"/documents/d/{LIB_DOCUMENT}/versions")).json()
        if not versions:
            print("FAILED: the library publishes no versions.")
            return 1
        version = versions[-1]
        print(f"library version   : {version.get('name')} ({version['id']})")

        # Through the VERSION. A workspace microversion is not reachable from
        # another document and the insert 404s -- that is the whole trap.
        spec = (await client.get(
            f"/featurestudios/d/{LIB_DOCUMENT}/v/{version['id']}"
            f"/e/{LIB_ELEMENT}/featurespecs")).json()["featureSpecs"][0]

        namespace = (f"d{LIB_DOCUMENT}::v{version['id']}::{spec['namespace']}")
        print(f"feature           : {spec['featureTypeName']}")
        print(f"parameters        : {len(spec.get('parameters') or [])}")
        print(f"external namespace: {namespace}")
        print()

        await list_features(client, "before")

        print("\ninserting, with no parameters set...")
        created = await client.post(
            f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/features",
            json_body={"feature": {
                "btType": "BTMFeature-134",
                "featureType": spec["featureType"],
                "name": FEATURE_NAME,
                "namespace": namespace,
                "parameters": [],
            }},
        )
        feature_id = created.json().get("feature", {}).get("featureId")
        print(f"  ACCEPTED - featureId {feature_id}")
        print()

        rows = await list_features(client, "after")
        print("\n  An ERROR status here is the expected result: nothing was "
              "selected for it to work on.")
        print("  What is proven is that Onshape resolved a FOREIGN namespace.")

        print("\nremoving it - never leave a feature we cannot reach...")
        try:
            await client.request(
                "DELETE",
                f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}"
                f"/features/featureid/{feature_id}")
        finally:
            print()
            remaining = await list_features(client, "restored")
            if any(f.get("name") == FEATURE_NAME for f in remaining):
                print("\n  WARNING: cleanup failed. Remove it by hand.")
                return 1

        print("\nPASSED: community FeatureScripts can be driven from the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
