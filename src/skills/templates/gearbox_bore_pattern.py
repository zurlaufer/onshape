"""Gearbox plate bore pattern — where the physics actually constrains the geometry.

Two shafts carrying a meshing gear pair. The centre distance between their
bearing bores is not a free choice: it is fixed by the tooth counts and the
diametral pitch.

    pitch diameter  = teeth / diametral_pitch          (inches)
    centre distance = (N_driving + N_driven) / (2 x DP)

So the gear ratio a student wants *determines* where the holes go. That makes
this the template where `src/physics/motors.py` stops being a separate
calculator and becomes an input to the CAD: given a motor and this ratio, the
output torque is known, and from it the tangential force the bores must react.

DEPENDENCY DIRECTION: this module imports from `src.physics`, never the reverse.
`src/physics/` stays free of Onshape, MCP and skill imports so a student can read
and test it in isolation (FM3). Importing *from* it preserves that.

Diametral pitch is quoted in imperial because every FRC gear is (20 DP is the
COTS standard, 32 DP for compact stages). Everything else is millimetres, and
the conversion happens in exactly one place below.
"""

from __future__ import annotations

import math
from typing import Any

from src.physics.motors import MOTORS, Gearbox, get_motor, reduction_from_stages

from .base import ParamSpec, Problem, Severity, check_required, merge_defaults

MM_PER_INCH = 25.4

#: Below this, a 20-degree pressure angle spur gear undercuts at the root and
#: loses tooth strength. 18 is the theoretical limit; 14 is what shops accept.
#: A guideline, so it warns rather than refuses.
MIN_TEETH_NO_UNDERCUT = 14

#: Minimum aluminium left between two bores before the web tears out.
MIN_WEB_MM = 3.0

#: Bearing bores this close to a plate edge are not worth trusting.
MIN_EDGE_MM = 4.0


class GearboxBorePattern:
    """Two bearing bores at the centre distance a gear pair requires."""

    name = "gearbox_bore_pattern"
    summary = (
        "Two bearing bores on a gearbox plate, positioned at the centre "
        "distance implied by a spur gear pair. Optionally checked against a "
        "motor's stall torque."
    )

    def parameters(self) -> list[ParamSpec]:
        return [
            ParamSpec("driving_teeth", "number",
                      "Tooth count of the pinion (on the motor shaft)."),
            ParamSpec("driven_teeth", "number",
                      "Tooth count of the driven gear."),
            ParamSpec("diametral_pitch", "number",
                      "Gear diametral pitch in teeth per inch. FRC COTS gears "
                      "are 20 DP; 32 DP is used for compact stages.",
                      unit="teeth/in", default=20.0),
            ParamSpec("bore_diameter", "number",
                      "Diameter of each bearing bore.", unit="mm"),
            ParamSpec("plate_width", "number",
                      "Plate dimension along the line between the two bores.",
                      unit="mm"),
            ParamSpec("plate_height", "number",
                      "Plate dimension across that line.", unit="mm"),
            ParamSpec("motor", "string",
                      f"Optional. Check the design against a motor's stall "
                      f"torque. One of: {', '.join(sorted(MOTORS))}.",
                      required=False),
            ParamSpec("motor_count", "number",
                      "Motors driving this stage.", default=1, required=False),
        ]

    # ---------------------------------------------------------- geometry

    def centre_distance_mm(self, params: dict[str, Any]) -> float:
        """Centre distance implied by the gear pair. The whole point."""
        params = merge_defaults(self.parameters(), params)
        teeth = float(params["driving_teeth"]) + float(params["driven_teeth"])
        return MM_PER_INCH * teeth / (2.0 * float(params["diametral_pitch"]))

    def pitch_diameters_mm(self, params: dict[str, Any]) -> tuple[float, float]:
        params = merge_defaults(self.parameters(), params)
        dp = float(params["diametral_pitch"])
        return (MM_PER_INCH * float(params["driving_teeth"]) / dp,
                MM_PER_INCH * float(params["driven_teeth"]) / dp)

    def reduction(self, params: dict[str, Any]) -> float:
        """Delegates to physics rather than re-deriving driven/driving here."""
        return reduction_from_stages(
            (float(params["driving_teeth"]), float(params["driven_teeth"])))

    # ---------------------------------------------------------- validate

    def validate(self, params: dict[str, Any]) -> list[Problem]:
        params = merge_defaults(self.parameters(), params)
        problems = check_required(self.parameters(), params)
        if any(p.is_error for p in problems):
            return problems

        driving = float(params["driving_teeth"])
        driven = float(params["driven_teeth"])
        bore = float(params["bore_diameter"])
        width = float(params["plate_width"])
        height = float(params["plate_height"])

        if driving < 1 or driven < 1:
            problems.append(Problem(
                Severity.ERROR, "Tooth counts must be at least 1.",
                parameter="driving_teeth"))
            return problems

        centre = self.centre_distance_mm(params)
        pd_driving, pd_driven = self.pitch_diameters_mm(params)
        ratio = self.reduction(params)

        # 1. The two bores must not run into each other.
        needed = bore + MIN_WEB_MM
        if centre < needed:
            problems.append(Problem(
                Severity.ERROR,
                f"The {driving:g}:{driven:g} pair at {params['diametral_pitch']:g} DP "
                f"puts the bores {centre:.1f} mm apart, but two {bore:g} mm bores "
                f"need {needed:.1f} mm ({bore:g} + {MIN_WEB_MM:g} mm web).",
                parameter="bore_diameter",
                suggestion=f"Use a bore under {centre - MIN_WEB_MM:.1f} mm, a higher "
                           f"tooth count, or a coarser pitch."))

        # 2. Both bores plus edge margin must fit on the plate.
        span = centre + bore + 2 * MIN_EDGE_MM
        if span > width:
            problems.append(Problem(
                Severity.ERROR,
                f"Bores plus edge margin span {span:.1f} mm "
                f"({centre:.1f} centre + {bore:g} bore + 2 x {MIN_EDGE_MM:g} edge) "
                f"but plate_width is {width:g} mm.",
                parameter="plate_width",
                suggestion=f"Widen the plate to at least {span:.0f} mm."))

        across = bore + 2 * MIN_EDGE_MM
        if across > height:
            problems.append(Problem(
                Severity.ERROR,
                f"A {bore:g} mm bore needs {across:.1f} mm of plate height but "
                f"plate_height is {height:g} mm.",
                parameter="plate_height"))

        # 3. Undercut — a guideline, hence a warning.
        if driving < MIN_TEETH_NO_UNDERCUT:
            problems.append(Problem(
                Severity.WARNING,
                f"A {driving:g}-tooth pinion undercuts at a 20 degree pressure "
                f"angle; below about {MIN_TEETH_NO_UNDERCUT} teeth the root is "
                f"weakened.",
                parameter="driving_teeth",
                suggestion=f"Use {MIN_TEETH_NO_UNDERCUT} teeth or more, or confirm "
                           f"the COTS gear is profile-shifted."))

        # 4. Does the driven gear overhang the plate?
        if pd_driven + 2 * MIN_EDGE_MM > height:
            problems.append(Problem(
                Severity.WARNING,
                f"The driven gear's pitch diameter is {pd_driven:.1f} mm, which "
                f"exceeds plate_height {height:g} mm - the gear will overhang "
                f"the plate.",
                parameter="plate_height",
                suggestion="Intentional on many designs; confirm it clears "
                           "surrounding structure."))

        # 5. Physics: what the bores actually have to react.
        problems.extend(self._check_torque(params, ratio, pd_driven))

        return problems

    def _check_torque(
        self, params: dict[str, Any], ratio: float, pd_driven_mm: float
    ) -> list[Problem]:
        """Fold the motor model in, when a motor was named.

        Reports rather than refuses. Whether a given plate survives a given load
        needs a stress model this project does not have yet (`stress.py` is
        unwritten), and inventing a threshold would be worse than saying the
        number and letting a student judge.
        """
        name = params.get("motor")
        if not name:
            return []

        try:
            motor = get_motor(str(name))
        except KeyError as exc:
            return [Problem(Severity.ERROR, str(exc), parameter="motor")]

        count = int(params.get("motor_count") or 1)
        stage = Gearbox(motor=motor, reduction=ratio, motor_count=count)

        # Tangential force at the pitch circle: F = T / r.
        radius_m = (pd_driven_mm / 2.0) / 1000.0
        force_n = stage.stall_torque_nm / radius_m if radius_m else 0.0

        problems = [Problem(
            Severity.WARNING,
            f"At stall, {count}x {motor.name} through {ratio:.2f}:1 produces "
            f"{stage.stall_torque_nm:.1f} N.m, which is {force_n:,.0f} N "
            f"tangential at the {pd_driven_mm:.1f} mm pitch diameter. "
            f"(Motor figures: {motor.stall_torque_nm:g} N.m stall, "
            f"source {motor.source})",
            parameter="motor",
            suggestion="Stall is the worst case, not the operating case. Check "
                       "the bearing and plate against this force.")]

        # A genuinely checkable geometric consequence: current draw at stall.
        if stage.stall_current_a > 40 * count:
            problems.append(Problem(
                Severity.WARNING,
                f"Stalling this stage draws {stage.stall_current_a:,.0f} A across "
                f"{count} motor(s) - far beyond a 40 A breaker each.",
                parameter="motor_count",
                suggestion="Current-limit in software; do not size hardware for stall."))
        return problems

    # ------------------------------------------------------------ render

    def render_probe(self, params: dict[str, Any]) -> str:
        """A bare lambda: no import, annotation or export (the endpoint rejects all three)."""
        params = merge_defaults(self.parameters(), params)
        centre = self.centre_distance_mm(params)
        bore = float(params["bore_diameter"])
        width = float(params["plate_width"])

        return f"""function(context is Context, queries)
{{
    var centreDistance = {centre:.4f} * millimeter;
    var boreDiameter = {bore:g} * millimeter;
    var plateBox = evBox3d(context, {{ "topology" : qEverything(EntityType.BODY) }});
    var actualWidth = plateBox.maxCorner[0] - plateBox.minCorner[0];

    if (actualWidth < centreDistance + boreDiameter)
    {{
        throw regenError("Plate is " ~ toString(actualWidth) ~
            " but the {params['driving_teeth']:g}:{params['driven_teeth']:g} pair needs "
            ~ toString(centreDistance + boreDiameter) ~ " for both bores.");
    }}
    if (actualWidth < {width:g} * millimeter * 0.5)
    {{
        throw regenError("Plate is far narrower than the stated plate_width of {width:g} mm.");
    }}
    return centreDistance / millimeter;
}}"""

    def render_module(self, params: dict[str, Any]) -> str:
        params = merge_defaults(self.parameters(), params)
        centre = self.centre_distance_mm(params)
        bore = float(params["bore_diameter"])
        pd_driving, pd_driven = self.pitch_diameters_mm(params)
        ratio = self.reduction(params)

        return f"""FeatureScript 3029;
import(path : "onshape/std/geometry.fs", version : "3029.0");

// Generated by frc-cad-copilot, template `{self.name}`.
// Gear pair : {params['driving_teeth']:g}T -> {params['driven_teeth']:g}T at \
{params['diametral_pitch']:g} DP  ({ratio:.3f}:1)
// Pitch dia : {pd_driving:.2f} mm driving, {pd_driven:.2f} mm driven
// Centre    : {centre:.4f} mm  -- fixed by the gear pair, not a free choice
annotation {{ "Feature Type Name" : "FRC Gearbox Bores" }}
export const frcGearboxBores = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
        // A Query parameter's annotation MUST carry a "Filter". Without one
        // the module fails to compile, and the write path reports no reason.
        annotation {{ "Name" : "Plate", "Filter" : EntityType.BODY }}
        definition.plate is Query;
    }}
    {{
        var centreDistance = {centre:.4f} * millimeter;
        var plateBox = evBox3d(context, {{ "topology" : definition.plate }});
        var available = plateBox.maxCorner[0] - plateBox.minCorner[0];

        if (available < centreDistance + {bore:g} * millimeter)
        {{
            throw regenError("Plate is too narrow for a {centre:.1f} mm centre "
                ~ "distance plus bores.", ["plate"]);
        }}

        opHole(context, id + "bores", {{
            "holeDefinition" : {{
                "holeDiameter" : {bore:g} * millimeter
            }},
            "locations" : [0 * millimeter, centreDistance],
            "scope" : definition.plate
        }});
    }});"""
