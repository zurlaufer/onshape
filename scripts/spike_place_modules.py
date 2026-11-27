"""Can the copilot place four swerve modules on a frame, with no mates?

`frc_assemble` puts parts where their Part Studio has them. A COTS module is
the case it cannot cover: one imported sub-assembly, needed at four corners,
each rotated. The 2023 session (2026-11-27) imported the SDS MK4i STEP and
built a frame drilled for it. This measures whether a rigid transform per
corner, FITTED from the geometry and not assumed, puts every mounting screw
in its hole.

Method:

  1. The module's six "Screw, 10-32 X 2.5" are its frame-mounting screws.
     Their axes, in the module's own coordinates, come from the module
     assembly's occurrence transforms and the parts' cylinder faces
     (bodydetails, not the scarce FeatureScript evaluate bucket).
  2. The frame's holes are the vertical 0.201 in cylinders of its tubes.
  3. For each quarter-turn rotation, every (screw 0 -> hole) pairing proposes
     a translation; keep those that put all six screws on holes within
     TOLERANCE_MM. Expect exactly four: one per corner. More would mean the
     pattern cannot tell corners apart, fewer that the frame is not drilled
     for this module.
  4. Z: the tube must sit between the module's two plates. The fit is only
     accepted if both plate faces meet the tube faces.
  5. In a new tab, assemble the robot's Part Studios with frc_assemble's
     own code, insert the module four times, move each into place, and add
     them to the robot group.
  6. Read the robot back and measure every screw against every hole from
     the robot's own occurrence transforms, then compare Onshape's bounding
     box with the one the parts predict.

RESULT, 2026-11-27: exactly four placements, one per corner and quarter
turn, every screw on a hole to 0.0000 mm, all 24 holes used, and the tube
meets both module plates (z 44.45 and 95.25) with no Z offset. The first
write used an ABSOLUTE occurrence transform: Onshape applied it to every
part inside the module, which collapsed onto its corner, while a check that
composed transforms itself still said 0.0000 mm. See step 6.

    ./.venv/Scripts/python.exe scripts/spike_place_modules.py [--write]

Without --write it stops after step 4. About 16 requests with it, none on
the evaluate bucket. Writes only to a new SPIKE robot tab of the scratch
document.
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402
from src.api.write_policy import WritePolicy  # noqa: E402
from src.skills.assemble import (  # noqa: E402
    GROUP_NAME, _group_feature, _group_members, assemble)

DID = "e424515893d4f1ab8b23e5c4"
WID = "65294e9343b8524f94ad5093"
MODULE_ASSEMBLY = "322dbe790faa5351d9e4cc2b"   # "SDS MK4i Swerve Module, 10-32, Falcons"
MODULE_PARTS = "9639629d2614fe40724dbf1f"      # its Part Studio, from the STEP import
FRAME = "48844594a8ce8cbe8a0d9fc5"             # "Drivetrain"
ELECTRONICS = "ce67ce162390112303ec81ab"
INTAKE = "d361c0b1ed991dc8390ccfd3"
SCREW = "10-32 X 2.5"
TOLERANCE_MM = 0.05
BASE = f"d/{DID}/w/{WID}"


def matrix(t):
    return [t[0:4], t[4:8], t[8:12], t[12:16]]


def apply(T, p):
    return [sum(T[r][c] * v for c, v in enumerate(list(p) + [1.0])) for r in range(3)]


def turn(T, a):
    return [sum(T[r][c] * a[c] for c in range(3)) for r in range(3)]


def compose(A, B):
    return [[sum(A[r][k] * B[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def leaf(definition, path):
    """The part instance an occurrence path ends at."""
    subs = {(s["elementId"], s.get("configuration", "default")): s
            for s in definition["subAssemblies"]}
    instances = definition["rootAssembly"]["instances"]
    for depth, iid in enumerate(path):
        inst = next(i for i in instances if i["id"] == iid)
        if depth == len(path) - 1:
            return inst
        instances = subs[(inst["elementId"], inst.get("configuration", "default"))]["instances"]


def screw_axes(definition, bodies, root=None):
    """(x, y) in mm of every mounting screw's axis, plus its axis direction.

    `root` maps the occurrence's own transform into another frame; the spike
    uses it to re-measure screws inside the robot assembly.
    """
    out = []
    for occ in definition["rootAssembly"]["occurrences"]:
        inst = leaf(definition, occ["path"])
        if inst["type"] != "Part" or SCREW not in inst["name"]:
            continue
        T = matrix(occ["transform"])
        if root is not None:
            T = compose(root, T)
        cylinders = [f["surface"] for f in bodies[inst["partId"]]["faces"]
                     if f["surface"]["type"] == "CYLINDER"]
        shank = min(cylinders, key=lambda s: s["radius"])
        o = apply(T, [shank["origin"][k] for k in "xyz"])
        a = turn(T, [shank["axis"][k] for k in "xyz"])
        out.append(((o[0] * 1000, o[1] * 1000), a, occ["path"]))
    return out


def frame_holes(bodies):
    holes = set()
    for body in bodies:
        if "2x1" not in body["properties"]["name"]:
            continue
        for f in body["faces"]:
            s = f["surface"]
            if (s["type"] == "CYLINDER" and abs(abs(s["axis"]["z"]) - 1) < 1e-9
                    and s["radius"] * 1000 < 4):
                holes.add((round(s["origin"]["x"] * 1000, 4), round(s["origin"]["y"] * 1000, 4)))
    return sorted(holes)


def horizontal_planes(bodies, name_part):
    zs = set()
    for body in bodies:
        if name_part not in body["properties"]["name"]:
            continue
        for f in body["faces"]:
            s = f["surface"]
            if s["type"] == "PLANE" and abs(abs(s["normal"]["z"]) - 1) < 1e-9:
                zs.add(round(s["origin"]["z"] * 1000, 3))
    return zs


def fit(screws, holes):
    """Every quarter-turn placement that puts all screws on holes."""
    found = []
    for quarter in range(4):
        c, s = round(math.cos(quarter * math.pi / 2)), round(math.sin(quarter * math.pi / 2))
        rotated = [(c * x - s * y, s * x + c * y) for x, y in screws]
        for hx, hy in holes:
            tx, ty = hx - rotated[0][0], hy - rotated[0][1]
            worst = max(min(math.dist((x + tx, y + ty), h) for h in holes) for x, y in rotated)
            if worst <= TOLERANCE_MM and not any(
                    q == quarter and math.dist((tx, ty), t) < 1 for q, t, _ in found):
                found.append((quarter, (tx, ty), worst))
    return found


def transform(quarter, t_mm):
    c, s = round(math.cos(quarter * math.pi / 2)), round(math.sin(quarter * math.pi / 2))
    return [c, -s, 0, t_mm[0] / 1000, s, c, 0, t_mm[1] / 1000, 0, 0, 1, 0, 0, 0, 0, 1]


async def main(write: bool) -> int:
    policy = WritePolicy.from_manifest()
    if not policy.allows(DID):
        raise policy.refuse(DID)
    async with OnshapeApiClient.from_env() as client:
        module = (await client.get(f"/assemblies/{BASE}/e/{MODULE_ASSEMBLY}")).json()
        parts = {b["id"]: b for b in (await client.get(
            f"/partstudios/{BASE}/e/{MODULE_PARTS}/bodydetails")).json()["bodies"]}
        frame = (await client.get(f"/partstudios/{BASE}/e/{FRAME}/bodydetails")).json()["bodies"]

        screws = screw_axes(module, parts)
        holes = frame_holes(frame)
        vertical = all(abs(abs(a[2]) - 1) < 1e-9 for _, a, _ in screws)
        print(f"1. module screws: {len(screws)}, all vertical: {vertical}")
        for (x, y), _, _ in screws:
            print(f"     ({x:9.3f}, {y:9.3f})")
        print(f"2. frame holes: {len(holes)}")

        placements = fit([p for p, _, _ in screws], holes)
        print(f"3. placements putting all {len(screws)} screws on holes: {len(placements)}")
        for quarter, (tx, ty), worst in placements:
            print(f"     rotate {quarter * 90:3d} deg, corner at ({tx:8.3f}, {ty:8.3f}) mm, "
                  f"worst screw-to-hole {worst:.4f} mm")
        used = {h for q, t, _ in placements for h in holes for x, y in
                [((round(math.cos(q * math.pi / 2)) * sx - round(math.sin(q * math.pi / 2)) * sy) + t[0],
                  (round(math.sin(q * math.pi / 2)) * sx + round(math.cos(q * math.pi / 2)) * sy) + t[1])
                 for (sx, sy), _, _ in screws] if math.dist((x, y), h) <= TOLERANCE_MM}
        print(f"   frame holes used: {len(used)} of {len(holes)}")

        # The modelled module's planes, in its own coordinates (the import
        # left its Part Studio bodies at their assembly positions for these
        # plates: checked by the screws, which share the same transforms).
        tube_z = horizontal_planes(frame, "2x1")
        plate_z = set()
        for occ in module["rootAssembly"]["occurrences"]:
            inst = leaf(module, occ["path"])
            if inst["type"] == "Part" and "Master Part" in inst["name"]:
                T = matrix(occ["transform"])
                for f in parts[inst["partId"]]["faces"]:
                    s = f["surface"]
                    if s["type"] == "PLANE" and abs(abs(turn(T, [s["normal"][k] for k in "xyz"])[2]) - 1) < 1e-9:
                        plate_z.add(round(apply(T, [s["origin"][k] for k in "xyz"])[2] * 1000, 3))
        meet = sorted(tube_z & plate_z)
        print(f"4. tube faces z={sorted(tube_z)}; module plate faces meeting them: {meet}")
        if len(placements) != 4 or len(meet) < 2:
            print("   NOT a clean fit; stopping before any write.")
            return 1
        if not write:
            print("\nDry run. Pass --write to insert and place the four modules.")
            return 0

        # A fresh tab: the whole robot, parts by frc_assemble's own code.
        aid = (await client.post(f"/assemblies/{BASE}",
                                 json_body={"name": "SPIKE robot"})).json()["id"]
        target = f"/assemblies/{BASE}/e/{aid}"
        studios = [(FRAME, "Drivetrain"), (ELECTRONICS, "Electronics"), (INTAKE, "Intake")]
        out = await assemble(client, document_id=DID, workspace_id=WID,
                             assembly_id=aid, studios=studios)
        print(f"\n5. frc_assemble: {out['inserted']} parts, group {out['group']}")

        for _ in placements:
            await client.post(f"{target}/instances", json_body={
                "documentId": DID, "elementId": MODULE_ASSEMBLY, "isAssembly": True})
        robot = (await client.get(target)).json()
        new = [i["id"] for i in robot["rootAssembly"]["instances"]
               if i.get("elementId") == MODULE_ASSEMBLY]
        # RELATIVE, measured: an absolute transform on a sub-assembly
        # instance is applied to every part inside it, and the module
        # collapses onto its origin (all 146 occurrences, 2026-11-27). A new
        # instance sits at identity, so the relative move IS the placement.
        for iid, (quarter, t, _) in zip(new, placements):
            await client.post(f"{target}/occurrencetransforms", json_body={
                "occurrences": [{"path": [iid]}], "transform": transform(quarter, t),
                "isRelative": True})

        features = (await client.get(f"{target}/features")).json()
        group = next(f for f in features["features"] if f.get("name") == GROUP_NAME)
        ids = sorted(set(_group_members(group)) | set(new))
        reply = (await client.post(
            f"{target}/features/featureid/{quote(group['featureId'], safe='')}",
            json_body=_group_feature(ids, group["featureId"]))).json()
        print(f"   modules: {len(new)}; group now {len(ids)} members, status "
              f"{(reply.get('featureState') or {}).get('featureStatus')}")

        # Measured from what Onshape reports for the ROBOT: its own nested
        # occurrence transforms, not the module's composed with ours. The
        # first version of this check composed them, and passed at 0.0000 mm
        # on a module whose parts had all collapsed onto one point.
        robot = (await client.get(target)).json()
        measured = screw_axes(robot, parts)
        worst = max(min(math.dist(p, h) for h in holes) for p, _, _ in measured)
        on = {h for p, _, _ in measured for h in holes if math.dist(p, h) <= TOLERANCE_MM}
        print(f"6. screws in the robot: {len(measured)}, worst screw-to-hole "
              f"{worst:.4f} mm, holes filled {len(on)} of {len(holes)}")

        # Independent of every transform above: Onshape's own boxes.
        keys = ("lowX", "lowY", "lowZ", "highX", "highY", "highZ")
        mbox = (await client.get(f"/assemblies/{BASE}/e/{MODULE_ASSEMBLY}/boundingboxes")).json()
        expect = []
        for eid, _ in studios:
            b = (await client.get(f"/partstudios/{BASE}/e/{eid}/boundingboxes")).json()
            expect.append([b[k] * 1000 for k in keys])
        for quarter, t, _ in placements:
            T = matrix(transform(quarter, t))
            corners = [apply(T, (x, y, z)) for x in (mbox["lowX"], mbox["highX"])
                       for y in (mbox["lowY"], mbox["highY"]) for z in (mbox["lowZ"], mbox["highZ"])]
            expect.append([min(c[i] for c in corners) * 1000 for i in range(3)]
                          + [max(c[i] for c in corners) * 1000 for i in range(3)])
        union = [min(b[i] for b in expect) for i in range(3)] + [max(b[i] for b in expect) for i in range(3, 6)]
        box = (await client.get(f"{target}/boundingboxes")).json()
        got = [box[k] * 1000 for k in keys]
        print(f"7. robot box (mm):    {[round(v, 2) for v in got]}")
        print(f"   expected from parts: {[round(v, 2) for v in union]}")
        print(f"   max difference: {max(abs(a - b) for a, b in zip(got, union)):.3f} mm")
        print(f"\nopen: https://cad.onshape.com/documents/{DID}/w/{WID}/e/{aid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--write" in sys.argv)))
