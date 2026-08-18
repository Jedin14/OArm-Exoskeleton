"""Publish UI commands to /exo/ui_command.

Two modes:

  one-shot   python publish_ui_command.py '{"action": "..."}'
  daemon     python publish_ui_command.py --daemon   (reads JSON lines on stdin)

DAEMON MODE EXISTS BECAUSE ONE-SHOT IS EXPENSIVE AND LOSSY
----------------------------------------------------------
lelab runs in a venv without rclpy, so it cannot publish in-process and falls
back to running this script. One-shot means a full python+rclpy startup plus up
to 1.5s of discovery waiting PER COMMAND, ~2-4s of CPU each.

A single episode fires roughly 8 of those in its first two seconds (the
episode-start unlock is re-asserted for both arms, 4 times) and up to 10 more
during homing (1Hz re-send). Overlapping, against a 30fps capture loop, that
produced two symptoms:

  * the arm stuttering at episode boundaries -- CPU contention, not control
  * "end episode" sometimes not homing -- under that load discovery frequently
    did not finish inside the 1.5s window, so get_subscription_count() stayed 0
    and the message was published to nobody. Intermittent by construction.

The daemon pays the startup and discovery cost ONCE and then publishes each line
immediately. Commands written while it is still discovering sit in the OS pipe
buffer, so none are lost.
"""

import sys
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String

TOPIC = '/exo/ui_command'
QOS = QoSProfile(
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)


def _wait_for_subscriber(node, pub, timeout_s):
    """Spin until the bridge has subscribed, or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
        if pub.get_subscription_count() > 0:
            return True
    return False


def _publish(node, pub, text, times=3):
    msg = String()
    msg.data = text
    for _ in range(times):
        pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.01)


def run_daemon():
    rclpy.init()
    node = rclpy.create_node('ui_command_publisher_daemon')
    pub = node.create_publisher(String, TOPIC, QOS)

    # Discovery once, up front. Anything lelab writes meanwhile waits in the pipe.
    _wait_for_subscriber(node, pub, 5.0)

    try:
        for line in sys.stdin:            # blocks; ends when lelab closes the pipe
            line = line.strip()
            if not line:
                continue
            # A subscriber can disappear across a bridge restart. Re-check cheaply
            # so the first command after one is not fired into the void.
            if pub.get_subscription_count() == 0:
                _wait_for_subscriber(node, pub, 2.0)
            _publish(node, pub, line)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def run_once(command_str):
    rclpy.init()
    node = rclpy.create_node('ui_command_publisher')
    pub = node.create_publisher(String, TOPIC, QOS)
    _wait_for_subscriber(node, pub, 1.5)
    _publish(node, pub, command_str, times=5)
    node.destroy_node()
    rclpy.shutdown()


def main():
    if len(sys.argv) < 2:
        print("Usage: publish_ui_command.py '{\"action\": \"...\"}' | --daemon")
        sys.exit(1)
    if sys.argv[1] == '--daemon':
        run_daemon()
    else:
        run_once(sys.argv[1])


if __name__ == '__main__':
    main()
