"""Can the copilot assemble a robot it built in robot coordinates?

The question that decides whether `frc_assemble` (the 8th tool slot) is worth
building. A workspace session on 2026-11-27 built a 2023 robot top-down: one
layout feature, and every subsystem in its own Part Studio, already placed in
robot coordinates. It then stopped at "final assembly by hand, my tools can't
write to an assembly". If a whole Part Studio inserted through the API lands
at its own coordinates (identity transform), the assembly needs no mates at
all, and the failure STATUS.md held against an assembly builder ("a wrong
mate fails silently") cannot happen.

What this measures, in order:

  1. Insert one whole Part Studio into a NEW assembly tab. How many instances
     does one insert create: one per part, or one composite?
  2. The occurrence transforms that come back. Identity, or not?
  3. The assembly's bounding box against the Part Studio's. Equal means the
     parts sit where they were designed, whatever the transforms say.
  4. A Group feature over the instances, so the robot cannot be dragged apart.
  5. Whether an instance names its source Part Studio, which is what lets a
     re-run skip what is already there.

    ./.venv/Scripts/python.exe scripts/spike_assemble.py

RESULT, 2026-11-27, on the "Drivetrain" tab (layout + MK4i frame, 5 parts):

  1. One whole-Part-Studio insert made 5 instances, one per part, each naming
     its source elementId and partId. So a re-run can skip what is there, and
     a part added to the studio later does NOT appear by itself: the tool
     must insert what is missing.
  2. All 5 occurrence transforms are identity.
  3. Assembly box == Part Studio box, to 0.0 mm: [-330.2, -330.2, 41.275] to
     [330.2, 330.2, 95.25].
  4. Group regenerates OK, but only with the parameter shape below. The first
     attempt (a top-level "occurrences" field) was stored with no parameters
     and went ERROR; it was fixed by updating that feature in place.
  5. Cost: 7 requests, on buckets of 1000-3000. None on evaluate.

So static placement needs no mates, and nothing can be silently mis-mated.

About 8 requests, none on the FeatureScript evaluate bucket. Writes only to a
new tab in the scratch document; the Part Studio it inserts is only read.
Deletes nothing: the copilot never holds OAuth2Delete.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402
from src.api.write_policy import WritePolicy  # noqa: E402

# Scratch only. Never a team's robot.
DID = "e424515893d4f1ab8b23e5c4"
WID = "65294e9343b8524f94ad5093"
#: "Drivetrain" tab of the 2026-11-27 session: layout plus frame, in robot coordinates.
PART_STUDIO = "48844594a8ce8cbe8a0d9fc5"
ASSEMBLY_NAME = "SPIKE assemble"

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _with_body(original):
    """A spike needs Onshape's error text, which the client drops on purpose."""
    def raise_for_status(method, api_path, response):
        if response.status_code >= 400:
            print(f"  !! {response.status_code} {method} {api_path}\n     {response.text[:800]}")
        return original(method, api_path, response)
    return staticmethod(raise_for_status)


OnshapeApiClient._raise_for_status = _with_body(OnshapeApiClient._raise_for_status)


def _box(data: dict) -> list[float]:
    keys = ("lowX", "lowY", "lowZ", "highX", "highY", "highZ")
    return [round(data[k] * 1000, 3) for k in keys]


async def main() -> int:
    policy = WritePolicy.from_manifest()
    if not policy.allows(DID):
        raise policy.refuse(DID)

    async with OnshapeApiClient.from_env() as client:
        base = f"d/{DID}/w/{WID}"

        studio_box = _box((await client.get(
            f"/partstudios/{base}/e/{PART_STUDIO}/boundingboxes")).json())
        print(f"part studio box (mm): {studio_box}")

        created = (await client.post(
            f"/assemblies/{base}", json_body={"name": ASSEMBLY_NAME})).json()
        aid = created["id"]
        print(f"new assembly tab    : {aid}")

        await client.post(f"/assemblies/{base}/e/{aid}/instances", json_body={
            "documentId": DID,
            "elementId": PART_STUDIO,
            "isWholePartStudio": True,
            "includePartTypes": ["PARTS"],
        })

        asm = (await client.get(f"/assemblies/{base}/e/{aid}")).json()
        root = asm["rootAssembly"]
        instances = root.get("instances", [])
        print(f"\n1. instances from one insert: {len(instances)}")
        for inst in instances:
            print(f"     {inst.get('type'):<8} {inst.get('name')!s:<28} "
                  f"elementId={inst.get('elementId')} partId={inst.get('partId')}")

        off = [o for o in root.get("occurrences", [])
               if [round(v, 9) for v in o["transform"]] != IDENTITY]
        print(f"\n2. occurrences: {len(root.get('occurrences', []))}, "
              f"not at identity: {len(off)}")
        for o in off[:5]:
            print(f"     {o['path']} {o['transform']}")

        asm_box = _box((await client.get(
            f"/assemblies/{base}/e/{aid}/boundingboxes")).json())
        diff = max(abs(a - b) for a, b in zip(asm_box, studio_box))
        print(f"\n3. assembly box (mm): {asm_box}\n   max difference: {diff} mm")

        print("\n5. source named on each instance:",
              all(i.get("elementId") == PART_STUDIO for i in instances))

        if len(instances) >= 2:
            # The parameter id comes from GET .../featurespecs ("mateGroup").
            # A top-level "occurrences" field is accepted, silently dropped,
            # and the group regenerates as ERROR with no parameters (measured).
            group = {"feature": {
                "btType": "BTMMateGroup-65",
                "featureType": "mateGroup",
                "name": "Robot (copilot)",
                "parameters": [{
                    "btType": "BTMParameterQueryWithOccurrenceList-67",
                    "parameterId": "occurrencesQuery",
                    "queries": [
                        {"btType": "BTMIndividualOccurrenceQuery-626", "path": [i["id"]]}
                        for i in instances],
                }],
            }}
            try:
                await client.post(f"/assemblies/{base}/e/{aid}/features", json_body=group)
            except Exception as exc:  # a spike reports, it does not stop
                print(f"\n4. group failed: {exc}")
            else:
                feats = (await client.get(f"/assemblies/{base}/e/{aid}/features")).json()
                states = feats.get("featureStates", {})
                for f in feats.get("features", []):
                    print(f"\n4. feature {f.get('name')!r} type={f.get('btType')} "
                          f"status={states.get(f.get('featureId'), {}).get('featureStatus')}")
        else:
            print("\n4. one instance only: nothing to group")

        print("\nquota:", json.dumps(client.quota.snapshot(), indent=1)[:1500])
        print(f"\nopen: https://cad.onshape.com/documents/{DID}/w/{WID}/e/{aid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
