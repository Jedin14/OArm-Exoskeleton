#!/usr/bin/env python3
"""
Deploy ACT policy on OpenArm hardware.

Usage:
    python deploy_act_policy.py                 # Both arms (default)
    python deploy_act_policy.py --right         # Right arm only
    python deploy_act_policy.py --model /path/to/model
"""

import argparse
import time
import threading
import cv2
import torch
import numpy as np
from lerobot.policies.act.modeling_act import ACTPolicy
import openarm_can as oa

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_PATH = "/home/jed/openarm_models/act_packet200/checkpoints/100000/pretrained_model"
RIGHT_CAN  = "can0"
LEFT_CAN   = "can1"
CONTROL_HZ = 10

# Motor types per arm (J1-J7)
MOTOR_TYPES = [
    oa.MotorType.DM8009, oa.MotorType.DM8009,
    oa.MotorType.DM4340, oa.MotorType.DM4340,
    oa.MotorType.DM4310, oa.MotorType.DM4310, oa.MotorType.DM4310,
]
MOTOR_SEND_IDS = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
MOTOR_RECV_IDS = [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]

# MIT gains: [J1, J2, J3, J4, J5, J6, J7, gripper]
KP = [240.0, 240.0, 240.0, 240.0, 24.0, 31.0, 25.0, 25.0]
KD = [  5.0,   5.0,   3.0,   5.0,  0.3,  0.3,  0.3,  0.3]

# ── Camera ────────────────────────────────────────────────────────────────────
class Camera:
    def __init__(self, dev):
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            raise RuntimeError(f"Camera failed to open: {dev}")
        self._frame = None
        self._lock  = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            self.cap.grab()
            ok, f = self.cap.retrieve()
            if ok:
                with self._lock:
                    self._frame = f.copy()

    def read(self):
        with self._lock:
            return self._frame

# ── Arm helper ────────────────────────────────────────────────────────────────
def init_arm(can_port):
    arm = oa.OpenArm(can_port, True)
    arm.init_arm_motors(MOTOR_TYPES, MOTOR_SEND_IDS, MOTOR_RECV_IDS)
    arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    arm.set_callback_mode_all(oa.CallbackMode.STATE)
    arm.enable_all()
    arm.recv_all(100)
    return arm

def read_arm_state(arm):
    """Return [j1..j7, gripper] positions in radians."""
    pos = [m.get_position() for m in arm.get_arm().get_motors()]
    pos.append(arm.get_gripper().get_motors()[0].get_position())
    return pos  # length 8

def send_arm_action(arm, action8):
    """Send 8-element radian action to arm + gripper via MIT control."""
    cmds = [oa.MITParam(KP[i], KD[i], action8[i], 0.0, 0.0) for i in range(7)]
    arm.get_arm().mit_control_all(cmds)
    arm.get_gripper().mit_control_all([oa.MITParam(KP[7], KD[7], action8[7], 0.0, 0.0)])
    arm.recv_all()

def img_to_tensor(frame):
    t = torch.from_numpy(frame).permute(2, 0, 1).float() / 255.0
    if torch.cuda.is_available():
        t = t.cuda()
    return t.unsqueeze(0)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--right", action="store_true",
                        help="Run right arm only (left arm stays passive)")
    parser.add_argument("--model", default=MODEL_PATH,
                        help="Path to pretrained model directory")
    args = parser.parse_args()

    mode = "RIGHT ONLY" if args.right else "BIMANUAL"
    print(f"\n=== ACT Deploy [{mode}] ===")

    # Load policy
    print(f"Loading model from {args.model}...")
    policy = ACTPolicy.from_pretrained(args.model)
    policy.eval()
    if torch.cuda.is_available():
        policy.cuda()

    # Init hardware
    print("Initializing right arm (can0)...")
    right_arm = init_arm(RIGHT_CAN)

    left_arm = None
    if not args.right:
        print("Initializing left arm (can1)...")
        left_arm = init_arm(LEFT_CAN)

    # Init cameras
    print("Starting cameras (main=10, right=4, left=12)...")
    cam_main  = Camera(10)
    cam_right = Camera(4)
    cam_left  = Camera(12)
    time.sleep(1.0)  # warmup

    print("Starting control loop... (Ctrl+C to stop)\n")
    step = 0
    try:
        while True:
            t0 = time.time()

            # ── 1. Read cameras ──────────────────────────────────────────────
            f_main  = cam_main.read()
            f_right = cam_right.read()
            f_left  = cam_left.read()
            if f_main is None or f_right is None or f_left is None:
                continue

            # ── 2. Read arm states ───────────────────────────────────────────
            right_arm.refresh_all()
            right_arm.recv_all()
            right_state = read_arm_state(right_arm)  # 8 values, radians

            left_state = [0.0] * 8
            if left_arm is not None:
                left_arm.refresh_all()
                left_arm.recv_all()
                left_state = read_arm_state(left_arm)  # 8 values, radians

            # ── 3. Build 32-dim state vector ─────────────────────────────────
            # Dataset layout: [left 0:8][right 8:16][ee_pose 16:30][grip 30:32]
            state = np.zeros(32, dtype=np.float32)
            state[0:8]  = left_state   # left arm  (zeros if right-only)
            state[8:16] = right_state  # right arm
            # indices 16:32 = ee_pose + gripper_state (leave as zero)

            # ── 4. Policy inference ──────────────────────────────────────────
            state_t = torch.tensor(state, dtype=torch.float32)
            if torch.cuda.is_available():
                state_t = state_t.cuda()

            obs = {
                "observation.images.main_camera":  img_to_tensor(f_main),
                "observation.images.right_camera": img_to_tensor(f_right),
                "observation.images.left_camera":  img_to_tensor(f_left),
                "observation.state": state_t.unsqueeze(0),
            }

            with torch.inference_mode():
                action = policy.select_action(obs).squeeze(0).cpu().numpy()
            # action layout: [left 0:8][right 8:16] — matches state layout

            # ── 5. Send commands ─────────────────────────────────────────────
            right_action = action[8:16]
            send_arm_action(right_arm, right_action)

            if left_arm is not None:
                left_action = action[0:8]
                send_arm_action(left_arm, left_action)

            # ── 6. Debug print every 10 steps ────────────────────────────────
            step += 1
            if step % 10 == 1:
                delta = [right_action[i] - right_state[i] for i in range(7)]
                print(f"Step {step:4d} | "
                      f"right_pos: {[f'{v:.3f}' for v in right_state[:7]]} | "
                      f"action: {[f'{v:.3f}' for v in right_action[:7]]} | "
                      f"delta: {[f'{v:.3f}' for v in delta]}")

            # ── 7. Rate control ──────────────────────────────────────────────
            elapsed = time.time() - t0
            sleep_t = (1.0 / CONTROL_HZ) - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        print("Disabling motors...")
        right_arm.disable_all()
        right_arm.recv_all()
        if left_arm is not None:
            left_arm.disable_all()
            left_arm.recv_all()
        print("Done.")

if __name__ == "__main__":
    main()
