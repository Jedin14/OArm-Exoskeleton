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

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"PyTorch using device: {DEVICE}")

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_PATH = "/home/jed/openarm_models/act_packet200/checkpoints/100000/pretrained_model"
RIGHT_CAN  = "can0"
LEFT_CAN   = "can1"
INFERENCE_HZ = 30    # Dataset was recorded at 30 fps; model assumes 30Hz temporal ensembling
CONTROL_HZ   = 100   # High-frequency motor control loop for smooth interpolation
GRIPPER_HOME_PATH = Path(__file__).with_name("gripper_home.yaml")

# Match the ROS 2 OpenArm hardware mapping used during exoskeleton training.
# BOTH action and observation record the gripper as finger_joint1 aperture in
# METRES (0.0=closed, 0.044=open). Datasets recorded before /exo/gripper_command_m
# existed hold the ACTION as a normalised 0..1 trigger instead — check
# meta/info.json's action stats: a max of ~1.0 means normalised, ~0.044 means
# metres. Set GRIPPER_ACTION_IN_METRES=False to deploy one of those older ones.
GRIPPER_OPEN_JOINT_M = 0.044
GRIPPER_OPEN_MOTOR_DELTA_RAD = -1.0472
GRIPPER_ACTION_IN_METRES = True
# Upper bound for the gripper action channel, in whatever unit it carries. The
# clips below used a hardcoded 1.0, which is a no-op against a metres action
# (0..0.044) and would let a bad inference command a metre of aperture.
GRIPPER_ACTION_MAX = GRIPPER_OPEN_JOINT_M if GRIPPER_ACTION_IN_METRES else 1.0

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
        import os
        if isinstance(dev, str) and os.path.exists(dev):
            dev = os.path.realpath(dev)
            
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
    """Convert the model's gripper action to absolute motor radians.

    The action is an aperture in metres (GRIPPER_ACTION_IN_METRES), matching
    observation.state, so it is divided by the open aperture to recover the
    opening fraction the motor mapping needs. Older datasets emit the fraction
    directly; the flag switches back for those.
    """
    if GRIPPER_ACTION_IN_METRES:
        open_fraction = float(np.clip(action_value, 0.0, GRIPPER_OPEN_JOINT_M)) / GRIPPER_OPEN_JOINT_M
    else:
        open_fraction = float(np.clip(action_value, 0.0, 1.0))
    return closed_motor_pos + open_fraction * GRIPPER_OPEN_MOTOR_DELTA_RAD

def observation_to_action_seed(state8):
    """Seed the action smoother from the currently observed aperture.

    With the action in metres this is already the right unit — state[7] IS an
    aperture — so no rescale is needed. Rescaling here while the action is metres
    would seed the smoother ~23x too small and snap the gripper shut on startup.
    """
    seed = np.array(state8, dtype=np.float32)
    if not GRIPPER_ACTION_IN_METRES:
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
    return t.to(DEVICE).unsqueeze(0)


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
    
    img_mean = stats["observation.images.main_camera.mean"].to(DEVICE)
    img_std  = stats["observation.images.main_camera.std"].to(DEVICE)
    state_mean = stats["observation.state.mean"].to(DEVICE)
    state_std  = stats["observation.state.std"].to(DEVICE)
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
        
        state_t = torch.tensor(state, dtype=torch.float32).to(DEVICE)

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


def kill_conflicting_processes():
    import os
    import signal
    import subprocess
    print("Checking for conflicting teleop/lelab processes...")
    patterns = [
        "ros2 launch qnbot_teleoperator",
        "ros2 launch openarm_bringup",
        "websocket_teleoperator",
        "exo_retargeting_node",
        "exoskeleton_bridge_node",
        "ros2_lelab_bridge.py",
        "lelab",
        "controller_manager/spawner",
        "ros2_control_node",
        "robot_state_publisher",
        "joint_state_broadcaster",
        "rviz2",
        "openarm_teleop.sh"
    ]
    
    current_pid = os.getpid()
    parent_pid = os.getppid()
    
    # Try SIGTERM first
    for pat in patterns:
        try:
            output = subprocess.check_output(["pgrep", "-f", pat], text=True)
            for pid_str in output.strip().split('\n'):
                if not pid_str: continue
                pid = int(pid_str)
                if pid in (current_pid, parent_pid, 1):
                    continue
                print(f"Killing conflicting process {pid} ({pat})")
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        except subprocess.CalledProcessError:
            pass
            
    time.sleep(1)
    
    # Force kill with SIGKILL
    for pat in patterns:
        try:
            output = subprocess.check_output(["pgrep", "-f", pat], text=True)
            for pid_str in output.strip().split('\n'):
                if not pid_str: continue
                pid = int(pid_str)
                if pid in (current_pid, parent_pid, 1):
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        except subprocess.CalledProcessError:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    kill_conflicting_processes()
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
    policy.to(DEVICE)

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
    cam_main  = Camera("/dev/main_camera")
    cam_right = Camera("/dev/right_camera")
    cam_left  = Camera("/dev/left_camera")
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
                right_target[7] = float(np.clip(right_target[7], 0.0, GRIPPER_ACTION_MAX))
                left_target[7] = float(np.clip(left_target[7], 0.0, GRIPPER_ACTION_MAX))
                
                # Initialize smoothing from current position to avoid initial jump
                if current_action_right is None:
                    current_action_right = observation_to_action_seed(right_st)
                if current_action_left is None and is_bimanual:
                    current_action_left = observation_to_action_seed(left_st)
                    
                # Smooth pursuit
                current_action_right += alpha * (right_target - current_action_right)
                current_action_right[7] = float(np.clip(current_action_right[7], 0.0, GRIPPER_ACTION_MAX))
                send_arm_action(right_arm, current_action_right, gripper_homes["right"])
                
                if is_bimanual:
                    current_action_left += alpha * (left_target - current_action_left)
                    current_action_left[7] = float(np.clip(current_action_left[7], 0.0, GRIPPER_ACTION_MAX))
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
