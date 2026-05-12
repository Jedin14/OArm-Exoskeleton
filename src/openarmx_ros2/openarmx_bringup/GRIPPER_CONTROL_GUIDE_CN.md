# 夹爪控制器配置指南

## 问题：能否同时使用两个控制器控制夹爪？

**答案：不能。** 在ROS2 Control中，一个关节的命令接口在同一时间只能被一个控制器占用。

## 两种夹爪控制方式对比

### 方式1：GripperActionController（默认配置）

**配置文件**：`openarmx_v10_bimanual_controllers.yaml`

**特点**：
- ✅ 支持力反馈
- ✅ 支持`max_effort`限制
- ✅ 支持堵转检测（`allow_stalling`）
- ✅ 更安全，适合抓取任务
- ❌ 使用Action接口，不能用Topic直接控制
- ❌ 不适合遥操作场景

**使用方法**：
```bash
# 启动（默认配置）
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit

# 控制夹爪（通过Action）
ros2 action send_goal /left_gripper_controller/gripper_cmd \
    control_msgs/action/GripperCommand \
    "{command: {position: 0.04, max_effort: 10.0}}"
```

**话题**：
- Action: `/left_gripper_controller/gripper_cmd`
- Action: `/right_gripper_controller/gripper_cmd`

---

### 方式2：ForwardCommandController（新配置）

**配置文件**：`openarmx_v10_bimanual_controllers_gripper_forward.yaml`

**特点**：
- ✅ 使用Topic直接控制
- ✅ 实时响应快
- ✅ 适合遥操作场景
- ✅ 可以与手臂控制器使用相同的接口
- ❌ 没有力反馈
- ❌ 没有堵转检测
- ❌ 需要自己实现安全保护

**使用方法**：
```bash
# 启动（使用新配置）
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml

# 控制夹爪（通过Topic）
ros2 topic pub /left_gripper_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.04]" --once

# 关闭夹爪
ros2 topic pub /left_gripper_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.0]" --once
```

**话题**：
- Topic: `/left_gripper_forward_position_controller/commands`
- Topic: `/right_gripper_forward_position_controller/commands`

---

## 如何选择？

### 选择方式1（GripperActionController）如果：
- 你需要进行抓取任务
- 你需要力反馈和堵转检测
- 你可以接受Action接口的复杂性
- **推荐用于：生产环境、精确抓取、安全要求高的场景**

### 选择方式2（ForwardCommandController）如果：
- 你在做遥操作
- 你需要实时控制夹爪跟随主动端
- 你希望夹爪和手臂使用统一的控制接口
- **推荐用于：遥操作、示教、实时控制场景**

---

## 遥操作场景完整配置

如果你要实现双臂遥操作（包括夹爪），推荐使用方式2：

### 从动端启动

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml
```

### 验证夹爪话题

```bash
ros2 topic list | grep gripper
# 应该看到：
# /left_gripper_forward_position_controller/commands
# /right_gripper_forward_position_controller/commands
```

### 在遥操作节点中添加夹爪控制

你的遥操作节点可以这样发布夹爪命令：

```cpp
// 创建夹爪发布器
gripper_left_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(
    "/left_gripper_forward_position_controller/commands", 10);

// 控制循环中发布夹爪位置
auto gripper_msg = std_msgs::msg::Float64MultiArray();
gripper_msg.data = {gripper_position};  // 0.0 (关闭) 到 0.04 (打开)
gripper_left_pub_->publish(gripper_msg);
```

---

## 重要提示

### ⚠️ 不能同时运行两个控制器

**错误做法**：
```bash
# ❌ 这样做会失败！
ros2 run controller_manager spawner left_gripper_controller
ros2 run controller_manager spawner left_gripper_forward_position_controller
# 第二个spawner会报错：接口已被占用
```

**原因**：
- `left_gripper_controller`声明了`openarmx_left_finger_joint1`的position命令接口
- `left_gripper_forward_position_controller`无法再声明同一个接口
- ROS2 Control使用独占资源模型

### ⚠️ 切换控制器需要重启

如果你已经启动了`left_gripper_controller`，想切换到`left_gripper_forward_position_controller`：

```bash
# 方法1：停止和启动（可能不工作，因为配置文件中没有定义）
ros2 control switch_controllers \
    --stop-controllers left_gripper_controller \
    --start-controllers left_gripper_forward_position_controller

# 方法2：重新启动launch文件（推荐）
# 按Ctrl+C停止当前launch
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    controllers_file:=openarmx_v10_bimanual_controllers_gripper_forward.yaml
```

---

## Python示例：遥操作中控制夹爪

```python
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray

class TeleopGripperNode(Node):
    def __init__(self):
        super().__init__('teleop_gripper_node')

        # 创建发布器
        self.gripper_pub = self.create_publisher(
            Float64MultiArray,
            '/left_gripper_forward_position_controller/commands',
            10
        )

        # 创建定时器，100Hz
        self.timer = self.create_timer(0.01, self.control_callback)

    def control_callback(self):
        # 从主动端读取夹爪位置（这里假设为0.02）
        leader_gripper_position = 0.02

        # 发送到从动端
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

## C++示例：遥操作中控制夹爪

```cpp
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/float64_multi_array.hpp"

class TeleopGripperNode : public rclcpp::Node
{
public:
    TeleopGripperNode() : Node("teleop_gripper_node")
    {
        // 创建发布器
        gripper_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(
            "/left_gripper_forward_position_controller/commands", 10);

        // 创建定时器，100Hz
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(10),
            std::bind(&TeleopGripperNode::control_callback, this));
    }

private:
    void control_callback()
    {
        // 从主动端读取夹爪位置（这里假设为0.02）
        double leader_gripper_position = 0.02;

        // 发送到从动端
        auto msg = std_msgs::msg::Float64MultiArray();
        msg.data = {leader_gripper_position};
        gripper_pub_->publish(msg);
    }

    rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr gripper_pub_;
    rclcpp::TimerBase::SharedPtr timer_;
};
```

---

## 总结

- **默认配置**：使用`GripperActionController`，适合抓取任务
- **遥操作配置**：使用`ForwardCommandController`（新配置文件），适合实时控制
- **不能同时运行**：一个关节只能被一个控制器控制
- **切换方法**：通过`controllers_file`参数指定不同的配置文件

选择适合你应用场景的配置即可！
