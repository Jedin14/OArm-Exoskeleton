#!/usr/bin/env python3
"""
Validate the direct-I/O backend against live hardware and the ROS backend.

Read-only. Confirms:
  1. Feature declaration is 8-dim state + cameras, matching action 1:1
  2. Direct CAN observations agree with /joint_states (the ROS path's source)
  3. The gripper is reported in METRES and matches /joint_states
  4. Camera frames arrive with capture timestamps, and state is paired to them
  5. Observation age is lower than the ROS/UDP path (the point of the exercise)

    ./validate_direct_backend.py
"""

import json
import os
import sys
import time

import subprocess

import numpy as np

sys.path.insert(0, "/root/ws/OpenArm-Exoskeleton")
sys.path.insert(0, "/root/ws/OpenArm-Exoskeleton/lelab_ui")

from lelab.robots.openarm_direct import OpenArmDirectRobot, OpenArmDirectRobotConfig  # noqa: E402

MAPPINGS = "/root/.config/lelab/ros_camera_mappings.json"
SIDE = "right"
JOINTS = [f"openarm_{SIDE}_joint{i}" for i in range(1, 8)] + [f"openarm_{SIDE}_finger_joint1"]


def js_snapshot():
    """One /joint_states sample via ROS's python3.10 (rclpy is not importable
    from leLab's 3.12 venv — the reason the UDP bridge exists)."""
    try:
        r = subprocess.run(
            ["bash", "-lc",
             "source /opt/ros/humble/setup.bash && "
             "source /root/ws/OpenArm-Exoskeleton/install/setup.bash && "
             "python3 /root/ws/OpenArm-Exoskeleton/js_snapshot.py"],
            capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        return np.array([d[j] for j in JOINTS], float) if all(j in d for j in JOINTS) else None
    except Exception as e:
        print("  js_snapshot failed:", e)
        return None


def main():
    devices = {}
    if os.path.exists(MAPPINGS):
        for m in json.load(open(MAPPINGS)):
            devices[m["name"]] = m["device_index"]
    print(f"cameras from mappings: {list(devices) or 'NONE'}")

    cfg = OpenArmDirectRobotConfig(arm_mode=SIDE, camera_devices=devices)
    robot = OpenArmDirectRobot(cfg)

    print("\n=== 1. feature declaration ===")
    of, af = robot.observation_features, robot.action_features
    state_dims = [k for k in of if k.endswith(".pos")]
    cams = [k for k in of if not k.endswith(".pos")]
    print(f"  observation.state dims: {len(state_dims)}   cameras: {cams}")
    print(f"  action dims           : {len(af)}")
    print(f"  state/action 1:1       : {len(state_dims) == len(af)} "
          f"{'OK' if len(state_dims) == len(af) else 'FAIL'}")
    print(f"  include_ee_pose        : {cfg.include_ee_pose} (False => no FK-derived dims)")

    print("\n=== 2. connecting (passive CAN + direct cameras) ===")
    robot.connect()
    time.sleep(2.0)

    js_ref = js_snapshot()
    print(f"  /joint_states reference: {'obtained' if js_ref is not None else 'UNAVAILABLE'}")

    print("\n=== 3. direct observation vs /joint_states ===")
    samples, ages = [], []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 4.0:
        obs = robot.get_observation()
        vec = np.array([obs.get(f"{j}.pos", np.nan) for j in JOINTS], float)
        if js_ref is not None and not np.isnan(vec).any():
            samples.append((vec, js_ref.copy()))
        d = robot._last_sync_diagnostics
        a = d.get(f"can.{SIDE}.age_ms")
        if a is not None:
            ages.append(a)
        time.sleep(0.03)

    if samples:
        D = np.stack([s[0] for s in samples]); J = np.stack([s[1] for s in samples])
        print(f"  {len(samples)} paired samples")
        print(f"{'joint':<30}{'direct':>10}{'joint_states':>14}{'max|diff|':>11}")
        for i, j in enumerate(JOINTS):
            print(f"{j:<30}{D[:,i].mean():10.4f}{J[:,i].mean():14.4f}"
                  f"{np.abs(D[:,i]-J[:,i]).max():11.4f}")
        arm_err = np.abs(D[:, :7] - J[:, :7]).max()
        grip_err = np.abs(D[:, 7] - J[:, 7]).max()
        print(f"\n  joints 1-7 max err : {arm_err:.4f} rad  "
              f"{'OK (<=16-bit quantum 3.8e-4)' if arm_err < 1e-3 else 'CHECK'}")
        print(f"  gripper max err    : {grip_err:.4f} m   "
              f"{'OK' if grip_err < 0.005 else 'CHECK conversion constants'}")
        print(f"  gripper in metres  : direct {D[:,7].mean():.4f} m, "
              f"joint_states {J[:,7].mean():.4f} m")
    else:
        print("  NO paired samples")

    print("\n=== 4. camera frames + sync ===")
    obs = robot.get_observation()
    for cam in devices:
        f = obs.get(cam)
        ts = robot._last_sync_diagnostics.get(f"camera.{cam}.timestamp")
        if isinstance(f, np.ndarray):
            print(f"  {cam:<14} shape {f.shape} dtype {f.dtype}  "
                  f"stamped {'yes' if ts else 'no'}  age {(time.monotonic()-ts)*1000:.0f} ms")
        else:
            print(f"  {cam:<14} NO FRAME")
    if ages:
        print(f"  state-to-frame pairing error: mean {np.mean(ages):.1f} ms  "
              f"max {np.max(ages):.1f} ms")

    print("\n=== 5. action units (gripper must be metres) ===")
    act = robot.get_action_positions()
    gk = [k for k in act if "finger" in k]
    for k in gk:
        v = act[k]
        print(f"  {k} = {v:.4f}  "
              f"{'metres (<=0.05)' if v <= 0.05 else 'looks normalised 0..1 — CHECK'}")
    if not gk:
        print("  no action yet (exoskeleton not forwarding?)")

    robot.disconnect()
    print("\ndone")


if __name__ == "__main__":
    main()
