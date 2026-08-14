#!/usr/bin/env python3
"""
外骨骼-机械臂桥接节点（平滑版）

功能：
1. 订阅 /left_arm/joint_command 和 /right_arm/joint_command
2. 将命令平滑后发布到 forward_position_controller
3. 将夹爪命令平滑后发送到 GripperActionController
4. 启动时先从当前姿态平滑回零（home），再进入跟随模式
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import rclpy
import yaml
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray


def _ease_in_out(p: float) -> float:
    """
    Smoothstep 3p^2 - 2p^3. Maps [0,1] -> [0,1] with ZERO velocity at both
    ends, so a transition accelerates and decelerates instead of stepping
    straight to full speed.

    Why this matters for speed: the previous profile was a linear ramp, whose
    velocity jumps 0 -> full instantaneously at t=0 and back at t=end. That
    infinite jerk is what forces the very low unlock speed cap (0.15 rad/s,
    ~36x below the slowest joint's 5.445 rad/s hardware limit) — shortening a
    linear ramp would slam the motors harder and trip exactly the faults the
    old comment warned about. Easing removes the jerk, which is what makes a
    ~1 s unlock safe. Peak velocity is 1.5x the average, so size the speed cap
    with that headroom in mind.
    """
    p = 0.0 if p < 0.0 else (1.0 if p > 1.0 else p)
    return p * p * (3.0 - 2.0 * p)


class ExoskeletonBridgeNode(Node):
    def __init__(self):
        super().__init__('exoskeleton_bridge_node')

        # Existing parameters
        self.declare_parameter('gripper_scaling_factor', 0.02)
        self.declare_parameter('left_joint_multipliers', [1.0] * 7)
        self.declare_parameter('right_joint_multipliers', [1.0] * 7)
        self.declare_parameter('left_gripper_reverse', False)
        self.declare_parameter('right_gripper_reverse', False)

        # New smoothing / homing parameters
        self.declare_parameter('control_rate_hz', 100.0)
        self.declare_parameter('joint_smoothing_alpha', 0.45)
        self.declare_parameter('joint_max_delta_per_sec', 1.8)
        self.declare_parameter('gripper_smoothing_alpha', 0.75)
        self.declare_parameter('gripper_max_delta_per_sec', 0.180)
        # gripper_threshold / gripper_action_min_period_sec / gripper_max_effort
        # are gone: they only ever throttled and bounded GripperCommand action
        # GOALS. The gripper is now a position command in the same message as
        # the arm joints, so there is no goal to rate-limit, and closing force is
        # set by GRIPPER_DEFAULT_KP/KD in v10_simple_hardware.hpp (20.0 / 2.5)
        # rather than by a per-goal max_effort.
        # CAN interfaces, used only to read gripper torque for the force cap
        # (passive/read-only). Defaults match openarm_teleop.sh's RIGHT_CAN/LEFT_CAN.
        self.declare_parameter('right_can_interface', 'can0')
        self.declare_parameter('left_can_interface', 'can1')
        self.declare_parameter('gripper_min_position_m', 0.0)
        self.declare_parameter('gripper_max_position_m', 0.050)
        self.declare_parameter('gripper_close_extra_m', 0.0)
        self.declare_parameter('homing_gripper_duration_sec', 1.0)
        # Gripper blend time when UNLOCKING back to exoskeleton following.
        # Independent of the arm's (much longer) unlock ramp — see control_loop.
        self.declare_parameter('unlock_gripper_duration_sec', 0.4)
        # Arm unlock (home -> exoskeleton pose) speed. Duration is
        # max(unlock_duration_sec, distance / unlock_max_speed_rad_s).
        # Slowest joints (j3/j4) allow 5.445 rad/s; smoothstep peaks at 1.5x
        # the average, so 2.5 rad/s average -> 3.75 rad/s peak, ~69% of limit.
        # Homing (exoskeleton pose -> home) uses the same eased profile and the
        # same speed budget as unlocking; there is no reason for the return
        # trip to be slower now that the jerk is gone.
        self.declare_parameter('homing_duration_sec', 1.0)
        self.declare_parameter('homing_max_speed_rad_s', 2.5)
        self.declare_parameter('unlock_duration_sec', 1.0)
        self.declare_parameter('unlock_max_speed_rad_s', 2.5)
        self.declare_parameter('enable_boot_homing', True)
        self.declare_parameter('boot_homing_duration_sec', 8.0)
        self.declare_parameter('boot_homing_arm_target', [0.0] * 7)
        self.declare_parameter('boot_homing_gripper_target', 0.044)

        self.gripper_scale = float(self.get_parameter('gripper_scaling_factor').value)
        self.left_joint_multipliers = self._parse_joint_multipliers(
            self.get_parameter('left_joint_multipliers').value, 'left'
        )
        self.right_joint_multipliers = self._parse_joint_multipliers(
            self.get_parameter('right_joint_multipliers').value, 'right'
        )
        self.left_gripper_reverse = bool(self.get_parameter('left_gripper_reverse').value)
        self.right_gripper_reverse = bool(self.get_parameter('right_gripper_reverse').value)

        self.control_rate_hz = max(10.0, float(self.get_parameter('control_rate_hz').value))
        self.joint_smoothing_alpha = float(np.clip(self.get_parameter('joint_smoothing_alpha').value, 0.01, 1.0))
        self.joint_max_delta_per_sec = max(0.05, float(self.get_parameter('joint_max_delta_per_sec').value))
        self.gripper_smoothing_alpha = float(np.clip(self.get_parameter('gripper_smoothing_alpha').value, 0.01, 1.0))
        self.gripper_max_delta_per_sec = max(0.002, float(self.get_parameter('gripper_max_delta_per_sec').value))
        self.right_can_interface = str(self.get_parameter('right_can_interface').value)
        self.left_can_interface = str(self.get_parameter('left_can_interface').value)
        self.gripper_min_position_m = float(self.get_parameter('gripper_min_position_m').value)
        self.gripper_max_position_m = float(self.get_parameter('gripper_max_position_m').value)
        if self.gripper_max_position_m <= self.gripper_min_position_m:
            self.get_logger().warn('Invalid gripper range, fallback to [0.0, 0.044]')
            self.gripper_min_position_m = 0.0
            self.gripper_max_position_m = 0.044
        self.gripper_close_extra_m = max(0.0, float(self.get_parameter('gripper_close_extra_m').value))
        self.homing_gripper_duration_sec = max(
            0.1, float(self.get_parameter('homing_gripper_duration_sec').value)
        )
        self.unlock_gripper_duration_sec = max(
            0.05, float(self.get_parameter('unlock_gripper_duration_sec').value)
        )
        self.homing_duration_sec = max(0.2, float(self.get_parameter('homing_duration_sec').value))
        self.homing_max_speed_rad_s = max(0.05, float(self.get_parameter('homing_max_speed_rad_s').value))
        self.unlock_duration_sec = max(0.2, float(self.get_parameter('unlock_duration_sec').value))
        self.unlock_max_speed_rad_s = max(0.05, float(self.get_parameter('unlock_max_speed_rad_s').value))
        self.enable_boot_homing = bool(self.get_parameter('enable_boot_homing').value)
        self.boot_homing_duration_sec = max(0.2, float(self.get_parameter('boot_homing_duration_sec').value))
        self.boot_homing_arm_target = self._parse_joint_multipliers(
            self.get_parameter('boot_homing_arm_target').value, 'boot_homing_arm_target'
        )
        self.boot_homing_gripper_target = float(self.get_parameter('boot_homing_gripper_target').value)

        # IO
        self.left_arm_sub = self.create_subscription(JointState, '/left_arm/joint_command', self.left_arm_callback, 10)
        self.right_arm_sub = self.create_subscription(JointState, '/right_arm/joint_command', self.right_arm_callback, 10)
        self.joint_states_sub = self.create_subscription(JointState, '/joint_states', self.joint_states_callback, 10)

        self.left_arm_pub = self.create_publisher(Float64MultiArray, '/left_forward_position_controller/commands', 10)
        self.right_arm_pub = self.create_publisher(Float64MultiArray, '/right_forward_position_controller/commands', 10)

        # Commanded gripper aperture in METRES, [left, right].
        #
        # This node is the only one that knows gripper_scaling_factor and the
        # min/max clamp, so it is the only one that can say what aperture was
        # actually commanded. Recording used to take the gripper straight off
        # /{side}_arm/joint_command, which carries the exoskeleton's normalised
        # 0..1 trigger — so datasets held action in 0..1 and observation.state in
        # metres for the same channel. Publishing the metres here lets the
        # recorder use one unit without duplicating this scale factor.
        self.gripper_cmd_m_pub = self.create_publisher(Float64MultiArray, '/exo/gripper_command_m', 10)

        # What this node believes about the force cap, so the UI can state it as a
        # FACT instead of inferring it. Layout, NaN where not applicable:
        #   [left_cap, right_cap, left_hold, right_hold, left_torque, right_torque]
        #
        # Added because "the limit is not being enforced" was only ever a deduction
        # from peak torque -- there was no way to tell an unarmed cap from an armed
        # cap that was being outrun. Those need completely different fixes, so the
        # UI has to be able to distinguish them.
        self.gripper_cap_state_pub = self.create_publisher(
            Float64MultiArray, '/exo/gripper_cap_state', 10
        )

        # No gripper ActionClient: the finger joint is published as the 8th
        # entry of the forward position controller command (see _publish_arm).

        # Desired inputs from retargeting
        self.input_arm = {'left': np.zeros(7, dtype=float), 'right': np.zeros(7, dtype=float)}
        self.input_gripper = {'left': 0.0, 'right': 0.0}
        self.have_input = {'left': False, 'right': False}
        # Whether a real trigger value has ever arrived for this side. input_gripper
        # is pre-seeded to 0.0, which is indistinguishable from a genuine
        # fully-closed reading -- see gripper_cmd_m_pub.
        self._gripper_reported = {'left': False, 'right': False}
        # Aperture floor per side while a force limit is latched (metres), or
        # None for unlimited. Set from the UI via set_gripper_limit; see where it
        # is applied in _update_input_from_arm_msg.
        self.gripper_limit_m = {'left': None, 'right': None}
        # Release the virtual trigger exactly when the operator opens past the
        # held aperture.  While their request is still more closed than that
        # aperture, the recorded action remains pinned at the contact point.
        self.gripper_limit_release_m = 0.0

        # Closing-torque cap per side (Nm), or None. Enforced IN THE CONTROL LOOP
        # against a locally-read CAN torque, not from lelab.
        #
        # An earlier version had lelab watch torque and publish an aperture floor
        # over ROS. That round trip is ~20-40ms, and by the time the floor landed
        # the gripper had already squeezed well past the cap -- measured 6.84 Nm
        # against a 4.49 Nm cap. Enforcement has to sit where the command is
        # produced, at control rate, or it is always chasing.
        self.gripper_torque_cap_nm = self._load_persisted_torque_caps()
        self._gripper_torque_readers = {}
        self._gripper_torque_nm = {'left': 0.0, 'right': 0.0}
        # The hardware closing-force limiter holds at about 1.8 Nm for a 2.0 Nm
        # UI cap.  Latch the command at 1.75 Nm so the mirrored dataset action
        # freezes at contact even though the hardware deliberately never drives
        # all the way to the UI cap.
        self.declare_parameter('gripper_torque_tolerance_nm', 0.25)
        self.gripper_torque_tolerance_nm = max(
            0.0, float(self.get_parameter('gripper_torque_tolerance_nm').value)
        )

        # Closing speed while a torque cap is armed.
        #
        # This is a hard physical trade-off: the hold only reacts to a torque
        # reading, one control tick
        # (10ms) after the position that produced it. At full speed
        # (gripper_max_delta_per_sec, 0.8 m/s = 8mm/tick) that one blind tick is
        # what produced the measured 7 Nm spike against a 2 Nm cap -- and no
        # amount of tuning the freeze/back-off logic can shrink a spike that
        # already happened before the first reading came back. The only way to
        # bound it is to not be moving as fast when contact happens.
        #
        # This value is fit to YOUR hardware's actual response, not a theoretical
        # one: a simple KP*error model predicts ~3.8 Nm of overshoot at 0.8 m/s,
        # but 7 Nm was measured -- a 1.84x gap, presumably from KD damping torque,
        # actuator/CAN latency beyond one tick, or motor dynamics this model
        # doesn't capture. Overshoot scales ~linearly with this speed, so halving/
        # doubling it roughly halves/doubles the peak above the cap -- retest with
        # Reset Peaks on the Motor Forces page and adjust:
        #   ros2 param set /exoskeleton_bridge_node gripper_capped_close_speed_m_s 0.08
        #
        # Only closing is affected; opening is always at full, unrestricted speed
        # (see the `if desired_gripper < current` guard below), and this rate
        # applies ONLY while a cap is configured at all -- with no cap set, the
        # gripper behaves exactly as it did before any of this existed.
        self.declare_parameter('gripper_capped_close_speed_m_s', 0.06)
        self.gripper_capped_close_speed_m_s = max(
            0.0, float(self.get_parameter('gripper_capped_close_speed_m_s').value)
        )
        self._start_gripper_torque_readers()

        # Off-thread writer for the /tmp cap-state file (see the control_loop
        # tail). control_loop is the 100Hz ROS timer callback that also publishes
        # BOTH arms' commands, so anything synchronous in it -- even 3 fast
        # syscalls plus JSON encoding -- steals from that tick's 10ms budget and
        # delays when the NEXT tick fires, for both arms, not just the gripper.
        # This showed up as periodic jerkiness synced to the file's 5Hz write
        # rate. The control loop now only ever does a non-blocking queue put;
        # this thread does the actual open/dump/replace.
        self._cap_state_queue = queue.Queue(maxsize=1)
        threading.Thread(
            target=self._cap_state_writer_loop, name="cap_state_writer", daemon=True
        ).start()

        # Current measured robot state
        self.current_arm = {'left': None, 'right': None}
        self.current_gripper = {'left': None, 'right': None}

        # Commanded smoothed state
        self.cmd_arm = {'left': np.zeros(7, dtype=float), 'right': np.zeros(7, dtype=float)}
        self.cmd_gripper = {'left': 0.0, 'right': 0.0}

        # Boot homing state machine
        self.boot_phase = 'wait_state' if self.enable_boot_homing else 'follow'
        self.boot_start_time = None
        self.boot_start_arm = {'left': np.zeros(7, dtype=float), 'right': np.zeros(7, dtype=float)}
        self.boot_start_gripper = {'left': 0.0, 'right': 0.0}

        # UI command states for fixed home lock
        self.left_fixed_home = False
        self.right_fixed_home = False
        self.lock_state = {'left': False, 'right': False}
        self.transition_start_time = {'left': 0.0, 'right': 0.0}
        self.transition_start_arm = {'left': np.zeros(7, dtype=float), 'right': np.zeros(7, dtype=float)}
        self.transition_start_gripper = {'left': 0.0, 'right': 0.0}
        self.transition_duration = {'left': 8.0, 'right': 8.0}
        self.force_transition = {'left': False, 'right': False}
        self.homing_active = {'left': False, 'right': False}
        self.homing_target_arm = {'left': np.zeros(7, dtype=float), 'right': np.zeros(7, dtype=float)}
        self.homing_target_gripper = {'left': 0.0, 'right': 0.0}
        self.homing_until = {'left': 0.0, 'right': 0.0}
        from std_msgs.msg import String
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        _ui_cmd_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.ui_command_sub = self.create_subscription(
            String, '/exo/ui_command', self.ui_command_callback, _ui_cmd_qos
        )

        self.last_control_time = None
        self.control_timer = self.create_timer(1.0 / self.control_rate_hz, self.control_loop)

    def _cap_state_writer_loop(self):
        while True:
            payload = self._cap_state_queue.get()   # blocks here, never in control_loop
            try:
                tmp = "/tmp/lelab_gripper_cap_state.json.tmp"
                with open(tmp, "w") as fh:
                    json.dump(payload, fh)
                os.replace(tmp, "/tmp/lelab_gripper_cap_state.json")
            except Exception:
                pass   # diagnostics must never disturb anything that depends on this thread


    def ui_command_callback(self, msg):
        import json
        try:
            data = json.loads(msg.data)
            action = data.get('action')
            if action == 'toggle_left_home':
                self.left_fixed_home = bool(data.get('value', False))
                self.get_logger().info(f"UI Command: Left arm home fixed = {self.left_fixed_home}")
            elif action == 'toggle_right_home':
                self.right_fixed_home = bool(data.get('value', False))
                self.get_logger().info(f"UI Command: Right arm home fixed = {self.right_fixed_home}")
            elif action == 'set_gripper_torque_cap':
                side = data.get('side')
                cap = data.get('torque_nm')
                if side in ('left', 'right') and cap:
                    self.gripper_torque_cap_nm[side] = float(cap)
                    self.gripper_limit_m[side] = None   # re-arm; hold engages on torque
                    available = self._gripper_torque(side) is not None
                    self.get_logger().info(
                        f"UI Command: {side} gripper torque capped at {float(cap):.2f} Nm"
                        + ("" if available else " (WARNING: no CAN torque available, cap cannot engage)")
                    )
                else:
                    self.get_logger().warn(
                        f"UI Command: ignoring malformed set_gripper_torque_cap {data}"
                    )
            elif action == 'clear_gripper_torque_cap':
                side = data.get('side')
                if side in ('left', 'right'):
                    self.gripper_torque_cap_nm[side] = None
                    self.gripper_limit_m[side] = None
                    self.get_logger().info(f"UI Command: {side} gripper torque cap released")
            elif action == 'set_gripper_limit':
                side = data.get('side')
                aperture = data.get('aperture_m')
                if side in ('left', 'right') and aperture is not None:
                    bounded = float(np.clip(float(aperture),
                                            self.gripper_min_position_m,
                                            self.gripper_max_position_m))
                    self.gripper_limit_m[side] = bounded
                    self.get_logger().info(
                        f"UI Command: {side} gripper force limit latched at {bounded:.4f} m "
                        "(will not close further)"
                    )
                else:
                    self.get_logger().warn(
                        f"UI Command: ignoring malformed set_gripper_limit {data}"
                    )
            elif action == 'clear_gripper_limit':
                side = data.get('side')
                if side in ('left', 'right'):
                    self.gripper_limit_m[side] = None
                    self.get_logger().info(f"UI Command: {side} gripper force limit released")
            elif action == 'home_all':
                self.left_fixed_home = True
                self.right_fixed_home = True
                self.get_logger().info(f"UI Command: Both arms home fixed = True (home_all)")
            elif action == 'set_home_target':
                self.custom_home_left = data.get('left_arm')
                self.custom_home_right = data.get('right_arm')
                self.custom_gripper_left = data.get('left_gripper')
                self.custom_gripper_right = data.get('right_gripper')
                
                if data.get('lock_all', False):
                    self.left_fixed_home = True
                    self.right_fixed_home = True
                
                # Force the control_loop to recompute the transition (and its
                # duration) for the new target.
                #
                # This used to invalidate lock_state by INVERTING it. That was
                # unsafe: if this message arrived while the arm was unlocked
                # (which happens because /positions/{id}/move-to publishes
                # set_home_target and home_all as two independent processes,
                # with no ordering guarantee), the inverted lock_state made the
                # detector fire an UNLOCK transition on the next tick — whose
                # target is self.input_arm, the live exoskeleton pose. The arm
                # then tracked the operator for the few ms until home_all
                # arrived: invisible when still, a visible lurch on fast motion.
                #
                # Only force a recompute for an arm that is ALREADY locked, so
                # this can never initiate exoskeleton following. If the arm is
                # unlocked we just record the new target; the subsequent
                # home_all/lock_all sets is_locked and the detector fires a
                # normal homing transition by itself.
                for side in ('left', 'right'):
                    if (self.left_fixed_home if side == 'left' else self.right_fixed_home):
                        self.force_transition[side] = True


                self.get_logger().info("UI Command: Custom home target updated (triggered dynamic transition)")
        except Exception as e:
            self.get_logger().error(f"Error parsing ui_command: {e}")

    def _parse_joint_multipliers(self, raw_value, name):
        default = [1.0] * 7
        parsed = raw_value
        if isinstance(raw_value, str):
            try:
                parsed = yaml.safe_load(raw_value)
            except Exception:
                self.get_logger().warn(f'[{name}] parse failed: {raw_value}, fallback to {default}')
                return default
        if not isinstance(parsed, (list, tuple)) or len(parsed) != 7:
            self.get_logger().warn(f'[{name}] invalid: {parsed}, fallback to {default}')
            return default
        try:
            return [float(x) for x in parsed]
        except Exception:
            self.get_logger().warn(f'[{name}] non-numeric: {parsed}, fallback to {default}')
            return default

    def left_arm_callback(self, msg: JointState):
        self._update_input_from_arm_msg('left', msg)

    def right_arm_callback(self, msg: JointState):
        self._update_input_from_arm_msg('right', msg)

    def _update_input_from_arm_msg(self, side, msg: JointState):
        if len(msg.position) < 7:
            return

        multipliers = self.left_joint_multipliers if side == 'left' else self.right_joint_multipliers
        arm_positions = np.array(msg.position[:7], dtype=float)
        self.input_arm[side] = arm_positions * np.array(multipliers, dtype=float)
        self.have_input[side] = True

        gripper_norm = 0.0
        has_gripper_data = False
        if len(msg.position) >= 8:
            gripper_norm = float(msg.position[7])
            has_gripper_data = True
        elif f'{side}_gripper_joint' in msg.name:
            try:
                gripper_idx = msg.name.index(f'{side}_gripper_joint')
                if gripper_idx < len(msg.position):
                    gripper_norm = float(msg.position[gripper_idx])
                    has_gripper_data = True
            except ValueError:
                pass

        if has_gripper_data:
            gripper_norm = float(np.clip(gripper_norm, 0.0, 1.0))
            if (side == 'left' and self.left_gripper_reverse) or (side == 'right' and self.right_gripper_reverse):
                gripper_norm = 1.0 - gripper_norm
            requested_m = float(
                np.clip(
                    gripper_norm * self.gripper_scale,
                    self.gripper_min_position_m,
                    self.gripper_max_position_m
                )
            )
            # The operator's UNCLAMPED intent. The force cap is NOT applied here
            # any more: it is enforced in the control loop against locally-read
            # torque (see _apply_gripper_torque_cap), because a cap applied to the
            # trigger value alone cannot react to how hard the gripper is actually
            # squeezing.
            self.input_gripper[side] = requested_m
            self._gripper_reported[side] = True
            # The /exo/gripper_command_m mirror the recorder stores as `action` is
            # published from the control loop, not here, so it carries the
            # force-capped command rather than this raw intent.

    def joint_states_callback(self, msg: JointState):
        index_by_name = {name: idx for idx, name in enumerate(msg.name)}
        for side in ('left', 'right'):
            arm_joint_names = [f'openarm_{side}_joint{i}' for i in range(1, 8)]
            if all(name in index_by_name for name in arm_joint_names):
                self.current_arm[side] = np.array(
                    [msg.position[index_by_name[name]] for name in arm_joint_names], dtype=float
                )

            gripper_name = f'openarm_{side}_finger_joint1'
            if gripper_name in index_by_name:
                self.current_gripper[side] = float(msg.position[index_by_name[gripper_name]])

    def _smooth_vector(self, current, target, alpha, max_delta):
        blended = current + alpha * (target - current)
        delta = np.clip(blended - current, -max_delta, max_delta)
        return current + delta

    def _smooth_scalar(self, current, target, alpha, max_delta):
        blended = current + alpha * (target - current)
        delta = float(np.clip(blended - current, -max_delta, max_delta))
        return current + delta

    def _load_persisted_torque_caps(self):
        """Read the gripper torque caps from lelab's config file at startup.

        THE CAP MUST NOT DEPEND ON A ROS HANDSHAKE. openarm_teleop.sh starts lelab
        BEFORE this node, and lelab pushed the cap once over /exo/ui_command with
        VOLATILE durability -- so if anything polled it before this node existed,
        the message went to no subscriber and was never re-sent. This node then ran
        with no cap at all, which is exactly the "limit is not being enforced"
        symptom: the reader was up, the UI showed a cap, and nothing enforced it.

        Reading the file makes the cap correct from the first control tick, in any
        start order, with or without lelab running. lelab still publishes changes
        for immediate effect.
        """
        caps = {'left': 2.0, 'right': 2.0}   # matches lelab's DEFAULT_GRIPPER_TORQUE_CAP_NM
        path = Path.home() / ".config" / "lelab" / "motor_config.json"
        try:
            import json
            with path.open() as fh:
                stored = (json.load(fh) or {}).get("gripper_torque_cap_nm", {})
            for side in ('left', 'right'):
                if stored.get(side):
                    caps[side] = float(stored[side])
            self.get_logger().info(f"gripper torque caps loaded from {path}: {caps}")
        except FileNotFoundError:
            self.get_logger().info(f"no {path}; gripper torque caps default to {caps}")
        except Exception as e:
            self.get_logger().warn(f"cannot read {path} ({e}); caps default to {caps}")
        return caps

    def _start_gripper_torque_readers(self):
        """Passive CAN listeners for the two gripper motors (recv id 0x18).

        Read-only, so this cannot collide with ros2_control driving the same
        motors. Failure is non-fatal: without torque the cap simply cannot
        engage, and that is reported once rather than taking the node down.
        """
        import os
        import sys

        root = os.environ.get("OPENARM_REPO_ROOT") or str(
            Path(__file__).resolve().parents[3]
        )
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import openarm_direct_io as io
        except Exception as e:
            self.get_logger().warn(
                f"openarm_direct_io unavailable ({e}); gripper torque cap disabled"
            )
            return

        channels = {'right': self.right_can_interface, 'left': self.left_can_interface}
        for side, channel in channels.items():
            if not channel:
                continue
            try:
                reader = io.StateReader(channel, [0x18], {0x18: io.DM4310}, fd=True)
                reader.start()
                self._gripper_torque_readers[side] = reader
                self.get_logger().info(
                    f"gripper torque reader on {channel} ({side}) for force limiting"
                )
            except Exception as e:
                self.get_logger().warn(
                    f"no gripper torque on {channel} ({side}): {e}; cap disabled for it"
                )

    def _gripper_torque(self, side):
        """Latest |gripper torque| in Nm, or None when unavailable/stale."""
        reader = self._gripper_torque_readers.get(side)
        if reader is None:
            return None
        try:
            fb = reader.latest_feedback().get(0x18)
        except Exception:
            return None
        if fb is None:
            return None
        if time.monotonic() - fb.timestamp > 0.2:
            return None   # bus quiet: do not clamp on a stale number
        return abs(float(fb.torque))

    def _apply_gripper_torque_cap(self, side, desired_gripper):
        """Latch the closing setpoint when torque reaches the configured cap."""
        cap = self.gripper_torque_cap_nm.get(side)
        if cap is None:
            return desired_gripper

        torque = self._gripper_torque(side)
        if torque is None:
            return desired_gripper

        held = self.gripper_limit_m.get(side)
        current = float(self.cmd_gripper[side])

        # An existing contact hold behaves like a virtual trigger position.
        # Keep reporting that position while the real trigger asks to close
        # farther.  An opening request always wins, even if the torque sample
        # is still high for a tick or two after the command changes.
        if held is not None:
            if desired_gripper > held + self.gripper_limit_release_m:
                self.gripper_limit_m[side] = None
                return desired_gripper
            return held

        # Never arm a force hold while opening.  The torque feedback necessarily
        # lags the command, so it can still show contact force on the first few
        # opening ticks.
        if desired_gripper >= current:
            return desired_gripper

        # Enter the hold at cap - tolerance (1.75 Nm by default for a 2.00 Nm
        # cap).  The hold is the last published setpoint: it stops further
        # closing but does not command an opening/back-off motion.
        trip_nm = max(0.0, cap - self.gripper_torque_tolerance_nm)
        if torque >= trip_nm:
            self.gripper_limit_m[side] = current
            self.get_logger().info(
                f"{side} gripper reached {torque:.2f} Nm "
                f"(trip {trip_nm:.2f}, cap {cap:.2f}); "
                f"holding at {current:.4f} m"
            )
            return current

        # Closing, cap armed, not yet frozen: this is the approach into whatever
        # is about to be gripped. Rate-limit it so the eventual first over-cap
        # tick is a small step instead of a full-speed one -- seven guaranteed
        # trip whenever this stays at 0.8 m/s. 0 or negative disables it (escape
        # hatch back to the pre-this-feature behaviour).
        if self.gripper_capped_close_speed_m_s <= 0.0:
            return desired_gripper
        step = self.gripper_capped_close_speed_m_s / max(1.0, self.control_rate_hz)
        return max(desired_gripper, current - step)

    def _publish_arm(self, side, arm_values, gripper_value):
        """Publish the 7 arm joints AND the finger joint as one command array.

        The forward position controller owns the finger joint now (it is the 8th
        entry in its `joints` list), so the gripper is just another position
        command. This replaced a GripperCommand ActionClient, which added an
        action server that could silently be unavailable -- dropping every
        gripper command while the arm joints kept tracking normally.

        Sending both in one message also means the gripper can no longer lag the
        arm: they are commanded from the same tick at the same 100 Hz, with no
        goal-rate throttling or change threshold in between.
        """
        msg = Float64MultiArray()
        msg.data = [float(x) for x in arm_values] + [float(gripper_value)]
        if side == 'left':
            self.left_arm_pub.publish(msg)
        else:
            self.right_arm_pub.publish(msg)

    def _initialize_from_current_state(self, now_sec):
        for side in ('left', 'right'):
            if self.current_arm[side] is None or self.current_gripper[side] is None:
                return False

        for side in ('left', 'right'):
            self.cmd_arm[side] = self.current_arm[side].copy()
            current_gripper = float(
                np.clip(self.current_gripper[side], self.gripper_min_position_m, self.gripper_max_position_m)
            )
            self.cmd_gripper[side] = current_gripper
            self.boot_start_arm[side] = self.current_arm[side].copy()
            self.boot_start_gripper[side] = current_gripper

        self.boot_start_time = now_sec
        if self.enable_boot_homing:
            self.boot_phase = 'homing'
            self.get_logger().info('Boot homing started: smooth move to home target.')
        else:
            self.boot_phase = 'follow'
        return True

    def control_loop(self):
        now_sec = self.get_clock().now().nanoseconds * 1e-9
        if self.last_control_time is None:
            self.last_control_time = now_sec
            return

        dt = now_sec - self.last_control_time
        self.last_control_time = now_sec
        if dt <= 0.0:
            return

        joint_max_delta = self.joint_max_delta_per_sec * dt
        gripper_max_delta = self.gripper_max_delta_per_sec * dt

        if self.boot_phase == 'wait_state':
            if not self._initialize_from_current_state(now_sec):
                return

        if self.boot_phase == 'homing':
            progress = min(1.0, (now_sec - self.boot_start_time) / self.boot_homing_duration_sec)
            target_arm = np.array(self.boot_homing_arm_target, dtype=float)
            target_gripper = float(
                np.clip(self.boot_homing_gripper_target, self.gripper_min_position_m, self.gripper_max_position_m)
            )

            for side in ('left', 'right'):
                desired_arm = (1.0 - progress) * self.boot_start_arm[side] + progress * target_arm
                desired_gripper = (1.0 - progress) * self.boot_start_gripper[side] + progress * target_gripper
                desired_gripper = float(
                    np.clip(desired_gripper, self.gripper_min_position_m, self.gripper_max_position_m)
                )

                self.cmd_arm[side] = self._smooth_vector(
                    self.cmd_arm[side], desired_arm, self.joint_smoothing_alpha, joint_max_delta
                )
                self.cmd_gripper[side] = self._smooth_scalar(
                    self.cmd_gripper[side], desired_gripper, self.gripper_smoothing_alpha, gripper_max_delta
                )

                self._publish_arm(side, self.cmd_arm[side], self.cmd_gripper[side])

            if progress >= 1.0:
                self.boot_phase = 'follow'
                self.get_logger().info('Boot homing completed. Follow mode enabled.')
            return

        # Follow mode
        for side in ('left', 'right'):
            is_locked = (side == 'left' and self.left_fixed_home) or (side == 'right' and self.right_fixed_home)
            
            # Calculate raw target based on lock state (needed BEFORE checking state change to compute distance)
            if is_locked:
                if side == 'left' and getattr(self, 'custom_home_left', None) is not None:
                    target_arm = np.array(self.custom_home_left, dtype=float)
                    target_gripper = float(self.custom_gripper_left)
                elif side == 'right' and getattr(self, 'custom_home_right', None) is not None:
                    target_arm = np.array(self.custom_home_right, dtype=float)
                    target_gripper = float(self.custom_gripper_right)
                else:
                    target_arm = np.array(self.boot_homing_arm_target, dtype=float)
                    target_gripper = float(
                        np.clip(self.boot_homing_gripper_target, self.gripper_min_position_m, self.gripper_max_position_m)
                    )
            elif self.homing_active[side]:
                # A homing transition is still in flight. Do NOT hand control
                # back to the exoskeleton mid-motion: hold the homing target
                # until the ramp completes. Without this latch, an unlock
                # arriving while the arm is on its way home makes it abandon
                # the trajectory and jump toward the operator's current pose —
                # the faster the operator is moving, the bigger the lurch.
                # The latch clears on completion, below.
                target_arm = self.homing_target_arm[side]
                target_gripper = self.homing_target_gripper[side]
            else:
                target_arm = self.input_arm[side] if self.have_input[side] else self.cmd_arm[side]
                target_gripper = self.input_gripper[side] if self.have_input[side] else self.cmd_gripper[side]

            # Detect state change (or an explicitly forced recompute)
            if is_locked != self.lock_state[side] or self.force_transition[side]:
                self.force_transition[side] = False
                self.lock_state[side] = is_locked
                self.transition_start_time[side] = now_sec
                self.transition_start_arm[side] = self.cmd_arm[side].copy()
                self.transition_start_gripper[side] = self.cmd_gripper[side]
                
                # Compute distance-based duration to prevent overly fast motions
                max_dist = float(np.max(np.abs(target_arm - self.transition_start_arm[side])))
                if is_locked:
                    # Homing motion (end of episode / home button). Same eased
                    # profile and speed budget as unlocking — see _ease_in_out.
                    computed_duration = max(
                        self.homing_duration_sec, max_dist / self.homing_max_speed_rad_s
                    )
                else:
                    # Unlocking to exoskeleton. Fast is safe here only because
                    # the profile is eased (see _ease_in_out): zero velocity at
                    # both ends, no step change. Still speed-capped so a very
                    # large move stretches rather than exceeding joint limits.
                    computed_duration = max(
                        self.unlock_duration_sec, max_dist / self.unlock_max_speed_rad_s
                    )
                    
                self.transition_duration[side] = computed_duration
                
                if is_locked:
                    self.homing_active[side] = True
                    self.homing_target_arm[side] = np.array(target_arm, dtype=float)
                    self.homing_target_gripper[side] = float(target_gripper)
                    self.homing_until[side] = now_sec + computed_duration

                action_str = "Locking to home" if is_locked else "Unlocking to exoskeleton"
                self.get_logger().info(f"{side.capitalize()} arm: {action_str} with {self.transition_duration[side]:.1f}s transition.")

            # Apply slow transition if within the dynamic window
            # Release the homing latch once the ramp has finished, so a later
            # unlock is honoured normally. Checked after the state-change block
            # above, so the tick that starts homing has elapsed == 0 and keeps
            # the latch.
            if self.homing_active[side] and now_sec >= self.homing_until[side]:
                self.homing_active[side] = False
                # An unlock that arrived DURING the ramp was computed against the
                # latched home target, so its transition covered ~zero distance
                # and lock_state already flipped to False -- meaning no further
                # state change would ever fire the real unlock. The arm then
                # crawled out of home on the rate-limited fallback path instead
                # of the eased unlock ramp, which reads as "the arm is stuck at
                # the start of the episode". Fire the transition now that the
                # exoskeleton pose is usable as a target.
                # Deliberately only a flag: the transition block above has already
                # run this tick, so the ramp starts on the NEXT one, from the
                # current pose to the live exoskeleton target. Rewriting the
                # target here instead would step straight to the operator's pose
                # on this tick with no easing.
                if not is_locked:
                    self.force_transition[side] = True
                    self.get_logger().info(
                        f"{side.capitalize()} arm: homing ramp finished while unlocked; "
                        "starting the deferred unlock transition."
                    )

            time_since_transition = now_sec - self.transition_start_time[side]
            if time_since_transition < self.transition_duration[side]:
                raw_progress = time_since_transition / self.transition_duration[side]
                # Eased, so the arm glides in and out instead of stepping to
                # full speed at t=0 and stopping dead at t=end.
                progress = _ease_in_out(raw_progress)
                desired_arm = (1.0 - progress) * self.transition_start_arm[side] + progress * target_arm
                # The gripper does not need to wait for the slower arm
                # trajectory in EITHER direction — it gets its own short ramp.
                #
                # Unlock previously used `progress`, tying the gripper to the
                # arm's unlock ramp. That ramp is max(8.0, dist/0.15) seconds,
                # deliberately slow so the arm cannot lurch to the operator's
                # pose and trip a motor fault. The gripper is a small
                # independent actuator with no such constraint, so inheriting
                # that duration just made it crawl for 8+ s after every
                # unlock before it would follow the trigger properly.
                if is_locked:
                    ramp = self.homing_gripper_duration_sec
                else:
                    ramp = self.unlock_gripper_duration_sec
                gripper_progress = min(1.0, time_since_transition / ramp)
                desired_gripper = (
                    (1.0 - gripper_progress) * self.transition_start_gripper[side]
                    + gripper_progress * target_gripper
                )
            else:
                desired_arm = target_arm
                desired_gripper = target_gripper

            # No negative-position overshoot: the hardware linear formula maps
            # negative joint values to the wrong (opening) motor direction.
            # High GRIPPER_DEFAULT_KP in the hardware holds firmly at 0.0.
            desired_gripper = float(
                np.clip(desired_gripper, self.gripper_min_position_m, self.gripper_max_position_m)
            )

            # Determine effective max_delta and alpha for this tick
            # During automated transitions, the trajectory is already speed-limited by transition_duration.
            # We bypass individual joint clipping and smoothing lag so all joints stay perfectly coordinated and arrive exactly on time.
            # When locked and past the transition, command the exact target position
            # directly — eliminates the exponential-filter steady-state error so
            # motors converge to the precise home position rather than
            # asymptotically approaching it.
            if time_since_transition < self.transition_duration[side]:
                effective_joint_max_delta = 10.0  # Bypass clipping
                effective_alpha = 1.0             # Bypass exponential lag
            elif is_locked:
                # Locked and transition complete: snap to exact target.
                #
                # The gripper is now part of the same position command as the
                # arm, so it is held at its exact target every tick just like
                # the arm joints. This used to re-send the gripper goal only
                # every 2 s (a keep-alive, to avoid flooding the action server
                # with goals) while the arm was commanded continuously -- so
                # while parked at home the gripper was only being told its
                # target once every 2 seconds.
                self.cmd_arm[side] = desired_arm.copy() if hasattr(desired_arm, 'copy') else np.array(desired_arm, dtype=float)
                self.cmd_gripper[side] = desired_gripper
                self._publish_arm(side, self.cmd_arm[side], self.cmd_gripper[side])
                continue
            else:
                effective_joint_max_delta = joint_max_delta
                effective_alpha = self.joint_smoothing_alpha

            self.cmd_arm[side] = self._smooth_vector(
                self.cmd_arm[side], desired_arm, effective_alpha, effective_joint_max_delta
            )
            # Force cap on the FINAL command, at control rate, using locally-read
            # torque. Applied after smoothing so what gets published is exactly
            # what the cap allows — and because /exo/gripper_command_m mirrors
            # this same value, the recorded `action` tops out here too.
            desired_gripper = self._apply_gripper_torque_cap(side, desired_gripper)
            self.cmd_gripper[side] = self._smooth_scalar(
                self.cmd_gripper[side], desired_gripper, self.gripper_smoothing_alpha, gripper_max_delta
            )

            self._publish_arm(side, self.cmd_arm[side], self.cmd_gripper[side])

        # Mirror the COMMANDED gripper aperture in metres for the recorder, once
        # per control tick for both sides. This is what lands in the dataset as
        # `action`, so it must be the value actually sent to the controller --
        # i.e. after the force cap and after smoothing -- not the operator's raw
        # trigger. Published here rather than from the trigger callback for
        # exactly that reason.
        #
        # NaN for a side that has never reported a trigger: cmd_gripper is seeded
        # from the measured state, so a single-arm rig would otherwise publish a
        # plausible-looking number for an arm nobody is driving and the recorder
        # would store it as that gripper's action. NaN is unambiguous and the
        # consumer skips it.
        if any(self._gripper_reported.values()):
            mirror = Float64MultiArray()
            mirror.data = [
                float(self.cmd_gripper[s]) if self._gripper_reported[s] else float('nan')
                for s in ('left', 'right')
            ]
            self.gripper_cmd_m_pub.publish(mirror)

        # Cap state, ~5Hz. Cheap, and it is the only way to know from outside
        # whether this node actually has a cap armed and is reading torque.
        self._cap_state_ticks = getattr(self, '_cap_state_ticks', 0) + 1
        if self._cap_state_ticks % max(1, int(self.control_rate_hz // 5)) == 0:
            nan = float('nan')
            state = Float64MultiArray()
            state.data = [
                float(self.gripper_torque_cap_nm[s]) if self.gripper_torque_cap_nm[s] else nan
                for s in ('left', 'right')
            ] + [
                float(self.gripper_limit_m[s]) if self.gripper_limit_m[s] is not None else nan
                for s in ('left', 'right')
            ] + [
                (lambda t: nan if t is None else float(t))(self._gripper_torque(s))
                for s in ('left', 'right')
            ]
            self.gripper_cap_state_pub.publish(state)
            # Also to a file, because that is the channel lelab can actually read:
            # its venv is python3.12 with no rclpy (which is why _send_ui_command
            # falls back to a subprocess publisher), so a ROS topic can never reach
            # it. Same approach openarm_camera_bridge_node.py already uses for
            # /tmp/lelab_camera_status.json.
            #
            # Non-blocking hand-off ONLY -- the actual file I/O happens on
            # _cap_state_writer_loop's thread, never here. maxsize=1 + put_nowait
            # means a writer that's briefly behind just gets its stale sample
            # overwritten by the next one rather than the control loop blocking
            # on a full queue.
            payload = {
                "updated_at": time.time(),
                "cap_nm": {s: self.gripper_torque_cap_nm[s] for s in ('left', 'right')},
                "hold_m": {s: self.gripper_limit_m[s] for s in ('left', 'right')},
                "torque_nm": {s: self._gripper_torque(s) for s in ('left', 'right')},
                "close_speed_m_s": self.gripper_capped_close_speed_m_s,
            }
            try:
                self._cap_state_queue.get_nowait()   # drop any stale unread sample
            except queue.Empty:
                pass
            try:
                self._cap_state_queue.put_nowait(payload)
            except queue.Full:
                pass


def main(args=None):
    rclpy.init(args=args)
    node = ExoskeletonBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
