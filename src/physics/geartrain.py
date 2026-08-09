"""Spur gear trains: which stacks reach a reduction, and which can be built.

Dependency-free, like the rest of `src/physics`. Everything here is arithmetic
on tooth counts and millimetres.

THE CONSTRAINT THIS FILE EXISTS FOR. A three-stage spur train has four shafts.
Stage *i*'s driven gear rides on shaft *i*, and shaft *i+1* passes straight
through that gear's plane as a bare shaft, at stage *i+1*'s centre distance.
The gear and the shaft are then competing for the same space:

    centre_distance(stage i+1) >= outer_radius(driven gear of stage i)
                                  + shaft_radius + clearance

which couples consecutive stages. A high per-stage ratio wants a small pinion,
a small pinion gives a small centre distance, and a small centre distance is
exactly what the *previous* stage's big gear cannot tolerate. So driven gears
have to GROW along the chain.

`11:60 x 11:60` fails this by 4.8 mm on a 1/2 in hex shaft with a standard
spacer. No layout rescues it: the distance that clashes is a mesh distance,
fixed by the tooth counts, not a free parameter.

A checker that tests gears against BEARINGS instead passes all of these, which
is how the constraint gets missed. Bearings sit in the plate; shafts run
between the plates, and that is where the collision is.

UNITS: millimetres and teeth. Diametral pitch is in teeth per inch because that
is how FRC gears are sold; the inch conversion is done here, once.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

__all__ = [
    "MM_PER_INCH",
    "COTS_TEETH",
    "HEX_SHAFT_RADIUS_MM",
    "SPACER_RADIUS_MM",
    "BEARING_OD_MM",
    "EDGE_MARGIN_MM",
    "Stage",
    "GearStack",
    "PlateLayout",
    "pitch_radius_mm",
    "outer_radius_mm",
    "centre_distance_mm",
    "search_stacks",
    "fold_layout",
]

MM_PER_INCH = 25.4

#: Tooth counts actually sold as FRC 20 DP COTS gears. Not every integer is
#: buyable, and a stack of unbuyable gears is a drawing, not a design.
COTS_TEETH = (
    11, 12, 13, 14, 15, 16, 17, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38,
    40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72,
)

#: Circumradius of 1/2 in hex stock: half the across-flats, over cos(30).
HEX_SHAFT_RADIUS_MM = (0.5 * MM_PER_INCH / 2.0) / math.cos(math.radians(30.0))

#: A hex spacer is fatter than the shaft it rides on, and it is what a gear
#: actually hits. Use this, not the bare shaft, unless you have measured.
SPACER_RADIUS_MM = 0.75 * MM_PER_INCH / 2.0

#: FRC 1/2 in hex flanged bearing, 1.125 in OD. Verify against your supplier
#: before cutting — this is the common standard, not a datasheet reading.
BEARING_OD_MM = 1.125 * MM_PER_INCH

#: Material left outside a bore before the plate edge. 0.15 in, rule of thumb.
EDGE_MARGIN_MM = 0.15 * MM_PER_INCH


@dataclass(frozen=True)
class Stage:
    """One spur mesh: a pinion driving a gear."""

    driving: int
    driven: int

    def __post_init__(self) -> None:
        if self.driving < 1 or self.driven < 1:
            raise ValueError("tooth counts must be positive")

    @property
    def ratio(self) -> float:
        return self.driven / self.driving

    def centre_distance(self, dp: float = 20.0) -> float:
        return centre_distance_mm(self.driving, self.driven, dp)

    def __str__(self) -> str:
        return f"{self.driving}:{self.driven}"


def pitch_radius_mm(teeth: int, dp: float = 20.0) -> float:
    """Pitch radius. Where the gears roll on each other."""
    return teeth / dp / 2.0 * MM_PER_INCH


def outer_radius_mm(teeth: int, dp: float = 20.0) -> float:
    """Tip radius. One module of addendum beyond the pitch radius.

    This, not the pitch radius, is what collides with things.
    """
    return (teeth + 2.0) / dp / 2.0 * MM_PER_INCH


def centre_distance_mm(driving: int, driven: int, dp: float = 20.0) -> float:
    """Distance between the two shafts. Fixed by the mesh, never a free choice."""
    return (driving + driven) / (2.0 * dp) * MM_PER_INCH


@dataclass(frozen=True)
class GearStack:
    """An ordered chain of spur stages, and whether it can be built."""

    stages: tuple[Stage, ...]
    dp: float = 20.0

    @property
    def ratio(self) -> float:
        total = 1.0
        for stage in self.stages:
            total *= stage.ratio
        return total

    @property
    def centre_distances(self) -> tuple[float, ...]:
        return tuple(s.centre_distance(self.dp) for s in self.stages)

    def clearance_problems(
        self,
        *,
        shaft_radius_mm: float = SPACER_RADIUS_MM,
        clearance_mm: float = 1.0,
        bearing_od_mm: float = BEARING_OD_MM,
        wall_mm: float = 3.0,
    ) -> list[str]:
        """Every reason this stack cannot be built, in plain sentences.

        Two families of problem. The chain one is the subtle one described in
        the module docstring. The bore one is blunter: two bearings whose
        centres are closer than an OD plus a wall leave no plate between them.
        """
        problems: list[str] = []

        for index in range(len(self.stages) - 1):
            gear = self.stages[index].driven
            needed = outer_radius_mm(gear, self.dp) + shaft_radius_mm + clearance_mm
            have = self.stages[index + 1].centre_distance(self.dp)
            if have < needed:
                problems.append(
                    f"stage {index + 1}'s {gear}T driven gear fouls shaft "
                    f"{index + 2}: needs {needed:.2f} mm of centre distance, "
                    f"stage {index + 2} gives {have:.2f} mm "
                    f"({needed - have:.2f} mm interference)"
                )

        for index, stage in enumerate(self.stages, start=1):
            have = stage.centre_distance(self.dp)
            needed = bearing_od_mm + wall_mm
            if have < needed:
                problems.append(
                    f"stage {index} ({stage}) puts its bores {have:.2f} mm "
                    f"apart, leaving less than {wall_mm:.1f} mm of plate "
                    f"between {bearing_od_mm:.2f} mm bearings"
                )

        return problems

    def is_buildable(
        self,
        *,
        shaft_radius_mm: float = SPACER_RADIUS_MM,
        clearance_mm: float = 1.0,
        bearing_od_mm: float = BEARING_OD_MM,
        wall_mm: float = 3.0,
    ) -> bool:
        """The same test as `clearance_problems`, without describing the failure.

        Kept separate rather than `not clearance_problems()` because the search
        calls it across a million candidates, nearly all of which fail, and
        formatting a sentence to explain each one is most of the run time.
        """
        for index in range(len(self.stages) - 1):
            needed = (outer_radius_mm(self.stages[index].driven, self.dp)
                      + shaft_radius_mm + clearance_mm)
            if self.stages[index + 1].centre_distance(self.dp) < needed:
                return False
        minimum = bearing_od_mm + wall_mm
        return all(s.centre_distance(self.dp) >= minimum for s in self.stages)

    def __str__(self) -> str:
        return " x ".join(str(s) for s in self.stages)


def search_stacks(
    *,
    ratio_min: float,
    ratio_max: float,
    stages: int = 3,
    dp: float = 20.0,
    max_pinion: int = 18,
    min_stage_ratio: float = 3.0,
    teeth: tuple[int, ...] = COTS_TEETH,
    **clearance_kwargs,
) -> list[GearStack]:
    """Every buildable stack in a reduction window, tightest plate span first.

    Sorted by the sum of centre distances, which is the cheapest proxy for how
    much plate the train will need. It is only a proxy: the real footprint
    depends on how the train is folded, so treat the ordering as a shortlist to
    lay out rather than a verdict.
    """
    pairs = [
        Stage(driving, driven)
        for driving in teeth
        if driving <= max_pinion
        for driven in teeth
        if driven >= min_stage_ratio * driving
    ]

    found: list[GearStack] = []
    for combination in itertools.product(pairs, repeat=stages):
        stack = GearStack(combination, dp)
        if not (ratio_min <= stack.ratio <= ratio_max):
            continue
        if not stack.is_buildable(**clearance_kwargs):
            continue
        found.append(stack)

    found.sort(key=lambda s: sum(s.centre_distances))
    return found


# ---------------------------------------------------------------- plate layout


@dataclass(frozen=True)
class PlateLayout:
    """Where the shafts sit, and the rectangle that covers them.

    `shafts` are millimetre (x, y) centres with the plate's corner at (0, 0),
    which is the form a sketch wants.
    """

    stack: GearStack
    shafts: tuple[tuple[float, float], ...]
    width_mm: float
    height_mm: float

    @property
    def longest_side_mm(self) -> float:
        return max(self.width_mm, self.height_mm)

    @property
    def area_mm2(self) -> float:
        return self.width_mm * self.height_mm


def _largest_gear_radii(stack: GearStack) -> list[float]:
    """Biggest gear tip radius riding on each shaft."""
    radii = [0.0] * (len(stack.stages) + 1)
    for index, stage in enumerate(stack.stages):
        radii[index] = max(radii[index], outer_radius_mm(stage.driving, stack.dp))
        radii[index + 1] = max(radii[index + 1], outer_radius_mm(stage.driven, stack.dp))
    return radii


def fold_layout(
    stack: GearStack,
    *,
    step_deg: int = 5,
    shaft_radius_mm: float = SPACER_RADIUS_MM,
    clearance_mm: float = 1.0,
    bearing_od_mm: float = BEARING_OD_MM,
    wall_mm: float = 3.0,
    edge_margin_mm: float = EDGE_MARGIN_MM,
    objective: str = "longest",
) -> PlateLayout | None:
    """Zigzag the train to the smallest plate, or None if nothing fits.

    `objective` is not a detail. Minimising AREA picks the collinear train every
    time — a long thin sliver really is the smallest rectangle by area, and also
    the longest part on the robot. Minimising the LONGEST side is what "compact"
    usually means. Both are offered because either can be the right answer:
    a sliver is ideal bolted along a frame rail.

    Meshing neighbours are exempt from the gear-clearance test by construction;
    non-neighbours are not, and neither is any pair of bores.

    Shape and orientation are searched separately. Whether a train collides with
    itself does not depend on how the whole thing is rotated, so validity is
    tested once per shape and only the bounding box is swept over rotations.
    That is worth doing: the collision test is the expensive half, and this runs
    it `360/step_deg` times less often.
    """
    if objective not in ("longest", "area"):
        raise ValueError("objective must be 'longest' or 'area'")

    radii = _largest_gear_radii(stack)
    distances = stack.centre_distances
    bore_gap = bearing_od_mm + wall_mm
    pad = bearing_od_mm / 2.0 + edge_margin_mm
    turns = [math.radians(a) for a in range(-180, 180, step_deg)]
    pairs = [
        (i, j, bore_gap if j == i + 1
         else max(bore_gap, radii[i] + shaft_radius_mm + clearance_mm,
                  radii[j] + shaft_radius_mm + clearance_mm))
        for i, j in itertools.combinations(range(len(distances) + 1), 2)
    ]

    best: PlateLayout | None = None
    best_key: tuple[float, float] | None = None

    # The first link's direction is the global rotation, so the shape is fixed
    # by the remaining turns. One less nested loop than it looks.
    for shape in itertools.product(turns, repeat=len(distances) - 1):
        points = [(0.0, 0.0)]
        heading = 0.0
        for index, distance in enumerate(distances):
            if index:
                heading += shape[index - 1]
            x, y = points[-1]
            points.append((x + distance * math.cos(heading),
                           y + distance * math.sin(heading)))

        if any(math.dist(points[i], points[j]) < minimum for i, j, minimum in pairs):
            continue

        for rotation in turns:
            cos_r, sin_r = math.cos(rotation), math.sin(rotation)
            spun = [(x * cos_r - y * sin_r, x * sin_r + y * cos_r)
                    for x, y in points]
            xs = [p[0] for p in spun]
            ys = [p[1] for p in spun]
            width = max(xs) - min(xs) + 2 * pad
            height = max(ys) - min(ys) + 2 * pad
            key = ((max(width, height), width * height) if objective == "longest"
                   else (width * height, max(width, height)))
            if best_key is None or key < best_key:
                origin_x, origin_y = min(xs) - pad, min(ys) - pad
                best_key = key
                best = PlateLayout(
                    stack=stack,
                    shafts=tuple((x - origin_x, y - origin_y) for x, y in spun),
                    width_mm=width,
                    height_mm=height,
                )

    return best
