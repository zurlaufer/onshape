"""Tests for `plate_pocket_pattern`, the face-selection template.

Offline. The numbers pinned here are not our own output played back — they were
measured against Onshape's kernel by `scripts/verify_pocket_geometry.py`, which
builds a plate, cuts these pockets and reports the volume actually removed.
That script agreed with the arithmetic below to 0.0000%.

That matters more for this template than the others. `tube_hole_pattern` and
`gearbox_bore_pattern` report dimensions a student can check with a ruler; this
one reports **grams**, and a wrong figure would send a team to inspection over
weight with no way to see the error coming.
"""

from __future__ import annotations

import pytest

from src.skills.apply import decode_fsvalue
from src.skills.templates import TEMPLATES
from src.skills.templates.plate_pocket_pattern import ALUMINIUM_6061_DENSITY
from src.skills.validate import validate_source

#: The layout verified live: 150 x 150 x 6.35 mm plate, 3 x 3 pockets.
VERIFIED = {
    "plate_width": 150.0,
    "plate_length": 150.0,
    "plate_thickness": 6.35,
    "columns": 3,
    "rows": 3,
    "rib_width": 8.0,
    "border_width": 12.0,
}

#: What Onshape's kernel measured removing, in mm^3. Do not "correct" this to
#: whatever the Python happens to produce — re-run the verification script.
MEASURED_REMOVED_MM3 = 76_835.0


@pytest.fixture
def template():
    return TEMPLATES["plate_pocket_pattern"]


class TestGeometry:
    """Pocket size is derived, not asked for.

    The student picks the layout and the arithmetic determines how big each
    pocket can be — the same shape of constraint as the gear ratio fixing centre
    distance in `gearbox_bore_pattern`. That is what makes an impossible layout
    catchable for free, before any request is spent.
    """

    def test_pocket_size_by_hand(self, template):
        """150 - 2x12 border - 2x8 rib = 110 mm, over 3 pockets = 36.667."""
        width, length = template.pocket_size(VERIFIED)
        assert width == pytest.approx(36.6667, abs=1e-3)
        assert length == pytest.approx(36.6667, abs=1e-3)

    def test_mass_matches_the_kernel(self, template):
        """The regression that protects the only number we cannot eyeball."""
        expected_g = MEASURED_REMOVED_MM3 * 1e-9 * ALUMINIUM_6061_DENSITY * 1000.0
        assert template.mass_removed_g(VERIFIED) == pytest.approx(
            expected_g, rel=5e-4)
        assert template.mass_removed_g(VERIFIED) == pytest.approx(207.5, abs=1.0)

    def test_a_shallower_pocket_removes_proportionally_less(self, template):
        through = template.mass_removed_g(VERIFIED)
        shallow = template.mass_removed_g(dict(VERIFIED, pocket_depth=3.0))
        assert shallow == pytest.approx(through * 3.0 / 6.35, rel=1e-9)

    def test_an_impossible_layout_reports_zero_not_negative(self, template):
        """A negative mass would read as adding material to the robot."""
        assert template.mass_removed_g(dict(VERIFIED, columns=20)) == 0.0


class TestValidation:
    def errors(self, template, **overrides):
        return [p for p in template.validate(dict(VERIFIED, **overrides))
                if p.is_error]

    def test_the_verified_layout_is_clean(self, template):
        assert self.errors(template) == []

    def test_too_many_columns_is_refused_showing_the_arithmetic(self, template):
        found = self.errors(template, columns=20)
        assert found, "20 pockets cannot fit across 150 mm"
        assert "border" in found[0].message and "rib" in found[0].message

    def test_a_pocket_narrower_than_the_cutter_is_refused(self, template):
        """A 1/4 inch end mill cannot enter a 6 mm slot, however neat the maths.
        This is the check that knows about the shop, not just the geometry.

        13 columns: 150 - 24 border - 12x4 rib = 78 mm, so 6.0 mm each — under
        the 6.35 mm the cutter needs. The layout is arithmetically fine and
        physically impossible, which is exactly the gap this check covers.
        """
        found = self.errors(template, columns=13, rib_width=4.0)
        assert any("cutter" in e.message for e in found)

    def test_a_pocket_the_cutter_just_fits_is_allowed(self, template):
        """The boundary matters: 10.4 mm is wider than a 6.35 mm cutter, so this
        must pass. A check that refuses workable layouts is worse than none."""
        assert self.errors(template, columns=9, rib_width=4.0) == []

    def test_depth_beyond_thickness_is_refused(self, template):
        found = self.errors(template, pocket_depth=10.0)
        assert any("exceeds plate_thickness" in e.message for e in found)

    def test_a_paper_thin_floor_warns(self, template):
        problems = template.validate(dict(VERIFIED, pocket_depth=6.0))
        assert any("floor" in p.message for p in problems)

    def test_a_through_cut_is_not_mistaken_for_a_thin_floor(self, template):
        problems = template.validate(dict(VERIFIED, pocket_depth=6.35))
        assert not any("floor" in p.message for p in problems)

    def test_thin_ribs_warn_without_blocking(self, template):
        problems = template.validate(dict(VERIFIED, rib_width=1.0))
        assert not any(p.is_error for p in problems), "a rule of thumb, not a law"
        assert any("buckle" in p.message for p in problems)

    def test_heavy_pocketing_warns_about_stiffness(self, template):
        problems = template.validate(
            dict(VERIFIED, rib_width=4.0, border_width=5.0))
        assert any("stiffness" in p.message for p in problems)

    def test_the_mass_saved_is_always_reported(self, template):
        """It is what the team is chasing, so it is never conditional."""
        messages = [p.message for p in template.validate(VERIFIED)]
        assert any("g of 6061 aluminium" in m for m in messages)

    def test_the_square_corner_approximation_is_disclosed(self, template):
        """We model more removal than a router achieves, so the saving we quote
        is optimistic. Naming the direction of the error is the point."""
        messages = [p.message for p in template.validate(VERIFIED)]
        assert any("square corners" in m and "slightly under" in m
                   for m in messages)


class TestRendering:
    def test_the_query_parameter_carries_a_filter(self, template):
        """Measured 2026-08-07: without a Filter the module does not compile and
        the write path gives no reason. This test is the tripwire."""
        module = template.render_module(VERIFIED)
        assert "definition.face is Query" in module
        assert '"Filter" : EntityType.FACE && GeometryType.PLANE' in module

    def test_the_probe_is_a_bare_lambda(self, template):
        probe = template.render_probe(VERIFIED)
        for forbidden in ("import", "annotation", "export"):
            assert forbidden not in probe, "the evaluate endpoint rejects it"

    def test_both_forms_require_a_solid(self, template):
        """Found live 2026-08-09: against sheet bodies the whole
        sketch/extrude/boolean chain completes, removes nothing, and reports
        success. A false pass is worse than a failure — it looks like proof."""
        for source in (template.render_probe(VERIFIED),
                       template.render_module(VERIFIED)):
            assert "BodyType.SOLID" in source

    def test_the_grid_is_solved_in_featurescript_not_baked_in(self, template):
        """So the feature fits the face it is applied to, not the one described
        when it was generated."""
        module = template.render_module(VERIFIED)
        assert "faceBox.maxCorner[0] - faceBox.minCorner[0]" in module
        assert "36.6" not in module, "the pocket size must not be a literal"

    def test_the_probe_cross_checks_the_stated_plate_size(self, template):
        probe = template.render_probe(VERIFIED)
        assert "150" in probe
        assert "regenError" in probe

    def test_generated_source_passes_our_own_validation(self, template):
        assert not any(p.is_error for p in validate_source(
            template.render_probe(VERIFIED), is_module=False))
        assert not any(p.is_error for p in validate_source(
            template.render_module(VERIFIED), is_module=True))

    def test_no_false_unknown_symbol_warnings(self, template):
        """`planeToCSys`, `qOwnerBody` and `qSketchRegion` are valid and were
        flagged as unknown until they compiled. A warning that is usually wrong
        teaches the reader to skip the one time it is right."""
        warnings = [p.message for p in validate_source(
            template.render_module(VERIFIED), is_module=True)]
        for name in ("planeToCSys", "qOwnerBody", "qSketchRegion"):
            assert not any(name in m for m in warnings), f"{name} is real"


class TestFsValueDecoding:
    """Onshape wraps a two-entry map in ~400 bytes of btType scaffolding."""

    def test_a_map_becomes_a_dict(self):
        raw = [
            {"btType": "BTFSValueMapEntry-2077",
             "key": {"btType": "...ValueString", "value": "pocketU_mm"},
             "value": {"btType": "...ValueNumber", "value": 36.667}},
            {"btType": "BTFSValueMapEntry-2077",
             "key": {"btType": "...ValueString", "value": "removed_mm3"},
             "value": {"btType": "...ValueNumber", "value": MEASURED_REMOVED_MM3}},
        ]
        assert decode_fsvalue(raw) == {
            "pocketU_mm": 36.667, "removed_mm3": MEASURED_REMOVED_MM3}

    def test_an_array_stays_a_list(self):
        raw = {"btType": "...ValueArray", "value": [
            {"btType": "...ValueNumber", "value": 1.0},
            {"btType": "...ValueNumber", "value": 2.0}]}
        assert decode_fsvalue(raw) == [1.0, 2.0]

    def test_a_scalar_unwraps(self):
        assert decode_fsvalue({"btType": "...ValueNumber", "value": 3.0}) == 3.0

    def test_empty_and_missing_survive(self):
        assert decode_fsvalue(None) is None
        assert decode_fsvalue([]) == []
