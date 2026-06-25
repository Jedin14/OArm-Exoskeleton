import sys
import json
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

def main():
    if len(sys.argv) < 2:
        print("Usage: python publish_ui_command.py '{\"action\": \"...\"}'")
        sys.exit(1)
        
    command_str = sys.argv[1]
    
    rclpy.init()
    node = rclpy.create_node('ui_command_publisher')
    pub = node.create_publisher(String, '/exo/ui_command', 10)
    
    msg = String()
    msg.data = command_str
    
    import time
    
    # Wait for discovery
    timeout = 2.0
    start = time.time()
    while pub.get_subscription_count() == 0 and (time.time() - start) < timeout:
        time.sleep(0.05)
        
    # Publish a few times to ensure delivery
    for _ in range(5):
        pub.publish(msg)
        time.sleep(0.1)
        
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
