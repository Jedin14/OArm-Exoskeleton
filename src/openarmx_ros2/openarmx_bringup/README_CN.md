# OpenArmX Bringup

[English](README.md) | [中文](#概述)

---

### 概述

本包为 OpenArmX 双臂机器人系统提供启动配置。包含启动文件和控制器配置，支持使用 ros2_control 以多种控制模式和硬件接口启动机器人。

### 功能特性

- 双臂 7 自由度配置（left_arm 和 right_arm）
- 双夹爪控制（8 自由度模式，集成夹爪）
- 支持仿真模式（虚拟硬件）和真实硬件模式
- 支持 CAN 2.0 和 CAN-FD 通信
- 支持 MIT（运控）和 CSP（位置）控制模式
- 多种控制器类型：轨迹控制器和前向位置控制器
- 支持命名空间，适用于多机器人配置
- RViz 可视化

### 包结构

```
openarmx_bringup/
├── config/
│   └── v10_controllers/
│       ├── openarmx_v10_bimanual_controllers.yaml          # 主双臂控制器配置
│       ├── openarmx_v10_bimanual_controllers_namespaced.yaml # 带命名空间的控制器配置
│       └── openarmx_v10_controllers.yaml                   # 单臂控制器配置
├── launch/
│   └── openarmx.bimanual.launch.py    # 主双臂启动文件
├── rviz/
│   └── bimanual.rviz                 # RViz 配置
├── GRIPPER_CONTROL_GUIDE.md          # 夹爪控制详细文档
├── CMakeLists.txt
├── package.xml
├── README.md
└── README_CN.md
```

### 依赖项

- ROS 2 Humble
- ros2_control
- controller_manager
- joint_state_broadcaster
- joint_trajectory_controller
- forward_command_controller（可选，安装命令：`sudo apt-get install ros-humble-forward-command-controller`）
- openarmx_description
- openarmx_hardware

### 安装

1. 确保工作空间已配置所有 OpenArmX 包
2. 编译本包：

```bash
colcon build --packages-select openarmx_bringup
source install/setup.bash
```

### 使用方法

#### 单机器人启动（默认配置）

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py
```

#### 单机器人启动（MIT 控制模式，推荐用于遥操作）

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py control_mode:=mit
```

#### 双机器人配置

**主动臂：**
```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can0 \
    left_can_interface:=can1 \
    control_mode:=mit
```

**从动臂：**
```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit
```

#### 仿真模式（无需真实硬件）

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    use_fake_hardware:=true
```

### 启动参数

#### 核心参数

| 参数 | 类型 | 默认值 | 可选值 | 说明 |
|------|------|--------|--------|------|
| `robot_controller` | string | `joint_trajectory_controller` | `joint_trajectory_controller`, `forward_position_controller` | 机器人控制器类型 |
| `control_mode` | string | `mit` | `mit`, `csp` | 底层电机控制模式 |
| `use_fake_hardware` | bool | `false` | `true`, `false` | 启用仿真硬件 |

#### `robot_controller` 详解

**1. `joint_trajectory_controller`（默认，推荐用于运动规划）**
- 支持平滑的轨迹插值
- 适用场景：MoveIt 运动规划、预定义轨迹执行
- 接口类型：Action (`control_msgs/action/FollowJointTrajectory`)
- 启动的控制器：
  - `left_joint_trajectory_controller`
  - `right_joint_trajectory_controller`
  - `left_gripper_controller`
  - `right_gripper_controller`

**2. `forward_position_controller`（推荐用于遥操作）**
- 直接位置命令控制器，实时响应快
- 适用场景：遥操作、示教、实时控制
- 接口类型：Topic (`std_msgs/msg/Float64MultiArray`)
- 启动的控制器：
  - `left_forward_position_controller`（包含夹爪作为第 8 个关节）
  - `right_forward_position_controller`（包含夹爪作为第 8 个关节）

#### `control_mode` 详解

| 模式 | 说明 | 适用场景 | 特点 |
|------|------|----------|------|
| `mit` | MIT 运控模式 | 力控、柔顺控制、遥操作 | 支持力矩控制、低阻抗、可拖动示教 |
| `csp` | CSP 位置模式 | 高精度位置控制 | 高刚度、精确定位 |

**注意**：修改系统刚性可通过调整 `openarmx_hardware/include/openarmx_hardware/v10_simple_hardware.hpp` 中的 KP/KD 参数。

#### 硬件接口参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `right_can_interface` | string | `can0` | 右臂 CAN 接口 |
| `left_can_interface` | string | `can1` | 左臂 CAN 接口 |
| `can_fd` | string | `false` | 启用 CAN-FD (true) 或使用经典 CAN (false) |

**常见 CAN 接口配置：**
- 单机器人：`can0`（右臂），`can1`（左臂）
- 双机器人主动端：`can0`（右臂），`can1`（左臂）
- 双机器人从动端：`can2`（右臂），`can3`（左臂）

#### 高级参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `description_package` | string | `openarmx_description` | URDF/xacro 文件所在的包 |
| `description_file` | string | `v10.urdf.xacro` | 机器人描述文件名 |
| `arm_type` | string | `v10` | 机器人手臂类型 |
| `runtime_config_package` | string | `openarmx_bringup` | 控制器配置文件所在的包 |
| `controllers_file` | string | `openarmx_v10_bimanual_controllers.yaml` | 控制器配置文件名 |
| `arm_prefix` | string | `` (空) | 话题命名空间前缀 |

### 控制器说明

#### 自动启动的控制器

无论选择哪种 `robot_controller`，以下控制器始终会启动：

1. **joint_state_broadcaster**：发布关节状态
2. **夹爪控制器**（使用 `joint_trajectory_controller` 时）：
   - `left_gripper_controller`
   - `right_gripper_controller`
3. **手臂控制器**（根据 `robot_controller` 参数选择）

#### 查看活动控制器

```bash
sudo apt-get install ros-humble-ros2controlcli
ros2 control list_controllers
```

### 话题和接口

#### 关节状态广播

- `/joint_states` - 所有关节的状态

#### 轨迹控制器模式

- `/left_joint_trajectory_controller/follow_joint_trajectory`（Action）
- `/right_joint_trajectory_controller/follow_joint_trajectory`（Action）
- `/left_gripper_controller/gripper_cmd`（Action）
- `/right_gripper_controller/gripper_cmd`（Action）

#### 前向位置控制器模式

- `/left_forward_position_controller/commands`（Topic：`std_msgs/msg/Float64MultiArray`）
- `/right_forward_position_controller/commands`（Topic：`std_msgs/msg/Float64MultiArray`）

### 夹爪控制

关于夹爪控制的详细信息，包括：
- GripperActionController 与 ForwardCommandController 对比
- 遥操作配置
- Python 和 C++ 代码示例

请参阅 [GRIPPER_CONTROL_GUIDE_CN.md](GRIPPER_CONTROL_GUIDE_CN.md)。

### 使用示例

#### 示例 1：使用轨迹控制器启动（MoveIt）

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=joint_trajectory_controller \
    control_mode:=mit
```

#### 示例 2：使用位置控制器启动（遥操作从动端）

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit \
    right_can_interface:=can2 \
    left_can_interface:=can3
```

#### 示例 3：使用 CSP 位置模式

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    control_mode:=csp
```

#### 示例 4：发送位置命令（前向位置控制器）

```bash
# 向左臂发送位置（7 个关节 + 1 个夹爪）
ros2 topic pub /left_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.02]" --once
```

---

## 许可证

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## 作者

- **Zhang Li** (张力)
- 公司: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- 网站: https://openarmx.com/

## 版本

**当前版本**：1.0.0

---

## 📞 联系我们

### 成都长数机器人有限公司
**Chengdu Changshu Robotics Co., Ltd.**

| 联系方式 | 信息 |
|---------|------|
| 📧 邮箱 | openarmrobot@gmail.com |
| 📱 电话/微信 | +86-17746530375 |
| 🌐 官网 | <https://openarmx.com/> |
| 📍 地址 | 天津经济技术开发区西区新业八街11号华诚机械厂 |
| 👤 联系人 | 王先生 |