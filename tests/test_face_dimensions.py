"""Offline checks on the face-size feature's generated FeatureScript.

What these cannot check is whether Onshape agrees; that is the probe's job
(`face_dimensions.render_probe`, one evaluate request). These guard the traps
that are already measured, so a later edit cannot reintroduce them.
"""

from __future__ import annotations

import math
import re

import pytest

from src.skills import face_dimensions as fd
from src.skills.face_dimensions import _rotated_outline
from src.skills.validate import validate_source


def _errors(source: str, *, is_module: bool) -> list:
    return [p for p in validate_source(source, is_module=is_module) if p.is_error]


def test_probe_is_a_bare_lambda():
    probe = fd.render_probe()
    assert probe.startswith("function(context is Context, queries)")
    assert _errors(probe, is_module=False) == []


def test_module_compiles_locally():
    assert _errors(fd.render_module(), is_module=True) == []


def test_query_parameter_carries_a_planar_face_filter():
    # Without a Filter the module does not compile and the write path says
    # nothing. With a non-planar one, evPlane fails at regeneration instead of
    # the pick being refused in the UI.
    assert '"Filter" : EntityType.FACE && GeometryType.PLANE' in fd.render_module()


def test_no_variable_is_named_box():
    for source in (fd.render_probe(), fd.render_module()):
        assert not re.search(r"\bvar\s+box\b", source)


def test_lengths_are_reported_in_whole_millimetres():
    module = fd.render_module()
    assert "round(max(bestU, bestV) / millimeter)" in module
    assert "round(min(bestU, bestV) / millimeter)" in module
    # A raw length must never reach toString().
    for arg in re.findall(r"toString\(([^)]*)\)", module):
        assert arg in {"widthMm", "heightMm"}


def test_probe_and_module_share_one_measurement():
    # Emitted from one function so they cannot drift; the probe is only worth
    # its request if it exercised the module's own code.
    marker = "var axes = [facePlane.x];"
    assert marker in fd.render_probe() and marker in fd.render_module()


def test_probe_plate_is_the_size_it_claims():
    corners = _rotated_outline()
    sides = [math.dist(a, b) for a, b in zip(corners, corners[1:] + corners[:1])]
    width, height = fd.PROBE_PLATE_MM
    assert sides == [
        pytest.approx(s) for s in (width, height, width, height)
    ]
    # And it really is rotated, or the probe would not test the edge directions.
    (x0, y0), (x1, y1) = corners[0], corners[1]
    assert math.degrees(math.atan2(y1 - y0, x1 - x0)) == \
        pytest.approx(fd.PROBE_ROTATION_DEG)
