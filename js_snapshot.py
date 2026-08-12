#!/usr/bin/env python3
"""Dump one /joint_states sample as JSON. Runs under ROS's python3.10, since
rclpy's C extension is built for 3.10 while leLab's venv is 3.12 — the same
split that makes the UDP bridge necessary."""
import json, sys, rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

class P(Node):
    def __init__(self):
        super().__init__("js_snapshot"); self.out=None
        self.create_subscription(JointState, "/joint_states", self.cb, 10)
    def cb(self, m):
        if self.out is None:
            self.out = dict(zip(m.name, [float(x) for x in m.position]))

rclpy.init(); p=P()
for _ in range(300):
    rclpy.spin_once(p, timeout_sec=0.02)
    if p.out: break
print(json.dumps(p.out or {}))
p.destroy_node(); rclpy.shutdown()
