"""Size the climber gearbox from the requirement, and show the working.

Costs nothing and touches nothing — pure arithmetic, no Onshape, no credentials.
Run it whenever the robot's mass, the arm length or the motor count changes.

    python scripts/size_climber.py

Reads its design from `src/skills/climber_plate.py`; edit there, not here.

The three sections answer three different questions, and the order matters:

  1. what the mechanism demands, before any gearbox exists
  2. what a gearbox would have to do to HOLD it — the question a swing
     simulation silently skips, and the one that fails quietly in a match
  3. what actually folds it in time, and whether the stack can be built
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.physics.climber import (  # noqa: E402
    ClimberGeometry,
    simulate_fold,
    static_hold_reduction,
)
from src.physics.geartrain import GearStack, fold_layout, search_stacks  # noqa: E402
from src.physics.motors import MOTORS  # noqa: E402
from src.skills.climber_plate import (  # noqa: E402
    BATTERY_RESISTANCE_OHM,
    CURRENT_LIMIT_A,
    GEOMETRY,
    MOTOR_COUNT,
    MOTOR_NAME,
    STACK,
    SUPPLY_LIMIT_A,
    plate_geometry,
)

#: The battery as ReCalc models it; see climber_plate.SUPPLY_LIMIT_A.
BATTERY = {"supply_limit_a": SUPPLY_LIMIT_A,
           "battery_resistance_ohm": BATTERY_RESISTANCE_OHM}

TARGET_S = 0.5

# This is a standalone CLI, not the MCP server, so stdout is safe here.


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    motor = MOTORS[MOTOR_NAME]
    peak_load, peak_psi = GEOMETRY.peak_torque()

    rule("1. WHAT THE MECHANISM DEMANDS")
    print(f"robot {GEOMETRY.mass_kg:g} kg, arm {GEOMETRY.arm_m * 1000:.0f} mm, "
          f"CoM {GEOMETRY.com_offset_m * 1000:.0f} mm behind the pivot")
    print(f"lift            : {GEOMETRY.lift_m * 1000:.0f} mm")
    print(f"energy          : {GEOMETRY.energy_j:.0f} J")
    print(f"peak load torque: {peak_load:.1f} N.m at psi = "
          f"{math.degrees(peak_psi):.0f} deg")
    print()
    print(f"{'psi':>6} {'lever':>9} {'torque':>10} {'inertia':>10}")
    for degrees in (90, 105, 120, 135, 150, 165, 179):
        psi = math.radians(degrees)
        print(f"{degrees:>5}° {GEOMETRY.lever(psi) * 1000:>8.1f}mm "
              f"{GEOMETRY.torque_nm(psi):>9.1f}N.m "
              f"{GEOMETRY.effective_inertia(psi):>9.2f}")
    print("\nTorque peaks in the middle and unloads to nearly nothing. Putting the")
    print("robot's mass at the pivot instead predicts a maximum at the very end,")
    print("which is a different machine and one reduction stage more expensive.")

    if abs(GEOMETRY.arm_m - GEOMETRY.com_offset_m) < 0.02:
        print("\nWARNING: arm length is within 20 mm of the CoM offset. At L == d the")
        print("lever tends to L rather than to zero, so torque peaks at the END and")
        print("never unloads. Move the arm length away from the CoM offset.")

    rule("2. WHAT IT WOULD TAKE TO HOLD IT")
    print("Reduction needed to hold at the worst angle, by current allowed.\n")
    print(f"{'motors':>7} {'40 A':>9} {'60 A':>9} {'80 A':>9} {'120 A':>9}")
    for count in (1, 2):
        cells = [
            f"{static_hold_reduction(motor, GEOMETRY, amps=a, motor_count=count):>8.0f}:"
            for a in (40.0, 60.0, 80.0, 120.0)
        ]
        print(f"{count:>7} {' '.join(cells)}")
    print("\nA ratchet is assumed to carry this. Without one these are the numbers")
    print("the gearbox must meet, and they are far above what the fold needs.")

    rule("3. WHAT FOLDS IT IN TIME")
    print(f"target {math.degrees(math.pi / 2):.0f} deg in {TARGET_S} s, "
          f"{motor.name}, battery modelled ({SUPPLY_LIMIT_A:g} A supply limit "
          f"per motor, {BATTERY_RESISTANCE_OHM * 1000:g} mOhm)\n")
    print(f"{'motors':>7} {'limit':>7} {'best G':>8} {'time':>8} "
          f"{'total A':>9} {'margin':>8}")
    for count in (1, 2):
        for limit in (80.0, 120.0):
            best = None
            for reduction in range(20, 401, 5):
                result = simulate_fold(
                    motor, GEOMETRY, reduction=float(reduction),
                    motor_count=count, current_limit_a=limit, **BATTERY,
                )
                if result.completed and (best is None or result.seconds < best.seconds):
                    best = result
            if best is None:
                print(f"{count:>7} {limit:>6.0f}A {'—':>8}  never folds")
                continue
            flag = "OK" if best.seconds <= TARGET_S else "SLOW"
            print(f"{count:>7} {limit:>6.0f}A {best.reduction:>8.0f} "
                  f"{best.seconds:>7.2f}s {best.total_current_a:>8.0f}A "
                  f"{best.torque_margin:>7.2f}x  {flag}")

    rule("4. THE CHOSEN STACK")
    print(f"{STACK}  =  {STACK.ratio:.1f}:1")
    problems = STACK.clearance_problems()
    if problems:
        print("NOT BUILDABLE:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("buildable: every driven gear clears the next stage's shaft")
    ideal = simulate_fold(
        motor, GEOMETRY, reduction=STACK.ratio,
        motor_count=MOTOR_COUNT, current_limit_a=CURRENT_LIMIT_A,
    )
    result = simulate_fold(
        motor, GEOMETRY, reduction=STACK.ratio,
        motor_count=MOTOR_COUNT, current_limit_a=CURRENT_LIMIT_A, **BATTERY,
    )
    print(f"\n{MOTOR_COUNT} x {motor.name} at {CURRENT_LIMIT_A:g} A stator:")
    print(f"  fixed 12 V      : {ideal.seconds:.3f} s, margin {ideal.torque_margin:.2f}x")
    print(f"  with the battery: {result.seconds:.3f} s, margin "
          f"{result.torque_margin:.2f}x, battery down to "
          f"{result.min_battery_v:.1f} V, {result.peak_supply_a:.0f} A peak supply")
    print(f"  {'OK' if result.completed and result.seconds <= TARGET_S else 'SLOW'} "
          f"against the {TARGET_S} s target")

    plate = plate_geometry()
    print(f"\nplate {plate.width_mm:.3f} x {plate.height_mm:.3f} x "
          f"{plate.thickness_mm:g} mm")
    print(f"volume {plate.volume_mm3:,.4f} mm3, {plate.mass_g:.1f} g in 6061")
    for index, (x, y) in enumerate(plate.layout.shafts):
        print(f"  shaft {index}: ({x:7.3f}, {y:7.3f})")

    rule("5. ALTERNATIVES IN THE SAME WINDOW")
    stacks = search_stacks(ratio_min=100.0, ratio_max=155.0, stages=3)
    print(f"{len(stacks)} buildable 3-stage stacks between 100:1 and 155:1. "
          f"Laying out the 8 tightest.\n")
    print(f"{'stack':<28} {'ratio':>8} {'plate':>18} {'fold':>8}")
    seen: set[tuple[int, ...]] = set()
    shown = 0
    for stack in stacks:
        key = tuple(sorted((s.driving, s.driven) for s in stack.stages))
        if key in seen:
            continue
        seen.add(key)
        layout = fold_layout(stack)
        if layout is None:
            continue
        folded = simulate_fold(
            motor, GEOMETRY, reduction=stack.ratio,
            motor_count=MOTOR_COUNT, current_limit_a=CURRENT_LIMIT_A, **BATTERY,
        )
        timing = f"{folded.seconds:.2f}s" if folded.completed else "stalls"
        print(f"{str(stack):<28} {stack.ratio:>7.1f}: "
              f"{layout.width_mm:>7.1f} x {layout.height_mm:<7.1f} {timing:>8}")
        shown += 1
        if shown >= 8:
            break

    print("\nNothing here is measured against Onshape. Run "
          "scripts/verify_plate_geometry.py for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
