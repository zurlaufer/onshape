"""Tests for src/physics/motors.py.

The point of these is trust (FM6). A wrong constant that a student builds to
kills the tool socially, so the vendor-published figures are pinned here and
the linear model is checked against each vendor's own peak-power number.
"""

from __future__ import annotations

import math

import pytest

from src.physics.motors import (
    MOTORS,
    Gearbox,
    Motor,
    get_motor,
    reduction_from_stages,
)

# --------------------------------------------------------------------------
# Vendor figures. Changing a number here means the vendor changed it — go and
# check the source URL first.
# --------------------------------------------------------------------------


class TestPublishedConstants:
    def test_kraken_x60_trapezoidal(self):
        """https://docs.wcproducts.com/ -> Kraken X60 -> Motor Performance"""
        m = get_motor("kraken_x60")
        assert m.free_speed_rpm == 6000.0
        assert m.stall_torque_nm == 7.09
        assert m.stall_current_a == 366.0
        assert m.free_current_a == 2.0
        assert m.published_peak_power_w == 1108.0

    def test_kraken_x60_foc(self):
        """FOC trades top speed for torque: slower free speed, higher stall."""
        m = get_motor("kraken_x60_foc")
        assert m.free_speed_rpm == 5800.0
        assert m.stall_torque_nm == 9.37
        assert m.stall_current_a == 483.0
        assert m.published_peak_power_w == 1405.0

        trap = get_motor("kraken_x60")
        assert m.free_speed_rpm < trap.free_speed_rpm
        assert m.stall_torque_nm > trap.stall_torque_nm

    def test_neo_uses_rev_published_figures_not_the_folklore_ones(self):
        """REGRESSION GUARD — do not "fix" these numbers.

        3.28 N.m / 181 A circulate widely in FRC materials and are not what REV
        publishes. REV's own page gives 2.6 N.m / 105 A.
        https://docs.revrobotics.com/brushless/neo/v1.1
        """
        m = get_motor("neo")
        assert m.free_speed_rpm == 5676.0
        assert m.stall_torque_nm == 2.6, "REV publishes 2.6 N.m, not 3.28"
        assert m.stall_current_a == 105.0, "REV publishes 105 A, not 181"
        assert m.free_current_a == 1.8

    @pytest.mark.parametrize("key", sorted(MOTORS))
    def test_every_motor_cites_a_source(self, key):
        assert MOTORS[key].source.startswith("http")


# --------------------------------------------------------------------------
# Model validation: does the linear approximation reproduce vendor numbers?
# --------------------------------------------------------------------------


class TestModelAgreesWithVendors:
    @pytest.mark.parametrize("key", sorted(MOTORS))
    def test_computed_peak_power_matches_published(self, key):
        """Linear model vs the vendor's own peak-power figure.

        Agreement to within 6% across all three motors is what justifies using
        this model at all. The NEO is the loosest at ~4.8%; both Krakens land
        inside 1.5%.
        """
        m = MOTORS[key]
        error = abs(m.peak_power_w - m.published_peak_power_w) / m.published_peak_power_w
        assert error < 0.06, (
            f"{m.name}: computed {m.peak_power_w:.0f} W vs published "
            f"{m.published_peak_power_w:.0f} W ({error:.1%} off)"
        )

    @pytest.mark.parametrize("key", sorted(MOTORS))
    def test_curve_endpoints(self, key):
        m = MOTORS[key]
        assert m.torque_at_rpm(0.0) == pytest.approx(m.stall_torque_nm)
        assert m.torque_at_rpm(m.free_speed_rpm) == pytest.approx(0.0, abs=1e-9)
        assert m.current_at_torque(0.0) == pytest.approx(m.free_current_a)
        assert m.current_at_torque(m.stall_torque_nm) == pytest.approx(m.stall_current_a)

    @pytest.mark.parametrize("key", sorted(MOTORS))
    def test_peak_power_occurs_at_half_free_speed(self, key):
        m = MOTORS[key]
        half = m.free_speed_rpm / 2.0
        best = max(
            (m.power_at_rpm(m.free_speed_rpm * f / 200.0) for f in range(1, 200)),
        )
        assert m.power_at_rpm(half) == pytest.approx(best, rel=1e-3)
        assert m.power_at_rpm(half) == pytest.approx(m.peak_power_w, rel=1e-9)

    @pytest.mark.parametrize("key", sorted(MOTORS))
    def test_torque_is_monotonically_decreasing(self, key):
        m = MOTORS[key]
        speeds = [m.free_speed_rpm * i / 20.0 for i in range(21)]
        torques = [m.torque_at_rpm(s) for s in speeds]
        assert all(a >= b for a, b in zip(torques, torques[1:]))

    def test_above_free_speed_clamps_to_zero_not_negative(self):
        """The linear model would go negative, which it cannot actually predict."""
        m = get_motor("neo")
        assert m.torque_at_rpm(m.free_speed_rpm * 1.5) == 0.0

    def test_torque_constant_is_consistent(self):
        m = get_motor("kraken_x60")
        usable_current = m.stall_current_a - m.free_current_a
        assert m.kt_nm_per_amp * usable_current == pytest.approx(m.stall_torque_nm)


# --------------------------------------------------------------------------
# Gear trains
# --------------------------------------------------------------------------


class TestReductionFromStages:
    def test_single_stage(self):
        assert reduction_from_stages((12, 60)) == pytest.approx(5.0)

    def test_compound_stages_multiply(self):
        assert reduction_from_stages((12, 60), (14, 42)) == pytest.approx(15.0)

    def test_overdrive_is_below_one(self):
        assert reduction_from_stages((60, 12)) == pytest.approx(0.2)

    def test_rejects_empty_and_nonpositive(self):
        with pytest.raises(ValueError):
            reduction_from_stages()
        with pytest.raises(ValueError):
            reduction_from_stages((0, 60))


class TestGearbox:
    def test_reduction_trades_speed_for_torque(self):
        g = Gearbox(motor=get_motor("neo"), reduction=10.0, efficiency=1.0)
        assert g.free_speed_rpm == pytest.approx(567.6)
        assert g.stall_torque_nm == pytest.approx(26.0)

    def test_efficiency_reduces_torque_but_not_speed(self):
        lossless = Gearbox(motor=get_motor("neo"), reduction=10.0, efficiency=1.0)
        lossy = Gearbox(motor=get_motor("neo"), reduction=10.0, efficiency=0.9)
        assert lossy.free_speed_rpm == pytest.approx(lossless.free_speed_rpm)
        assert lossy.stall_torque_nm == pytest.approx(
            lossless.stall_torque_nm * 0.9
        )

    def test_motor_count_scales_torque_and_current(self):
        one = Gearbox(motor=get_motor("kraken_x60"), reduction=8.0, motor_count=1)
        four = Gearbox(motor=get_motor("kraken_x60"), reduction=8.0, motor_count=4)
        assert four.stall_torque_nm == pytest.approx(one.stall_torque_nm * 4)
        assert four.stall_current_a == pytest.approx(one.stall_current_a * 4)
        assert four.free_speed_rpm == pytest.approx(one.free_speed_rpm)

    def test_stall_current_dwarfs_a_40a_breaker(self):
        """Sanity anchor: four stalled Krakens pull ~1464 A, not 160 A."""
        g = Gearbox(motor=get_motor("kraken_x60"), reduction=8.0, motor_count=4)
        assert g.stall_current_a == pytest.approx(1464.0)

    def test_output_torque_curve_matches_endpoints(self):
        g = Gearbox(motor=get_motor("neo"), reduction=10.0, efficiency=1.0)
        assert g.torque_at_output_rpm(0.0) == pytest.approx(g.stall_torque_nm)
        assert g.torque_at_output_rpm(g.free_speed_rpm) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"reduction": 0.0},
            {"reduction": -1.0},
            {"motor_count": 0},
            {"efficiency": 0.0},
            {"efficiency": 1.5},
        ],
    )
    def test_rejects_invalid_configuration(self, kwargs):
        params = {"motor": get_motor("neo"), "reduction": 10.0, **kwargs}
        with pytest.raises(ValueError):
            Gearbox(**params)


# --------------------------------------------------------------------------
# Validation and citation
# --------------------------------------------------------------------------


class TestMotorValidation:
    def _valid(self, **overrides):
        params = {
            "name": "Test",
            "free_speed_rpm": 5000.0,
            "stall_torque_nm": 3.0,
            "stall_current_a": 100.0,
            "free_current_a": 2.0,
            "nominal_voltage_v": 12.0,
            "published_peak_power_w": 400.0,
            "source": "https://example.com/spec",
        }
        params.update(overrides)
        return params

    @pytest.mark.parametrize(
        "overrides",
        [
            {"free_speed_rpm": 0.0},
            {"stall_torque_nm": -1.0},
            {"stall_current_a": 1.0},  # below free_current
            {"source": ""},
        ],
    )
    def test_rejects_bad_inputs(self, overrides):
        with pytest.raises(ValueError):
            Motor(**self._valid(**overrides))

    def test_get_motor_lists_options_on_typo(self):
        with pytest.raises(KeyError, match="kraken_x60"):
            get_motor("krakken")


class TestDescribeCitesItsNumbers:
    """FM6: output must let a student check the figures by hand."""

    def test_motor_describe_includes_source_and_values(self):
        text = get_motor("neo").describe()
        assert "docs.revrobotics.com" in text
        assert "5,676 RPM" in text
        assert "2.60 N.m" in text

    def test_gearbox_describe_includes_the_motor_block(self):
        text = Gearbox(motor=get_motor("kraken_x60"), reduction=8.0, motor_count=4).describe()
        assert "4x Kraken X60" in text
        assert "8.00:1" in text
        assert "docs.wcproducts.com" in text
