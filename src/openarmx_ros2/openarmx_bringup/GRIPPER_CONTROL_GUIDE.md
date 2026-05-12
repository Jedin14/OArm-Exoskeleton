# Gripper Controller Configuration Guide

## Question: Can two controllers control the gripper simultaneously?

**Answer: No.** In ROS2 Control, a joint's command interface can only be occupied by one controller at a time.

## Comparison of Two Gripper Control Methods

### Method 1: GripperActionController (Default Configuration)

**Configuration File**: `openarmx_v10_bimanual_controllers.yaml`

**Features**:
- Supports force feedback
- Supports `max_effort` limit
- Supports stall detection (`allow_stalling`)
- Safer, suitable for grasping tasks
- Uses Action interface, cannot be controlled directly via Topic
- Not suitable for teleoperation scenarios

**Usage**:
```bash
# Launch (default configuration)
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit

# Control gripper (via Action)
ros2 action send_goal /left_gripper_controller/gripper_cmd \
    control_msgs/action/GripperCommand \
    "{command: {position: 0.04, max_effort: 10.0}}"
```

**Topics**:
- Action: `/left_gripper_controller/gripper_cmd`
- Action: `/right_gripper_controller/gripper_cmd`

---

### Method 2: ForwardCommandController (New Configuration)

**Configuration File**: `openarmx_v10_bimanual_controllers_gripper_forward.yaml`

**Features**:
- Direct control via Topic
- Fast real-time response
- Suitable for teleoperation scenarios
- Can use the same interface as arm controllers
- No force feedback
- No stall detection
- Requires implementing your own safety protection

**Usage**:
```bash
# Launch (using new configuration)
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml

# Control gripper (via Topic)
ros2 topic pub /left_gripper_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.04]" --once

# Close gripper
ros2 topic pub /left_gripper_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.0]" --once
```

**Topics**:
- Topic: `/left_gripper_forward_position_controller/commands`
- Topic: `/right_gripper_forward_position_controller/commands`

---

## How to Choose?

### Choose Method 1 (GripperActionController) if:
- You need to perform grasping tasks
- You need force feedback and stall detection
- You can accept the complexity of Action interface
- **Recommended for: Production environments, precise grasping, high-safety scenarios**

### Choose Method 2 (ForwardCommandController) if:
- You are doing teleoperation
- You need real-time gripper control following the leader
- You want unified control interface for gripper and arm
- **Recommended for: Teleoperation, teaching, real-time control scenarios**

---

## Complete Teleoperation Configuration

If you want to implement bimanual teleoperation (including grippers), Method 2 is recommended:

### Launch Follower Robot

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml
```

### Verify Gripper Topics

```bash
ros2 topic list | grep gripper
# You should see:
# /left_gripper_forward_position_controller/commands
# /right_gripper_forward_position_controller/commands
```

### Add Gripper Control in Teleoperation Node

Your teleoperation node can publish gripper commands like this:

```cpp
// Create gripper publisher
gripper_left_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(
    "/left_gripper_forward_position_controller/commands", 10);

// Publish gripper position in control loop
auto gripper_msg = std_msgs::msg::Float64MultiArray();
gripper_msg.data = {gripper_position};  // 0.0 (closed) to 0.04 (open)
gripper_left_pub_->publish(gripper_msg);
```

---

## Important Notes

### Cannot Run Two Controllers Simultaneously

**Wrong approach**:
```bash
# This will fail!
ros2 run controller_manager spawner left_gripper_controller
ros2 run controller_manager spawner left_gripper_forward_position_controller
# The second spawner will error: interface already occupied
```

**Reason**:
- `left_gripper_controller` claims the position command interface of `openarmx_left_finger_joint1`
- `left_gripper_forward_position_controller` cannot claim the same interface again
- ROS2 Control uses an exclusive resource model

### Switching Controllers Requires Restart

If you have already launched `left_gripper_controller` and want to switch to `left_gripper_forward_position_controller`:

```bash
# Method 1: Stop and start (may not work if not defined in config file)
ros2 control switch_controllers \
    --stop-controllers left_gripper_controller \
    --start-controllers left_gripper_forward_position_controller

# Method 2: Restart launch file (recommended)
# Press Ctrl+C to stop current launch
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml
```

---

## Python Example: Gripper Control in Teleoperation

```python
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray

class TeleopGripperNode(Node):
    def __init__(self):
        super().__init__('teleop_gripper_node')

        # Create publisher
        self.gripper_pub = self.create_publisher(
            Float64MultiArray,
            '/left_gripper_forward_position_controller/commands',
            10
        )

        # Create timer, 100Hz
        self.timer = self.create_timer(0.01, self.control_callback)

    def control_callback(self):
        # Read gripper position from leader (assuming 0.02 here)
        leader_gripper_position = 0.02

        # Send to follower
        msg = Float64MultiArray()
        msg.data = [leader_gripper_position]
        self.gripper_pub.publish(msg)

def main():
    rclpy.init()
    node = TeleopGripperNode()
    rclpy.spin(node)
    rclpy.shutdown()
```

---

## C++ Example: Gripper Control in Teleoperation

```cpp
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/float64_multi_array.hpp"

class TeleopGripperNode : public rclcpp::Node
{
public:
    TeleopGripperNode() : Node("teleop_gripper_node")
    {
        // Create publisher
        gripper_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(
            "/left_gripper_forward_position_controller/commands", 10);

        // Create timer, 100Hz
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(10),
            std::bind(&TeleopGripperNode::control_callback, this));
    }

private:
    void control_callback()
    {
        // Read gripper position from leader (assuming 0.02 here)
        double leader_gripper_position = 0.02;

        // Send to follower
        auto msg = std_msgs::msg::Float64MultiArray();
        msg.data = {leader_gripper_position};
        gripper_pub_->publish(msg);
    }

    rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr gripper_pub_;
    rclcpp::TimerBase::SharedPtr timer_;
};
```

---

## Summary

- **Default configuration**: Uses `GripperActionController`, suitable for grasping tasks
- **Teleoperation configuration**: Uses `ForwardCommandController` (new config file), suitable for real-time control
- **Cannot run simultaneously**: One joint can only be controlled by one controller
- **Switching method**: Specify different config files via `controllers_file` parameter

Choose the configuration that suits your application scenario!
