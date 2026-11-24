"""Commit the climber gearbox plate to a scratch document.

    python scripts/verify_plate_geometry.py   # do this first, it is free-ish
    python scripts/build_climber_plate.py

Writes a Feature Studio and inserts (or updates) the custom feature in the
Gearbox Part Studio. Idempotent: run it again after changing the design in
`src/skills/climber_plate.py` and it edits what is already there rather than
stacking up duplicates.

The careful part — write, compile, insert, repoint, and roll back a failed
insert — now lives in `src.skills.apply.persist_feature`, so this script and the
MCP tool cannot drift apart. Two rules it enforces, both learned the hard way:

  * The Feature Studio source is NOT restored afterwards. `apply_module`'s
    default puts the old source back, which is right for verifying a template
    and catastrophic here — a custom feature is pinned to the microversion that
    defined it, so restoring strands it in ERROR forever.

  * Rewriting the source moves that microversion. Any feature already inserted
    still points at the old one, so after every rewrite the inserted feature is
    updated with the fresh namespace. Skipping that is precisely how the
    document ended up with a dead `Boom 1`.

Writes are confined to the allowlist in `robot.toml`, enforced inside the client
itself. Never point this at a team's robot document.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient, OnshapeConfigError  # noqa: E402
from src.api.write_policy import WritePolicy, WriteRefused  # noqa: E402
from src.skills.apply import persist_feature  # noqa: E402
from src.skills.climber_plate import (  # noqa: E402
    VOLUME_TOLERANCE_PCT,
    plate_geometry,
    render_module,
)

DOCUMENT_ID = "e424515893d4f1ab8b23e5c4"  # asm-try1
WORKSPACE_ID = "65294e9343b8524f94ad5093"
PART_STUDIO_ID = "9667e7d938f0d5482922646e"  # Gearbox
FEATURE_STUDIO_NAME = "Climber Gearbox"
FEATURE_NAME = "Climber Gearbox Plate"

logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if "-v" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)


async def ensure_feature_studio(client: OnshapeApiClient) -> str:
    """Return the Feature Studio's element id, creating the tab if needed."""
    base = f"/documents/d/{DOCUMENT_ID}/w/{WORKSPACE_ID}/elements"
    elements = (await client.get(base)).json()
    for element in elements:
        if element.get("name") == FEATURE_STUDIO_NAME:
            print(f"reusing Feature Studio {FEATURE_STUDIO_NAME!r}: {element['id']}")
            return element["id"]

    created = (await client.post(
        f"/featurestudios/d/{DOCUMENT_ID}/w/{WORKSPACE_ID}",
        json_body={"name": FEATURE_STUDIO_NAME},
    )).json()
    print(f"created Feature Studio {FEATURE_STUDIO_NAME!r}: {created['id']}")
    return created["id"]


async def main() -> int:
    plate = plate_geometry()
    module = render_module(plate)

    print(f"plate {plate.width_mm:.4f} x {plate.height_mm:.4f} x "
          f"{plate.thickness_mm:g} mm, {len(plate.layout.shafts)} bores, "
          f"{plate.mass_g:.1f} g in 6061")
    print(f"module is {len(module):,} characters\n")

    try:
        policy = WritePolicy.from_manifest()
        if not policy.allows(DOCUMENT_ID):
            raise policy.refuse(DOCUMENT_ID)
    except WriteRefused as exc:
        print(f"Refused:\n  {exc}", file=sys.stderr)
        return 2

    try:
        client = OnshapeApiClient.from_env()
    except OnshapeConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 2

    async with client:
        studio_id = await ensure_feature_studio(client)

        # The write / compile / insert / repoint sequence this script worked out
        # now lives in `persist_feature`, so the MCP tool and this script cannot
        # drift apart. It also adds what this script lacked: an insert that
        # fails to regenerate is rolled back rather than left in the tree.
        result = await persist_feature(
            client, module,
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
            feature_studio_id=studio_id,
            part_studio_id=PART_STUDIO_ID,
            feature_name=FEATURE_NAME,
        )
        for problem in result.problems:
            print(f"  [{problem.severity.value}] {problem.message}")
        if not result.ok:
            print("\nRun scripts/verify_plate_geometry.py for a line number.",
                  file=sys.stderr)
            return 1

        print(f"feature type : {result.feature_type}")
        print(f"namespace    : {result.namespace}")
        print("updated existing feature" if result.updated
              else "inserted a new feature")

        part_studio = (
            f"/partstudios/d/{DOCUMENT_ID}/w/{WORKSPACE_ID}/e/{PART_STUDIO_ID}"
        )
        features = (await client.get(f"{part_studio}/features")).json()
        states = features.get("featureStates", {})
        # Judge only OUR feature. The Gearbox studio is shared scratch space:
        # on 2026-11-24 a Tube Converter spike's `Extrude 1` sat there at INFO
        # (its body consumed by the converter) and failed this script although
        # the plate was perfect. Other features are listed, not judged.
        print("\nGearbox Part Studio:")
        failed = []
        for feature in features.get("features", []):
            status = states.get(feature.get("featureId"), {}).get("featureStatus")
            ours = feature.get("name") == FEATURE_NAME
            print(f"  {feature.get('name')} ({feature.get('featureType')}): {status}"
                  f"{'' if ours else '  (not ours)'}")
            if ours and status != "OK":
                failed.append(feature.get("name"))

        parts = (await client.get(
            f"/parts/d/{DOCUMENT_ID}/w/{WORKSPACE_ID}/e/{PART_STUDIO_ID}")).json()
        print(f"\nparts: {[p.get('name') for p in parts] or 'none'}")
        if parts:
            # The studio can hold other parts, so `parts[0]` is not necessarily
            # the plate. Measure each and keep the closest: a stranger matching
            # the prediction to 0.001 % by coincidence is not a real risk.
            volumes = []
            for part in parts:
                properties = (await client.get(
                    f"/parts/d/{DOCUMENT_ID}/w/{WORKSPACE_ID}/e/{PART_STUDIO_ID}"
                    f"/partid/{part['partId']}/massproperties")).json()
                body = next(iter(properties.get("bodies", {}).values()))
                volumes.append((body.get("volume", [0.0])[0] * 1e9, part.get("name")))
            volume_mm3, name = min(
                volumes, key=lambda v: abs(v[0] - plate.volume_mm3))
            print(f"measured part          : {name}")
            drift_pct = (abs(volume_mm3 - plate.volume_mm3)
                         / plate.volume_mm3 * 100.0)
            print(f"volume in the document : {volume_mm3:,.4f} mm3")
            print(f"predicted              : {plate.volume_mm3:,.4f} mm3")
            print(f"difference             : {drift_pct:.5f}%  "
                  f"(tolerance {VOLUME_TOLERANCE_PCT}%)")
            if drift_pct > VOLUME_TOLERANCE_PCT:
                print("\nWARNING: the committed solid is not the one we computed.")
                return 1

        if failed:
            print(f"\nFAILED — features in error: {', '.join(failed)}")
            return 1

    print("\nDone. The Feature Studio source must stay as written — restoring it "
          "would strand the feature just inserted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
