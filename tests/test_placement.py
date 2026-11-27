"""Tests for src/skills/placement.py and `assemble(place=...)`.

Offline. The geometry is built in bodydetails' own shape, and the stub
Onshape models the two behaviours measured on 2026-11-27: a sub-assembly
inserts at identity with its parts in their own layout, and an ABSOLUTE
occurrence transform collapses every part inside it onto one point.

The rules under test are the refusals (too few holes, a symmetric pattern,
no contact, an ambiguous height) and that a placement is only trusted when
the assembly's own nested occurrences say so.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from src.skills.assemble import GROUP_NAME, AssembleError, assemble
from src.skills.placement import (
    PlacementError,
    fit,
    height,
    module_geometry,
    target_geometry,
)

DOC = "d" * 24
ROBOT = "r" * 24
MODULE = "m" * 24
MODULE_PARTS = "p" * 24
FRAME = "f" * 24
HALF = 330.2                     # a 26 in frame, mm
#: The MK4i's mounting pattern, measured: 0.5 in in from each edge, 4.5, 5.5
#: and 6.5 in along it from the corner, in the module's own coordinates.
PATTERN = [(-12.7, 114.3), (-12.7, 139.7), (-12.7, 165.1),
           (-114.3, 12.7), (-139.7, 12.7), (-165.1, 12.7)]
SHIFT_MM = 10.0                  # the part's own layout is offset; its occurrence undoes it


def _pt(x, y, z):
    return {"x": x / 1000, "y": y / 1000, "z": z / 1000}


def cylinder(x, y, r, z0=36.0, z1=105.0, axis=(0, 0, 1)):
    return {"surface": {"type": "CYLINDER", "origin": _pt(x, y, z0),
                        "axis": {"x": axis[0], "y": axis[1], "z": axis[2]},
                        "radius": r / 1000},
            "box": {"minCorner": _pt(x - r, y - r, z0), "maxCorner": _pt(x + r, y + r, z1)}}


def plane(z, x0, y0, x1, y1):
    return {"surface": {"type": "PLANE", "origin": _pt(x0, y0, z),
                        "normal": {"x": 0, "y": 0, "z": 1}},
            "box": {"minCorner": _pt(x0, y0, z), "maxCorner": _pt(x1, y1, z)}}


def module_body(*, pattern=PATTERN, dz=0.0, radius=2.6, axis=(0, 0, 1)):
    """An L of plates with mounting holes, plus a bearing bore to ignore."""
    s = SHIFT_MM
    faces = [cylinder(x + s, y, radius, axis=axis) for x, y in pattern]
    faces.append(cylinder(-60 + s, 60, 11.0))                       # not a fastener
    faces += [plane(z + dz, -170 + s, 0, 0 + s, 170) for z in (36.5, 44.45, 95.25, 100.0)]
    return {"id": "PLATE", "type": "SOLID", "faces": faces}


def module_definition():
    t = [1, 0, 0, -SHIFT_MM / 1000, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    return {"rootAssembly": {
        "instances": [{"id": "P1", "type": "Part", "documentId": DOC,
                       "elementId": MODULE_PARTS, "partId": "PLATE", "name": "Plate"}],
        "occurrences": [{"path": ["P1"], "transform": t}]},
        "subAssemblies": []}


def _corner_holes():
    """Each corner drilled for the pattern, turned to face outwards."""
    holes = []
    for q, (cx, cy) in enumerate([(HALF, -HALF), (HALF, HALF), (-HALF, HALF), (-HALF, -HALF)]):
        c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][q]
        holes += [(c * x - s * y + cx, s * x + c * y + cy) for x, y in PATTERN]
    return holes


def frame_details(holes=None, flats_z=(44.45, 95.25)):
    tube = {"id": "TUBE", "type": "SOLID",
            "faces": [cylinder(x, y, 2.553, 44.45, 95.25) for x, y in (holes or _corner_holes())]
            + [plane(z, -HALF, -HALF, HALF, HALF) for z in flats_z]}
    pan = {"id": "PAN", "type": "SOLID",
           "faces": [cylinder(x, y, 1.65, 41.0, 44.45) for x, y in ((-14, -14), (14, 14), (86, 82))]
           + [plane(41.0, -HALF, -HALF, HALF, HALF)]}
    return {"bodies": [tube, pan]}


def module_axes(**kwargs):
    return module_geometry(module_definition(), {MODULE_PARTS: {"PLATE": module_body(**kwargs)}})


# -------------------------------------------------------------- pure geometry

class TestFit:
    def test_four_corners_four_quarter_turns(self):
        axes, flats, _ = module_axes()
        holes, target_flats = target_geometry(frame_details())
        placements = fit(axes, holes)
        assert [p.quarter for p in placements] == [0, 1, 2, 3]
        assert {(round(p.tx, 3), round(p.ty, 3)) for p in placements} == {
            (HALF, -HALF), (HALF, HALF), (-HALF, HALF), (-HALF, -HALF)}
        assert all(len(p.holes) == 6 for p in placements)

    def test_the_part_layout_shift_is_undone_by_its_occurrence(self):
        """Geometry is read through the module's own occurrence transforms."""
        axes, _, _ = module_axes()
        assert any(math.isclose(a.x, -12.7, abs_tol=1e-6) and math.isclose(a.y, 114.3, abs_tol=1e-6)
                   for a in axes)

    def test_too_few_holes_is_refused(self):
        axes, _, _ = module_axes()
        holes, _ = target_geometry(frame_details(holes=_corner_holes()[:2]))
        with pytest.raises(PlacementError, match="drilled for this module"):
            fit(axes, holes)

    def test_a_symmetric_pattern_is_refused(self):
        square = [(-50, 50), (-100, 50), (-50, 100), (-100, 100)]
        axes, _, _ = module_axes(pattern=square)
        holes, _ = target_geometry(frame_details(holes=[(x + 200, y) for x, y in square]))
        with pytest.raises(PlacementError, match="symmetric"):
            fit(axes, holes)

    def test_mounting_axes_along_x_are_refused_not_tipped(self):
        axes, _, _ = module_axes(axis=(1, 0, 0))
        holes, _ = target_geometry(frame_details())
        with pytest.raises(PlacementError, match="X or Y"):
            fit([a for a in axes if a.radii != (11.0,)], holes)


class TestHeight:
    def _first(self, **kwargs):
        axes, flats, _ = module_axes(**kwargs)
        return fit(axes, target_geometry(frame_details())[0])[0], flats

    def test_the_tube_between_the_plates_needs_no_offset(self):
        p, flats = self._first()
        assert height(p, flats, target_geometry(frame_details())[1]) == (0.0, (44.45, 95.25))

    def test_a_module_modelled_10_mm_high_comes_down_10_mm(self):
        p, flats = self._first(dz=10.0)
        dz, faces = height(p, flats, target_geometry(frame_details())[1])
        assert dz == pytest.approx(-10.0) and faces == (44.45, 95.25)

    def test_no_face_to_rest_on_is_refused(self):
        p, flats = self._first()
        with pytest.raises(PlacementError, match="cannot be set"):
            height(p, flats, target_geometry(frame_details(flats_z=()))[1])

    def test_an_ambiguous_height_is_refused(self):
        """One module face and two frame faces: it could sit on either."""
        p, _ = self._first()
        lone = [f for f in module_axes()[1] if f.z == 100.0]
        with pytest.raises(PlacementError, match="ambiguous"):
            height(p, lone, target_geometry(frame_details())[1])


# --------------------------------------------------------- assemble(place=)

def _matrix(t):
    return [t[0:4], t[4:8], t[8:12], t[12:16]]


def _flat(m):
    return [v for row in m for v in row]


def _compose(a, b):
    A, B = _matrix(a), _matrix(b)
    return _flat([[sum(A[r][k] * B[k][c] for k in range(4)) for c in range(4)] for r in range(4)])


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _FakeOnshape:
    """An empty robot assembly, the module, and the frame."""

    def __init__(self, *, collapse=False, body=None):
        self.collapse = collapse
        self.body = body or module_body()
        self.module = module_definition()
        self.instances: list[dict] = []
        self.occurrences: dict[tuple, list[float]] = {}
        self.features: list[dict] = []
        self.posts: list[tuple[str, dict]] = []

    def _robot(self):
        return {"rootAssembly": {"instances": self.instances,
                                 "occurrences": [{"path": list(p), "transform": t}
                                                 for p, t in self.occurrences.items()]},
                "subAssemblies": [{"elementId": MODULE, "configuration": "default",
                                   "instances": self.module["rootAssembly"]["instances"]}]}

    async def get(self, path, **_):
        if path.endswith(f"/e/{ROBOT}"):
            return _Response(self._robot())
        if path.endswith(f"/e/{MODULE}"):
            return _Response(self.module)
        if path.endswith(f"/e/{MODULE_PARTS}/bodydetails"):
            return _Response({"bodies": [self.body]})
        if path.endswith(f"/e/{FRAME}/bodydetails"):
            return _Response(frame_details())
        if path.endswith("/features"):
            return _Response({"features": self.features})
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path, *, json_body=None, **_):
        self.posts.append((path, json_body))
        if path.endswith("/instances"):
            iid = f"MOD{len(self.instances) + 1}"
            self.instances.append({"id": iid, "type": "Assembly", "documentId": DOC,
                                   "elementId": json_body["elementId"], "name": f"MK4i <{iid}>"})
            self.occurrences[(iid,)] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            for occ in self.module["rootAssembly"]["occurrences"]:
                self.occurrences[(iid, *occ["path"])] = list(occ["transform"])
            return _Response({})
        if path.endswith("/occurrencetransforms"):
            iid = json_body["occurrences"][0]["path"][0]
            move = json_body["transform"]
            for p in [p for p in self.occurrences if p[0] == iid]:
                if json_body["isRelative"] and not self.collapse:
                    self.occurrences[p] = _compose(move, self.occurrences[p])
                else:   # measured: absolute is applied to every part inside
                    self.occurrences[p] = list(move)
            return _Response({})
        feature = dict(json_body["feature"])
        if "/featureid/" in path:
            self.features[0].update(feature)
        else:
            feature["featureId"] = "G1"
            self.features.append(feature)
        return _Response({"feature": feature, "featureState": {"featureStatus": "OK"}})


def place(client, **kwargs):
    return asyncio.run(assemble(
        client, document_id=DOC, workspace_id="w" * 24, assembly_id=ROBOT, studios=[],
        place=[(MODULE, "MK4i", FRAME, "Drivetrain")], **kwargs))


def _members(client):
    return [q["path"][0] for q in client.features[0]["parameters"][0]["queries"]]


class TestPlaceModules:
    def test_preview_fits_and_writes_nothing(self):
        robot = _FakeOnshape()
        out = place(robot, preview=True)
        rows = out["placed"]["MK4i"]["placements"]
        assert [r["state"] for r in rows] == ["would insert"] * 4
        assert out["placed"]["MK4i"]["height_offset_mm"] == 0.0
        assert robot.posts == []

    def test_four_copies_moved_relatively_and_grouped(self):
        robot = _FakeOnshape()
        out = place(robot)
        assert out["ok"], out
        moves = [b for p, b in robot.posts if p.endswith("/occurrencetransforms")]
        assert len(moves) == 4 and all(m["isRelative"] for m in moves)
        assert sorted(_members(robot)) == ["MOD1", "MOD2", "MOD3", "MOD4"]
        assert all(r["worst_hole_mm"] == 0.0 for r in out["placed"]["MK4i"]["placements"])

    def test_a_rerun_finds_them_and_inserts_none(self):
        robot = _FakeOnshape()
        place(robot)
        robot.posts.clear()
        out = place(robot)
        assert not any(p.endswith("/instances") for p, _ in robot.posts)
        assert [r["state"] for r in out["placed"]["MK4i"]["placements"]] == ["already there"] * 4
        assert len(robot.features) == 1

    def test_a_collapsed_module_is_caught_and_kept_out_of_the_group(self):
        """The measured failure: a check that composed transforms itself
        passed at 0.0000 mm on exactly this."""
        robot = _FakeOnshape(collapse=True)
        out = place(robot)
        assert out["ok"] is False
        rows = out["placed"]["MK4i"]["placements"]
        assert all(r["state"].startswith("NOT IN PLACE") for r in rows)
        assert not robot.features or not set(_members(robot)) & {"MOD1", "MOD2", "MOD3", "MOD4"}

    def test_an_unfit_module_is_refused_before_any_write(self):
        """A pattern the frame was not drilled for. Moving the module would not
        do: a translated pattern is the same pattern, and still fits."""
        robot = _FakeOnshape(body=module_body(pattern=[(-30, 30), (-60, 30), (-30, 80)]))
        with pytest.raises(AssembleError, match="drilled for this module"):
            asyncio.run(assemble(
                robot, document_id=DOC, workspace_id="w" * 24, assembly_id=ROBOT,
                studios=[], place=[(MODULE, "MK4i", FRAME, "Drivetrain")]))
        assert robot.posts == []
