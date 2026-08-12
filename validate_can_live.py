#!/usr/bin/env python3
"""
Validate the passive CAN StateReader against live hardware.

Read-only: opens a SocketCAN socket and decodes the feedback frames that
ros2_control's polling already elicits. It never transmits, so it cannot
contend with ros2_control for the bus or disturb the running teleop.

Compares decoded motor positions against /joint_states (which ros2_control
derives from those same CAN frames) to confirm the bit layout, scaling and
motor-ID mapping are right, and measures the real feedback rate to check it
supports 30 fps observation pairing.

    ./validate_can_live.py            # right arm on can0
    ./validate_can_live.py can1 left  # left arm
"""

import sys
import time

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

from openarm_direct_io import DM4310, StateReader

CHANNEL = sys.argv[1] if len(sys.argv) > 1 else "can0"
SIDE = sys.argv[2] if len(sys.argv) > 2 else "right"

# Observed live on can0: 0x011-0x018 at ~200 Hz each = 7 joints + gripper.
RECV_IDS = [0x011, 0x012, 0x013, 0x014, 0x015, 0x016, 0x017, 0x018]
JOINTS = [f"openarm_{SIDE}_joint{i}" for i in range(1, 8)] + [f"openarm_{SIDE}_finger_joint1"]


class JointStateProbe(Node):
    def __init__(self):
        super().__init__("can_validation_probe")
        self.latest = None
        self.stamp = 0.0
        self.count = 0
        self.create_subscription(JointState, "/joint_states", self._cb, 10)

    def _cb(self, msg: JointState):
        idx = {n: i for i, n in enumerate(msg.name)}
        if not all(j in idx for j in JOINTS):
            return
        self.latest = np.array([msg.position[idx[j]] for j in JOINTS], dtype=float)
        self.stamp = time.monotonic()
        self.count += 1


def main():
    print(f"channel={CHANNEL}  side={SIDE}")
    print(f"recv ids: {[hex(i) for i in RECV_IDS]}\n")

    reader = StateReader(CHANNEL, RECV_IDS, DM4310, fd=True).start()
    rclpy.init()
    probe = JointStateProbe()

    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < 5.0:
        rclpy.spin_once(probe, timeout_sec=0.02)
        can_vec, can_ts = reader.latest()
        if can_vec is not None and probe.latest is not None:
            samples.append((can_vec.copy(), probe.latest.copy(), can_ts, probe.stamp))
        time.sleep(0.01)

    dt = time.monotonic() - t0
    print("=== throughput ===")
    print(f"  CAN frames seen    : {reader.frames_seen}  ({reader.frames_seen/dt:.0f}/s)")
    print(f"  CAN frames decoded : {reader.frames_decoded}  ({reader.frames_decoded/dt:.0f}/s)")
    per_motor = reader.frames_decoded / dt / len(RECV_IDS)
    print(f"  per-motor rate     : {per_motor:.0f} Hz  "
          f"{'OK for 30fps' if per_motor >= 30 else 'TOO SLOW for 30fps'}")
    print(f"  /joint_states msgs : {probe.count}  ({probe.count/dt:.0f}/s)")

    if not samples:
        print("\nNO PAIRED SAMPLES — check that ros2_control is running and the "
              "recv ids / side match your hardware.")
        reader.stop(); probe.destroy_node(); rclpy.shutdown()
        return 1

    can_arr = np.stack([s[0] for s in samples])
    js_arr = np.stack([s[1] for s in samples])
    print(f"\n=== decoded CAN vs /joint_states  ({len(samples)} paired samples) ===")
    print(f"{'joint':<28}{'CAN mean':>11}{'JS mean':>11}{'mean diff':>11}{'max diff':>10}")
    for i, name in enumerate(JOINTS):
        d = can_arr[:, i] - js_arr[:, i]
        print(f"{name:<28}{can_arr[:,i].mean():11.4f}{js_arr[:,i].mean():11.4f}"
              f"{d.mean():11.4f}{np.abs(d).max():10.4f}")

    diff = np.abs(can_arr - js_arr)
    print(f"\n  overall max |diff| : {diff.max():.4f} rad")
    print(f"  overall mean |diff|: {diff.mean():.4f} rad")
    # A near-constant per-joint offset means a calibration/zero offset rather
    # than a decode error; scatter around zero means they genuinely agree.
    offs = (can_arr - js_arr).mean(axis=0)
    resid = np.abs((can_arr - js_arr) - offs).max()
    print(f"  residual after removing a constant per-joint offset: {resid:.4f} rad")
    if diff.max() < 0.02:
        print("\n  VERDICT: decode matches /joint_states directly.")
    elif resid < 0.02:
        print(f"\n  VERDICT: decode is correct up to a fixed offset per joint:")
        print(f"           {np.round(offs, 4)}")
        print("           (motor zero vs joint zero — a calibration offset, not a decode bug)")
    else:
        print("\n  VERDICT: MISMATCH beyond a constant offset — check recv-id order, "
              "motor types, or gear ratios.")

    reader.stop()
    probe.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
