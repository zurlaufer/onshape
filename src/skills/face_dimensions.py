"""Face size: the width and height of a planar face, in its own plane.

The question a student asks before every pocket pattern, gusset or sheet-metal
flat: "how big is this face, really?" Onshape's measure tool answers it with a
world-axis box, which is wrong for any plate that is not square to the world.

THE IDIOM, AND THE TRAP IN IT
-----------------------------
`evBox3d` accepts a cSys, and `planeToCSys(evPlane(face))` puts the box in the
face's own plane, so w = 0 and u/v are in-plane extents (see the corpus example
`featurescript/verified/measure_face_in_own_plane.fs`).

But the plane fixes only the *normal*. Its x direction comes from how the face
was made: a rectangle sketched at 30 deg on Top keeps Top's x axis, so the box
is drawn at 30 deg to the plate and every number is too large. The fix is the
rotating-calipers result: a polygon's minimum-area bounding rectangle has one
side collinear with one of its edges. So the box is taken along the plane's own
x **and** along every straight edge of the face, and the smallest wins. For a
rectangle that is exact; for a face with arcs it is the tightest box over the
directions the face itself offers. A face with no straight edge (a disc) falls
back to the plane's x, where any direction is as good as another.

Width is the longer side, height the shorter. The plane's u/v labels are an
accident of construction, so ordering by size is the only labelling that is the
same every time the feature regenerates.

The result is the face's *envelope*. An L-shaped face reports the rectangle
around the L, and the message says "envelope" so nobody reads it as area.

Lengths are reported in whole millimetres: `toString()` on a length renders
"0.15000000000000002 meter", which reads as a broken tool.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import math

__all__ = [
    "FEATURE_NAME",
    "PROBE_PLATE_MM",
    "PROBE_ROTATION_DEG",
    "render_module",
    "render_probe",
]

FEATURE_NAME = "FRC Face Size"

#: The probe builds its own plate, so its answer is known in advance. 150 x 75
#: rather than a square so that a swapped width/height shows up.
PROBE_PLATE_MM = (150.0, 75.0)

#: Rotated in the sketch, not with a transform: a transformed body carries its
#: plane's x axis round with it and the naive box would come out right by
#: luck. Sketching the rotated outline on Top is what a student actually does,
#: and leaves the face's plane x at 30 deg to its edges.
PROBE_ROTATION_DEG = 30.0

_PROBE_THICKNESS_MM = 6.35


def _measure_source(face_expr: str, indent: str) -> str:
    """The shared body: measure one planar face and leave widthMm/heightMm.

    Emitted into both the probe and the module so the two cannot drift; only
    how the face is obtained differs.
    """
    body = f"""var measuredFace = {face_expr};
var facePlane = evPlane(context, {{ "face" : measuredFace }});

// Candidate box directions: the plane's own x, plus every straight edge.
// The plane fixes only the normal; its x axis is whatever the face was built
// with, and a box along it is too big for any face not square to it.
var axes = [facePlane.x];
var straightEdges = evaluateQuery(context, qGeometry(
    qAdjacent(measuredFace, AdjacencyType.EDGE, EntityType.EDGE),
    GeometryType.LINE));
for (var edge in straightEdges)
{{
    var direction = evEdgeTangentLine(context, {{
        "edge" : edge,
        "parameter" : 0
    }}).direction;
    var inPlane = direction - dot(direction, facePlane.normal) * facePlane.normal;
    if (norm(inPlane) > 1e-9)
    {{
        axes = append(axes, normalize(inPlane));
    }}
}}

// Smallest box wins. `box` is a reserved word -- hence `faceBox`.
var bestU = undefined;
var bestV = undefined;
for (var axis in axes)
{{
    var faceBox = evBox3d(context, {{
        "topology" : measuredFace,
        "cSys" : coordSystem(facePlane.origin, axis, facePlane.normal)
    }});
    var u = faceBox.maxCorner[0] - faceBox.minCorner[0];
    var v = faceBox.maxCorner[1] - faceBox.minCorner[1];
    if (bestU == undefined || u * v < bestU * bestV)
    {{
        bestU = u;
        bestV = v;
    }}
}}

// Whole millimetres; width is the longer side so the labels never swap.
var widthMm = round(max(bestU, bestV) / millimeter);
var heightMm = round(min(bestU, bestV) / millimeter);"""
    return "\n".join(indent + line if line else "" for line in body.splitlines())


def _rotated_outline() -> list[tuple[float, float]]:
    """Corners of the probe plate, rotated in the sketch plane."""
    width, height = PROBE_PLATE_MM
    a = math.radians(PROBE_ROTATION_DEG)
    corners = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    return [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))
            for x, y in corners]


def render_probe() -> str:
    """A bare lambda that builds a rotated plate and measures it.

    Self-contained: the evaluate endpoint runs real modelling ops and persists
    nothing, so the probe sketches a 150 x 75 mm rectangle at 30 deg on Top,
    extrudes it and measures its largest face. The answer is known, so the
    probe checks the measurement rather than trusting it, and it does not
    depend on what the target Part Studio holds.

    It also returns the box along the plane's own x — the naive idiom — so the
    result shows what the edge directions are for.
    """
    corners = _rotated_outline()
    segments = "\n".join(
        f'    skLineSegment(sketch, "side{i}", {{ '
        f'"start" : vector({x0:.6f}, {y0:.6f}) * millimeter, '
        f'"end" : vector({x1:.6f}, {y1:.6f}) * millimeter }});'
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(corners, corners[1:] + corners[:1]))
    )
    width, height = PROBE_PLATE_MM

    find_face = """(function()
    {
        var faces = evaluateQuery(context, qGeometry(
            qCreatedBy(makeId("faceSizePlate"), EntityType.FACE), GeometryType.PLANE));
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
    // A {width:g} x {height:g} mm plate sketched at {PROBE_ROTATION_DEG:g} deg on Top.
    var sketch = newSketchOnPlane(context, makeId("faceSizeSketch"), {{
        "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
    }});
{segments}
    skSolve(sketch);
    opExtrude(context, makeId("faceSizePlate"), {{
        "entities" : qSketchRegion(makeId("faceSizeSketch")),
        "direction" : vector(0, 0, 1),
        "endBound" : BoundingType.BLIND,
        "endDepth" : {_PROBE_THICKNESS_MM:g} * millimeter
    }});

{_measure_source(find_face, "    ")}

    // The naive idiom, for comparison: the box along the plane's own x only.
    var naiveBox = evBox3d(context, {{
        "topology" : measuredFace,
        "cSys" : planeToCSys(facePlane)
    }});

    return {{
        "width_mm" : widthMm,
        "height_mm" : heightMm,
        "expected_width_mm" : {width:g},
        "expected_height_mm" : {height:g},
        "straight_edges" : size(straightEdges),
        "plane_x_box_u_mm" : round((naiveBox.maxCorner[0] - naiveBox.minCorner[0]) / millimeter),
        "plane_x_box_v_mm" : round((naiveBox.maxCorner[1] - naiveBox.minCorner[1]) / millimeter)
    }};
}}"""


def render_module() -> str:
    """The Feature Studio module: select one planar face, get its size.

    The Filter restricts the pick to planar faces, so a cylinder cannot be
    selected and fail later. (A Query parameter's annotation must carry a
    Filter at all, or the module does not compile and the write path does not
    say why.) The feature changes no geometry; it reports.
    """
    return f"""FeatureScript 3029;
import(path : "onshape/std/geometry.fs", version : "3029.0");

// Generated by frc-cad-copilot from src/skills/face_dimensions.py.
//
// Reports the width and height of a planar face, measured in the face's own
// plane, in whole millimetres. Width is the longer side. The box is taken
// along every straight edge of the face and the smallest wins, so a plate
// drawn at an angle is measured along its own sides, not the sketch's axes.
// It is the face's envelope: an L-shaped face reports the rectangle around it.

annotation {{ "Feature Type Name" : "{FEATURE_NAME}" }}
export const frcFaceSize = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
        // A Query parameter's annotation MUST carry a Filter or the module will
        // not compile, and the write path will not say why.
        annotation {{ "Name" : "Planar face",
                     "Filter" : EntityType.FACE && GeometryType.PLANE,
                     "MaxNumberOfPicks" : 1 }}
        definition.face is Query;
    }}
    {{
        if (size(evaluateQuery(context, definition.face)) == 0)
        {{
            throw regenError("Select a planar face to measure.", ["face"]);
        }}

{_measure_source("definition.face", "        ")}

        reportFeatureInfo(context, id, "Face envelope: " ~ toString(widthMm) ~
            " x " ~ toString(heightMm) ~ " mm (width x height, in the face's own plane)");
    }});"""
