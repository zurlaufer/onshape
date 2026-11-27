"""Place a COTS sub-assembly where its mounting holes meet a Part Studio's holes.

`frc_assemble` puts parts where their Part Studio has them. A bought module,
imported once as an assembly and needed at four corners, has no such place:
it goes wherever the frame was drilled for it. So the placement is FITTED
from the geometry, not given, and the fit is the evidence.

Measured on the 2023 session's MK4i and frame (scripts/spike_place_modules.py,
2026-11-27): exactly four placements, one per corner and quarter turn, every
screw on a hole to 0.0000 mm, the tube meeting both module plates with no
height offset.

THE METHOD, AND THE LIMITS THAT COME WITH IT

  * Mounting axes are VERTICAL cylinders, in the module and in the target.
    A module whose model has its mounting axes along X or Y is refused, not
    tipped over: the rotation is about Z only, in quarter turns. Robot frames
    are square, and a discrete set of rotations is what lets the result say
    "exactly four" rather than "about four".
  * A module axis matches a target hole when they are within TOLERANCE_MM in
    plan and their radii are compatible: a screw through the hole or a hole
    over the hole (RADIUS_RATIO). Recognised by geometry, never by a part
    name: the spike's "10-32 X 2.5" is an MK4i detail.
  * Every quarter turn and every (axis -> hole) pairing proposes a placement;
    the ones that put the most holes under axes win. Refused: fewer than
    MIN_HOLES, a pattern symmetric enough that two rotations fill the same
    holes, and two placements that share a hole.
  * Height. Holes say nothing about Z. The module's flat faces around the
    mounting axes are slid onto the flat faces of the drilled bodies around
    the holes, and the offset that makes the most of them meet wins (no
    offset, if it ties). Ambiguous or no contact is refused.

TWO TRAPS, BOTH MEASURED 2026-11-27

  * An ABSOLUTE `occurrencetransforms` on a sub-assembly instance is applied
    to every part inside it: all 146 occurrences of the MK4i collapsed onto
    its corner. A fresh instance sits at identity, so the placement is sent
    as a RELATIVE move.
  * The first check composed the module's transforms with ours and passed at
    0.0000 mm on that collapsed module. `verify` reads the assembly's own
    nested occurrences, and also compares each one with what it should be.

No `print()` here: this runs inside the MCP server, where stdout is protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = [
    "PlacementError", "Axis", "Flat", "Placement", "ModulePlan",
    "module_geometry", "target_geometry", "fit", "height", "verify",
    "plan_placement", "TOLERANCE_MM",
]

TOLERANCE_MM = 0.05
FACE_TOLERANCE_MM = 0.01
MIN_HOLES = 3
#: Up to a 1/2 in bolt. Bearing bores and shafts are not fastener holes.
MAX_HOLE_RADIUS_MM = 6.5
#: Module radius over hole radius. 0.74 measured: the MK4i's 10-32 shank
#: (1.897 mm) in the frame's 0.201 in hole (2.553 mm). Above 1 by a hair, for
#: a hole drilled over the same hole.
RADIUS_RATIO = (0.6, 1.02)
_QUARTERS = ((1, 0), (0, 1), (-1, 0), (0, -1))   # (cos, sin)
#: Vote bin for candidate moves. Must exceed TOLERANCE_MM for the bound in
#: `fit` to hold.
_BIN_MM = 0.5
_AXIS_TOLERANCE = 1e-6


class PlacementError(ValueError):
    """A placement this module will not make; the message says why."""


# ------------------------------------------------------------------ geometry

def matrix(t: list[float]) -> list[list[float]]:
    return [list(t[0:4]), list(t[4:8]), list(t[8:12]), list(t[12:16])]


def compose(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _apply(T, p) -> list[float]:
    return [T[r][0] * p[0] + T[r][1] * p[1] + T[r][2] * p[2] + T[r][3] for r in range(3)]


def _turn(T, v) -> list[float]:
    return [T[r][0] * v[0] + T[r][1] * v[1] + T[r][2] * v[2] for r in range(3)]


def _vec(d: dict) -> list[float]:
    return [float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("z", 0.0))]


@dataclass(frozen=True)
class Axis:
    """A vertical cylinder, in millimetres. `radii` holds every cylinder on
    this axis: a screw in a plate hole is one axis with two radii."""

    x: float
    y: float
    radii: tuple[float, ...]
    z_lo: float = 0.0
    z_hi: float = 0.0
    bodies: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Flat:
    """A horizontal planar face: its height and plan box, in millimetres."""

    z: float
    x_lo: float
    y_lo: float
    x_hi: float
    y_hi: float
    body: str = ""

    def covers(self, x: float, y: float) -> bool:
        t = TOLERANCE_MM
        return self.x_lo - t <= x <= self.x_hi + t and self.y_lo - t <= y <= self.y_hi + t


def _box_mm(T, face) -> list[float]:
    box = face.get("box") or {}
    lo, hi = _vec(box.get("minCorner", {})), _vec(box.get("maxCorner", {}))
    corners = [_apply(T, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
               for z in (lo[2], hi[2])]
    return ([min(c[i] for c in corners) * 1000 for i in range(3)]
            + [max(c[i] for c in corners) * 1000 for i in range(3)])


def _faces(faces: list[dict], T, body: str) -> tuple[list[Axis], list[Flat]]:
    axes: list[Axis] = []
    flats: list[Flat] = []
    for face in faces:
        s = face.get("surface") or {}
        kind = s.get("type")
        if kind == "CYLINDER":
            a = _turn(T, _vec(s.get("axis", {})))
            if abs(abs(a[2]) - 1) > _AXIS_TOLERANCE:
                continue
            o = _apply(T, _vec(s.get("origin", {})))
            b = _box_mm(T, face)
            axes.append(Axis(o[0] * 1000, o[1] * 1000, (float(s["radius"]) * 1000,),
                             b[2], b[5], frozenset({body})))
        elif kind == "PLANE":
            n = _turn(T, _vec(s.get("normal", {})))
            if abs(abs(n[2]) - 1) > _AXIS_TOLERANCE:
                continue
            o = _apply(T, _vec(s.get("origin", {})))
            b = _box_mm(T, face)
            flats.append(Flat(o[2] * 1000, b[0], b[1], b[3], b[4], body))
    return axes, flats


def merge(axes: list[Axis]) -> list[Axis]:
    """One axis per plan position, keeping every radius and body on it."""
    merged: dict[tuple[float, float], Axis] = {}
    for a in axes:
        key = (round(a.x, 2), round(a.y, 2))
        m = merged.get(key)
        merged[key] = a if m is None else Axis(
            m.x, m.y, tuple(sorted(set(m.radii) | set(a.radii))),
            min(m.z_lo, a.z_lo), max(m.z_hi, a.z_hi), m.bodies | a.bodies)
    return list(merged.values())


def _leaf(definition: dict, path: list[str]) -> dict | None:
    """The instance an occurrence path ends at."""
    subs = {(s.get("elementId"), s.get("configuration", "default")): s
            for s in definition.get("subAssemblies", [])}
    instances = definition.get("rootAssembly", {}).get("instances", [])
    for depth, iid in enumerate(path):
        inst = next((i for i in instances if i.get("id") == iid), None)
        if inst is None or depth == len(path) - 1:
            return inst
        sub = subs.get((inst.get("elementId"), inst.get("configuration", "default")))
        if sub is None:
            return None
        instances = sub.get("instances", [])
    return None


def module_geometry(definition: dict, bodies: dict[str, dict[str, dict]],
                    prefix: str | None = None) -> tuple[list[Axis], list[Flat], int]:
    """Vertical axes and horizontal faces of an assembly's parts, from its own
    occurrence transforms. `bodies` is element id -> part id -> bodydetails
    body. With `prefix`, only the parts inside that top-level instance, still
    in the assembly's coordinates. Returns (axes, flats, parts not measured).
    """
    axes: list[Axis] = []
    flats: list[Flat] = []
    unmeasured = 0
    for occ in definition.get("rootAssembly", {}).get("occurrences", []):
        path = occ.get("path") or []
        if prefix is not None and (len(path) < 2 or path[0] != prefix):
            continue
        inst = _leaf(definition, path)
        if inst is None or inst.get("type") != "Part":
            continue
        body = bodies.get(inst.get("elementId"), {}).get(inst.get("partId"))
        if body is None:
            unmeasured += 1
            continue
        a, f = _faces(body.get("faces", []), matrix(occ["transform"]), "/".join(path))
        axes += a
        flats += f
    return merge(axes), flats, unmeasured


def target_geometry(bodydetails: dict) -> tuple[list[Axis], list[Flat]]:
    """A Part Studio's fastener holes and flat faces, in its own coordinates."""
    identity = matrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    axes: list[Axis] = []
    flats: list[Flat] = []
    for body in bodydetails.get("bodies", []):
        if body.get("type") != "SOLID":
            continue
        a, f = _faces(body.get("faces", []), identity, body.get("id", ""))
        axes += [x for x in a if min(x.radii) <= MAX_HOLE_RADIUS_MM]
        flats += f
    return merge(axes), flats


# ---------------------------------------------------------------------- fit

@dataclass
class Placement:
    """One module position: a quarter turn, then a move, in millimetres."""

    quarter: int
    tx: float
    ty: float
    holes: tuple[Axis, ...]
    tz: float = 0.0
    faces: tuple[float, ...] = ()

    def transform(self) -> list[float]:
        """Onshape's row-major 4x4, translation in metres."""
        c, s = _QUARTERS[self.quarter]
        return [c, -s, 0, self.tx / 1000, s, c, 0, self.ty / 1000,
                0, 0, 1, self.tz / 1000, 0, 0, 0, 1]

    def describe(self) -> dict:
        return {"at_mm": [round(self.tx, 1), round(self.ty, 1)],
                "rotation_deg": self.quarter * 90, "holes": len(self.holes)}


def _compatible(module_radii, hole_radii) -> bool:
    lo, hi = RADIUS_RATIO
    return any(lo * h <= m <= hi * h for m in module_radii for h in hole_radii)


def _rotate(x: float, y: float, quarter: int) -> tuple[float, float]:
    c, s = _QUARTERS[quarter]
    return c * x - s * y, s * x + c * y


def fit(axes: list[Axis], holes: list[Axis]) -> list[Placement]:
    """Every placement that puts the most holes under module axes.

    Raises PlacementError when that is fewer than MIN_HOLES, when the pattern
    cannot tell two rotations apart, or when two placements share a hole.
    """
    if not holes:
        raise PlacementError("The target Part Studio has no vertical fastener holes.")
    if not axes:
        raise PlacementError(
            "The module has no vertical holes or screws. Its model may have its "
            "mounting axes along X or Y; this places by quarter turns about Z only.")
    all_r = [r for h in holes for r in h.radii]
    lo, hi = RADIUS_RATIO[0] * min(all_r), RADIUS_RATIO[1] * max(all_r)
    candidates = [a for a in axes if any(lo <= r <= hi for r in a.radii)]

    grid: dict[tuple[int, int], list[int]] = {}
    for i, h in enumerate(holes):
        grid.setdefault((math.floor(h.x), math.floor(h.y)), []).append(i)

    def hole_at(x: float, y: float, radii) -> int | None:
        cx, cy = math.floor(x), math.floor(y)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in grid.get((gx, gy), ()):
                    h = holes[i]
                    if math.hypot(h.x - x, h.y - y) <= TOLERANCE_MM and _compatible(radii, h.radii):
                        return i
        return None

    # Voting first, exact check after. Every compatible (axis, hole) pair
    # votes for the move that would put that axis on that hole, binned at
    # _BIN_MM. A placement's hits all vote within TOLERANCE_MM of it, so the
    # holes voting in a bin and its neighbours bound what any move in that bin
    # can score, and most bins are never checked. Checking every pair's move
    # took 50 s on the MK4i (1,298 axes, 58 holes); this takes well under one.
    turned_by_q = [[(*_rotate(a.x, a.y, q), a.radii) for a in candidates] for q in range(4)]
    votes: dict[tuple[int, int, int], tuple[set[int], dict[tuple[int, int], tuple[float, float]]]] = {}
    for q, turned in enumerate(turned_by_q):
        for mx, my, mr in turned:
            for i, h in enumerate(holes):
                if not _compatible(mr, h.radii):
                    continue
                tx, ty = h.x - mx, h.y - my
                hit, moves = votes.setdefault(
                    (q, math.floor(tx / _BIN_MM), math.floor(ty / _BIN_MM)), (set(), {}))
                hit.add(i)
                moves.setdefault((round(tx * 100), round(ty * 100)), (tx, ty))

    def bound(key: tuple[int, int, int]) -> int:
        q, bx, by = key
        near: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cell = votes.get((q, bx + dx, by + dy))
                if cell:
                    near |= cell[0]
        return len(near)

    best: dict[tuple[int, frozenset[int]], tuple[float, float]] = {}
    best_n = 0
    for key, limit in sorted(((k, bound(k)) for k in votes), key=lambda kv: -kv[1]):
        if limit < max(best_n, MIN_HOLES):
            break
        q = key[0]
        for tx, ty in votes[key][1].values():
            hits = frozenset(i for i in (hole_at(x + tx, y + ty, r) for x, y, r in turned_by_q[q])
                             if i is not None)
            if len(hits) > best_n:
                best_n, best = len(hits), {}
            if len(hits) == best_n and (q, hits) not in best:
                best[(q, hits)] = (tx, ty)

    if best_n < MIN_HOLES:
        raise PlacementError(
            f"No placement puts {MIN_HOLES} or more of the target's holes under the "
            f"module's (best: {best_n}). Is the target drilled for this module?")

    by_holes: dict[frozenset[int], list[int]] = {}
    for q, hits in best:
        by_holes.setdefault(hits, []).append(q)
    symmetric = [qs for qs in by_holes.values() if len(qs) > 1]
    if symmetric:
        raise PlacementError(
            f"The hole pattern is symmetric: rotations {[q * 90 for q in symmetric[0]]} "
            f"deg fill the same {best_n} holes, so the holes cannot say which way the "
            f"module faces. Place this one by hand.")
    used: set[int] = set()
    for hits in by_holes:
        if used & hits:
            raise PlacementError(
                "Two placements share a hole, so they would overlap. The holes do not "
                "single out where the modules go.")
        used |= hits

    return sorted((Placement(q, tx, ty, tuple(holes[i] for i in sorted(hits)))
                   for (q, hits), (tx, ty) in best.items()),
                  key=lambda p: (p.quarter, round(p.tx, 3), round(p.ty, 3)))


def height(placement: Placement, module_flats: list[Flat],
           target_flats: list[Flat]) -> tuple[float, tuple[float, ...]]:
    """The Z offset that makes the most faces meet around the mounting holes.

    Returns (offset mm, target face heights that meet). No offset wins a tie;
    any other tie is refused, as is a module with nothing to rest on.
    """
    points = [(h.x, h.y) for h in placement.holes]
    bodies = set().union(*(h.bodies for h in placement.holes))
    module_z = sorted({round(f.z, 4) for f in (_place_flat(f, placement) for f in module_flats)
                       if any(f.covers(x, y) for x, y in points)})
    target_z = sorted({round(f.z, 4) for f in target_flats
                       if f.body in bodies and any(f.covers(x, y) for x, y in points)})
    if not module_z or not target_z:
        raise PlacementError(
            "No flat face of the module meets a flat face of the drilled part around "
            "the holes, so its height cannot be set from contact.")

    offsets: list[float] = []
    for dz in sorted(t - m for t in target_z for m in module_z):
        if not offsets or dz - offsets[-1] > FACE_TOLERANCE_MM:
            offsets.append(dz)

    def meeting(dz: float) -> tuple[float, ...]:
        return tuple(t for t in target_z
                     if any(abs(m + dz - t) <= FACE_TOLERANCE_MM for m in module_z))

    scored = {dz: meeting(dz) for dz in offsets}
    most = max(len(v) for v in scored.values())
    tied = [dz for dz, v in scored.items() if len(v) == most]
    zero = [dz for dz in tied if abs(dz) <= FACE_TOLERANCE_MM]
    if zero:
        return 0.0, scored[zero[0]]
    if len(tied) == 1:
        return round(tied[0], 4), scored[tied[0]]
    raise PlacementError(
        f"The height is ambiguous: offsets {[round(d, 2) for d in tied]} mm each make "
        f"{most} face(s) meet. Place this one by hand.")


def _place_flat(f: Flat, p: Placement) -> Flat:
    a = _rotate(f.x_lo, f.y_lo, p.quarter)
    b = _rotate(f.x_hi, f.y_hi, p.quarter)
    return Flat(f.z + p.tz, min(a[0], b[0]) + p.tx, min(a[1], b[1]) + p.ty,
                max(a[0], b[0]) + p.tx, max(a[1], b[1]) + p.ty, f.body)


# ------------------------------------------------------------------- verify

def verify(assembly: dict, instance_id: str, placement: Placement, module: dict,
           bodies: dict[str, dict[str, dict]]) -> dict:
    """Is this module instance where the placement says, by the assembly's own account?

    Three readings, each from what Onshape reports for the ASSEMBLY:
      * its top-level transform against the one sent;
      * every part inside it against the module's own layout (catches the
        collapse an absolute move causes);
      * each fitted hole against the nearest mounting axis, measured from the
        assembly's nested occurrences, never from transforms composed here.
    """
    occurrences = {tuple(o["path"]): o["transform"]
                   for o in assembly.get("rootAssembly", {}).get("occurrences", [])}
    top = occurrences.get((instance_id,))
    if top is None:
        return {"in_place": False, "why": "the instance is not in the assembly"}
    top_error_mm = max(abs(a - b) * (1000 if i in (3, 7, 11) else 1)
                       for i, (a, b) in enumerate(zip(top, placement.transform())))

    T = matrix(top)
    drift_mm = 0.0
    for occ in module.get("rootAssembly", {}).get("occurrences", []):
        got = occurrences.get((instance_id, *occ["path"]))
        if got is None:
            continue
        expected = compose(T, matrix(occ["transform"]))
        g = matrix(got)
        drift_mm = max(drift_mm, max(abs(expected[r][3] - g[r][3]) * 1000 for r in range(3)))

    axes, _, _ = module_geometry(assembly, bodies, prefix=instance_id)
    worst_mm = max((min((math.hypot(a.x - h.x, a.y - h.y) for a in axes), default=math.inf)
                    for h in placement.holes), default=math.inf)
    in_place = top_error_mm <= 1e-3 and drift_mm <= 0.01 and worst_mm <= TOLERANCE_MM
    out = {"in_place": in_place, "worst_hole_mm": round(worst_mm, 4)}
    if drift_mm > 0.01:
        out["why"] = (f"parts inside it moved up to {drift_mm:.1f} mm from the module's "
                      f"own layout")
    elif top_error_mm > 1e-3:
        out["why"] = "it is not at the fitted position"
    return out


# ---------------------------------------------------------------- planning

@dataclass
class ModulePlan:
    """Everything needed to place one module, read before any write."""

    module_id: str
    module_name: str
    target_name: str
    definition: dict
    bodies: dict[str, dict[str, dict]]
    placements: list[Placement]
    unmeasured: int = 0
    present: dict[int, str] = field(default_factory=dict)   # placement index -> instance id

    def describe(self) -> dict:
        first = self.placements[0]
        out: dict = {
            "onto": self.target_name,
            "height_offset_mm": round(first.tz, 2),
            "faces_meeting_mm": [round(z, 3) for z in first.faces],
            "placements": [p.describe() for p in self.placements],
        }
        if self.unmeasured:
            out["parts_not_measured"] = self.unmeasured
        return out


async def plan_placement(client, *, base: str, document_id: str, module_id: str,
                         module_name: str, target_id: str, target_name: str) -> ModulePlan:
    """Read the module and the target, and fit. Reads only; raises
    PlacementError on anything this will not place."""
    definition = (await client.get(f"/assemblies/{base}/e/{module_id}")).json()
    elements = {i.get("elementId")
                for group in [definition.get("rootAssembly", {})] + definition.get("subAssemblies", [])
                for i in group.get("instances", [])
                if i.get("type") == "Part" and i.get("documentId") == document_id}
    bodies: dict[str, dict[str, dict]] = {}
    for eid in sorted(e for e in elements if e):
        details = (await client.get(f"/partstudios/{base}/e/{eid}/bodydetails")).json()
        bodies[eid] = {b["id"]: b for b in details.get("bodies", [])}
    axes, flats, unmeasured = module_geometry(definition, bodies)

    target = (await client.get(f"/partstudios/{base}/e/{target_id}/bodydetails")).json()
    holes, target_flats = target_geometry(target)

    placements = fit(axes, holes)
    for p in placements:
        p.tz, p.faces = height(p, flats, target_flats)
    return ModulePlan(module_id, module_name, target_name, definition, bodies,
                      placements, unmeasured)


def placed_already(definition: dict, plan: ModulePlan) -> dict[int, str]:
    """Placement index -> instance id, for module instances already at a fitted position."""
    occurrences = {tuple(o["path"]): o["transform"]
                   for o in definition.get("rootAssembly", {}).get("occurrences", [])}
    found: dict[int, str] = {}
    for inst in definition.get("rootAssembly", {}).get("instances", []):
        if inst.get("type") != "Assembly" or inst.get("elementId") != plan.module_id:
            continue
        top = occurrences.get((inst["id"],))
        if top is None:
            continue
        for i, p in enumerate(plan.placements):
            if i not in found and all(abs(a - b) <= 1e-6 for a, b in zip(top, p.transform())):
                found[i] = inst["id"]
                break
    return found
