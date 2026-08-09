"""Tests for src/physics/geartrain.py.

The centrepiece is `TestChainClearance`. That constraint cost a full design
iteration to find: a stack was chosen, laid out, and only then shown to be
unbuildable because a gear and the next shaft wanted the same space. The wrong
check — gears against bearings — passes every one of the bad cases, so the
tests pin the bad cases explicitly rather than only the good ones.

No network, no credentials.
"""

from __future__ import annotations

import math

import pytest

from src.physics.geartrain import (
    BEARING_OD_MM,
    COTS_TEETH,
    HEX_SHAFT_RADIUS_MM,
    MM_PER_INCH,
    SPACER_RADIUS_MM,
    GearStack,
    Stage,
    centre_distance_mm,
    fold_layout,
    outer_radius_mm,
    pitch_radius_mm,
    search_stacks,
)

GOOD = GearStack((Stage(11, 46), Stage(11, 54), Stage(11, 62)))
BAD = GearStack((Stage(11, 60), Stage(11, 60), Stage(12, 60)))


class TestGearArithmetic:
    def test_pitch_radius_at_20_dp(self):
        """A 60T 20 DP gear is 3 inches across the pitch circle."""
        assert pitch_radius_mm(60) == pytest.approx(1.5 * MM_PER_INCH)

    def test_outer_radius_adds_one_module(self):
        assert outer_radius_mm(60) - pitch_radius_mm(60) == \
            pytest.approx(MM_PER_INCH / 20.0)

    def test_outer_radius_is_what_collides(self):
        assert outer_radius_mm(60) > pitch_radius_mm(60)

    def test_centre_distance_is_the_sum_of_pitch_radii(self):
        assert centre_distance_mm(11, 46) == \
            pytest.approx(pitch_radius_mm(11) + pitch_radius_mm(46))

    def test_hex_circumradius(self):
        """1/2 in across flats, so the corners sit further out than 6.35 mm."""
        assert HEX_SHAFT_RADIUS_MM == pytest.approx(7.33, abs=0.01)
        assert HEX_SHAFT_RADIUS_MM > 0.5 * MM_PER_INCH / 2

    def test_bearing_od_is_the_frc_standard(self):
        assert BEARING_OD_MM == pytest.approx(28.575, abs=0.001)

    def test_stage_rejects_nonsense(self):
        with pytest.raises(ValueError):
            Stage(0, 60)
        with pytest.raises(ValueError):
            Stage(11, -1)


class TestStack:
    def test_ratio_multiplies(self):
        assert GOOD.ratio == pytest.approx((46 / 11) * (54 / 11) * (62 / 11))
        assert GOOD.ratio == pytest.approx(115.7, abs=0.1)

    def test_str_is_readable(self):
        assert str(GOOD) == "11:46 x 11:54 x 11:62"

    def test_centre_distances(self):
        assert GOOD.centre_distances == pytest.approx((36.195, 41.275, 46.355),
                                                      abs=0.01)


class TestChainClearance:
    """Stage i's driven gear versus stage i+1's shaft. The binding constraint."""

    def test_the_shipped_stack_is_buildable(self):
        assert GOOD.is_buildable()
        assert GOOD.clearance_problems() == []

    def test_the_stack_that_had_to_be_rejected(self):
        """11:60 x 11:60 x 12:60 — chosen first, then found impossible."""
        problems = BAD.clearance_problems()
        assert problems, "this stack must not pass; it interferes by 4.8 mm"
        assert "fouls shaft 2" in problems[0]

    def test_the_interference_is_about_five_millimetres(self):
        gear = outer_radius_mm(60)
        needed = gear + SPACER_RADIUS_MM + 1.0
        have = centre_distance_mm(11, 60)
        assert needed - have == pytest.approx(4.81, abs=0.05)

    def test_no_layout_can_rescue_it(self):
        """The clashing distance is a MESH distance, so it is not free."""
        assert centre_distance_mm(11, 60) == pytest.approx(45.085, abs=0.01)
        for stack in (BAD, GearStack((Stage(12, 60),) * 3),
                      GearStack((Stage(14, 50),) * 3)):
            assert not stack.is_buildable()

    def test_driven_gears_must_grow_along_the_chain(self):
        """Shrinking gears fail; growing ones pass. The rule in one assertion."""
        growing = GearStack((Stage(11, 46), Stage(11, 54), Stage(11, 62)))
        shrinking = GearStack((Stage(11, 62), Stage(11, 54), Stage(11, 46)))
        assert growing.is_buildable()
        assert not shrinking.is_buildable()

    def test_last_stage_is_unconstrained(self):
        """Nothing follows it, so its driven gear can be as big as you like."""
        assert GearStack((Stage(11, 46), Stage(11, 72))).is_buildable()

    def test_a_bare_shaft_is_more_forgiving_than_a_spacer(self):
        """Which radius you measure decides whether this stack exists.

        11:46 x 11:52 needs 38.81 mm of centre distance to clear bare hex and
        41.00 mm to clear a 0.75 in spacer. It has 40.00 mm — so it passes on
        the shaft and fails on the hardware actually riding it. The default is
        the spacer for exactly this reason.
        """
        stack = GearStack((Stage(11, 46), Stage(11, 52)))
        assert stack.is_buildable(shaft_radius_mm=HEX_SHAFT_RADIUS_MM)
        assert not stack.is_buildable(shaft_radius_mm=SPACER_RADIUS_MM)
        assert not stack.is_buildable()  # the default must be the strict one

    def test_bores_that_would_overlap_are_caught(self):
        """A separate failure: two bearings with no plate left between them."""
        tight = GearStack((Stage(11, 11),))
        problems = tight.clearance_problems()
        assert any("bores" in p for p in problems)

    def test_a_bearing_only_check_would_pass_the_bad_stack(self):
        """Why the constraint gets missed. Bearings sit in the plate; the
        collision is between the plates, where the shafts are."""
        gear = outer_radius_mm(60)
        have = centre_distance_mm(11, 60)
        assert have > gear + BEARING_OD_MM / 2 - 12  # a lenient bearing test
        assert have < gear + SPACER_RADIUS_MM + 1.0  # the real one


class TestSearch:
    def test_finds_only_buildable_stacks(self):
        found = search_stacks(ratio_min=100.0, ratio_max=120.0, stages=3)
        assert found
        assert all(s.is_buildable() for s in found)

    def test_every_result_is_in_the_window(self):
        found = search_stacks(ratio_min=100.0, ratio_max=110.0, stages=3)
        assert all(100.0 <= s.ratio <= 110.0 for s in found)

    def test_sorted_by_span(self):
        found = search_stacks(ratio_min=100.0, ratio_max=120.0, stages=3)
        spans = [sum(s.centre_distances) for s in found]
        assert spans == sorted(spans)

    def test_uses_only_buyable_tooth_counts(self):
        found = search_stacks(ratio_min=100.0, ratio_max=110.0, stages=3)
        for stack in found[:50]:
            for stage in stack.stages:
                assert stage.driving in COTS_TEETH
                assert stage.driven in COTS_TEETH

    def test_an_impossible_window_returns_nothing(self):
        assert search_stacks(ratio_min=1e6, ratio_max=2e6, stages=2) == []


class TestFoldLayout:
    def test_lays_out_the_shipped_stack(self):
        layout = fold_layout(GOOD)
        assert layout is not None
        assert len(layout.shafts) == 4
        assert layout.longest_side_mm < 90.0

    def test_shafts_sit_at_their_centre_distances(self):
        layout = fold_layout(GOOD)
        for index, distance in enumerate(GOOD.centre_distances):
            a, b = layout.shafts[index], layout.shafts[index + 1]
            assert math.hypot(a[0] - b[0], a[1] - b[1]) == \
                pytest.approx(distance, abs=1e-6)

    def test_every_shaft_is_inside_the_plate(self):
        layout = fold_layout(GOOD)
        margin = BEARING_OD_MM / 2
        for x, y in layout.shafts:
            assert margin <= x <= layout.width_mm - margin
            assert margin <= y <= layout.height_mm - margin

    def test_area_and_longest_are_different_objectives(self):
        """Minimising area picks the long thin sliver. It is a real answer, but
        it is not what 'compact' usually means, so both are offered."""
        compact = fold_layout(GOOD, objective="longest")
        slim = fold_layout(GOOD, objective="area")
        assert slim.area_mm2 <= compact.area_mm2
        assert compact.longest_side_mm <= slim.longest_side_mm

    def test_rejects_an_unknown_objective(self):
        with pytest.raises(ValueError):
            fold_layout(GOOD, objective="smallest")
