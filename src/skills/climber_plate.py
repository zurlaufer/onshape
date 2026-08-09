"""The climber gearbox plate: one concrete design, and the code that draws it.

`src/physics/climber.py` and `src/physics/geartrain.py` are general. This is the
instantiation for one robot — 60 kg, pivot at the frame edge, CoM at the frame
centre — and it exists so that the sizing script, the geometry probe and the
build script all read the design from one place instead of three copies of the
same magic numbers drifting apart.

Change `GEOMETRY` or `STACK` here and every script follows.

WHY THESE TOOTH COUNTS. Not because 115.7:1 was the target — several stacks hit
that. Because `GearStack.clearance_problems` rejects the obvious ones. The
driven gears grow 46 -> 54 -> 62 to keep each stage's big gear clear of the next
stage's shaft; see the docstring in `geartrain.py`. The first stack chosen for
this robot, 11:60 x 11:60 x 12:60, interferes by 4.8 mm and cannot be built at
any plate size.

The plate itself is deliberately plain: a rectangle and four bores. Pockets,
mounting holes and the motor bolt pattern are not here yet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.physics.climber import ClimberGeometry
from src.physics.geartrain import (
    BEARING_OD_MM,
    GearStack,
    PlateLayout,
    Stage,
    fold_layout,
)

__all__ = [
    "GEOMETRY",
    "STACK",
    "MOTOR_NAME",
    "MOTOR_COUNT",
    "CURRENT_LIMIT_A",
    "PLATE_THICKNESS_MM",
    "BORE_DIAMETER_MM",
    "ALUMINIUM_6061_DENSITY",
    "VOLUME_TOLERANCE_PCT",
    "PlateGeometry",
    "plate_geometry",
    "render_probe",
    "render_module",
]

#: The mechanism this gearbox drives.
GEOMETRY = ClimberGeometry(mass_kg=60.0, arm_m=0.40, com_offset_m=0.35)

#: 115.7:1 in three spur stages, all of them buildable.
STACK = GearStack((Stage(11, 46), Stage(11, 54), Stage(11, 62)))

MOTOR_NAME = "kraken_x60_foc"
MOTOR_COUNT = 2
#: Per motor. Two at 80 A beat one at 120 A on every axis that matters here:
#: faster, more torque margin, and each motor runs cool enough to survive it.
CURRENT_LIMIT_A = 80.0

#: 1/4 in aluminium.
PLATE_THICKNESS_MM = 6.35
BORE_DIAMETER_MM = BEARING_OD_MM

#: kg/m^3.
ALUMINIUM_6061_DENSITY = 2700.0

#: How far Onshape's measured volume may sit from ours, as a percentage.
#: Must be relative, not absolute: the kernel and Python disagree on a cylinder
#: in the last few decimal places, which is ~0.03 mm^3 on this plate and would
#: scale with any larger part. Measured drift is about 0.0001%.
VOLUME_TOLERANCE_PCT = 0.001


@dataclass(frozen=True)
class PlateGeometry:
    """Everything needed to draw the plate, and to check it afterwards."""

    layout: PlateLayout
    thickness_mm: float
    bore_diameter_mm: float

    @property
    def width_mm(self) -> float:
        return self.layout.width_mm

    @property
    def height_mm(self) -> float:
        return self.layout.height_mm

    @property
    def solid_volume_mm3(self) -> float:
        return self.width_mm * self.height_mm * self.thickness_mm

    @property
    def bore_volume_mm3(self) -> float:
        """Volume of one bore."""
        return math.pi * (self.bore_diameter_mm / 2.0) ** 2 * self.thickness_mm

    @property
    def volume_mm3(self) -> float:
        """Finished volume, all bores removed."""
        return self.solid_volume_mm3 - len(self.layout.shafts) * self.bore_volume_mm3

    @property
    def mass_g(self) -> float:
        return self.volume_mm3 * 1e-9 * ALUMINIUM_6061_DENSITY * 1000.0

    @property
    def face_count(self) -> int:
        """6 box faces plus one cylindrical wall per bore."""
        return 6 + len(self.layout.shafts)


def plate_geometry(
    stack: GearStack = STACK,
    *,
    thickness_mm: float = PLATE_THICKNESS_MM,
    bore_diameter_mm: float = BORE_DIAMETER_MM,
    **layout_kwargs,
) -> PlateGeometry:
    """Solve the layout and wrap it with the plate's own dimensions."""
    layout = fold_layout(stack, **layout_kwargs)
    if layout is None:
        raise ValueError(f"no valid plate layout for {stack}")
    return PlateGeometry(layout, thickness_mm, bore_diameter_mm)


# ------------------------------------------------------------- FeatureScript
#
# Two renderings of the same solid. `render_probe` is a bare lambda for the
# evaluate endpoint — no import, no annotation, no export, all three of which
# that endpoint rejects. `render_module` is the real feature. Probe first: it
# costs one request and reports line numbers, where the Feature Studio write
# path reports nothing at all.
#
# `box` is a reserved word in FeatureScript. Nothing here may be named that.


def _bore_lines(plate: PlateGeometry, sketch_var: str, indent: str) -> str:
    radius = plate.bore_diameter_mm / 2.0
    return "\n".join(
        f'{indent}skCircle({sketch_var}, "bore{index}", {{ "center" : '
        f'vector({x:g}, {y:g}) * millimeter, "radius" : {radius:g} * millimeter }});'
        for index, (x, y) in enumerate(plate.layout.shafts)
    )


def render_probe(plate: PlateGeometry) -> str:
    """Build the plate inside the evaluate endpoint and measure it.

    Persists nothing. Returns the volumes and the bounding box so the caller can
    compare Onshape's kernel against our own arithmetic before committing.
    """
    return f"""function(context is Context, queries)
{{
    var plateId = makeId("climberPlate");
    var outline = newSketchOnPlane(context, plateId + "sketch", {{
        "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
    }});
    skRectangle(outline, "outline", {{
        "firstCorner"  : vector(0, 0) * millimeter,
        "secondCorner" : vector({plate.width_mm:g}, {plate.height_mm:g}) * millimeter
    }});
    skSolve(outline);
    opExtrude(context, plateId + "extrude", {{
        "entities"  : qSketchRegion(plateId + "sketch"),
        "direction" : vector(0, 0, 1),
        "endBound"  : BoundingType.BLIND,
        "endDepth"  : {plate.thickness_mm:g} * millimeter
    }});
    var plateBody = qCreatedBy(plateId + "extrude", EntityType.BODY);
    var volumeBefore = evVolume(context, {{ "entities" : plateBody }});

    var boreId = makeId("climberBores");
    var bores = newSketchOnPlane(context, boreId + "sketch", {{
        "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
    }});
{_bore_lines(plate, "bores", "    ")}
    skSolve(bores);
    opExtrude(context, boreId + "cut", {{
        "entities"  : qSketchRegion(boreId + "sketch"),
        "direction" : vector(0, 0, 1),
        "endBound"  : BoundingType.BLIND,
        "endDepth"  : {plate.thickness_mm:g} * millimeter
    }});
    opBoolean(context, boreId + "subtract", {{
        "tools"         : qCreatedBy(boreId + "cut", EntityType.BODY),
        "targets"       : plateBody,
        "operationType" : BooleanOperationType.SUBTRACTION
    }});

    var volumeAfter = evVolume(context, {{ "entities" : plateBody }});
    var plateBounds = evBox3d(context, {{ "topology" : plateBody }});
    return {{
        "before_mm3" : volumeBefore / (millimeter ^ 3),
        "after_mm3"  : volumeAfter / (millimeter ^ 3),
        "width_mm"   : (plateBounds.maxCorner[0] - plateBounds.minCorner[0]) / millimeter,
        "height_mm"  : (plateBounds.maxCorner[1] - plateBounds.minCorner[1]) / millimeter,
        "thick_mm"   : (plateBounds.maxCorner[2] - plateBounds.minCorner[2]) / millimeter,
        "faces"      : size(evaluateQuery(context, qOwnedByBody(plateBody, EntityType.FACE)))
    }};
}}"""


def render_module(plate: PlateGeometry, *, stack: GearStack = STACK) -> str:
    """The permanent custom feature.

    Whatever writes this must NOT restore the Feature Studio's previous source
    afterwards. A custom feature is pinned to the microversion that defined it,
    so restoring strands it in `ERROR` forever with no way back.
    """
    shafts = "\n".join(
        f"//   shaft {i}: ({x:.3f}, {y:.3f}) mm"
        for i, (x, y) in enumerate(plate.layout.shafts)
    )
    return f"""FeatureScript 3029;
import(path : "onshape/std/geometry.fs", version : "3029.0");

// Generated by frc-cad-copilot from src/skills/climber_plate.py.
//
// Rotating-arm climber gearbox plate. Pivot at the frame edge, {GEOMETRY.mass_kg:g} kg robot,
// CoM {GEOMETRY.com_offset_m * 1000:.0f} mm behind the pivot, {GEOMETRY.arm_m * 1000:.0f} mm arm, \
{GEOMETRY.lift_m * 1000:.0f} mm of lift.
//
// Gear train : {stack}  =  {stack.ratio:.1f}:1
// Motors     : {MOTOR_COUNT} x Kraken X60 FOC, {CURRENT_LIMIT_A:g} A stator limit each
// Holding    : a ratchet carries the static load, NOT this gearbox
//
// Driven gears grow along the chain because each stage's driven gear has to
// clear the NEXT stage's shaft. That constraint fixes these tooth counts, not
// the reduction — see src/physics/geartrain.py.
//
{shafts}
//
// Bores are {plate.bore_diameter_mm:g} mm for the FRC 1/2 in hex flanged bearing.
// `box` is a reserved word in FeatureScript. Do not name anything that.

annotation {{ "Feature Type Name" : "Climber Gearbox Plate" }}
export const climberGearboxPlate = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
    }}
    {{
        var outline = newSketchOnPlane(context, id + "plateSketch", {{
            "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
        }});
        skRectangle(outline, "outline", {{
            "firstCorner"  : vector(0, 0) * millimeter,
            "secondCorner" : vector({plate.width_mm:g}, {plate.height_mm:g}) * millimeter
        }});
        skSolve(outline);

        opExtrude(context, id + "plateExtrude", {{
            "entities"  : qSketchRegion(id + "plateSketch"),
            "direction" : vector(0, 0, 1),
            "endBound"  : BoundingType.BLIND,
            "endDepth"  : {plate.thickness_mm:g} * millimeter
        }});
        var plateBody = qCreatedBy(id + "plateExtrude", EntityType.BODY);

        var bores = newSketchOnPlane(context, id + "boreSketch", {{
            "sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))
        }});
{_bore_lines(plate, "bores", "        ")}
        skSolve(bores);

        opExtrude(context, id + "boreCut", {{
            "entities"  : qSketchRegion(id + "boreSketch"),
            "direction" : vector(0, 0, 1),
            "endBound"  : BoundingType.BLIND,
            "endDepth"  : {plate.thickness_mm:g} * millimeter
        }});
        opBoolean(context, id + "boreSubtract", {{
            "tools"         : qCreatedBy(id + "boreCut", EntityType.BODY),
            "targets"       : plateBody,
            "operationType" : BooleanOperationType.SUBTRACTION
        }});
    }});"""
