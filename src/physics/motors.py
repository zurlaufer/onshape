"""FRC motor models: torque-speed curves, current draw, and gear reductions.

Deliberately dependency-free — no Onshape, no MCP, no third-party packages.
A student can open this file, read it, and run its tests understanding nothing
else in the repo. Keep it that way.

Every motor carries a `source` URL, and `describe()` prints the constants it
used. If a number here is wrong and a student builds to it, the tool is dead
socially no matter how right it is the rest of the time — so the numbers must
be checkable by hand against the vendor's page.

UNITS: SI throughout, except speeds exposed in RPM because that is how every
FRC datasheet quotes them. Every function signature names its units.

MODEL: the standard linear DC motor approximation, valid at constant voltage:

    torque(w)  = stall_torque * (1 - w / free_speed)
    current(T) = free_current + (T / stall_torque) * (stall_current - free_current)

Real brushless motors deviate from this, mostly near stall. Computed peak power
lands within a few percent of every vendor's published figure (the tests assert
this), which is the accuracy this is good for. It is not a substitute for a
thermal model or a full dyno curve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "Motor",
    "Gearbox",
    "MOTORS",
    "get_motor",
    "reduction_from_stages",
    "clamp_voltage_for_current_limits",
]


@dataclass(frozen=True)
class Motor:
    """A brushless/brushed motor's published free-load and stall characteristics.

    Attributes:
        name: Human-readable name, including commutation mode where it matters.
        free_speed_rpm: No-load speed at nominal voltage.
        stall_torque_nm: Torque at zero speed, nominal voltage.
        stall_current_a: Current draw at stall, nominal voltage.
        free_current_a: No-load current draw.
        nominal_voltage_v: Voltage the above figures are quoted at.
        published_peak_power_w: The vendor's own peak-power figure, kept so the
            linear model can be checked against it rather than trusted.
        source: URL the numbers came from. Required — see the module docstring.
    """

    name: str
    free_speed_rpm: float
    stall_torque_nm: float
    stall_current_a: float
    free_current_a: float
    nominal_voltage_v: float
    published_peak_power_w: float
    source: str

    def __post_init__(self) -> None:
        if self.free_speed_rpm <= 0:
            raise ValueError(f"{self.name}: free_speed_rpm must be positive")
        if self.stall_torque_nm <= 0:
            raise ValueError(f"{self.name}: stall_torque_nm must be positive")
        if self.stall_current_a <= self.free_current_a:
            raise ValueError(
                f"{self.name}: stall_current_a must exceed free_current_a"
            )
        if not self.source:
            raise ValueError(
                f"{self.name}: a source URL is required so the numbers stay checkable"
            )

    @property
    def free_speed_rad_s(self) -> float:
        """No-load speed in rad/s."""
        return self.free_speed_rpm * 2.0 * math.pi / 60.0

    @property
    def kt_nm_per_amp(self) -> float:
        """Torque constant: N.m produced per amp above the free current."""
        return self.stall_torque_nm / (self.stall_current_a - self.free_current_a)

    @property
    def back_emf_kt_nm_per_amp(self) -> float:
        """The same constant, worked out from the free-speed end of the sheet.

        In SI units a DC machine's torque constant equals its back-EMF constant.
        At free speed the back-EMF is the supply voltage minus the drop across
        the winding at free current. The resistance is V / stall current. So:

            Kt = (V - I_free * V / I_stall) / w_free

        That uses only numbers already on the vendor's sheet, so it can be
        checked by hand. When the two estimates disagree, the sheet disagrees
        with itself. Measured 2026-11-23: both Krakens agree to within 2.5 %.
        The NEO does not. REV's 2.6 N.m / 105 A implies 0.0252, but its 473 rpm/V
        implies 0.0198, and ReCalc's independent dyno-sourced constants give
        0.0196. REV's 105 A is probably supply current measured through a
        controller, not winding current, but that has not been measured here.
        """
        resistance = self.nominal_voltage_v / self.stall_current_a
        back_emf = self.nominal_voltage_v - self.free_current_a * resistance
        return back_emf / self.free_speed_rad_s

    def torque_at_rpm(self, rpm: float) -> float:
        """Torque (N.m) available at a given shaft speed, at nominal voltage.

        Clamped to zero above free speed — the linear model would otherwise
        report negative torque, which reads as a braking value it cannot
        actually predict.
        """
        if rpm < 0:
            raise ValueError("rpm must be non-negative")
        return max(0.0, self.stall_torque_nm * (1.0 - rpm / self.free_speed_rpm))

    def current_at_torque(self, torque_nm: float) -> float:
        """Current draw (A) to produce a given torque, at nominal voltage."""
        if torque_nm < 0:
            raise ValueError("torque_nm must be non-negative")
        span = self.stall_current_a - self.free_current_a
        return self.free_current_a + (torque_nm / self.stall_torque_nm) * span

    def torque_at_current(self, amps: float) -> float:
        """Torque (N.m) a current limit allows.

        This is the shape a stator current limit takes in a simulation: the
        controller will not exceed `amps`, so torque is capped even where the
        speed-based curve would allow more. Clamped at zero, because below the
        free current the motor is not producing useful torque at all.

        Uses the SMALLER of the two torque constants (see
        `back_emf_kt_nm_per_amp`). This is where climbers and holds get sized,
        and an optimistic torque-per-amp is the error that drops a robot. With
        the NEO's published sheet, the stall-end constant alone over-states
        current-limited torque by about 27 %. For motors whose sheet agrees with
        itself this is the exact inverse of `current_at_torque`.
        """
        kt = min(self.kt_nm_per_amp, self.back_emf_kt_nm_per_amp)
        return max(0.0, (amps - self.free_current_a) * kt)

    def power_at_rpm(self, rpm: float) -> float:
        """Mechanical output power (W) at a given shaft speed."""
        omega = rpm * 2.0 * math.pi / 60.0
        return self.torque_at_rpm(rpm) * omega

    @property
    def resistance_ohm(self) -> float:
        """Winding resistance, from the stall point: V / I_stall."""
        return self.nominal_voltage_v / self.stall_current_a

    @property
    def peak_power_w(self) -> float:
        """Peak mechanical power (W) under the linear model.

        Maximised at half free speed and half stall torque, so this reduces to
        stall_torque * free_speed_rad_s / 4. Compare against
        `published_peak_power_w` — the tests assert they agree closely.
        """
        return self.stall_torque_nm * self.free_speed_rad_s / 4.0

    def describe(self) -> str:
        """A citation block, so a student can verify every number by hand."""
        return (
            f"{self.name}\n"
            f"  free speed    : {self.free_speed_rpm:,.0f} RPM\n"
            f"  stall torque  : {self.stall_torque_nm:.2f} N.m\n"
            f"  stall current : {self.stall_current_a:.0f} A\n"
            f"  free current  : {self.free_current_a:.1f} A\n"
            f"  at voltage    : {self.nominal_voltage_v:.0f} V\n"
            f"  peak power    : {self.peak_power_w:.0f} W computed, "
            f"{self.published_peak_power_w:.0f} W published\n"
            f"  source        : {self.source}"
        )


# Verified against vendor documentation on 2026-08-02. Do not add a motor
# without a source URL that states these figures — the dataclass enforces it.
#
# Note on the NEO: 3.28 N.m / 181 A circulate widely in FRC materials and are
# NOT what REV publishes. REV's own page gives 2.6 N.m / 105 A. Use the vendor.
MOTORS: dict[str, Motor] = {
    "kraken_x60": Motor(
        name="Kraken X60 (trapezoidal)",
        free_speed_rpm=6000.0,
        stall_torque_nm=7.09,
        stall_current_a=366.0,
        free_current_a=2.0,
        nominal_voltage_v=12.0,
        published_peak_power_w=1108.0,
        source="https://docs.wcproducts.com/welcome/electronics/kraken-x60/kraken-x60-motor/overview-and-features/motor-performance",
    ),
    "kraken_x60_foc": Motor(
        name="Kraken X60 (FOC)",
        free_speed_rpm=5800.0,
        stall_torque_nm=9.37,
        stall_current_a=483.0,
        free_current_a=2.0,
        nominal_voltage_v=12.0,
        published_peak_power_w=1405.0,
        source="https://docs.wcproducts.com/welcome/electronics/kraken-x60/kraken-x60-motor/overview-and-features/motor-performance",
    ),
    "neo": Motor(
        name="REV NEO V1.1",
        free_speed_rpm=5676.0,
        stall_torque_nm=2.6,
        stall_current_a=105.0,
        free_current_a=1.8,
        nominal_voltage_v=12.0,
        published_peak_power_w=406.0,
        source="https://docs.revrobotics.com/brushless/neo/v1.1",
    ),
}


def clamp_voltage_for_current_limits(
    v_applied: float,
    v_back_emf: float,
    r_ohms: float,
    stator_limit_a: float | None,
    supply_limit_a: float | None,
    v_supply: float,
) -> float:
    """The voltage a controller can apply without breaking either current limit.

    Ported from ReCalc (github.com/tervay/recalc, MIT licence),
    `wpilib/patches/wpilibc/wasm/sim_util.h`, `ClampVoltageForCurrentLimits`.
    It was read on 2026-11-26, because ReCalc models the SUPPLY limit and this
    project did not (FINDINGS 2026-11-23).

    Stator limit, linear around back-EMF:  |V - E| / R <= I_stator
    Supply limit, from power balance across the controller (P_in = P_out),
    I_supply = I_stator * V / V_supply, which gives the quadratic
        V^2 - E*V - I_supply_limit * R * V_supply <= 0
    The result is clamped to +-V_supply, because a controller cannot apply more
    than the battery gives it. A limit of None means unlimited.
    """
    stator = math.inf if stator_limit_a is None else stator_limit_a
    supply = math.inf if supply_limit_a is None else supply_limit_a

    v_min = v_back_emf - stator * r_ohms
    v_max = v_back_emf + stator * r_ohms
    if math.isfinite(supply):
        root = math.sqrt(max(0.0, v_back_emf * v_back_emf / 4.0
                              + supply * r_ohms * v_supply))
        v_min = max(v_min, v_back_emf / 2.0 - root)
        v_max = min(v_max, v_back_emf / 2.0 + root)
    return min(max(max(v_min, min(v_applied, v_max)), -v_supply), v_supply)


def get_motor(key: str) -> Motor:
    """Look up a motor by key, with a helpful error listing what is available."""
    try:
        return MOTORS[key]
    except KeyError:
        raise KeyError(
            f"Unknown motor {key!r}. Available: {', '.join(sorted(MOTORS))}"
        ) from None


def reduction_from_stages(*stages: tuple[float, float]) -> float:
    """Total reduction of a compound gear train.

    Each stage is a `(driving_teeth, driven_teeth)` pair. A 12t pinion driving
    a 60t gear is `(12, 60)` and reduces 5:1.

        >>> reduction_from_stages((12, 60), (14, 42))
        15.0

    Returns:
        Reduction ratio N, meaning N:1. Greater than 1 is a reduction.
    """
    if not stages:
        raise ValueError("at least one stage is required")
    total = 1.0
    for driving, driven in stages:
        if driving <= 0 or driven <= 0:
            raise ValueError(f"tooth counts must be positive, got ({driving}, {driven})")
        total *= driven / driving
    return total


@dataclass(frozen=True)
class Gearbox:
    """One or more identical motors behind a single reduction.

    Attributes:
        motor: The motor model.
        reduction: Reduction ratio N, meaning N:1. Must be positive; values
            below 1 are overdrives, which are legal but usually a mistake.
        motor_count: Motors sharing the output shaft.
        efficiency: Mechanical efficiency, 0-1. A common rule of thumb is ~0.95
            per gear stage, so a 2-stage gearbox is roughly 0.90.
    """

    motor: Motor
    reduction: float
    motor_count: int = 1
    efficiency: float = 0.95

    def __post_init__(self) -> None:
        if self.reduction <= 0:
            raise ValueError("reduction must be positive")
        if self.motor_count < 1:
            raise ValueError("motor_count must be at least 1")
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError("efficiency must be in (0, 1]")

    @property
    def free_speed_rpm(self) -> float:
        """Output-shaft no-load speed (RPM). Reduction trades speed for torque."""
        return self.motor.free_speed_rpm / self.reduction

    @property
    def stall_torque_nm(self) -> float:
        """Output-shaft stall torque (N.m), after reduction and losses."""
        return (
            self.motor.stall_torque_nm
            * self.reduction
            * self.motor_count
            * self.efficiency
        )

    @property
    def stall_current_a(self) -> float:
        """Total current draw at stall (A) across all motors.

        Worth checking against your breaker budget: a stalled Kraken pulls far
        more than a 40 A breaker allows.
        """
        return self.motor.stall_current_a * self.motor_count

    def torque_at_output_rpm(self, output_rpm: float) -> float:
        """Output torque (N.m) at a given output-shaft speed."""
        motor_rpm = output_rpm * self.reduction
        return (
            self.motor.torque_at_rpm(motor_rpm)
            * self.reduction
            * self.motor_count
            * self.efficiency
        )

    def describe(self) -> str:
        """A citation block covering both the gearbox and its motor."""
        plural = "s" if self.motor_count > 1 else ""
        return (
            f"{self.motor_count}x {self.motor.name} at {self.reduction:.2f}:1 "
            f"({self.efficiency:.0%} efficient)\n"
            f"  output free speed  : {self.free_speed_rpm:,.0f} RPM\n"
            f"  output stall torque: {self.stall_torque_nm:.2f} N.m\n"
            f"  stall current      : {self.stall_current_a:,.0f} A "
            f"total across {self.motor_count} motor{plural}\n"
            f"\n{self.motor.describe()}"
        )
