#!/usr/bin/env python3
"""
safety_watchdog.py — OpenArm Torque / Velocity Safety Watchdog

Monitors all arm motors over CAN in real-time and immediately disables
ALL motors if any joint exceeds a safe torque or velocity threshold.
Run this in a SEPARATE terminal alongside deploy_act_policy.py.

Usage:
    python3 safety_watchdog.py                # Monitor right arm (can0)
    python3 safety_watchdog.py --both         # Monitor both arms
    python3 safety_watchdog.py --torque 0.6   # Use 60% of tMax as threshold
"""

import argparse
import time
import signal
import sys
import openarm_can as oa

# ── Motor config (must match deploy_act_policy.py) ────────────────────────────
MOTOR_TYPES    = [
    oa.MotorType.DM8009, oa.MotorType.DM8009,
    oa.MotorType.DM4340, oa.MotorType.DM4340,
    oa.MotorType.DM4310, oa.MotorType.DM4310, oa.MotorType.DM4310,
]
MOTOR_SEND_IDS = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
MOTOR_RECV_IDS = [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]

RIGHT_CAN = "can0"
LEFT_CAN  = "can1"

# ── Safety thresholds (fraction of motor tMax / vMax) ─────────────────────────
# 0.75 = trip at 75% of rated peak torque.
# Lower = safer, higher = more permissive.
DEFAULT_TORQUE_FRACTION  = 0.75
DEFAULT_VELOCITY_FRACTION = 0.85

# Window for consecutive violations before tripping — avoids false positives
# from single-sample CAN glitches.
VIOLATION_WINDOW = 3   # must exceed threshold for this many consecutive reads

# Poll rate for watchdog
WATCHDOG_HZ = 200   # 200 Hz — faster than control loop


# ── Colour helpers ────────────────────────────────────────────────────────────
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def get_motor_limits():
    """Return {MotorType: (tMax, vMax)} for all motor types used."""
    limits = {}
    for mt in set(MOTOR_TYPES):
        lp = oa.Motor.get_limit_param(mt)
        limits[mt] = (lp.tMax, lp.vMax)
    return limits


def init_arm_readonly(can_port):
    """
    Initialise arm in STATE callback mode so we can read motor data.
    Does NOT send any control commands.
    """
    arm = oa.OpenArm(can_port, True)
    arm.init_arm_motors(MOTOR_TYPES, MOTOR_SEND_IDS, MOTOR_RECV_IDS)
    arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    arm.set_callback_mode_all(oa.CallbackMode.STATE)
    arm.enable_all()
    arm.recv_all(100)
    return arm


def emergency_stop(arms, reason):
    """Immediately disable all motors on all arms."""
    print(f"\n{RED}{BOLD}⚠  EMERGENCY STOP — {reason}{RESET}")
    print(f"{RED}Disabling ALL motors...{RESET}")
    for label, arm in arms:
        try:
            arm.disable_all()
            arm.recv_all()
            print(f"  {label}: disabled ✓")
        except Exception as e:
            print(f"  {label}: disable failed — {e}")
    print(f"{RED}{BOLD}MOTORS DISABLED. Safe to approach the arm.{RESET}\n")


def monitor_loop(arms, torque_frac, velocity_frac, limits):
    """
    Main watchdog loop. Returns when a violation is detected.
    arms: list of (label, arm_object)
    """
    violation_counts = {}   # (arm_label, joint_idx) → consecutive violation count

    print(f"\n{GREEN}{BOLD}Safety Watchdog Active{RESET}")
    print(f"  Torque threshold : {torque_frac*100:.0f}% of rated tMax")
    print(f"  Velocity threshold: {velocity_frac*100:.0f}% of rated vMax")
    print(f"  Trip window      : {VIOLATION_WINDOW} consecutive samples")
    print(f"  Poll rate        : {WATCHDOG_HZ} Hz")
    print(f"\n  Motor tMax values:")
    for mt, (tmax, vmax) in limits.items():
        print(f"    {mt}: tMax={tmax:.1f} Nm  →  trip at {tmax*torque_frac:.1f} Nm")
    print(f"\n  Monitoring... (Ctrl+C to stop)\n")

    dt = 1.0 / WATCHDOG_HZ
    step = 0

    while True:
        for label, arm in arms:
            arm.refresh_all()
            arm.recv_all()

            all_motors = (
                [(f"J{i+1}", m, MOTOR_TYPES[i]) for i, m in enumerate(arm.get_arm().get_motors())]
                + [("Grip", arm.get_gripper().get_motors()[0], oa.MotorType.DM4310)]
            )

            for joint_name, motor, mtype in all_motors:
                t_abs  = abs(motor.get_torque())
                v_abs  = abs(motor.get_velocity())
                t_max, v_max = limits[mtype]

                t_trip = t_max * torque_frac
                v_trip = v_max * velocity_frac

                key = (label, joint_name)
                torque_over   = t_abs > t_trip
                velocity_over = v_abs > v_trip

                if torque_over or velocity_over:
                    violation_counts[key] = violation_counts.get(key, 0) + 1
                    what = []
                    if torque_over:
                        what.append(f"torque {t_abs:.1f}/{t_trip:.1f} Nm")
                    if velocity_over:
                        what.append(f"vel {v_abs:.1f}/{v_trip:.1f} rad/s")
                    
                    count = violation_counts[key]
                    print(f"  {YELLOW}[{label} {joint_name}] WARN ({count}/{VIOLATION_WINDOW}): "
                          f"{', '.join(what)}{RESET}")

                    if count >= VIOLATION_WINDOW:
                        reason = (
                            f"{label} {joint_name} exceeded limit — "
                            + ", ".join(what)
                        )
                        return reason   # triggers emergency stop in caller
                else:
                    violation_counts[key] = 0   # reset on clean reading

        # Status line every 2 seconds
        step += 1
        if step % (WATCHDOG_HZ * 2) == 0:
            # Print max torque fraction across all joints for quick overview
            summaries = []
            for label, arm in arms:
                arm.refresh_all()
                arm.recv_all()
                motors = list(arm.get_arm().get_motors()) + arm.get_gripper().get_motors()
                types  = MOTOR_TYPES + [oa.MotorType.DM4310]
                fracs  = [
                    abs(m.get_torque()) / limits[mt][0]
                    for m, mt in zip(motors, types)
                ]
                max_f = max(fracs)
                j_idx = fracs.index(max_f)
                joint_names = [f"J{i+1}" for i in range(7)] + ["Grip"]
                color = RED if max_f > torque_frac else (YELLOW if max_f > 0.5 else GREEN)
                summaries.append(
                    f"{label}: peak {color}{max_f*100:.0f}%{RESET} @ {joint_names[j_idx]}"
                )
            print(f"  [{step//WATCHDOG_HZ:5d}s] " + "  |  ".join(summaries))

        time.sleep(dt)


def main():
    parser = argparse.ArgumentParser(description="OpenArm safety torque watchdog")
    parser.add_argument("--both",     action="store_true", help="Monitor both arms (can0 + can1)")
    parser.add_argument("--torque",   type=float, default=DEFAULT_TORQUE_FRACTION,
                        help=f"Torque trip fraction of tMax (default {DEFAULT_TORQUE_FRACTION})")
    parser.add_argument("--velocity", type=float, default=DEFAULT_VELOCITY_FRACTION,
                        help=f"Velocity trip fraction of vMax (default {DEFAULT_VELOCITY_FRACTION})")
    args = parser.parse_args()

    limits = get_motor_limits()

    print(f"{BOLD}OpenArm Safety Watchdog{RESET}")
    print(f"Initialising {'both arms' if args.both else 'right arm (can0)'}...")

    try:
        right_arm = init_arm_readonly(RIGHT_CAN)
        arms = [("RIGHT(can0)", right_arm)]

        if args.both:
            left_arm = init_arm_readonly(LEFT_CAN)
            arms.append(("LEFT(can1)", left_arm))

    except Exception as e:
        print(f"{RED}Failed to initialise arm: {e}{RESET}")
        sys.exit(1)

    # Clean Ctrl+C exit
    def _sigint(sig, frame):
        print(f"\n{YELLOW}Watchdog stopped by user. Motors NOT disabled.{RESET}")
        sys.exit(0)
    signal.signal(signal.SIGINT, _sigint)

    # Run until a violation trips the watchdog
    while True:
        reason = monitor_loop(arms, args.torque, args.velocity, limits)
        emergency_stop(arms, reason)

        # After e-stop, wait for user to acknowledge before re-arming
        print(f"{YELLOW}Press ENTER to re-arm the watchdog (motors stay disabled until deploy script re-enables them).{RESET}")
        input()
        print(f"{GREEN}Watchdog re-armed. The deploy script will re-enable motors on next run.{RESET}\n")


if __name__ == "__main__":
    main()
