"""Face selection for applied features. Offline.

The fixture is the bodydetails of a real 150 x 150 x 6.35 mm plate, trimmed to
the fields used. It was captured 2026-11-25 from the "copilot fix check" Part
Studio. Note the plate's top AND bottom both report a +Z plane normal.
"""

from __future__ import annotations

import pytest

from src.skills.selections import (
    SelectionError,
    declared_query_parameters,
    query_parameter,
    resolve_face,
)


def _face(fid, normal, z_max, area_m2):
    return {"id": fid, "area": area_m2,
            "surface": {"type": "PLANE", "normal": dict(zip("xyz", normal))},
            "box": {"maxCorner": {"x": 0.075, "y": 0.075, "z": z_max}}}


PLATE = {"bodies": [{"id": "JHD", "type": "SOLID", "faces": [
    _face("JHC", (-1, 0, 0), 0.00635, 0.0009525),
    _face("JHS", (0, 1, 0), 0.00635, 0.0009525),
    _face("JHO", (-1, 0, 0), 0.00635, 0.0009525),
    _face("JHW", (0, 1, 0), 0.00635, 0.0009525),
    _face("JHK", (0, 0, 1), 0.00635, 0.0225),   # top
    _face("JHG", (0, 0, 1), 0.0, 0.0225),       # bottom, same +Z normal
]}]}


def test_top_is_the_highest_face_square_to_z():
    assert resolve_face(PLATE, "top").face_id == "JHK"


def test_bottom_is_told_apart_by_height_not_by_normal():
    """bodydetails gives the PLANE normal; both read +Z."""
    assert resolve_face(PLATE, "bottom").face_id == "JHG"


def test_largest_planar_breaks_the_top_bottom_tie_upward():
    assert resolve_face(PLATE, "largest_planar").face_id == "JHK"


def test_a_known_id_passes_through():
    assert resolve_face(PLATE, "JHC").face_id == "JHC"


def test_an_unknown_id_is_refused_not_dropped():
    """Onshape silently drops an unresolvable id (2026-08-11). Refuse it here,
    and list what exists."""
    with pytest.raises(SelectionError, match="JHK"):
        resolve_face(PLATE, "ZZZZ")


def test_surfaces_are_not_candidates():
    sheet = {"bodies": [{**PLATE["bodies"][0], "type": "SHEET"}]}
    with pytest.raises(SelectionError, match="solid"):
        resolve_face(sheet, "top")


def test_the_description_is_in_millimetres():
    chosen = resolve_face(PLATE, "top")
    assert chosen.area_mm2 == pytest.approx(22500.0)
    assert chosen.z_mm == pytest.approx(6.35)


def test_declared_query_parameters_are_found():
    module = ('precondition { annotation { "Name" : "Face" } '
              'definition.face is Query; definition.depth is ValueWithUnits; }')
    assert declared_query_parameters(module) == {"face"}


def test_the_parameter_uses_deterministic_ids_and_a_face_filter():
    param = query_parameter("face", ["JHK"])
    assert param["btType"] == "BTMParameterQueryList-148"
    assert param["queries"][0]["deterministicIds"] == ["JHK"]
    assert param["filter"]["entityType"] == "FACE"
    assert param["parameterId"] == "face"
