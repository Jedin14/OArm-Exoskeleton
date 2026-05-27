import sys
import time
import subprocess
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
from sensor_msgs.msg import JointState

class HomingNode(Node):
    def __init__(self):
        super().__init__('lelab_homing_node')
        self.left_pub = self.create_publisher(Float64MultiArray, '/left_forward_position_controller/commands', 10)
        self.right_pub = self.create_publisher(Float64MultiArray, '/right_forward_position_controller/commands', 10)
        self.joint_sub = self.create_subscription(JointState, '/joint_states', self.joint_callback, 10)
        
        self.current_left = [0.0] * 7
        self.current_right = [0.0] * 7
        self.got_state = False
        
        # Left joint names in order expected by controller
        self.left_names = [
            'openarm_left_joint1', 'openarm_left_joint2', 'openarm_left_joint3', 'openarm_left_joint4',
            'openarm_left_joint5', 'openarm_left_joint6', 'openarm_left_joint7'
        ]
        self.right_names = [
            'openarm_right_joint1', 'openarm_right_joint2', 'openarm_right_joint3', 'openarm_right_joint4',
            'openarm_right_joint5', 'openarm_right_joint6', 'openarm_right_joint7'
        ]

    def joint_callback(self, msg: JointState):
        if not self.got_state:
            try:
                for i, name in enumerate(self.left_names):
                    if name in msg.name:
                        idx = msg.name.index(name)
                        self.current_left[i] = msg.position[idx]
                for i, name in enumerate(self.right_names):
                    if name in msg.name:
                        idx = msg.name.index(name)
                        self.current_right[i] = msg.position[idx]
                self.got_state = True
            except Exception as e:
                self.get_logger().error(f"Error parsing joints: {e}")

    def home(self):
        self.get_logger().info("Waiting for joint states...")
        start_wait = time.time()
        while not self.got_state and time.time() - start_wait < 2.0:
            rclpy.spin_once(self, timeout_sec=0.1)
            
        if not self.got_state:
            self.get_logger().warning("Could not get joint states, starting from 0 (may jerk!)")
            
        self.get_logger().info("Smoothly homing arms...")
        max_dist = max([abs(p) for p in self.current_left] + [abs(p) for p in self.current_right] + [0.01])
        # Ensure max 0.02 rad per step
        steps = max(30, int(max_dist / 0.02))
        # Reduce sleep time for large steps so it doesn't take 20 seconds, minimum 0.05s
        sleep_time = max(0.05, min(0.1, 3.0 / steps))
        
        for step in range(1, steps + 1):
            fraction = step / float(steps)
            left_msg = Float64MultiArray()
            right_msg = Float64MultiArray()
            
            # Interpolate towards 0
            left_msg.data = [pos * (1.0 - fraction) for pos in self.current_left]
            right_msg.data = [pos * (1.0 - fraction) for pos in self.current_right]
            
            self.left_pub.publish(left_msg)
            self.right_pub.publish(right_msg)
            time.sleep(sleep_time)
            
        self.get_logger().info("Arms homed.")

def main():
    print("Killing existing teleoperation nodes...")
    subprocess.run(["pkill", "-f", "exoskeleton_bridge_node"])
    subprocess.run(["pkill", "-f", "websocket_teleoperator"])
    time.sleep(1.0)
    
    rclpy.init()
    node = HomingNode()
    try:
        node.home()
    finally:
        node.destroy_node()
        rclpy.shutdown()
        
    print("Shutting down LeLab UI...")
    subprocess.run(["pkill", "-f", "uvicorn lelab.server:app"])
    subprocess.run(["pkill", "-f", "openarm_teleop.sh"])

if __name__ == '__main__':
    main()
