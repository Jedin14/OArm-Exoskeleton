#!/usr/bin/env python3
"""
ROS 2 to LeLab Bridge Node

Subscribes to:
- /joint_states (observation.state)
- /left_arm/joint_command (left arm action)
- /right_arm/joint_command (right arm action)

Broadcasts the latest state via UDP to LeLab (port 19092) at 100Hz.
"""

import json
import socket
import threading
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState, Joy


class LeLabBridgeNode(Node):
    def __init__(self):
        super().__init__('lelab_bridge_node')

        self.get_logger().info('LeLab UDP Bridge Node started.')

        # State storage
        self.latest_obs = {}
        self.latest_action = {}
        self.latest_buttons = []
        self.lock = threading.Lock()

        # UDP Setup
        self.udp_ip = "127.0.0.1"
        self.udp_port = 19092
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Subscriptions
        self.create_subscription(JointState, '/joint_states', self.obs_callback, 10)
        self.create_subscription(JointState, '/left_arm/joint_command', self.left_action_callback, 10)
        self.create_subscription(JointState, '/right_arm/joint_command', self.right_action_callback, 10)
        self.create_subscription(Joy, '/exo/gamepad_keys', self.joy_callback, 10)

        # Broadcast Timer (100Hz)
        self.create_timer(0.01, self.broadcast_loop)

    def obs_callback(self, msg: JointState):
        with self.lock:
            for name, pos in zip(msg.name, msg.position):
                # Only keep openarm joints
                if "openarm" in name:
                    self.latest_obs[name] = float(pos)

    def _extract_action(self, side: str, msg: JointState):
        with self.lock:
            # Map up to 7 arm joints
            for i in range(min(7, len(msg.position))):
                self.latest_action[f"openarm_{side}_joint{i+1}"] = float(msg.position[i])
            
            # Map gripper if present
            if len(msg.position) >= 8:
                self.latest_action[f"openarm_{side}_finger_joint1"] = float(msg.position[7])
            elif f'{side}_gripper_joint' in msg.name:
                try:
                    idx = msg.name.index(f'{side}_gripper_joint')
                    self.latest_action[f"openarm_{side}_finger_joint1"] = float(msg.position[idx])
                except ValueError:
                    pass

    def left_action_callback(self, msg: JointState):
        self._extract_action('left', msg)

    def right_action_callback(self, msg: JointState):
        self._extract_action('right', msg)

    def joy_callback(self, msg: Joy):
        with self.lock:
            self.latest_buttons = list(msg.buttons)

    def broadcast_loop(self):
        with self.lock:
            payload = {
                "observation": self.latest_obs.copy(),
                "action": self.latest_action.copy(),
                "buttons": self.latest_buttons.copy(),
                "timestamp": time.time()
            }
        
        # Only send if we have some data
        if payload["observation"] or payload["action"]:
            try:
                data = json.dumps(payload).encode('utf-8')
                self.sock.sendto(data, (self.udp_ip, self.udp_port))
            except Exception as e:
                self.get_logger().error(f"UDP send error: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = LeLabBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
