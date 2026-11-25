"""End-to-end geometry check: does our arithmetic match Onshape's kernel?

Every other verification in this project stops at "Onshape accepted it". This
one asks the harder question: **is the number we told the student true?**

`plate_pocket_pattern` reports how many grams a pocket layout removes. That
figure comes from Python — pocket size from the grid, times depth, times the
density of 6061 aluminium. Nothing had ever checked it against the geometry a
CAD kernel actually produces.

The method works because of a capability found on 2026-08-09: **the evaluate
endpoint runs real modelling operations and persists nothing.** So one request
can build a test plate, pocket it, and measure the volume before and after —
a self-contained experiment that leaves no trace in the document. It needs no
prepared geometry, which matters, because the scratch Part Studio turned out to
hold only sheet bodies and every earlier "verified live" run had been operating
on surfaces without saying so.

The grid arithmetic under test is spliced in from the template itself
(`_grid_source`), not copied here, so this cannot pass against a stale copy.

    ./.venv/Scripts/python.exe scripts/verify_pocket_geometry.py
    ./.venv/Scripts/python.exe scripts/verify_pocket_geometry.py --rotate 30

`--rotate` draws the plate at an angle on Top. That is the case that broke the
grid before 2026-11-25, and the removed volume must not change with it.

Costs 1 request on the FeatureScript evaluate bucket (ceiling ~100/day).
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402
from src.skills.apply import decode_fsvalue, probe_body  # noqa: E402
from src.skills.templates import TEMPLATES  # noqa: E402
from src.skills.templates.plate_pocket_pattern import (  # noqa: E402
    ALUMINIUM_6061_DENSITY,
)

# Scratch document only (FM2). Never a team's robot.
DOCUMENT_ID = "e424515893d4f1ab8b23e5c4"  # asm-try1
WORKSPACE_ID = "65294e9343b8524f94ad5093"
PART_STUDIO_ID = "4fa46792842ff31b21179dad"

PARAMS = {
    "plate_width": 150.0,
    "plate_length": 150.0,
    "plate_thickness": 6.35,
    "columns": 3,
    "rows": 3,
    "rib_width": 8.0,
    "border_width": 12.0,
}

#: How far Onshape's measured volume may sit from ours before this fails.
#: Tight on purpose — these are the same rectangles computed twice, so anything
#: beyond rounding means the two models actually disagree.
TOLERANCE = 0.005


def _outline(width: float, length: float, rotation_deg: float) -> str:
    """The plate's sketch. Rotated in the SKETCH, never with a transform.

    A transformed body carries its plane's x axis round with it, so a naive
    box would come out right by luck. Drawing the rectangle at an angle on Top
    is what a student does, and it is the case that broke the grid
    (2026-11-25: 167 x 140 measured for a 150 x 75 plate).
    """
    if not rotation_deg:
        return (f'    skRectangle(base, "outline", {{\n'
                f'        "firstCorner"  : vector(0, 0) * millimeter,\n'
                f'        "secondCorner" : vector({width:g}, {length:g}) * millimeter\n'
                f'    }});')
    a = math.radians(rotation_deg)
    corners = [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))
               for x, y in ((0, 0), (width, 0), (width, length), (0, length))]
    return "\n".join(
        f'    skLineSegment(base, "side{i}", {{ '
        f'"start" : vector({x0:.6f}, {y0:.6f}) * millimeter, '
        f'"end" : vector({x1:.6f}, {y1:.6f}) * millimeter }});'
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(corners, corners[1:] + corners[:1])))


def build_probe(template, params: dict, rotation_deg: float = 0.0) -> str:
    """A lambda that builds a plate, pockets it with the template's own grid,
    and reports the volume it removed."""
    thickness = float(params["plate_thickness"])
    width = float(params["plate_width"])
    length = float(params["plate_length"])
    cols, rows = int(params["columns"]), int(params["rows"])

    plate_face = """(function()
    {
        var planar = qGeometry(qOwnedByBody(testPlate, EntityType.FACE),
                               GeometryType.PLANE);
        var faces = evaluateQuery(context, planar);
        var best = faces[0];
        var bestArea = evArea(context, { "entities" : best });
        for (var f in faces)
        {
            var area = evArea(context, { "entities" : f });
            if (area > bestArea) { best = f; bestArea = area; }
        }
        return best;
    })()"""

    return f"""function(context is Context, queries)
{{
    // --- build a plate to cut, so this needs no prepared geometry ---------
    var plateId = makeId("verifyPlate");
    var base = newSketchOnPlane(context, plateId + "sketch", {{
        "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
    }});
{_outline(width, length, rotation_deg)}
    skSolve(base);
    opExtrude(context, plateId + "extrude", {{
        "entities"  : qSketchRegion(plateId + "sketch"),
        "direction" : vector(0, 0, 1),
        "endBound"  : BoundingType.BLIND,
        "endDepth"  : {thickness:g} * millimeter
    }});
    var testPlate = qCreatedBy(plateId + "extrude", EntityType.BODY);
    var volumeBefore = evVolume(context, {{ "entities" : testPlate }});

    // --- the template's own grid arithmetic, spliced in -------------------
{template._grid_source(params, plate_face)}

    // --- cut the pockets --------------------------------------------------
    var cutId = makeId("verifyPockets");
    var sketch = newSketchOnPlane(context, cutId + "sketch", {{
        "sketchPlane" : alignedPlane
    }});
    for (var row = 0; row < {rows}; row += 1)
    {{
        for (var col = 0; col < {cols}; col += 1)
        {{
            var uMin = faceBox.minCorner[0] + border + col * (pocketU + rib);
            var vMin = faceBox.minCorner[1] + border + row * (pocketV + rib);
            skRectangle(sketch, "p" ~ row ~ "_" ~ col, {{
                "firstCorner"  : vector(uMin, vMin),
                "secondCorner" : vector(uMin + pocketU, vMin + pocketV)
            }});
        }}
    }}
    skSolve(sketch);
    opExtrude(context, cutId + "cut", {{
        "entities"  : qSketchRegion(cutId + "sketch"),
        "direction" : -facePlane.normal,
        "endBound"  : BoundingType.BLIND,
        "endDepth"  : {thickness:g} * millimeter
    }});
    opBoolean(context, cutId + "subtract", {{
        "tools"         : qCreatedBy(cutId + "cut", EntityType.BODY),
        "targets"       : testPlate,
        "operationType" : BooleanOperationType.SUBTRACTION
    }});

    var volumeAfter = evVolume(context, {{ "entities" : testPlate }});
    return {{
        "before_mm3"  : volumeBefore / (millimeter ^ 3),
        "after_mm3"   : volumeAfter / (millimeter ^ 3),
        "removed_mm3" : (volumeBefore - volumeAfter) / (millimeter ^ 3),
        "pocketU_mm"  : pocketU / millimeter,
        "pocketV_mm"  : pocketV / millimeter
    }};
}}"""


async def main() -> int:
    rotation = 0.0
    if "--rotate" in sys.argv:
        rotation = float(sys.argv[sys.argv.index("--rotate") + 1])
        print(f"Plate drawn at {rotation:g} deg on Top")
        print()
    template = TEMPLATES["plate_pocket_pattern"]

    predicted_g = template.mass_removed_g(PARAMS)
    predicted_mm3 = predicted_g / 1000.0 / ALUMINIUM_6061_DENSITY * 1e9
    pocket_w, pocket_l = template.pocket_size(PARAMS)

    print("Prediction, from Python alone")
    print(f"  pocket size    : {pocket_w:.3f} x {pocket_l:.3f} mm")
    print(f"  volume removed : {predicted_mm3:,.0f} mm3")
    print(f"  mass removed   : {predicted_g:.1f} g of 6061")
    print()

    async with OnshapeApiClient.from_env() as client:
        result = await probe_body(
            client, build_probe(template, PARAMS, rotation),
            document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID,
            part_studio_id=PART_STUDIO_ID,
        )
        # The request just made is the cheapest honest reading of the scarce
        # evaluate bucket, so report it. The FS MCP evaluation
        # (docs/eval-onshape-fs-mcp.md) uses this line as its before/after
        # measurement.
        for route, state in client.quota.snapshot().items():
            if "featurescript" in route:
                print(f"Quota: {route} has {state.get('remaining')} of "
                      f"~{state.get('ceiling')} left")
                print()

    if not result.ok:
        print("FAILED: Onshape rejected the probe.")
        for problem in result.problems:
            where = f" (line {problem.line})" if problem.line else ""
            print(f"  [{problem.severity.value}] {problem.message}{where}")
        return 1

    measured = result.value if isinstance(result.value, dict) else {}
    removed = measured.get("removed_mm3", 0.0)

    print("Measured, by Onshape's kernel")
    print(f"  pocket size    : {measured.get('pocketU_mm', 0):.3f} x "
          f"{measured.get('pocketV_mm', 0):.3f} mm")
    print(f"  plate before   : {measured.get('before_mm3', 0):,.0f} mm3")
    print(f"  plate after    : {measured.get('after_mm3', 0):,.0f} mm3")
    print(f"  volume removed : {removed:,.0f} mm3")
    print()

    if predicted_mm3 <= 0:
        print("FAILED: the template predicted no material removed.")
        return 1

    error = abs(removed - predicted_mm3) / predicted_mm3
    print(f"Disagreement: {error:.4%} (tolerance {TOLERANCE:.2%})")
    if error > TOLERANCE:
        print("FAILED: our arithmetic and Onshape's geometry disagree.")
        return 1

    print(f"PASSED: the reported {predicted_g:.0f} g is what the kernel removes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
