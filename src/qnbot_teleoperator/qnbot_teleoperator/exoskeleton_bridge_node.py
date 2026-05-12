#!/usr/bin/env python3
import time
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray
from control_msgs.action import GripperCommand

# Joint name lists matching the controller YAML
LEFT_JOINTS  = ['openarm_left_joint1',  'openarm_left_joint2',  'openarm_left_joint3',
                 'openarm_left_joint4',  'openarm_left_joint5',  'openarm_left_joint6',
                 'openarm_left_joint7']
RIGHT_JOINTS = ['openarm_right_joint1', 'openarm_right_joint2', 'openarm_right_joint3',
                 'openarm_right_joint4', 'openarm_right_joint5', 'openarm_right_joint6',
                 'openarm_right_joint7']


class ArmState:
    """Holds per-arm blend/smooth state."""
    def __init__(self):
        self.current   = None   # np.array – last published positions (7,)
        self.blending  = False  # True while in the startup blend phase
        self.blend_start_time  = None
        self.blend_start_pos   = None   # arm position at blend start
        self.first_target      = None   # exo target at blend start


class ExoskeletonBridgeNode(Node):
    def __init__(self):
        super().__init__('exoskeleton_bridge_node')

        # ── Parameters ──────────────────────────────────────────
        self.declare_parameter('gripper_threshold',      0.003)
        self.declare_parameter('gripper_scaling_factor', 0.02)
        # Seconds to blend from current → first exo command (safety)
        self.declare_parameter('blend_time',   3.0)
        # EMA alpha: 0 = no movement, 1 = no smoothing. 0.15 is gentle.
        self.declare_parameter('smooth_alpha', 0.15)

        self.gripper_threshold = self.get_parameter('gripper_threshold').value
        self.gripper_scale     = self.get_parameter('gripper_scaling_factor').value
        self.blend_time        = self.get_parameter('blend_time').value
        self.smooth_alpha      = self.get_parameter('smooth_alpha').value

        # ── Arm state ────────────────────────────────────────────
        self.left_state  = ArmState()
        self.right_state = ArmState()

        # ── Joint state subscriber (reads current HW positions) ──
        self.joint_states_cache = {}   # joint_name -> position
        self.create_subscription(JointState, '/joint_states',
                                 self._joint_states_cb, 10)

        # ── Left arm ─────────────────────────────────────────────
        self.left_arm_sub = self.create_subscription(
            JointState, '/left_arm/joint_command', self.left_arm_callback, 10)
        self.left_arm_pub = self.create_publisher(
            Float64MultiArray, '/left_forward_position_controller/commands', 10)
        self.left_gripper_client = ActionClient(
            self, GripperCommand, '/left_gripper_controller/gripper_cmd')

        # ── Right arm ────────────────────────────────────────────
        self.right_arm_sub = self.create_subscription(
            JointState, '/right_arm/joint_command', self.right_arm_callback, 10)
        self.right_arm_pub = self.create_publisher(
            Float64MultiArray, '/right_forward_position_controller/commands', 10)
        self.right_gripper_client = ActionClient(
            self, GripperCommand, '/right_gripper_controller/gripper_cmd')

        # ── Gripper state ────────────────────────────────────────
        self.last_left_gripper_pos  = 0.0
        self.last_right_gripper_pos = 0.0

        # ── Log counters ─────────────────────────────────────────
        self.log_counter_left      = 0
        self.log_counter_right     = 0
        self.log_interval          = 50
        self.gripper_log_counter_left  = 0
        self.gripper_log_counter_right = 0
        self.gripper_log_interval  = 100

        self.get_logger().info(
            f'Exoskeleton Bridge Node started\n'
            f'  blend_time        : {self.blend_time:.1f} s  (safe startup interpolation)\n'
            f'  smooth_alpha      : {self.smooth_alpha:.2f}   (EMA per-frame filter)\n'
            f'  gripper_threshold : {self.gripper_threshold} m\n'
            f'  gripper_scale     : {self.gripper_scale}     (exo 1.0 → {self.gripper_scale} m)'
        )

    # ── Joint state cache ────────────────────────────────────────
    def _joint_states_cb(self, msg: JointState):
        for name, pos in zip(msg.name, msg.position):
            self.joint_states_cache[name] = pos

    def _get_current_hw_positions(self, joint_names):
        """Read current hardware joint positions; return zeros if not available."""
        return np.array([self.joint_states_cache.get(n, 0.0) for n in joint_names])

    # ── Callbacks ────────────────────────────────────────────────
    def left_arm_callback(self, msg: JointState):
        self._process_arm_command(msg, self.left_arm_pub,
                                  self.left_gripper_client,
                                  self.left_state, LEFT_JOINTS,
                                  'left', '/left_gripper_controller/gripper_cmd')

    def right_arm_callback(self, msg: JointState):
        self._process_arm_command(msg, self.right_arm_pub,
                                  self.right_gripper_client,
                                  self.right_state, RIGHT_JOINTS,
                                  'right', '/right_gripper_controller/gripper_cmd')

    # ── Core processing ──────────────────────────────────────────
    def _process_arm_command(self, msg: JointState, arm_pub, gripper_client,
                              state: ArmState, joint_names, side, action_name):
        try:
            if len(msg.position) < 7:
                return

            target = np.array(msg.position[:7], dtype=float)

            # ── Gripper extraction ───────────────────────────────
            gripper_raw  = 0.0
            has_gripper  = False
            if len(msg.position) >= 8:
                gripper_raw = msg.position[7]
                has_gripper = True
            elif f'{side}_gripper_joint' in msg.name:
                try:
                    idx = msg.name.index(f'{side}_gripper_joint')
                    if idx < len(msg.position):
                        gripper_raw = msg.position[idx]
                        has_gripper = True
                except ValueError:
                    pass
            final_gripper = gripper_raw * self.gripper_scale if has_gripper else 0.0

            # ── Startup blend (first command ever for this arm) ──
            now = time.monotonic()
            if state.current is None:
                # First command: capture where the arm actually is right now
                hw_pos = self._get_current_hw_positions(joint_names)
                state.blend_start_pos  = hw_pos.copy()
                state.first_target     = target.copy()
                state.blend_start_time = now
                state.blending         = True
                state.current          = hw_pos.copy()
                self.get_logger().info(
                    f'[{side.upper()}] 🔀 Blend started: {self.blend_time:.1f}s from current → exo target\n'
                    f'  Current HW : {np.round(hw_pos, 2).tolist()}\n'
                    f'  Exo target : {np.round(target, 2).tolist()}'
                )

            if state.blending:
                elapsed = now - state.blend_start_time
                alpha_blend = min(elapsed / self.blend_time, 1.0)

                # Update first_target with latest exo command so we always
                # blend toward the CURRENT exo position (operator may have moved)
                state.first_target = target.copy()

                blended = state.blend_start_pos + alpha_blend * (state.first_target - state.blend_start_pos)

                if alpha_blend >= 1.0:
                    state.blending = False
                    self.get_logger().info(
                        f'[{side.upper()}] ✅ Blend complete — full teleoperation active')

                commanded = blended
            else:
                # ── EMA smoothing ────────────────────────────────
                commanded = state.current + self.smooth_alpha * (target - state.current)

            state.current = commanded.copy()

            # ── Publish to ForwardCommandController (8 values) ──
            cmds = Float64MultiArray()
            cmds_list = commanded.tolist()
            if has_gripper:
                cmds_list.append(final_gripper)
            else:
                cmds_list.append(0.0) # Fallback to 0 if no gripper data
            cmds.data = cmds_list
            arm_pub.publish(cmds)

            # ── Logging ──────────────────────────────────────────
            if side == 'left':
                self.log_counter_left += 1
                cnt = self.log_counter_left
            else:
                self.log_counter_right += 1
                cnt = self.log_counter_right

            if cnt % self.log_interval == 0:
                short = [f'{x:.2f}' for x in commanded[:4]]
                blend_str = f' [BLENDING]' if state.blending else ''
                self.get_logger().info(
                    f'[{side.upper()}]{blend_str} joints: {short}... gripper={final_gripper:.4f}')

            if has_gripper:
                if side == 'left':
                    self.gripper_log_counter_left += 1
                    if self.gripper_log_counter_left % self.gripper_log_interval == 0:
                        self.get_logger().info(
                            f'[LEFT] gripper raw={gripper_raw:.4f} → {final_gripper:.4f}m')
                else:
                    self.gripper_log_counter_right += 1
                    if self.gripper_log_counter_right % self.gripper_log_interval == 0:
                        self.get_logger().info(
                            f'[RIGHT] gripper raw={gripper_raw:.4f} → {final_gripper:.4f}m')

        except Exception as e:
            self.get_logger().error(f'Error processing {side} arm command: {e}')

def main(args=None):
    rclpy.init(args=args)
    node = ExoskeletonBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass  # Already shut down by launch system


if __name__ == '__main__':
    main()
