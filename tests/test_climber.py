"""Tests for src/physics/climber.py.

Two things are being defended here.

The first is the over-centre behaviour, because it is the whole reason the file
exists. If someone "simplifies" the lever back to m*g*L*sin(phi) these tests go
red, and they should: that formula describes a machine whose torque peaks at the
end of the stroke, and this one peaks in the middle.

The second is that a fold which completes is not the same as a gearbox that
works. `test_completing_a_fold_does_not_imply_it_can_hold` pins the case that
nearly shipped — a reduction that reaches the top on momentum while unable to
hold the load once it gets there.

No network, no credentials.
"""

from __future__ import annotations

import math

import pytest

from src.physics.climber import (
    GRAVITY,
    ClimberGeometry,
    best_reduction,
    simulate_fold,
    static_hold_reduction,
)
from src.physics.motors import MOTORS

START = math.pi / 2
END = math.pi


@pytest.fixture
def robot():
    """The design case: 60 kg, 400 mm arm, CoM at the frame centre."""
    return ClimberGeometry(mass_kg=60.0, arm_m=0.40, com_offset_m=0.35)


class TestGeometry:
    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            ClimberGeometry(mass_kg=0.0, arm_m=0.4, com_offset_m=0.35)
        with pytest.raises(ValueError):
            ClimberGeometry(mass_kg=60.0, arm_m=-0.4, com_offset_m=0.35)
        with pytest.raises(ValueError):
            ClimberGeometry(mass_kg=60.0, arm_m=0.4, com_offset_m=0.0)

    def test_radius_matches_law_of_cosines(self, robot):
        for degrees in (90, 120, 150, 180):
            psi = math.radians(degrees)
            expected = math.sqrt(
                robot.arm_m ** 2 + robot.com_offset_m ** 2
                + 2 * robot.arm_m * robot.com_offset_m * math.cos(psi)
            )
            assert robot.radius(psi) == pytest.approx(expected)

    def test_radius_shrinks_as_it_folds(self, robot):
        """Folding pulls the CoM closer to the bar. That shrink IS the lift."""
        radii = [robot.radius(math.radians(d)) for d in range(90, 181, 10)]
        assert radii == sorted(radii, reverse=True)

    def test_lift_is_the_shrink_in_radius(self, robot):
        assert robot.lift_m == pytest.approx(robot.radius(START) - robot.radius(END))
        assert robot.lift_m == pytest.approx(0.4823, abs=1e-3)

    def test_energy_is_mgh(self, robot):
        assert robot.energy_j == pytest.approx(
            robot.mass_kg * GRAVITY * robot.lift_m)


class TestOverCentre:
    """The behaviour that separates this model from the naive one."""

    def test_torque_peaks_in_the_middle_not_at_the_end(self, robot):
        peak, psi = robot.peak_torque()
        assert 140 < math.degrees(psi) < 165, (
            "peak torque must fall mid-fold; at the end means the naive "
            "mass-at-the-pivot model has crept back in")
        assert peak == pytest.approx(206.0, abs=0.5)

    def test_torque_unloads_at_the_end(self, robot):
        peak, _ = robot.peak_torque()
        assert robot.torque_nm(math.radians(179)) < 0.2 * peak

    def test_naive_model_would_be_higher_and_late(self, robot):
        """m*g*L at full extension — what the first pass predicted."""
        naive = robot.mass_kg * GRAVITY * robot.arm_m
        peak, _ = robot.peak_torque()
        assert naive == pytest.approx(235.4, abs=0.5)
        assert peak < naive

    def test_effective_inertia_collapses_with_the_lever(self, robot):
        assert robot.effective_inertia(math.radians(179)) < \
            0.05 * robot.effective_inertia(math.radians(150))

    def test_equal_arm_and_offset_is_finite_and_does_not_unload(self):
        """L == d is the geometry to avoid, and it must be visible, not a crash.

        Both numerator and denominator vanish at 180 degrees. The true limit is
        sqrt(L*d) = L, so torque peaks at the END and never unloads — the
        opposite of the useful case. The naive formula divides by zero here.
        """
        degenerate = ClimberGeometry(mass_kg=60.0, arm_m=0.35, com_offset_m=0.35)
        assert degenerate.lever(END) == pytest.approx(0.35)
        assert math.isfinite(degenerate.torque_nm(END))
        _, psi = degenerate.peak_torque()
        assert math.degrees(psi) > 175


class TestFold:
    def test_the_shipped_design_folds_in_time(self, robot):
        result = simulate_fold(
            MOTORS["kraken_x60_foc"], robot,
            reduction=115.7, motor_count=2, current_limit_a=80.0,
        )
        assert result.completed
        assert result.seconds < 0.5
        assert result.torque_margin > 1.4
        assert result.total_current_a == pytest.approx(160.0)

    def test_a_reduction_far_too_low_stalls_early(self, robot):
        result = simulate_fold(
            MOTORS["kraken_x60_foc"], robot,
            reduction=10.0, motor_count=1, current_limit_a=80.0,
        )
        assert not result.completed
        assert result.stalled_at_deg == pytest.approx(90.0, abs=1.0)

    def test_completing_a_fold_does_not_imply_it_can_hold(self, robot):
        """The failure mode a swing simulation alone cannot see.

        One Kraken at 80 A and G=150 reaches the top. Asked to hold the load
        once there, it needs G=158. So the mechanism arrives and then sags, and
        nothing in the fold result says so — `completed` is True either way.
        This is why `static_hold_reduction` is a separate question, and why a
        climber wants a ratchet rather than a motor holding station.
        """
        motor = MOTORS["kraken_x60_foc"]
        result = simulate_fold(
            motor, robot, reduction=150.0, motor_count=1, current_limit_a=80.0)
        assert result.completed

        needed = static_hold_reduction(motor, robot, amps=80.0, motor_count=1)
        assert needed > 150.0, "this test exists because completing != holding"
        assert needed == pytest.approx(158.2, abs=0.5)

    def test_more_motors_never_makes_it_slower(self, robot):
        motor = MOTORS["kraken_x60_foc"]
        one = simulate_fold(motor, robot, reduction=115.7,
                            motor_count=1, current_limit_a=80.0)
        two = simulate_fold(motor, robot, reduction=115.7,
                            motor_count=2, current_limit_a=80.0)
        assert two.completed
        assert not one.completed or two.seconds <= one.seconds

    def test_current_limit_binds(self, robot):
        motor = MOTORS["kraken_x60_foc"]
        tight = simulate_fold(motor, robot, reduction=115.7,
                              motor_count=2, current_limit_a=40.0)
        loose = simulate_fold(motor, robot, reduction=115.7,
                              motor_count=2, current_limit_a=120.0)
        assert loose.completed
        assert not tight.completed or tight.seconds > loose.seconds

    def test_rejects_nonsense(self, robot):
        with pytest.raises(ValueError):
            simulate_fold(MOTORS["neo"], robot, reduction=0.0)
        with pytest.raises(ValueError):
            simulate_fold(MOTORS["neo"], robot, reduction=50.0, motor_count=0)

    def test_best_reduction_returns_a_completing_fold(self, robot):
        best = best_reduction(
            MOTORS["kraken_x60_foc"], robot,
            candidates=range(80, 201, 20),   # narrow: the sweep itself is not
            motor_count=2, current_limit_a=80.0,  # what is under test here
        )
        assert best is not None
        assert best.completed
        assert best.seconds < 0.5

    def test_best_reduction_is_none_when_nothing_works(self, robot):
        """A NEO on its own is not going to lift 60 kg in this geometry."""
        assert best_reduction(
            MOTORS["neo"], robot,
            candidates=range(20, 60, 10), motor_count=1, current_limit_a=20.0,
        ) is None


class TestStaticHold:
    def test_more_current_needs_less_reduction(self, robot):
        motor = MOTORS["kraken_x60_foc"]
        low = static_hold_reduction(motor, robot, amps=40.0)
        high = static_hold_reduction(motor, robot, amps=120.0)
        assert low > high

    def test_two_motors_halve_it(self, robot):
        motor = MOTORS["kraken_x60_foc"]
        one = static_hold_reduction(motor, robot, amps=80.0, motor_count=1)
        two = static_hold_reduction(motor, robot, amps=80.0, motor_count=2)
        assert two == pytest.approx(one / 2.0)

    def test_holds_the_peak_not_the_average(self, robot):
        """Sized against the worst angle, which is the only one that matters."""
        motor = MOTORS["kraken_x60_foc"]
        reduction = static_hold_reduction(
            motor, robot, amps=80.0, motor_count=2, efficiency=0.857)
        delivered = motor.torque_at_current(80.0) * 2 * 0.857 * reduction
        peak, _ = robot.peak_torque()
        assert delivered == pytest.approx(peak)

    def test_current_below_free_current_is_refused(self, robot):
        with pytest.raises(ValueError):
            static_hold_reduction(MOTORS["kraken_x60_foc"], robot, amps=1.0)
