"""Plate pocketing — a grid of lightening pockets in a flat plate.

The most common weight-reduction operation in FRC. A gusset, swerve plate or
bellypan starts as solid aluminium and has material routed out of it until the
robot makes weight, leaving a lattice of ribs.

WHY THIS TEMPLATE IS THE HARD ONE
---------------------------------
The first two templates operate on a *body* — `evBox3d` on a whole tube, holes
placed along its length. This one operates on a **face the student picks**, which
is the topological-query territory FM1 warned about and the reason the whole
validation stack exists. Face selection was the open question the project had
been deferring; these are the mechanics, measured live 2026-08-09:

    var plane = evPlane(context, { "face" : definition.face });
    var box   = evBox3d(context, { "topology" : definition.face,
                                   "cSys" : planeToCSys(plane) });

(Superseded 2026-11-25: the face's plane keeps the x axis the face was built
with, so on a plate sketched at an angle that box is too big. The grid now
uses `face_dimensions.aligned_box_source`, which boxes along the face's own
straight edges. The history below is kept because it is how the idiom was found.)

`evBox3d` accepts a coordinate system, so a planar face measured in its *own*
plane comes back as clean u/v extents with w = 0. That is what makes a pocket
grid layable inside a face's real boundary rather than inside dimensions the
caller claimed. The grid is computed **in FeatureScript at regeneration time**,
not baked in as coordinates here — so the feature adapts to the face it is
actually applied to, and the numbers below are a cross-check rather than the
source of truth.

The query in the module carries `"Filter" : EntityType.FACE && GeometryType.PLANE`,
so Onshape will not let a student select a cylinder and get a confusing failure
later. (A Query parameter's annotation *must* carry a Filter at all — without one
the module fails to compile and the write path reports no reason.)

SHARP CORNERS, STATED HONESTLY
------------------------------
The pockets are modelled as rectangles. A routed pocket has the end mill's radius
in each corner, so the real part keeps a little material the model removes:
about `(4 - pi) * r^2` per pocket, ~13 mm^2 at a 1/4" cutter. That means the mass
saving reported here is very slightly **optimistic**, and the direction is worth
knowing. Modelling the fillets would mean building each pocket from eight sketch
primitives instead of one, which is a lot more surface for error in exactly the
region that is already the riskiest. Rectangles, plus an honest note.

Dimensions are millimetres throughout.
"""

from __future__ import annotations

from typing import Any

from ..face_dimensions import aligned_box_source
from ..standards import standard, team_note
from .base import ParamSpec, Problem, Severity, check_required, merge_defaults

#: 6061-T6, the aluminium FRC plate stock is almost always cut from. kg/m^3.
#: Density is what turns a pocket layout into the number a team actually cares
#: about — grams off the robot — so it belongs here rather than in a comment.
#:
#: NOT a team standard, deliberately: a team cannot decide what aluminium
#: weighs, and making it editable would invite tuning it until the mass came
#: out right. See `src/skills/standards.py` for where that line is drawn.
ALUMINIUM_6061_DENSITY = 2700.0

# The four numbers below are **shop policy**, and a team with a waterjet works
# to different ones than a team with a hand router. They live in
# `standards.toml` under `[plate_pocket]`; the names kept here are the defaults
# that apply when no team has said otherwise, so a fresh clone is unchanged.

#: A 1/4" end mill is the FRC shop default; its radius is the smallest internal
#: corner most teams can actually cut. Override: plate_pocket.end_mill_radius_mm
DEFAULT_CORNER_RADIUS_MM = 3.175


class PlatePocketPattern:
    """A rectangular grid of lightening pockets in one face of a flat plate."""

    name = "plate_pocket_pattern"
    summary = (
        "A grid of lightening pockets routed into a flat aluminium plate "
        "(the standard FRC gusset / swerve-plate / bellypan weight reduction). "
        "Reports the mass removed."
    )

    def parameters(self) -> list[ParamSpec]:
        return [
            ParamSpec("plate_width", "number",
                      "Plate dimension across the face being pocketed.", unit="mm"),
            ParamSpec("plate_length", "number",
                      "Plate dimension along the face being pocketed.", unit="mm"),
            ParamSpec("plate_thickness", "number",
                      "Plate stock thickness.", unit="mm"),
            ParamSpec("columns", "number",
                      "Pockets across the width.", default=2),
            ParamSpec("rows", "number",
                      "Pockets along the length.", default=2),
            ParamSpec("rib_width", "number",
                      "Material left between adjacent pockets.", unit="mm",
                      default=6.0),
            ParamSpec("border_width", "number",
                      "Material left around the outside of the plate.", unit="mm",
                      default=10.0),
            ParamSpec("pocket_depth", "number",
                      "How deep to cut. Omit to cut all the way through.",
                      unit="mm", required=False),
            ParamSpec("corner_radius", "number",
                      "End mill radius, for the manufacturability check.",
                      unit="mm",
                      default=standard("plate_pocket", "end_mill_radius_mm"),
                      required=False),
        ]

    # ------------------------------------------------------------ geometry

    def pocket_size(self, params: dict[str, Any]) -> tuple[float, float]:
        """Pocket width and length that fall out of the grid.

        The student chooses the *layout*; the pocket size is then determined,
        exactly as the gear ratio determines centre distance in
        `gearbox_bore_pattern`. Solving it here rather than asking for it is what
        makes an impossible layout arithmetic instead of a surprise.
        """
        params = merge_defaults(self.parameters(), params)
        cols = int(params["columns"])
        rows = int(params["rows"])
        rib = float(params["rib_width"])
        border = float(params["border_width"])

        width = (float(params["plate_width"]) - 2 * border - (cols - 1) * rib) / cols
        length = (float(params["plate_length"]) - 2 * border - (rows - 1) * rib) / rows
        return width, length

    def mass_removed_g(self, params: dict[str, Any]) -> float:
        """Grams of aluminium the pockets take off. The point of the exercise."""
        params = merge_defaults(self.parameters(), params)
        pocket_w, pocket_l = self.pocket_size(params)
        if pocket_w <= 0 or pocket_l <= 0:
            return 0.0
        count = int(params["columns"]) * int(params["rows"])
        depth = float(params.get("pocket_depth") or params["plate_thickness"])
        volume_mm3 = pocket_w * pocket_l * depth * count
        return volume_mm3 * 1e-9 * ALUMINIUM_6061_DENSITY * 1000.0

    # ------------------------------------------------------------ validate

    def validate(self, params: dict[str, Any]) -> list[Problem]:
        """Layers 3 and 4. Arithmetic only — no kernel, no network, no cost."""
        params = merge_defaults(self.parameters(), params)
        problems = check_required(self.parameters(), params)
        if any(p.is_error for p in problems):
            return problems

        # Read once, at the top, so every message below quotes the same number
        # the check used. Team overrides come from `standards.toml`; without one
        # these are the values that were hardcoded here before it existed.
        min_rib = standard("plate_pocket", "min_rib_mm")
        min_border = standard("plate_pocket", "min_border_mm")
        min_floor = standard("plate_pocket", "min_floor_mm")
        max_removed = standard("plate_pocket", "max_removed_fraction")
        shop_end_mill = standard("plate_pocket", "end_mill_radius_mm")

        cols = int(params["columns"])
        rows = int(params["rows"])
        if cols < 1 or rows < 1:
            problems.append(Problem(
                Severity.ERROR,
                f"rows and columns must each be at least 1, got {rows} x {cols}.",
                parameter="rows" if rows < 1 else "columns"))
            return problems

        plate_w = float(params["plate_width"])
        plate_l = float(params["plate_length"])
        thickness = float(params["plate_thickness"])
        rib = float(params["rib_width"])
        border = float(params["border_width"])
        radius = float(params["corner_radius"])
        depth = float(params.get("pocket_depth") or thickness)
        pocket_w, pocket_l = self.pocket_size(params)

        # 1. The grid has to leave a pocket at all.
        for value, axis, plate, count, param in (
            (pocket_w, "width", plate_w, cols, "columns"),
            (pocket_l, "length", plate_l, rows, "rows"),
        ):
            if value <= 0:
                consumed = 2 * border + (count - 1) * rib
                problems.append(Problem(
                    Severity.ERROR,
                    f"{count} pockets across the {axis} leave no room: "
                    f"2 x {border:g} border + {count - 1} x {rib:g} rib = "
                    f"{consumed:g} mm of a {plate:g} mm plate.",
                    parameter=param,
                    suggestion=f"Reduce {param}, rib_width or border_width."))

        if any(p.is_error for p in problems):
            return problems

        # 2. A pocket narrower than the tool cannot be cut at all.
        smallest = min(pocket_w, pocket_l)
        if smallest <= 2 * radius:
            problems.append(Problem(
                Severity.ERROR,
                f"The pockets come out {pocket_w:.1f} x {pocket_l:.1f} mm, and the "
                f"smaller side is not wider than the {2 * radius:g} mm cutter "
                f"(2 x {radius:g} mm radius). A {radius * 2:g} mm end mill cannot "
                f"enter it.",
                parameter="corner_radius",
                suggestion="Use fewer rows/columns, or a smaller end mill."))

        # 3. Depth against thickness. A floor or a through-cut, nothing between.
        if depth > thickness:
            problems.append(Problem(
                Severity.ERROR,
                f"pocket_depth {depth:g} mm exceeds plate_thickness "
                f"{thickness:g} mm.",
                parameter="pocket_depth",
                suggestion=f"Use at most {thickness:g} mm, or omit it to cut through."))
        elif depth < thickness:
            floor = thickness - depth
            if floor < min_floor:
                problems.append(Problem(
                    Severity.WARNING,
                    f"This leaves a {floor:.2f} mm floor under each pocket "
                    f"({thickness:g} - {depth:g} mm), thinner than the "
                    f"{min_floor:g} mm that survives handling."
                    + team_note("plate_pocket", "min_floor_mm"),
                    parameter="pocket_depth",
                    suggestion="Cut through instead, or leave at least "
                               f"{min_floor:g} mm."))

        # 4. Shop rules of thumb — guidance, so warnings.
        if rib < min_rib:
            problems.append(Problem(
                Severity.WARNING,
                f"rib_width {rib:g} mm is under {min_rib:g} mm. Ribs that thin "
                f"buckle under load rather than carry it, and are easy to tear "
                f"while routing." + team_note("plate_pocket", "min_rib_mm"),
                parameter="rib_width"))
        if border < min_border:
            problems.append(Problem(
                Severity.WARNING,
                f"border_width {border:g} mm is under {min_border:g} mm, which "
                f"leaves little flange for bolts or gussets at the plate edge."
                + team_note("plate_pocket", "min_border_mm"),
                parameter="border_width"))
        if radius < shop_end_mill:
            problems.append(Problem(
                Severity.WARNING,
                f"corner_radius {radius:g} mm is smaller than a 1/4\" end mill "
                f"({shop_end_mill:g} mm), the usual FRC shop minimum. "
                f"Check the tooling before committing to this."
                + team_note("plate_pocket", "end_mill_radius_mm"),
                parameter="corner_radius"))

        # 5. Stiffness. Pocketing is a trade, and this is the cost side.
        pocket_area = pocket_w * pocket_l * cols * rows
        removed = pocket_area / (plate_w * plate_l) if plate_w and plate_l else 0.0
        if removed > max_removed:
            problems.append(Problem(
                Severity.WARNING,
                f"These pockets remove {removed:.0%} of the plate area "
                f"({cols * rows} x {pocket_w:.1f} x {pocket_l:.1f} mm of "
                f"{plate_w:g} x {plate_l:g} mm). Past about "
                f"{max_removed:.0%} the bending stiffness falls off "
                f"quickly." + team_note("plate_pocket", "max_removed_fraction"),
                parameter="rows",
                suggestion="Widen the ribs, or pocket fewer cells."))

        # 6. The number the team is actually chasing.
        grams = self.mass_removed_g(params)
        through = depth >= thickness
        corner_penalty = (4 - 3.14159265) * radius * radius * cols * rows
        problems.append(Problem(
            Severity.WARNING,
            f"{cols * rows} pockets of {pocket_w:.1f} x {pocket_l:.1f} mm "
            f"{'through' if through else f'{depth:g} mm deep'} remove "
            f"{grams:.0f} g of 6061 aluminium "
            f"({ALUMINIUM_6061_DENSITY:g} kg/m3). Modelled with square corners, "
            f"so a routed part keeps about {corner_penalty:.0f} mm2 more material "
            f"and the real saving is slightly under this.",
            parameter="rows"))

        return problems

    # -------------------------------------------------------------- render

    def _grid_source(self, params: dict[str, Any], face_expr: str) -> str:
        """The shared body: measure the real face, then solve the grid on it.

        Emitted into both the probe and the module so the two cannot drift.
        `face_expr` is what differs — the probe has to *find* a face, the module
        is handed one by the student.
        """
        params = merge_defaults(self.parameters(), params)
        cols = int(params["columns"])
        rows = int(params["rows"])
        rib = float(params["rib_width"])
        border = float(params["border_width"])

        return f"""    var pocketFace = {face_expr};

    // A pocket only means something in a solid. Found the hard way: run against
    // a Part Studio holding only sheet bodies, the whole sketch/extrude/boolean
    // chain completes and reports success while removing nothing -- a false pass,
    // which is worse than a failure because it looks like verification.
    if (size(evaluateQuery(context, qBodyType(qOwnerBody(pocketFace),
                                              BodyType.SOLID))) == 0)
    {{
        throw regenError("That face belongs to a surface, not a solid body. " ~
            "Pocketing removes material, so it needs a solid plate.");
    }}

    // Boxed along the face's own sides, not the plane's x. planeToCSys keeps
    // the x the face was BUILT with, so a plate sketched at 30 deg measured
    // 167 x 140 instead of 150 x 75 and the grid was solved on the wrong box
    // (measured 2026-11-25). Sketch on `alignedPlane`, which shares faceBox's axes.
{aligned_box_source("pocketFace", "    ")}

    var uSpan = faceBox.maxCorner[0] - faceBox.minCorner[0];
    var vSpan = faceBox.maxCorner[1] - faceBox.minCorner[1];
    var border = {border:g} * millimeter;
    var rib = {rib:g} * millimeter;

    // Solved against the face we were actually given, not the stated size.
    var pocketU = (uSpan - 2 * border - {cols - 1} * rib) / {cols};
    var pocketV = (vSpan - 2 * border - {rows - 1} * rib) / {rows};

    // Report in whole millimetres. `toString` on a length yields things like
    // "0.15000000000000002 meter", and a student reading that learns nothing
    // except that the tool is untrustworthy.
    var uText = toString(round(uSpan / millimeter)) ~ " mm";
    var vText = toString(round(vSpan / millimeter)) ~ " mm";

    if (pocketU <= 0 * millimeter || pocketV <= 0 * millimeter)
    {{
        throw regenError("The selected face is " ~ uText ~ " x " ~ vText ~
            ", too small for {cols} x {rows} pockets with {border:g} mm " ~
            "border and {rib:g} mm ribs.");
    }}"""

    def render_probe(self, params: dict[str, Any]) -> str:
        """A bare lambda: find the largest planar face and check the grid on it.

        The module takes the face from the student. The probe has no selection to
        work with, so it picks the largest planar face — which for a plate is the
        one anybody would pocket. That makes this a real test of the query
        mechanics *and* of the arithmetic against real geometry, at one request.
        """
        params = merge_defaults(self.parameters(), params)
        plate_w = float(params["plate_width"])
        plate_l = float(params["plate_length"])

        find_face = """(function()
    {
        var solids = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);
        if (size(evaluateQuery(context, solids)) == 0)
        {
            throw regenError("This Part Studio has no solid body. Pocketing " ~
                "removes material from a plate, so there is nothing to pocket.");
        }
        var planar = qGeometry(qOwnedByBody(solids, EntityType.FACE),
                               GeometryType.PLANE);
        var faces = evaluateQuery(context, planar);
        if (size(faces) == 0)
        {
            throw regenError("No planar face on any solid body to pocket.");
        }
        var best = faces[0];
        var bestArea = evArea(context, { "entities" : best });
        for (var f in faces)
        {
            var area = evArea(context, { "entities" : f });
            if (area > bestArea) { best = f; bestArea = area; }
        }
        return best;
    })()"""

        # The stated plate size is cross-checked against the face we measured.
        # A mismatch means the caller is designing against the wrong part, which
        # is exactly the failure `tube_hole_pattern` caught on its first live run.
        stated = max(plate_w, plate_l)
        return f"""function(context is Context, queries)
{{
{self._grid_source(params, find_face)}

    var largestSpan = max(uSpan, vSpan);
    if (abs(largestSpan - {stated:g} * millimeter) > 1 * millimeter)
    {{
        throw regenError("The largest planar face is " ~ uText ~ " x " ~ vText ~
            " but the pockets were sized for a {plate_w:g} x {plate_l:g} mm plate.");
    }}

    return {{ "pocketU_mm" : pocketU / millimeter, "pocketV_mm" : pocketV / millimeter }};
}}"""

    def render_module(self, params: dict[str, Any]) -> str:
        """The full Feature Studio module.

        The face is a selection with a PLANE filter, so Onshape rejects a bad
        pick at selection time rather than as a regeneration error afterwards.
        """
        params = merge_defaults(self.parameters(), params)
        cols = int(params["columns"])
        rows = int(params["rows"])
        thickness = float(params["plate_thickness"])
        depth = float(params.get("pocket_depth") or thickness)

        return f"""FeatureScript 3029;
import(path : "onshape/std/geometry.fs", version : "3029.0");

// Generated by frc-cad-copilot, template `{self.name}`.
// Parameters: {_describe(params)}
// Removes about {self.mass_removed_g(params):.0f} g of 6061 aluminium.
annotation {{ "Feature Type Name" : "FRC Plate Pocket Pattern" }}
export const frcPlatePocketPattern = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
        // A Query parameter's annotation MUST carry a Filter or the module will
        // not compile, and the write path will not say why.
        annotation {{ "Name" : "Face to pocket",
                     "Filter" : EntityType.FACE && GeometryType.PLANE,
                     "MaxNumberOfPicks" : 1 }}
        definition.face is Query;
    }}
    {{
{self._grid_source(params, "definition.face")}

        var sketch = newSketchOnPlane(context, id + "pocketSketch", {{
            "sketchPlane" : alignedPlane
        }});

        for (var row = 0; row < {rows}; row += 1)
        {{
            for (var col = 0; col < {cols}; col += 1)
            {{
                var uMin = faceBox.minCorner[0] + border + col * (pocketU + rib);
                var vMin = faceBox.minCorner[1] + border + row * (pocketV + rib);
                skRectangle(sketch, "pocket" ~ row ~ "_" ~ col, {{
                    "firstCorner" : vector(uMin, vMin),
                    "secondCorner" : vector(uMin + pocketU, vMin + pocketV)
                }});
            }}
        }}
        skSolve(sketch);

        opExtrude(context, id + "pocketCut", {{
            "entities" : qSketchRegion(id + "pocketSketch"),
            "direction" : -facePlane.normal,
            "endBound" : BoundingType.BLIND,
            "endDepth" : {depth:g} * millimeter
        }});

        opBoolean(context, id + "pocketRemove", {{
            "tools" : qCreatedBy(id + "pocketCut", EntityType.BODY),
            "targets" : qOwnerBody(definition.face),
            "operationType" : BooleanOperationType.SUBTRACTION
        }});
    }});"""


def _describe(params: dict[str, Any]) -> str:
    keys = ("plate_width", "plate_length", "plate_thickness", "columns", "rows",
            "rib_width", "border_width", "pocket_depth", "corner_radius")
    return ", ".join(
        f"{k}={params[k]:g}" for k in keys if params.get(k) is not None
    )
