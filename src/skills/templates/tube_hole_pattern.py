"""Tube hole pattern — a row of holes along rectangular FRC tubing.

The most common operation on an FRC robot: 1x1 or 2x1 aluminium tube drilled at
regular spacing for gussets, bearings and standoffs.

Chosen as the first template because its validation is entirely arithmetic. A
hole wider than the tube, or a pattern longer than the stock, is provably
impossible without any geometry kernel — so it is caught locally, instantly, and
for zero API requests. That is the whole argument for the local validation
layer, made concrete.

Dimensions are millimetres throughout. FRC stock is imperial, but Onshape's
internals are metric and mixing units in generated source is a good way to
produce something that compiles and is silently wrong.
"""

from __future__ import annotations

from typing import Any

from ..standards import standard, team_note
from .base import ParamSpec, Problem, Severity, check_required, merge_defaults

# All three numbers below are shop policy and live in `standards.toml` under
# `[tube_hole]`. They are read through `standard()` at check time so a team can
# change them without touching code; these comments record what they mean.
#
#   tube_hole.min_wall_mm          aluminium between hole and tube edge, below
#                                  which it tears out under load
#   tube_hole.edge_distance_factor hole centre at least N diameters from the end
#   tube_hole.max_material_removed share of the length taken as holes before the
#                                  tube is materially weakened in bending


class TubeHolePattern:
    """A linear pattern of through-holes along a rectangular tube."""

    name = "tube_hole_pattern"
    summary = (
        "A row of evenly spaced through-holes along rectangular tube stock "
        "(the standard FRC 1x1 / 2x1 gusset and bearing pattern)."
    )

    def parameters(self) -> list[ParamSpec]:
        return [
            ParamSpec("tube_width", "number",
                      "Width of the tube face being drilled.", unit="mm"),
            ParamSpec("tube_length", "number",
                      "Overall length of the tube.", unit="mm"),
            ParamSpec("hole_diameter", "number",
                      "Diameter of each hole.", unit="mm"),
            ParamSpec("hole_count", "number",
                      "How many holes in the pattern.", default=2),
            ParamSpec("spacing", "number",
                      "Centre-to-centre distance between adjacent holes.", unit="mm"),
            ParamSpec("edge_distance", "number",
                      "Distance from the tube end to the first hole centre.",
                      unit="mm", required=False),
        ]

    # ------------------------------------------------------------ validate

    def validate(self, params: dict[str, Any]) -> list[Problem]:
        """Layers 3 and 4: contract, then dimensional arithmetic.

        Every message quotes the numbers it used so a student can check it by
        hand — the same discipline `src/physics/motors.py` follows by citing its
        vendor sources.
        """
        params = merge_defaults(self.parameters(), params)
        problems = check_required(self.parameters(), params)
        if any(p.is_error for p in problems):
            # Arithmetic on missing or malformed values would only produce noise.
            return problems

        min_wall = standard("tube_hole", "min_wall_mm")
        edge_factor = standard("tube_hole", "edge_distance_factor")
        max_removed = standard("tube_hole", "max_material_removed")

        width = float(params["tube_width"])
        length = float(params["tube_length"])
        diameter = float(params["hole_diameter"])
        count = int(params["hole_count"])
        spacing = float(params["spacing"])
        edge = float(params.get("edge_distance") or diameter * edge_factor)

        if count < 1:
            problems.append(Problem(
                Severity.ERROR,
                f"hole_count must be at least 1, got {count}.",
                parameter="hole_count"))
            return problems

        # 1. The hole has to fit across the face, with wall left on both sides.
        needed = diameter + 2 * min_wall
        if needed > width:
            problems.append(Problem(
                Severity.ERROR,
                f"A {diameter:g} mm hole needs {needed:g} mm of face "
                f"({diameter:g} + 2 x {min_wall:g} mm wall) but tube_width is "
                f"{width:g} mm." + team_note("tube_hole", "min_wall_mm"),
                parameter="hole_diameter",
                suggestion=f"Use a hole no larger than {width - 2 * min_wall:g} mm, "
                           f"or wider tube."))

        # 2. The pattern has to fit along the length.
        span = spacing * (count - 1) + 2 * edge
        if span > length:
            problems.append(Problem(
                Severity.ERROR,
                f"The pattern spans {span:g} mm "
                f"({spacing:g} x {count - 1} + 2 x {edge:g} edge) but tube_length "
                f"is {length:g} mm.",
                parameter="spacing",
                suggestion=(
                    f"Reduce spacing to {(length - 2 * edge) / (count - 1):.1f} mm, "
                    f"or hole_count to {int((length - 2 * edge) // spacing) + 1}."
                ) if count > 1 else "Reduce edge_distance or use a longer tube."))

        # 3. Holes must not overlap each other.
        if count > 1 and spacing <= diameter:
            problems.append(Problem(
                Severity.ERROR,
                f"spacing {spacing:g} mm is not greater than hole_diameter "
                f"{diameter:g} mm, so adjacent holes would merge.",
                parameter="spacing",
                suggestion=f"Use spacing above {diameter:g} mm; "
                           f"{diameter * 2:g} mm is a common choice."))

        # 4. Edge distance — a guideline, so a warning rather than a refusal.
        required_edge = diameter * edge_factor
        if edge < required_edge:
            problems.append(Problem(
                Severity.WARNING,
                f"edge_distance {edge:g} mm is under {edge_factor:g} hole "
                f"diameter(s) ({required_edge:g} mm). Standard practice keeps "
                f"the first hole that far from the end to avoid tear-out."
                + team_note("tube_hole", "edge_distance_factor"),
                parameter="edge_distance",
                suggestion=f"Use at least {required_edge:g} mm."))

        # 5. Structural warning: how much length is now hole.
        removed = (diameter * count) / length if length else 0.0
        if removed > max_removed:
            problems.append(Problem(
                Severity.WARNING,
                f"These holes remove {removed:.0%} of the tube length "
                f"({count} x {diameter:g} mm of {length:g} mm), past the "
                f"{max_removed:.0%} that significantly weakens it in bending."
                + team_note("tube_hole", "max_material_removed"),
                parameter="hole_count",
                suggestion="Reduce hole_count or hole_diameter if this tube carries load."))

        return problems

    # -------------------------------------------------------------- render

    def _positions(self, params: dict[str, Any]) -> list[float]:
        params = merge_defaults(self.parameters(), params)
        diameter = float(params["hole_diameter"])
        count = int(params["hole_count"])
        spacing = float(params["spacing"])
        edge = float(params.get("edge_distance")
                     or diameter * standard("tube_hole", "edge_distance_factor"))
        return [edge + i * spacing for i in range(count)]

    def render_probe(self, params: dict[str, Any]) -> str:
        """A bare lambda Onshape's evaluate endpoint will accept and report on.

        No import, annotation or export — the endpoint rejects all three. It
        re-derives the tube's real bounding box and asserts the pattern fits,
        so a mismatch between the caller's stated dimensions and the actual
        geometry surfaces as a `regenError` with a readable message.
        """
        params = merge_defaults(self.parameters(), params)
        positions = self._positions(params)
        length = float(params["tube_length"])
        diameter = float(params["hole_diameter"])
        pos_literal = ", ".join(f"{p:g} * millimeter" for p in positions)

        return f"""function(context is Context, queries)
{{
    var positions = [{pos_literal}];
    var tubeBox = evBox3d(context, {{ "topology" : qEverything(EntityType.BODY) }});
    var actualLength = tubeBox.maxCorner[0] - tubeBox.minCorner[0];
    // Whole millimetres: `toString` on a length can render 150 mm as
    // "0.15000000000000002 meter", which reads as a broken tool.
    if (actualLength < {length:g} * millimeter)
    {{
        throw regenError("Tube is " ~ toString(round(actualLength / millimeter)) ~
            " mm but the pattern was computed for {length:g} mm.");
    }}
    for (var p in positions)
    {{
        if (p > actualLength - {diameter / 2:g} * millimeter)
        {{
            throw regenError("Hole at " ~ toString(round(p / millimeter)) ~
                " mm falls outside the tube.");
        }}
    }}
    return size(positions);
}}"""

    def render_module(self, params: dict[str, Any]) -> str:
        """The full Feature Studio module.

        The `defineFeature` wrapper is fixed boilerplate — identical every time,
        verified once — so errors live in the body, which `render_probe` has
        already checked against Onshape with line numbers.
        """
        params = merge_defaults(self.parameters(), params)
        positions = self._positions(params)
        diameter = float(params["hole_diameter"])
        pos_literal = ", ".join(f"{p:g} * millimeter" for p in positions)

        return f"""FeatureScript 3029;
import(path : "onshape/std/geometry.fs", version : "3029.0");

// Generated by frc-cad-copilot, template `{self.name}`.
// Parameters: {_describe(params)}
annotation {{ "Feature Type Name" : "FRC Tube Hole Pattern" }}
export const frcTubeHolePattern = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
        annotation {{ "Name" : "Tube body", "Filter" : EntityType.BODY }}
        definition.tube is Query;
    }}
    {{
        var positions = [{pos_literal}];
        var tubeBox = evBox3d(context, {{ "topology" : definition.tube }});
        var actualLength = tubeBox.maxCorner[0] - tubeBox.minCorner[0];

        for (var i = 0; i < size(positions); i += 1)
        {{
            if (positions[i] > actualLength - {diameter / 2:g} * millimeter)
            {{
                throw regenError("Hole " ~ toString(i + 1) ~
                    " falls outside the selected tube.", ["tube"]);
            }}
        }}

        opHole(context, id + "holes", {{
            "holeDefinition" : {{
                "holeDiameter" : {diameter:g} * millimeter
            }},
            "locations" : positions,
            "scope" : definition.tube
        }});
    }});"""


def _describe(params: dict[str, Any]) -> str:
    keys = ("tube_width", "tube_length", "hole_diameter", "hole_count",
            "spacing", "edge_distance")
    return ", ".join(f"{k}={params[k]:g}" for k in keys if params.get(k) is not None)
