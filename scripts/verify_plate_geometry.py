"""Check the climber plate against Onshape's kernel before anything is committed.

Layer 5 only: builds the plate inside the evaluate endpoint, measures it, and
compares against the arithmetic in `src/skills/climber_plate.py`. Nothing is
persisted and no document is modified, so this is safe to run against anything.

    python scripts/verify_plate_geometry.py

Costs 1 request, on the FeatureScript evaluate bucket — the scarce one, roughly
84 a day. Exit code 0 means the two models agree.

The point is not that Onshape might compute a cylinder wrong. It is that our
arithmetic describes the solid we think we are making, and the only way to know
that is to build it and weigh it. This is also where FeatureScript mistakes
surface with a line number; the Feature Studio write path reports nothing.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient, OnshapeConfigError  # noqa: E402
from src.skills.apply import probe_body  # noqa: E402
from src.skills.climber_plate import (  # noqa: E402
    VOLUME_TOLERANCE_PCT,
    plate_geometry,
    render_probe,
)

DOCUMENT_ID = "e424515893d4f1ab8b23e5c4"  # asm-try1
WORKSPACE_ID = "65294e9343b8524f94ad5093"
PART_STUDIO_ID = "9667e7d938f0d5482922646e"  # Gearbox

logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if "-v" in sys.argv else logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)


async def main() -> int:
    plate = plate_geometry()

    print("Predicted, from Python alone")
    print(f"  plate          : {plate.width_mm:.4f} x {plate.height_mm:.4f} x "
          f"{plate.thickness_mm:g} mm")
    print(f"  solid          : {plate.solid_volume_mm3:>14,.4f} mm3")
    print(f"  one bore       : {plate.bore_volume_mm3:>14,.4f} mm3 "
          f"x {len(plate.layout.shafts)}")
    print(f"  finished       : {plate.volume_mm3:>14,.4f} mm3")
    print(f"  mass in 6061   : {plate.mass_g:>14,.1f} g")
    print()

    try:
        client = OnshapeApiClient.from_env()
    except OnshapeConfigError as exc:
        print(f"Configuration problem:\n  {exc}", file=sys.stderr)
        return 2

    async with client:
        result = await probe_body(
            client, render_probe(plate),
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
            part_studio_id=PART_STUDIO_ID,
        )

    if not result.ok:
        print("FAILED — Onshape rejected the probe.")
        for problem in result.problems:
            where = f" (line {problem.line})" if problem.line else ""
            print(f"  [{problem.severity.value}] {problem.message}{where}")
            if problem.suggestion:
                print(f"      {problem.suggestion}")
        return 1

    measured = result.value
    print("Measured by Onshape's kernel")
    print(f"  solid          : {measured['before_mm3']:>14,.4f} mm3")
    print(f"  finished       : {measured['after_mm3']:>14,.4f} mm3")
    print(f"  bounding size  : {measured['width_mm']:.4f} x "
          f"{measured['height_mm']:.4f} x {measured['thick_mm']:.4f} mm")
    print(f"  faces          : {measured['faces']:>14,.0f}")
    print()

    error_pct = abs(measured["after_mm3"] - plate.volume_mm3) / plate.volume_mm3 * 100
    faces_ok = int(measured["faces"]) == plate.face_count

    print(f"Volume disagreement : {error_pct:.4f}%  "
          f"(tolerance {VOLUME_TOLERANCE_PCT}%)")
    print(f"Face count          : {int(measured['faces'])}, "
          f"expected {plate.face_count} "
          f"(6 box faces + {len(plate.layout.shafts)} bore walls)")

    for problem in result.problems:
        print(f"  [{problem.severity.value}] {problem.message}")

    if error_pct > VOLUME_TOLERANCE_PCT:
        print("\nFAILED — the two models disagree by more than rounding.")
        return 1
    if not faces_ok:
        print("\nFAILED — wrong face count. A bore may have merged with an edge, "
              "or missed the plate entirely.")
        return 1

    print("\nPASS — our arithmetic and Onshape's kernel describe the same solid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
