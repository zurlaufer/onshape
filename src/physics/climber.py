"""Rotating-arm climber: what torque the gearbox actually has to produce.

A climber whose arm pivots at the frame edge, opens to hook a bar, then folds
to lift the robot. Dependency-free like `motors.py` — pure geometry and the
linear motor model, nothing from Onshape or MCP.

WHY THIS FILE EXISTS. The obvious model is to put the robot's mass at the pivot
and call the load `m*g*L*sin(phi)`. That model is not merely imprecise, it
describes a different machine: it says torque rises to a maximum at the end of
the stroke, when the real mechanism's torque *peaks in the middle and then
unloads*. Sizing a gearbox off the wrong one costs a reduction stage.

THE GEOMETRY

    hook   fixed in space, on the bar
    L      arm, hook -> pivot; the pivot is at the robot's frame edge
    d      pivot -> robot centre of mass (about half the frame length)
    psi    fold angle. 90 deg starts the fold; 180 deg is fully folded, the arm
           doubled back along the pivot->CoM line with the hook almost over the
           CoM. Note this is the SUPPLEMENT of the interior angle of the
           hook-pivot-CoM triangle — which is why the law of cosines below
           carries a plus sign where the textbook form has a minus. Read it the
           other way and a hand-check comes out at L+d instead of |L-d|.

Once the robot leaves the ground the arm and robot are one body hanging from
the hook, so it settles with its CoM directly below the bar. Hook to CoM is
then, by the law of cosines,

    r(psi) = sqrt(L^2 + d^2 + 2*L*d*cos(psi))

The gearbox drives `psi` and nothing else. Folding from 90 to 180 degrees
shrinks r, and the robot rises by exactly that shrink. Torque follows from
energy, tau = dV/dpsi with V = -m*g*r:

    tau(psi) = m*g * L*d*sin(psi) / r(psi)

Call `L*d*sin(psi)/r(psi)` the lever. It is an over-centre lever: it rises,
peaks around 150 degrees for typical proportions, and collapses to zero at 180.
Effective inertia is `m * lever^2` and collapses with it, so the last few
degrees are nearly free.

THE DEGENERATE CASE. When L == d the lever tends to sqrt(L*d) = L at psi=180
rather than to zero: numerator and denominator vanish together. Torque then
peaks at the *end* and never unloads, which is the one geometry to avoid. The
naive formula divides by zero there and hides it, so `lever` is written
half-angle to keep the limit finite and visible.

WHAT IS NOT MODELLED. The robot's rotational inertia about its own CoM, arm
mass, and friction. All three add to the torque required, so results here are
optimistic and want a margin. Holding the robot statically is assumed to be a
ratchet's job, not the gearbox's; size for the fold, then check the hold
separately with `static_hold_reduction`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.physics.motors import Motor

__all__ = [
    "GRAVITY",
    "ClimberGeometry",
    "FoldResult",
    "simulate_fold",
    "best_reduction",
    "static_hold_reduction",
]

#: m/s^2. Standard gravity; the one constant here nobody needs to check.
GRAVITY = 9.81

_START = math.pi / 2.0
_END = math.pi


@dataclass(frozen=True)
class ClimberGeometry:
    """A rotating-arm climber, in metres and kilograms.

    Attributes:
        mass_kg: Robot mass including bumpers and battery.
        arm_m: Hook to pivot. The pivot sits at the frame edge.
        com_offset_m: Pivot to the robot's centre of mass.
    """

    mass_kg: float
    arm_m: float
    com_offset_m: float

    def __post_init__(self) -> None:
        if self.mass_kg <= 0:
            raise ValueError("mass_kg must be positive")
        if self.arm_m <= 0:
            raise ValueError("arm_m must be positive")
        if self.com_offset_m <= 0:
            raise ValueError("com_offset_m must be positive")

    # ---------------------------------------------------------------- shape

    def radius(self, psi: float) -> float:
        """Hook to centre of mass (m) at fold angle `psi` (radians)."""
        return math.sqrt(
            self.arm_m ** 2
            + self.com_offset_m ** 2
            + 2.0 * self.arm_m * self.com_offset_m * math.cos(psi)
        )

    def lever(self, psi: float) -> float:
        """Effective moment arm (m) the gearbox works against.

        Half-angle form, so the L == d geometry stays finite at psi=180 instead
        of dividing by zero. See the module docstring — that case is the one
        worth being able to see.
        """
        half_sin = math.sin(psi / 2.0)
        half_cos = math.cos(psi / 2.0)
        denominator = math.sqrt(
            (self.arm_m - self.com_offset_m) ** 2
            + 4.0 * self.arm_m * self.com_offset_m * half_cos * half_cos
        )
        if denominator < 1e-12:
            return math.sqrt(self.arm_m * self.com_offset_m)
        return self.arm_m * self.com_offset_m * 2.0 * half_sin * half_cos / denominator

    def torque_nm(self, psi: float) -> float:
        """Load torque (N.m) the gearbox must supply at fold angle `psi`."""
        return self.mass_kg * GRAVITY * self.lever(psi)

    def effective_inertia(self, psi: float) -> float:
        """Inertia (kg.m^2) seen at the gearbox output, in the psi coordinate."""
        return self.mass_kg * self.lever(psi) ** 2

    # --------------------------------------------------------------- totals

    @property
    def lift_m(self) -> float:
        """How far the robot rises over a full 90 to 180 degree fold."""
        return self.radius(_START) - self.radius(_END)

    @property
    def energy_j(self) -> float:
        """Potential energy the motors must supply over the fold."""
        return self.mass_kg * GRAVITY * self.lift_m

    def peak_torque(self, samples: int = 2000) -> tuple[float, float]:
        """Largest load torque over the fold, and the angle it occurs at.

        Returns (torque_nm, psi_rad). Sampled rather than solved: the maximum
        has no tidy closed form and a few thousand samples is exact enough for
        a figure that then gets a safety factor anyway.
        """
        best_torque, best_psi = 0.0, _START
        for i in range(samples + 1):
            psi = _START + (_END - _START) * i / samples
            torque = self.torque_nm(psi)
            if torque > best_torque:
                best_torque, best_psi = torque, psi
        return best_torque, best_psi


@dataclass(frozen=True)
class FoldResult:
    """Outcome of one simulated fold.

    `torque_margin` is peak gearbox output torque over peak load torque. Read
    it alongside `seconds`: a fast fold with a margin near 1.0 is a mechanism
    that only just gets there, and the unmodelled terms all push it the wrong
    way.
    """

    completed: bool
    seconds: float
    reduction: float
    peak_output_torque_nm: float
    total_current_a: float
    torque_margin: float
    stalled_at_deg: float | None = None


def simulate_fold(
    motor: Motor,
    geometry: ClimberGeometry,
    *,
    reduction: float,
    motor_count: int = 1,
    current_limit_a: float | None = None,
    efficiency: float = 0.857,
    timestep_s: float = 2e-5,
    timeout_s: float = 5.0,
) -> FoldResult:
    """Integrate the fold from 90 to 180 degrees under the motor's own curve.

    Lagrange in the psi coordinate, with the inertia term the naive model does
    not have:

        tau = M(psi)*psi'' + 0.5*M'(psi)*psi'^2 + m*g*lever(psi)

    `efficiency` defaults to 0.95^3, three spur stages. A motor is taken to be
    torque-limited by whichever binds first, the speed curve or the stator
    current limit — that second one is what makes the difference between a
    plausible design and a thermal event.

    A fold that stalls returns `completed=False` with the angle it died at,
    which is more useful than a bare failure: stalling at 90 degrees means the
    reduction is far too low, stalling at 150 means it is close.
    """
    if reduction <= 0:
        raise ValueError("reduction must be positive")
    if motor_count < 1:
        raise ValueError("motor_count must be at least 1")

    peak_load, _ = geometry.peak_torque()
    epsilon = 1e-6
    psi, omega, elapsed = _START, 0.0, 0.0
    peak_output = 0.0

    while psi < _END - 1e-3 and elapsed < timeout_s:
        rpm = omega * reduction * 60.0 / (2.0 * math.pi)
        available = motor.torque_at_rpm(rpm)
        if current_limit_a is not None:
            available = min(available, motor.torque_at_current(current_limit_a))

        output = motor_count * efficiency * reduction * available
        peak_output = max(peak_output, output)

        inertia = geometry.effective_inertia(psi)
        if inertia < 1e-9:
            break  # over-centre: the lever has collapsed, the rest is free
        slope = (
            geometry.effective_inertia(psi + epsilon)
            - geometry.effective_inertia(psi - epsilon)
        ) / (2.0 * epsilon)

        alpha = (
            output - 0.5 * slope * omega * omega - geometry.torque_nm(psi)
        ) / inertia
        omega += alpha * timestep_s
        if omega <= 0.0:
            return FoldResult(
                completed=False,
                seconds=elapsed,
                reduction=reduction,
                peak_output_torque_nm=peak_output,
                total_current_a=(current_limit_a or 0.0) * motor_count,
                torque_margin=peak_output / peak_load if peak_load else 0.0,
                stalled_at_deg=math.degrees(psi),
            )
        psi += omega * timestep_s
        elapsed += timestep_s

    completed = psi >= _END - 1e-3 or geometry.effective_inertia(psi) < 1e-9
    return FoldResult(
        completed=completed,
        seconds=elapsed,
        reduction=reduction,
        peak_output_torque_nm=peak_output,
        total_current_a=(current_limit_a or 0.0) * motor_count,
        torque_margin=peak_output / peak_load if peak_load else 0.0,
        stalled_at_deg=None if completed else math.degrees(psi),
    )


def best_reduction(
    motor: Motor,
    geometry: ClimberGeometry,
    *,
    candidates: range | list[float] | None = None,
    **kwargs,
) -> FoldResult | None:
    """The reduction that folds fastest, or None if none of them completes."""
    if candidates is None:
        candidates = range(20, 401, 5)
    best: FoldResult | None = None
    for reduction in candidates:
        result = simulate_fold(motor, geometry, reduction=float(reduction), **kwargs)
        if result.completed and (best is None or result.seconds < best.seconds):
            best = result
    return best


def static_hold_reduction(
    motor: Motor,
    geometry: ClimberGeometry,
    *,
    amps: float,
    motor_count: int = 1,
    efficiency: float = 0.857,
) -> float:
    """Reduction needed merely to HOLD the robot at the worst fold angle.

    Worth computing even when a ratchet carries the static load, because it is
    the number a swing simulation cannot tell you. A fold can complete on
    momentum at a reduction that could never hold — the design reaches the top
    and then falls.
    """
    torque = motor.torque_at_current(amps) * motor_count * efficiency
    if torque <= 0:
        raise ValueError(f"{amps} A produces no useful torque on {motor.name}")
    peak_load, _ = geometry.peak_torque()
    return peak_load / torque
