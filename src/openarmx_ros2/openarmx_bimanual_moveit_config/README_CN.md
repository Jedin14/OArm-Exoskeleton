# OpenArmX Bimanual MoveIt Config

[English](#english) | [中文](#中文)

---

### 概述

本包为 OpenArmX 双臂机器人系统提供 MoveIt 2 配置。包含所有必要的配置文件、启动文件和脚本，使用 MoveIt 运动规划框架实现双臂及夹爪的运动规划与控制。

### 功能特性

- 双臂 7 自由度配置（left_arm 和 right_arm）
- 双夹爪控制（left_gripper 和 right_gripper）
- 支持仿真模式（虚拟硬件）和真实硬件模式
- 支持 CAN 2.0 和 CAN-FD 通信
- 支持 MIT 和 CSP 控制模式
- 预配置的运动规划组和状态
- 集成 KDL 运动学求解器
- RViz 可视化与 MoveIt 插件

### 包结构

```
openarmx_bimanual_moveit_config/
├── config/
│   ├── openarmx_bimanual.srdf          # 语义机器人描述文件
│   ├── openarmx_bimanual.urdf.xacro    # 机器人 URDF (Xacro)
│   ├── joint_limits.yaml              # 关节速度/加速度限制
│   ├── kinematics.yaml                # 运动学求解器配置
│   ├── moveit_controllers.yaml        # MoveIt 控制器配置
│   ├── ros2_controllers.yaml          # ros2_control 控制器配置
│   ├── initial_positions.yaml         # 默认关节位置
│   ├── pilz_cartesian_limits.yaml     # Pilz 规划器笛卡尔限制
│   ├── sensors_3d.yaml                # 3D 传感器配置
│   └── moveit.rviz                    # RViz 配置
├── launch/
│   ├── demo.launch.py                 # 主演示启动文件（真实硬件）
│   ├── demo_sim.launch.py             # 仿真模式演示启动文件
│   ├── move_group.launch.py           # MoveIt move_group 节点
│   ├── moveit_rviz.launch.py          # 带 MoveIt 插件的 RViz
│   ├── spawn_controllers.launch.py    # 控制器生成器
│   └── static_virtual_joint_tfs.launch.py
├── run_bimanual_moveit_sim.sh         # 快速启动脚本（仿真）
├── run_bimanual_moveit_with_can2.0.sh # 快速启动脚本（真实硬件）
├── CMakeLists.txt
├── package.xml
└── README.md
```

### 依赖项

- ROS 2 Humble
- MoveIt 2
- ros2_control
- openarmx_description
- openarmx_bringup
- openarmx_hardware
- openarmx_arm_driver

### 安装

1. 确保工作空间已配置所有 OpenArmX 包
2. 编译本包：

```bash
colcon build --packages-select openarmx_bimanual_moveit_config
source install/setup.bash
```

### 使用方法

#### 仿真模式（无需硬件）

用于无真实硬件的测试：

```bash
# 使用便捷脚本
./run_bimanual_moveit_sim.sh

# 或直接使用 ros2 launch
ros2 launch openarmx_bimanual_moveit_config demo_sim.launch.py
```

#### 真实硬件模式

**重要**：启动真实硬件前，请确保：
1. CAN 接口（can0、can1）已正确配置
2. 机械臂已上电且位于零点附近（偏差不超过 30 度）

##### 方法一，使用便捷脚本一键启动

```bash
# 使用便捷脚本（自动配置 CAN）
./run_bimanual_moveit_with_can2.0.sh
```

##### 方法二，手动配置 CAN 后启动

```bash
# 或手动配置 CAN 后启动
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

sudo ip link set can1 down
sudo ip link set can1 type can bitrate 1000000
sudo ip link set can1 up

ros2 launch openarmx_bimanual_moveit_config demo.launch.py
```

### 启动参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `description_package` | `openarmx_description` | 包含 URDF 文件的包 |
| `description_file` | `v10.urdf.xacro` | URDF/Xacro 文件名 |
| `arm_type` | `v10` | 机械臂类型 |
| `use_fake_hardware` | `false` | 启用仿真模式 |
| `robot_controller` | `joint_trajectory_controller` | 控制器类型（`forward_position_controller` 或 `joint_trajectory_controller`） |
| `control_mode` | `mit` | 电机控制模式（`mit` 或 `csp`） |
| `right_can_interface` | `can0` | 右臂 CAN 接口 |
| `left_can_interface` | `can1` | 左臂 CAN 接口 |

自定义参数示例：

```bash
ros2 launch openarmx_bimanual_moveit_config demo.launch.py control_mode:=mit
```

### 运动规划组

| 组名 | 关节 | 说明 |
|------|------|------|
| `left_arm` | joint1-7 | 左臂（7自由度）|
| `right_arm` | joint1-7 | 右臂（7自由度）|
| `left_gripper` | finger_joint1 | 左夹爪 |
| `right_gripper` | finger_joint1 | 右夹爪 |

### 预定义组状态

| 状态名 | 组 | 说明 |
|--------|----|----|
| `home` | left_arm / right_arm | 所有关节为 0 位置 |
| `hands_up` | left_arm / right_arm | 关节4 为 2 rad，其他为 0 |
| `closed` | left_gripper / right_gripper | 夹爪完全闭合 (0) |
| `half_closed` | left_gripper / right_gripper | 夹爪半闭合 (0.022) |
| `open` | left_gripper / right_gripper | 夹爪完全张开 (0.044) |

---

## 许可证

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## Author

- **Zhang Li** (张力)
- 公司: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- 网站: https://openarmx.com/

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