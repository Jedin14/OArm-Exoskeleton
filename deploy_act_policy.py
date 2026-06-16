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
import yaml
from pathlib import Path
from lerobot.policies.act.modeling_act import ACTPolicy
import openarm_can as oa

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_PATH = "/home/jed/openarm_models/act_packet200/checkpoints/100000/pretrained_model"
RIGHT_CAN  = "can0"
LEFT_CAN   = "can1"
INFERENCE_HZ = 30    # Dataset was recorded at 30 fps; model assumes 30Hz temporal ensembling
CONTROL_HZ   = 100   # High-frequency motor control loop for smooth interpolation
GRIPPER_HOME_PATH = Path(__file__).with_name("gripper_home.yaml")

# Match the ROS 2 OpenArm hardware mapping used during exoskeleton training.
# Actions record gripper as normalized opening: 0.0=closed, 1.0=open.
# Observations record finger_joint1 in meters: 0.0=closed, 0.044=open.
GRIPPER_OPEN_JOINT_M = 0.044
GRIPPER_OPEN_MOTOR_DELTA_RAD = -1.0472

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

def load_gripper_homes(path=GRIPPER_HOME_PATH):
    """Return closed gripper motor positions saved by save_gripper_home.py."""
    defaults = {"left": 0.0, "right": 0.0}
    if not path.exists():
        print(f"WARNING: {path} not found; using 0.0 rad as closed gripper home.")
        return defaults

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    homes = data.get("gripper_home", {})
    return {
        "left": float(homes.get("left_arm_can1", defaults["left"])),
        "right": float(homes.get("right_arm_can0", defaults["right"])),
    }

def gripper_motor_to_joint_m(motor_pos, closed_motor_pos):
    """Convert absolute gripper motor radians to dataset/ROS finger aperture in meters."""
    open_fraction = (float(motor_pos) - closed_motor_pos) / GRIPPER_OPEN_MOTOR_DELTA_RAD
    return float(np.clip(open_fraction, 0.0, 1.0) * GRIPPER_OPEN_JOINT_M)

def gripper_action_to_motor(action_value, closed_motor_pos):
    """Convert model gripper action 0..1 opening fraction to absolute motor radians."""
    open_fraction = float(np.clip(action_value, 0.0, 1.0))
    return closed_motor_pos + open_fraction * GRIPPER_OPEN_MOTOR_DELTA_RAD

def observation_to_action_seed(state8):
    """Use current observed aperture as the initial 0..1 gripper action for smoothing."""
    seed = np.array(state8, dtype=np.float32)
    seed[7] = float(np.clip(seed[7] / GRIPPER_OPEN_JOINT_M, 0.0, 1.0))
    return seed

def read_arm_state(arm, gripper_closed_motor_pos):
    """Return [j1..j7, gripper] in the same units as the LeRobot dataset."""
    pos = [m.get_position() for m in arm.get_arm().get_motors()]
    gripper_motor = arm.get_gripper().get_motors()[0].get_position()
    pos.append(gripper_motor_to_joint_m(gripper_motor, gripper_closed_motor_pos))
    return pos  # length 8

def send_arm_action(arm, action8, gripper_closed_motor_pos):
    """Send 8-element policy action to arm + gripper via MIT control."""
    cmds = [oa.MITParam(KP[i], KD[i], float(action8[i]), 0.0, 0.0) for i in range(7)]
    arm.get_arm().mit_control_all(cmds)
    gripper_motor_target = gripper_action_to_motor(action8[7], gripper_closed_motor_pos)
    arm.get_gripper().mit_control_all([oa.MITParam(KP[7], KD[7], gripper_motor_target, 0.0, 0.0)])
    arm.recv_all()

def img_to_tensor(frame):
    # cv2 reads frames in BGR, but LeRobot models are trained on RGB.
    # We MUST convert the color space, otherwise the ResNet encoder sees 
    # corrupted colors and outputs chaotic actions.
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(frame_rgb).permute(2, 0, 1).float() / 255.0
    if torch.cuda.is_available():
        t = t.cuda()
    return t.unsqueeze(0)


# ── Thread Shared State ───────────────────────────────────────────────────────
shared_lock = threading.Lock()
shared_right_state = None
shared_left_state = None
shared_target_action = None  # length 16: [left 0:8, right 8:16]
inference_running = True

def inference_worker(policy, cam_main, cam_right, cam_left, is_bimanual, stats):
    """Background thread running policy inference at ~10Hz."""
    global shared_right_state, shared_left_state, shared_target_action, inference_running
    import openarm_fk
    
    img_mean = stats["observation.images.main_camera.mean"].cuda()
    img_std  = stats["observation.images.main_camera.std"].cuda()
    state_mean = stats["observation.state.mean"].cuda()
    state_std  = stats["observation.state.std"].cuda()
    act_mean = stats["action.mean"].cpu().numpy()
    act_std  = stats["action.std"].cpu().numpy()

    while inference_running:
        t0 = time.time()
        
        f_main = cam_main.read()
        f_right = cam_right.read()
        f_left = cam_left.read()
        
        if f_main is None or f_right is None or f_left is None:
            time.sleep(0.01)
            continue
            
        with shared_lock:
            right_st = shared_right_state
            left_st = shared_left_state
            
        if right_st is None or (is_bimanual and left_st is None):
            time.sleep(0.01)
            continue
            
        # Build state
        state = openarm_fk.build_state(left_st, right_st)
        
        state_t = torch.tensor(state, dtype=torch.float32)
        if torch.cuda.is_available():
            state_t = state_t.cuda()

        obs = {
            "observation.images.main_camera":  (img_to_tensor(f_main) - img_mean) / img_std,
            "observation.images.right_camera": (img_to_tensor(f_right) - img_mean) / img_std,
            "observation.images.left_camera":  (img_to_tensor(f_left) - img_mean) / img_std,
            "observation.state": ((state_t - state_mean) / state_std).unsqueeze(0),
        }

        with torch.inference_mode():
            action = policy.select_action(obs).squeeze(0).cpu().numpy()
            action = (action * act_std) + act_mean
            
        with shared_lock:
            shared_target_action = action.copy()
            
        # Rate control for inference
        elapsed = time.time() - t0
        sleep_t = (1.0 / INFERENCE_HZ) - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global shared_right_state, shared_left_state, shared_target_action, inference_running

    parser = argparse.ArgumentParser()
    parser.add_argument("--right", action="store_true",
                        help="Run right arm only (left arm stays passive)")
    parser.add_argument("--model", default=MODEL_PATH,
                        help="Path to pretrained model directory")
    args = parser.parse_args()

    mode = "RIGHT ONLY" if args.right else "BIMANUAL"
    print(f"\n=== ACT Deploy [{mode}] ===")

    # Load policy and stats
    print(f"Loading model from {args.model}...")
    policy = ACTPolicy.from_pretrained(args.model)
    policy.eval()
    if torch.cuda.is_available():
        policy.cuda()

    import safetensors.torch
    stats_path = f"{args.model}/policy_preprocessor_step_3_normalizer_processor.safetensors"
    stats = safetensors.torch.load_file(stats_path)
    print("Loaded normalization stats from safetensors.")

    gripper_homes = load_gripper_homes()
    print(
        "Loaded closed gripper homes: "
        f"left={gripper_homes['left']:.4f} rad, right={gripper_homes['right']:.4f} rad"
    )

    # Init hardware
    print("Initializing right arm (can0)...")
    right_arm = init_arm(RIGHT_CAN)

    left_arm = None
    is_bimanual = not args.right
    if is_bimanual:
        print("Initializing left arm (can1)...")
        left_arm = init_arm(LEFT_CAN)

    print("Starting cameras (main, right, left)...")
    cam_main  = Camera("/dev/v4l/by-path/pci-0000:80:14.0-usb-0:4.1:1.3-video-index0")
    cam_right = Camera("/dev/v4l/by-path/pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0")
    cam_left  = Camera("/dev/v4l/by-path/pci-0000:80:14.0-usb-0:9.1:1.0-video-index0")
    time.sleep(1.0)  # warmup

    print("Starting Inference Thread...")
    inf_thread = threading.Thread(target=inference_worker, 
                                  args=(policy, cam_main, cam_right, cam_left, is_bimanual, stats), 
                                  daemon=True)
    inf_thread.start()

    print(f"Starting High-Frequency Control Loop ({CONTROL_HZ} Hz)... (Ctrl+C to stop)\n")
    
    current_action_right = None
    current_action_left = None
    step = 0
    dt = 1.0 / CONTROL_HZ
    
    # Exponential smoothing alpha for 100Hz interpolation towards 30Hz targets
    # alpha=0.08 closes ~22% of the gap over 33ms (3 steps). This creates a highly 
    # filtered, buttery smooth trajectory that gracefully absorbs inference jitter
    # without stalling the motors (which causes jerkiness).
    alpha = 0.08

    try:
        while True:
            t0 = time.time()

            # 1. Read hardware state
            right_arm.refresh_all()
            right_arm.recv_all()
            right_st = read_arm_state(right_arm, gripper_homes["right"])
            
            left_st = [-0.0091, -0.0444, 0.0713, 0.4007, 0.1466, 0.2824, -0.0231, 0.0408]
            if is_bimanual:
                left_arm.refresh_all()
                left_arm.recv_all()
                left_st = read_arm_state(left_arm, gripper_homes["left"])
                
            # Update shared state for inference
            with shared_lock:
                shared_right_state = right_st
                shared_left_state = left_st
                target_act = shared_target_action
                
            # 2. Interpolate and send action
            if target_act is not None:
                right_target = target_act[8:16].copy()
                left_target = target_act[0:8].copy()
                right_target[7] = float(np.clip(right_target[7], 0.0, 1.0))
                left_target[7] = float(np.clip(left_target[7], 0.0, 1.0))
                
                # Initialize smoothing from current position to avoid initial jump
                if current_action_right is None:
                    current_action_right = observation_to_action_seed(right_st)
                if current_action_left is None and is_bimanual:
                    current_action_left = observation_to_action_seed(left_st)
                    
                # Smooth pursuit
                current_action_right += alpha * (right_target - current_action_right)
                current_action_right[7] = float(np.clip(current_action_right[7], 0.0, 1.0))
                send_arm_action(right_arm, current_action_right, gripper_homes["right"])
                
                if is_bimanual:
                    current_action_left += alpha * (left_target - current_action_left)
                    current_action_left[7] = float(np.clip(current_action_left[7], 0.0, 1.0))
                    send_arm_action(left_arm, current_action_left, gripper_homes["left"])
                    
            # Debug print
            step += 1
            if step % int(CONTROL_HZ) == 1 and target_act is not None:
                delta = [right_target[i] - right_st[i] for i in range(7)]
                print(f"Step {step:5d} | "
                      f"pos: {[f'{v:.3f}' for v in right_st[:7]]} | "
                      f"tgt: {[f'{v:.3f}' for v in right_target[:7]]} | "
                      f"delta: {[f'{v:.3f}' for v in delta]} | "
                      f"grip_m={right_st[7]:.3f} grip_cmd={current_action_right[7]:.3f}")

            # Rate control
            elapsed = time.time() - t0
            sleep_t = dt - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        inference_running = False
        print("Disabling motors...")
        right_arm.disable_all()
        right_arm.recv_all()
        if left_arm is not None:
            left_arm.disable_all()
            left_arm.recv_all()
        print("Done.")

if __name__ == "__main__":
    main()
