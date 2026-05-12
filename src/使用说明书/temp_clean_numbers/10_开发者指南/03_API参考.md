# API参考

## OpenArmX API完整参考文档

详细的C++和Python API参考手册。


##  C++ API

### openarmx_hardware::OpenArmSystem

**头文件:** `openarmx_hardware/openarm_system.hpp`

**命名空间:** `openarmx_hardware`

**基类:** `hardware_interface::SystemInterface`

####  公共方法

##### on_init()

```cpp
CallbackReturn on_init(const hardware_interface::HardwareInfo & info) override;
```

**功能:** 初始化硬件接口

**参数:**
- `info` - 硬件配置信息(从URDF读取)

**返回值:**
- `CallbackReturn::SUCCESS` - 成功
- `CallbackReturn::ERROR` - 失败

**示例:**
```cpp
auto system = std::make_shared<openarmx_hardware::OpenArmSystem>();
auto result = system->on_init(hardware_info);
if (result != CallbackReturn::SUCCESS) {
    RCLCPP_ERROR(logger, "Failed to initialize");
}
```

##### on_configure()

```cpp
CallbackReturn on_configure(const rclcpp_lifecycle::State & previous_state) override;
```

**功能:** 配置硬件(建立CAN连接)

**参数:**
- `previous_state` - 前一个生命周期状态

**返回值:**
- `CallbackReturn::SUCCESS` - 配置成功
- `CallbackReturn::ERROR` - 配置失败

**注意:** 此方法中建立CAN总线连接

##### on_activate()

```cpp
CallbackReturn on_activate(const rclcpp_lifecycle::State & previous_state) override;
```

**功能:** 激活硬件(使能电机)

**参数:**
- `previous_state` - 前一个生命周期状态

**返回值:**
- `CallbackReturn::SUCCESS` - 激活成功
- `CallbackReturn::ERROR` - 激活失败

**注意:** 此方法中使能所有电机

##### on_deactivate()

```cpp
CallbackReturn on_deactivate(const rclcpp_lifecycle::State & previous_state) override;
```

**功能:** 停用硬件(失能电机)

**参数:**
- `previous_state` - 前一个生命周期状态

**返回值:**
- `CallbackReturn::SUCCESS` - 停用成功

**注意:** 此方法中失能所有电机并关闭CAN连接

##### read()

```cpp
hardware_interface::return_type read(
    const rclcpp::Time & time,
    const rclcpp::Duration & period) override;
```

**功能:** 从硬件读取状态

**参数:**
- `time` - 当前时间戳
- `period` - 更新周期

**返回值:**
- `return_type::OK` - 读取成功
- `return_type::ERROR` - 读取失败

**调用频率:** 由controller_manager的update_rate决定(通常100-200Hz)

**内部操作:**
1. 从CAN总线接收电机反馈
2. 更新`hw_positions_`, `hw_velocities_`, `hw_efforts_`
3. 更新StateInterface

##### write()

```cpp
hardware_interface::return_type write(
    const rclcpp::Time & time,
    const rclcpp::Duration & period) override;
```

**功能:** 向硬件写入命令

**参数:**
- `time` - 当前时间戳
- `period` - 更新周期

**返回值:**
- `return_type::OK` - 写入成功
- `return_type::ERROR` - 写入失败

**内部操作:**
1. 从CommandInterface读取命令
2. 通过CAN总线发送控制指令
3. 使用MIT模式控制电机

##### export_state_interfaces()

```cpp
std::vector<hardware_interface::StateInterface> export_state_interfaces() override;
```

**功能:** 导出状态接口供控制器读取

**返回值:** StateInterface向量

**导出的接口:**
- `<joint_name>/position` - 位置 (rad)
- `<joint_name>/velocity` - 速度 (rad/s)
- `<joint_name>/effort` - 力矩 (Nm)

**示例:**
```cpp
// 控制器中访问
double position = state_interfaces_[0].get_value();
```

##### export_command_interfaces()

```cpp
std::vector<hardware_interface::CommandInterface> export_command_interfaces() override;
```

**功能:** 导出命令接口供控制器写入

**返回值:** CommandInterface向量

**导出的接口:**
- `<joint_name>/position` - 位置命令 (rad)

**示例:**
```cpp
// 控制器中设置
command_interfaces_[0].set_value(target_position);
```


### openarmx_hardware::RobstrideCAN

**头文件:** `openarmx_hardware/robstride_can_interface.hpp`

**命名空间:** `openarmx_hardware`

####  构造函数

```cpp
explicit RobstrideCAN(const std::string & can_channel);
```

**参数:**
- `can_channel` - CAN通道名称 ("can0" 或 "can1")

**示例:**
```cpp
auto can = std::make_shared<RobstrideCAN>("can0");
```

####  公共方法

##### connect()

```cpp
bool connect();
```

**功能:** 连接CAN总线

**返回值:**
- `true` - 连接成功
- `false` - 连接失败

**示例:**
```cpp
if (!can->connect()) {
    std::cerr << "Failed to connect CAN" << std::endl;
}
```

##### disconnect()

```cpp
void disconnect();
```

**功能:** 断开CAN总线连接

##### send_motor_command()

```cpp
bool send_motor_command(
    uint8_t motor_id,
    float position,
    float velocity,
    float kp,
    float kd,
    float torque
);
```

**功能:** 发送电机控制命令(MIT模式)

**参数:**
- `motor_id` - 电机ID (1-7)
- `position` - 目标位置 (rad, 范围: -12.5 ~ 12.5)
- `velocity` - 目标速度 (rad/s, 范围: -30 ~ 30)
- `kp` - 位置增益 (范围: 0 ~ 500)
- `kd` - 速度增益 (范围: 0 ~ 5)
- `torque` - 前馈力矩 (Nm, 范围: -18 ~ 18)

**返回值:**
- `true` - 发送成功
- `false` - 发送失败

**示例:**
```cpp
// PD控制到位置0.5rad
can->send_motor_command(
    1,           // motor_id
    0.5,         // position
    0.0,         // velocity
    50.0,        // kp
    1.0,         // kd
    0.0          // torque
);
```

##### receive_motor_feedback()

```cpp
bool receive_motor_feedback(
    uint8_t motor_id,
    float & position,
    float & velocity,
    float & torque
);
```

**功能:** 接收电机反馈

**参数:**
- `motor_id` - 电机ID
- `position` - [输出] 当前位置 (rad)
- `velocity` - [输出] 当前速度 (rad/s)
- `torque` - [输出] 当前力矩 (Nm)

**返回值:**
- `true` - 接收成功
- `false` - 超时或失败

**示例:**
```cpp
float pos, vel, torque;
if (can->receive_motor_feedback(1, pos, vel, torque)) {
    std::cout << "Position: " << pos << " rad" << std::endl;
}
```

##### enable_motor()

```cpp
bool enable_motor(uint8_t motor_id);
```

**功能:** 使能电机

**参数:**
- `motor_id` - 电机ID (1-7)

**返回值:**
- `true` - 使能成功
- `false` - 使能失败

##### disable_motor()

```cpp
bool disable_motor(uint8_t motor_id);
```

**功能:** 失能电机

**参数:**
- `motor_id` - 电机ID (1-7)

**返回值:**
- `true` - 失能成功
- `false` - 失能失败

##### set_zero_position()

```cpp
bool set_zero_position(uint8_t motor_id);
```

**功能:** 设置当前位置为零位

**参数:**
- `motor_id` - 电机ID (1-7)

**返回值:**
- `true` - 设置成功
- `false` - 设置失败

**警告:** 此操作会将电机当前位置设为0,请确保机械臂处于安全姿态


### 控制器接口

####  JointTrajectoryController

**包:** `joint_trajectory_controller`

**类型:** `joint_trajectory_controller/JointTrajectoryController`

**头文件:** `<joint_trajectory_controller/joint_trajectory_controller.hpp>`

##### Action接口

**Action类型:** `control_msgs::action::FollowJointTrajectory`

**Action名称:** `<controller_name>/follow_joint_trajectory`

**Goal结构:**
```cpp
control_msgs::action::FollowJointTrajectory::Goal goal;

// 轨迹
goal.trajectory.joint_names = {"left_joint_1", "left_joint_2", ...};

trajectory_msgs::msg::JointTrajectoryPoint point;
point.positions = {0.0, 0.5, 1.0, ...};
point.velocities = {0.0, 0.0, 0.0, ...};  // 可选
point.time_from_start = rclcpp::Duration::from_seconds(1.0);

goal.trajectory.points.push_back(point);

// 容差(可选)
control_msgs::msg::JointTolerance tol;
tol.name = "left_joint_1";
tol.position = 0.01;  // rad
tol.velocity = 0.1;   // rad/s
goal.path_tolerance.push_back(tol);
```

**Result结构:**
```cpp
// 错误码
enum ErrorCode {
    SUCCESSFUL = 0,
    INVALID_GOAL = -1,
    INVALID_JOINTS = -2,
    OLD_HEADER_TIMESTAMP = -3,
    PATH_TOLERANCE_VIOLATED = -4,
    GOAL_TOLERANCE_VIOLATED = -5
};

auto result = future.get();
if (result->error_code == SUCCESSFUL) {
    // 成功
}
```

**Feedback结构:**
```cpp
void feedback_callback(
    GoalHandleFollowJointTrajectory::SharedPtr,
    const std::shared_ptr<const FollowJointTrajectory::Feedback> feedback)
{
    std::cout << "Desired position: " << feedback->desired.positions[0] << std::endl;
    std::cout << "Actual position: " << feedback->actual.positions[0] << std::endl;
    std::cout << "Error: " << feedback->error.positions[0] << std::endl;
}
```

##### 话题接口

**订阅:**
- `<controller_name>/joint_trajectory` (trajectory_msgs/JointTrajectory)
  - 直接发送轨迹(不使用Action)

**发布:**
- `<controller_name>/state` (control_msgs/JointTrajectoryControllerState)
  - 控制器状态(期望、实际、误差)
- `<controller_name>/controller_state` (control_msgs/JointTrajectoryControllerState)
  - 控制器详细状态

##### 参数

```cpp
// 获取参数
auto node = controller->get_node();
int update_rate = node->get_parameter("update_rate").as_int();
```

**主要参数:**
- `joints` (string_array) - 关节名称列表
- `command_interfaces` (string_array) - 命令接口类型(position/velocity/effort)
- `state_interfaces` (string_array) - 状态接口类型
- `state_publish_rate` (double, Hz) - 状态发布频率
- `action_monitor_rate` (double, Hz) - Action监控频率
- `constraints.*` (double) - 轨迹约束


##  Python API

### robstride模块

**文件:** `motor_tests_openarmx_com/lib/robstride.py`

####  Motor类

##### 构造函数

```python
Motor(bus: can.interface.Bus, motor_id: int, reduction_ratio: float = 1.0)
```

**参数:**
- `bus` - python-can的Bus对象
- `motor_id` - 电机ID (1-7)
- `reduction_ratio` - 减速比 (默认1.0)

**示例:**
```python
import can
from robstride import Motor

bus = can.interface.Bus(channel='can0', bustype='socketcan')
motor = Motor(bus, motor_id=1)
```

##### enable()

```python
def enable(self) -> bool:
```

**功能:** 使能电机

**返回值:**
- `True` - 成功
- `False` - 失败

**示例:**
```python
if motor.enable():
    print("Motor enabled")
```

##### disable()

```python
def disable(self) -> bool:
```

**功能:** 失能电机

**返回值:**
- `True` - 成功
- `False` - 失败

##### set_zero()

```python
def set_zero(self) -> bool:
```

**功能:** 设置当前位置为零位

**返回值:**
- `True` - 成功
- `False` - 失败

**警告:** 确保机械臂在安全位置

##### set_position()

```python
def set_position(
    self,
    position: float,
    vel: float = 0.0,
    kp: float = 30.0,
    kd: float = 1.0,
    torque: float = 0.0
) -> bool:
```

**功能:** MIT模式位置控制

**参数:**
- `position` (float) - 目标位置 (rad)
- `vel` (float) - 目标速度 (rad/s, 默认0)
- `kp` (float) - 位置增益 (默认30.0)
- `kd` (float) - 速度增益 (默认1.0)
- `torque` (float) - 前馈力矩 (Nm, 默认0)

**返回值:**
- `True` - 发送成功
- `False` - 发送失败

**示例:**
```python
# 简单位置控制
motor.set_position(position=0.5)

# 带速度规划
motor.set_position(position=1.0, vel=0.1)

# 柔顺控制(低增益)
motor.set_position(position=0.5, kp=10.0, kd=0.5)

# 力控(前馈力矩)
motor.set_position(position=0.0, kp=0.0, kd=0.0, torque=2.0)
```

##### get_feedback()

```python
def get_feedback(self, timeout: float = 0.1) -> Dict[str, float]:
```

**功能:** 获取电机反馈

**参数:**
- `timeout` (float) - 超时时间 (秒, 默认0.1)

**返回值:** 字典,包含:
- `position` (float) - 位置 (rad)
- `velocity` (float) - 速度 (rad/s)
- `torque` (float) - 力矩 (Nm)
- `temperature` (float) - 温度 (°C)
- `error_code` (int) - 错误码

**示例:**
```python
fb = motor.get_feedback()
print(f"Position: {fb['position']:.3f} rad")
print(f"Velocity: {fb['velocity']:.3f} rad/s")
print(f"Torque: {fb['torque']:.3f} Nm")
print(f"Temperature: {fb['temperature']:.1f} °C")

if fb['error_code'] != 0:
    print(f"Error: 0x{fb['error_code']:02X}")
```

##### get_status()

```python
def get_status(self) -> Dict[str, Any]:
```

**功能:** 获取电机详细状态

**返回值:** 字典,包含:
- `is_enabled` (bool) - 是否使能
- `is_fault` (bool) - 是否故障
- `error_code` (int) - 错误码
- `mode` (str) - 当前模式 ("MIT", "CSP", etc.)
- `voltage` (float) - 电压 (V)
- `current` (float) - 电流 (A)

**示例:**
```python
status = motor.get_status()
if status['is_fault']:
    print(f"Motor fault! Error: 0x{status['error_code']:02X}")
```


### MoveIt Python API

**包:** `moveit_py` (ROS2 Humble)

####  MoveGroupInterface

##### 初始化

```python
from moveit_py.planning import MoveGroupInterface
from rclpy.node import Node

class MyNode(Node):
    def __init__(self):
        super().__init__('my_node')
        self.move_group = MoveGroupInterface(
            node=self,
            group_name='left_arm'
        )
```

##### set_pose_target()

```python
def set_pose_target(
    self,
    pose: geometry_msgs.msg.Pose,
    end_effector_link: str = ""
) -> bool:
```

**功能:** 设置目标位姿

**参数:**
- `pose` - 目标位姿(Pose消息)
- `end_effector_link` - 末端执行器链接名(可选)

**返回值:**
- `True` - 设置成功
- `False` - 目标不可达

**示例:**
```python
from geometry_msgs.msg import Pose

pose = Pose()
pose.position.x = 0.3
pose.position.y = 0.2
pose.position.z = 0.5
pose.orientation.w = 1.0

if move_group.set_pose_target(pose):
    print("Target set")
```

##### set_joint_value_target()

```python
def set_joint_value_target(
    self,
    joint_values: List[float]
) -> bool:
```

**功能:** 设置关节目标

**参数:**
- `joint_values` - 关节角度列表 (rad)

**返回值:**
- `True` - 设置成功
- `False` - 目标不可达

**示例:**
```python
# 7关节机械臂
joint_values = [0.0, 0.5, 0.0, -1.0, 0.0, 1.5, 0.0]
move_group.set_joint_value_target(joint_values)
```

##### plan()

```python
def plan(self) -> Tuple[bool, trajectory_msgs.msg.JointTrajectory, float]:
```

**功能:** 规划轨迹

**返回值:** 元组
- `success` (bool) - 规划是否成功
- `trajectory` (JointTrajectory) - 规划的轨迹
- `planning_time` (float) - 规划耗时 (秒)

**示例:**
```python
success, trajectory, planning_time = move_group.plan()
if success:
    print(f"Planning succeeded in {planning_time:.3f}s")
    print(f"Trajectory has {len(trajectory.points)} points")
```

##### execute()

```python
def execute(
    self,
    trajectory: trajectory_msgs.msg.JointTrajectory
) -> bool:
```

**功能:** 执行轨迹

**参数:**
- `trajectory` - 要执行的轨迹

**返回值:**
- `True` - 执行成功
- `False` - 执行失败

**示例:**
```python
success, trajectory, _ = move_group.plan()
if success:
    if move_group.execute(trajectory):
        print("Execution complete")
```

##### go()

```python
def go(self, wait: bool = True) -> bool:
```

**功能:** 规划并执行(plan + execute)

**参数:**
- `wait` - 是否等待执行完成

**返回值:**
- `True` - 成功
- `False` - 失败

**示例:**
```python
# 设置目标并执行
pose = Pose()
# ... (设置pose)
move_group.set_pose_target(pose)

if move_group.go():
    print("Motion complete")
```

##### get_current_pose()

```python
def get_current_pose(
    self,
    end_effector_link: str = ""
) -> geometry_msgs.msg.PoseStamped:
```

**功能:** 获取当前位姿

**返回值:** PoseStamped消息

**示例:**
```python
current_pose = move_group.get_current_pose()
print(f"Position: ({current_pose.pose.position.x}, "
      f"{current_pose.pose.position.y}, "
      f"{current_pose.pose.position.z})")
```

##### get_current_joint_values()

```python
def get_current_joint_values(self) -> List[float]:
```

**功能:** 获取当前关节角度

**返回值:** 关节角度列表 (rad)

**示例:**
```python
joint_values = move_group.get_current_joint_values()
for i, angle in enumerate(joint_values):
    print(f"Joint {i+1}: {angle:.3f} rad")
```

##### set_max_velocity_scaling_factor()

```python
def set_max_velocity_scaling_factor(self, factor: float) -> None:
```

**功能:** 设置最大速度缩放因子

**参数:**
- `factor` (float) - 缩放因子 (0.0 ~ 1.0)

**示例:**
```python
# 50%最大速度
move_group.set_max_velocity_scaling_factor(0.5)
```

##### set_max_acceleration_scaling_factor()

```python
def set_max_acceleration_scaling_factor(self, factor: float) -> None:
```

**功能:** 设置最大加速度缩放因子

**参数:**
- `factor` (float) - 缩放因子 (0.0 ~ 1.0)

##### set_planner_id()

```python
def set_planner_id(self, planner_id: str) -> None:
```

**功能:** 设置规划器

**参数:**
- `planner_id` (str) - 规划器名称

**可用规划器:**
- `"RRTConnect"` - 快速,适合简单场景
- `"RRTstar"` - 最优路径
- `"PRM"` - 概率路图
- `"BiTRRT"` - 双向RRT

**示例:**
```python
move_group.set_planner_id("RRTConnect")
```


### ROS2 Python API (rclpy)

####  创建节点

```python
import rclpy
from rclpy.node import Node

class MyNode(Node):
    def __init__(self):
        super().__init__('my_node_name')
        self.get_logger().info('Node started')

def main():
    rclpy.init()
    node = MyNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
```

####  发布器

```python
from std_msgs.msg import String

class PublisherNode(Node):
    def __init__(self):
        super().__init__('publisher_node')

        # 创建发布器
        self.publisher = self.create_publisher(
            String,                    # 消息类型
            '/my_topic',               # 话题名
            10                         # 队列大小
        )

        # 定时发布
        self.timer = self.create_timer(
            1.0,                       # 周期(秒)
            self.timer_callback
        )

    def timer_callback(self):
        msg = String()
        msg.data = 'Hello World'
        self.publisher.publish(msg)
        self.get_logger().info(f'Published: {msg.data}')
```

####  订阅器

```python
from sensor_msgs.msg import JointState

class SubscriberNode(Node):
    def __init__(self):
        super().__init__('subscriber_node')

        # 创建订阅器
        self.subscription = self.create_subscription(
            JointState,                # 消息类型
            '/joint_states',           # 话题名
            self.listener_callback,    # 回调函数
            10                         # 队列大小
        )

    def listener_callback(self, msg):
        self.get_logger().info(f'Received {len(msg.name)} joints')
        for name, pos in zip(msg.name, msg.position):
            self.get_logger().info(f'{name}: {pos:.3f} rad')
```

####  服务客户端

```python
from controller_manager_msgs.srv import SwitchController

class ServiceClientNode(Node):
    def __init__(self):
        super().__init__('service_client_node')

        # 创建服务客户端
        self.client = self.create_client(
            SwitchController,
            '/controller_manager/switch_controller'
        )

        # 等待服务可用
        while not self.client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Waiting for service...')

    def call_service(self, start_controllers, stop_controllers):
        # 创建请求
        request = SwitchController.Request()
        request.start_controllers = start_controllers
        request.stop_controllers = stop_controllers
        request.strictness = SwitchController.Request.BEST_EFFORT

        # 异步调用
        future = self.client.call_async(request)
        rclpy.spin_until_future_complete(self, future)

        # 获取结果
        result = future.result()
        if result.ok:
            self.get_logger().info('Service call succeeded')
        else:
            self.get_logger().error('Service call failed')
```

####  Action客户端

```python
from rclpy.action import ActionClient
from control_msgs.action import FollowJointTrajectory

class ActionClientNode(Node):
    def __init__(self):
        super().__init__('action_client_node')

        # 创建Action客户端
        self.action_client = ActionClient(
            self,
            FollowJointTrajectory,
            '/left_arm_controller/follow_joint_trajectory'
        )

    def send_goal(self, trajectory):
        # 等待Action服务器
        self.action_client.wait_for_server()

        # 创建Goal
        goal_msg = FollowJointTrajectory.Goal()
        goal_msg.trajectory = trajectory

        # 发送Goal (异步)
        send_goal_future = self.action_client.send_goal_async(
            goal_msg,
            feedback_callback=self.feedback_callback
        )

        # 注册回调
        send_goal_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error('Goal rejected')
            return

        self.get_logger().info('Goal accepted')

        # 等待结果
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.result_callback)

    def feedback_callback(self, feedback_msg):
        feedback = feedback_msg.feedback
        self.get_logger().info(f'Feedback: {feedback.actual.positions[0]:.3f}')

    def result_callback(self, future):
        result = future.result().result
        if result.error_code == 0:
            self.get_logger().info('Goal succeeded!')
        else:
            self.get_logger().error(f'Goal failed: {result.error_code}')
```


##  命令行API

### ros2 control

```bash
# 列出控制器
ros2 control list_controllers

# 列出硬件接口
ros2 control list_hardware_interfaces

# 列出硬件组件
ros2 control list_hardware_components

# 加载控制器
ros2 control load_controller <controller_name>

# 配置控制器
ros2 control set_controller_state <controller_name> configure

# 启动控制器
ros2 control set_controller_state <controller_name> start

# 停止控制器
ros2 control set_controller_state <controller_name> stop

# 卸载控制器
ros2 control unload_controller <controller_name>

# 切换控制器
ros2 control switch_controllers \
    --start-controllers controller1 controller2 \
    --stop-controllers controller3
```

### ros2 topic

```bash
# 列出话题
ros2 topic list

# 话题信息
ros2 topic info /joint_states

# 话题频率
ros2 topic hz /joint_states

# 话题延迟
ros2 topic delay /joint_states

# 话题带宽
ros2 topic bw /joint_states

# 查看话题内容
ros2 topic echo /joint_states

# 发布消息
ros2 topic pub /test std_msgs/msg/String "{data: 'test'}"
```

### ros2 service

```bash
# 列出服务
ros2 service list

# 服务类型
ros2 service type /controller_manager/list_controllers

# 调用服务
ros2 service call \
    /controller_manager/list_controllers \
    controller_manager_msgs/srv/ListControllers
```

### ros2 param

```bash
# 列出参数
ros2 param list

# 获取参数
ros2 param get /controller_manager update_rate

# 设置参数
ros2 param set /controller_manager update_rate 200

# 导出参数
ros2 param dump /controller_manager > params.yaml

# 加载参数
ros2 param load /controller_manager params.yaml
```


##  配置文件API

### URDF/Xacro标签

####  ros2_control标签

```xml
<ros2_control name="OpenArmSystem" type="system">
  <hardware>
    <plugin>openarmx_hardware/OpenArmSystem</plugin>
    <param name="can0_channel">can0</param>
    <param name="can1_channel">can1</param>
    <param name="update_rate">100</param>
  </hardware>

  <joint name="left_joint_1">
    <command_interface name="position">
      <param name="min">-3.14</param>
      <param name="max">3.14</param>
    </command_interface>
    <state_interface name="position"/>
    <state_interface name="velocity"/>
    <state_interface name="effort"/>
  </joint>
</ros2_control>
```

### 控制器配置YAML

```yaml
controller_manager:
  ros__parameters:
    update_rate: 100  # Hz

    # 控制器列表
    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster

    left_arm_controller:
      type: joint_trajectory_controller/JointTrajectoryController

# 控制器参数
left_arm_controller:
  ros__parameters:
    joints:
      - left_joint_1
      - left_joint_2
      # ...

    command_interfaces:
      - position

    state_interfaces:
      - position
      - velocity

    state_publish_rate: 50.0
    action_monitor_rate: 20.0

    constraints:
      stopped_velocity_tolerance: 0.05
      goal_time: 0.5
      left_joint_1:
        trajectory: 0.1
        goal: 0.05
```


##  总结

### API参考要点

```
✓ C++ API - 硬件接口、控制器接口
✓ Python API - Motor类、MoveIt接口、ROS2接口
✓ 命令行API - ros2 control/topic/service/param
✓ 配置API - URDF标签、YAML参数
```

### 常用API速查

**C++硬件控制:**
```cpp
#include <openarmx_hardware/robstride_can_interface.hpp>
auto can = std::make_shared<RobstrideCAN>("can0");
can->send_motor_command(1, 0.5, 0.0, 50.0, 1.0, 0.0);
```

**Python电机控制:**
```python
from robstride import Motor
motor.set_position(position=0.5, kp=30.0, kd=1.0)
```

**Python运动规划:**
```python
from moveit_py.planning import MoveGroupInterface
move_group.set_pose_target(pose)
move_group.go()
```

### 下一步

- **04_贡献指南** - 如何贡献代码
- **02_扩展开发** - 实战开发示例
- **01_包结构说明** - 了解系统架构


*本文档版本: v1.0*
*最后更新: 2025年10月20日*
*成都长数机器人有限公司*
