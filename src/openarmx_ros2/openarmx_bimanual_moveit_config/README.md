# OpenArmX Bimanual MoveIt Config

[English](#english) | [中文](#中文)

---

### Overview

This package provides MoveIt 2 configuration for the OpenArmX bimanual (dual-arm) robot system. It includes all necessary configuration files, launch files, and scripts to enable motion planning and control for both left and right arms with grippers using the MoveIt Motion Planning Framework.

### Features

- Dual 7-DOF arm configuration (left_arm and right_arm)
- Gripper control for both arms (left_gripper and right_gripper)
- Support for both simulation (fake hardware) and real hardware modes
- CAN 2.0 and CAN-FD communication support
- MIT and CSP control mode support
- Pre-configured motion planning groups and states
- KDL kinematics solver integration
- RViz visualization with MoveIt plugin

### Package Structure

```
openarmx_bimanual_moveit_config/
├── config/
│   ├── openarmx_bimanual.srdf          # Semantic Robot Description
│   ├── openarmx_bimanual.urdf.xacro    # Robot URDF (Xacro)
│   ├── joint_limits.yaml              # Joint velocity/acceleration limits
│   ├── kinematics.yaml                # Kinematics solver configuration
│   ├── moveit_controllers.yaml        # MoveIt controller configuration
│   ├── ros2_controllers.yaml          # ros2_control controller configuration
│   ├── initial_positions.yaml         # Default joint positions
│   ├── pilz_cartesian_limits.yaml     # Pilz planner Cartesian limits
│   ├── sensors_3d.yaml                # 3D sensor configuration
│   └── moveit.rviz                    # RViz configuration
├── launch/
│   ├── demo.launch.py                 # Main demo launch (real hardware)
│   ├── demo_sim.launch.py             # Simulation mode demo launch
│   ├── move_group.launch.py           # MoveIt move_group node
│   ├── moveit_rviz.launch.py          # RViz with MoveIt plugin
│   ├── spawn_controllers.launch.py    # Controller spawner
│   └── static_virtual_joint_tfs.launch.py
├── run_bimanual_moveit_sim.sh         # Quick start script (simulation)
├── run_bimanual_moveit_with_can2.0.sh # Quick start script (real hardware)
├── CMakeLists.txt
├── package.xml
└── README.md
```

### Dependencies

- ROS 2 Humble
- MoveIt 2
- ros2_control
- openarmx_description
- openarmx_bringup
- openarmx_hardware
- openarmx_arm_driver

### Installation

1. Ensure your workspace is set up with all OpenArmX packages
2. Build the package:

```bash
colcon build --packages-select openarmx_bimanual_moveit_config
source install/setup.bash
```

### Usage

#### Simulation Mode (No Hardware Required)

For testing without real hardware:

```bash
# Using the convenience script
./run_bimanual_moveit_sim.sh

# Or directly with ros2 launch
ros2 launch openarmx_bimanual_moveit_config demo_sim.launch.py
```

#### Real Hardware Mode

**Important**: Before launching with real hardware, ensure:
1. CAN interfaces (can0, can1) are properly configured
2. Robot arms are powered on and positioned near zero position (within 30 degrees)

##### Method 1: One-key Launch with Convenience Script

```bash
# Using the convenience script (configures CAN automatically)
./run_bimanual_moveit_with_can2.0.sh
```

##### Method 2: Manual CAN Configuration and Launch

```bash
# Or manually configure CAN and launch
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

sudo ip link set can1 down
sudo ip link set can1 type can bitrate 1000000
sudo ip link set can1 up

ros2 launch openarmx_bimanual_moveit_config demo.launch.py
```

### Launch Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `description_package` | `openarmx_description` | Package containing URDF files |
| `description_file` | `v10.urdf.xacro` | URDF/Xacro filename |
| `arm_type` | `v10` | Robot arm type |
| `use_fake_hardware` | `false` | Enable simulation mode |
| `robot_controller` | `joint_trajectory_controller` | Controller type (`forward_position_controller` or `joint_trajectory_controller`) |
| `control_mode` | `mit` | Motor control mode (`mit` or `csp`) |
| `right_can_interface` | `can0` | CAN interface for right arm |
| `left_can_interface` | `can1` | CAN interface for left arm |

Example with custom parameters:

```bash
ros2 launch openarmx_bimanual_moveit_config demo.launch.py control_mode:=mit 
```

### Motion Planning Groups

| Group Name | Joints | Description |
|------------|--------|-------------|
| `left_arm` | joint1-7 | Left arm (7-DOF) |
| `right_arm` | joint1-7 | Right arm (7-DOF) |
| `left_gripper` | finger_joint1 | Left gripper |
| `right_gripper` | finger_joint1 | Right gripper |

### Pre-defined Group States

| State Name | Group | Description |
|------------|-------|-------------|
| `home` | left_arm / right_arm | All joints at 0 position |
| `hands_up` | left_arm / right_arm | Joint4 at 2 rad, others at 0 |
| `closed` | left_gripper / right_gripper | Gripper fully closed (0) |
| `half_closed` | left_gripper / right_gripper | Gripper half closed (0.022) |
| `open` | left_gripper / right_gripper | Gripper fully open (0.044) |

---

## License

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## Author

- **Zhang Li** (张力)
- Company: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- Website: https://openarmx.com/

---

## 📞 Contact Us

### Chengdu Changshu Robot Co., Ltd.

| Contact           | Information                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| 📧 Email          | [openarmrobot@gmail.com](mailto:openarmrobot@gmail.com)                                                      |
| 📱 Phone / WeChat | +86-17746530375                                                                                              |
| 🌐 Website        | [https://openarmx.com/](https://openarmx.com/)                                                               |
| 📍 Address        | Huacheng Machinery Plant, No.11 Xinye 8th Street, West Area, Tianjin Economic-Technological Development Area |
| 👤 Contact Person | Mr. Wang                                                                                                     |
