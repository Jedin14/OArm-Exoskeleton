import sys
import time
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String


def main():
    if len(sys.argv) < 2:
        print("Usage: python publish_ui_command.py '{\"action\": \"...\"}'")
        sys.exit(1)

    command_str = sys.argv[1]

    rclpy.init()
    node = rclpy.create_node('ui_command_publisher')

    _ui_cmd_qos = QoSProfile(
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
    )
    pub = node.create_publisher(String, '/exo/ui_command', _ui_cmd_qos)

    msg = String()
    msg.data = command_str

    # Spin briefly so DDS discovery completes and get_subscription_count() updates.
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
        if pub.get_subscription_count() > 0:
            break

    # Publish several times to ensure RELIABLE delivery
    for _ in range(5):
        pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.05)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
