# OpenArmX Bringup

[English](#overview) | [中文](README_CN.md)

---

### Overview

This package provides the bringup configuration for OpenArmX bimanual robot system. It includes launch files and controller configurations for starting the robot with various control modes and hardware interfaces using ros2_control.

### Features

- Dual 7-DOF arm configuration (left_arm and right_arm)
- Gripper control for both arms (8-DOF mode with integrated gripper)
- Support for both simulation (fake hardware) and real hardware modes
- CAN 2.0 and CAN-FD communication support
- MIT (motion control) and CSP (position) control mode support
- Multiple controller types: Joint Trajectory Controller and Forward Position Controller
- Namespace support for multi-robot setups
- RViz visualization

### Package Structure

```
openarmx_bringup/
├── config/
│   └── v10_controllers/
│       ├── openarmx_v10_bimanual_controllers.yaml          # Main bimanual controller config
│       ├── openarmx_v10_bimanual_controllers_namespaced.yaml # Namespaced controller config
│       └── openarmx_v10_controllers.yaml                   # Single arm controller config
├── launch/
│   └── openarmx.bimanual.launch.py    # Main bimanual launch file
├── rviz/
│   └── bimanual.rviz                 # RViz configuration
├── GRIPPER_CONTROL_GUIDE.md          # Detailed gripper control documentation
├── CMakeLists.txt
├── package.xml
├── README.md
└── README_CN.md
```

### Dependencies

- ROS 2 Humble
- ros2_control
- controller_manager
- joint_state_broadcaster
- joint_trajectory_controller
- forward_command_controller (optional, install via: `sudo apt-get install ros-humble-forward-command-controller`)
- openarmx_description
- openarmx_hardware

### Installation

1. Ensure your workspace is set up with all OpenArmX packages
2. Build the package:

```bash
colcon build --packages-select openarmx_bringup
source install/setup.bash
```

### Usage

#### Single Robot Launch (Default Configuration)

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py
```

#### Single Robot Launch (MIT Control Mode, Recommended for Teleoperation)

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py control_mode:=mit
```

#### Dual Robot Setup

**Leader Robot:**
```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can0 \
    left_can_interface:=can1 \
    control_mode:=mit
```

**Follower Robot:**
```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit
```

#### Simulation Mode (No Hardware Required)

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    use_fake_hardware:=true
```

### Launch Parameters

#### Core Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `robot_controller` | string | `joint_trajectory_controller` | `joint_trajectory_controller`, `forward_position_controller` | Robot controller type |
| `control_mode` | string | `mit` | `mit`, `csp` | Low-level motor control mode |
| `use_fake_hardware` | bool | `false` | `true`, `false` | Enable simulation hardware |

#### `robot_controller` Options

**1. `joint_trajectory_controller` (Default, Recommended for Motion Planning)**
- Supports smooth trajectory interpolation
- Use cases: MoveIt motion planning, pre-defined trajectory execution
- Interface: Action (`control_msgs/action/FollowJointTrajectory`)
- Spawned controllers:
  - `left_joint_trajectory_controller`
  - `right_joint_trajectory_controller`
  - `left_gripper_controller`
  - `right_gripper_controller`

**2. `forward_position_controller` (Recommended for Teleoperation)**
- Direct position command controller with real-time response
- Use cases: Teleoperation, teaching, real-time control
- Interface: Topic (`std_msgs/msg/Float64MultiArray`)
- Spawned controllers:
  - `left_forward_position_controller` (includes gripper as 8th joint)
  - `right_forward_position_controller` (includes gripper as 8th joint)

#### `control_mode` Options

| Mode | Description | Use Cases | Characteristics |
|------|-------------|-----------|-----------------|
| `mit` | MIT Motion Control | Force control, compliant control, teleoperation | Supports torque control, low impedance, drag teaching |
| `csp` | Cyclic Synchronous Position | High-precision position control | High stiffness, precise positioning |

**Note**: System stiffness can be adjusted by modifying KP/KD parameters in `openarmx_hardware/include/openarmx_hardware/v10_simple_hardware.hpp`.

#### Hardware Interface Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `right_can_interface` | string | `can0` | CAN interface for right arm |
| `left_can_interface` | string | `can1` | CAN interface for left arm |
| `can_fd` | string | `false` | Enable CAN-FD (true) or classic CAN (false) |

**Common CAN Interface Configurations:**
- Single robot: `can0` (right arm), `can1` (left arm)
- Dual robot leader: `can0` (right arm), `can1` (left arm)
- Dual robot follower: `can2` (right arm), `can3` (left arm)

#### Advanced Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `description_package` | string | `openarmx_description` | Package containing URDF/xacro files |
| `description_file` | string | `v10.urdf.xacro` | Robot description filename |
| `arm_type` | string | `v10` | Robot arm type |
| `runtime_config_package` | string | `openarmx_bringup` | Package containing controller config |
| `controllers_file` | string | `openarmx_v10_bimanual_controllers.yaml` | Controller configuration filename |
| `arm_prefix` | string | `` (empty) | Topic namespace prefix |

### Controllers

#### Auto-started Controllers

Regardless of `robot_controller` selection, these controllers always start:

1. **joint_state_broadcaster**: Publishes joint states
2. **Gripper controllers** (when using `joint_trajectory_controller`):
   - `left_gripper_controller`
   - `right_gripper_controller`
3. **Arm controllers** (based on `robot_controller` parameter)

#### View Active Controllers

```bash
sudo apt-get install ros-humble-ros2controlcli
ros2 control list_controllers
```

### Topics and Interfaces

#### Joint State Broadcasting

- `/joint_states` - Joint states for all joints

#### Joint Trajectory Controller Mode

- `/left_joint_trajectory_controller/follow_joint_trajectory` (Action)
- `/right_joint_trajectory_controller/follow_joint_trajectory` (Action)
- `/left_gripper_controller/gripper_cmd` (Action)
- `/right_gripper_controller/gripper_cmd` (Action)

#### Forward Position Controller Mode

- `/left_forward_position_controller/commands` (Topic: `std_msgs/msg/Float64MultiArray`)
- `/right_forward_position_controller/commands` (Topic: `std_msgs/msg/Float64MultiArray`)

### Gripper Control

For detailed gripper control information including:
- GripperActionController vs ForwardCommandController comparison
- Teleoperation configuration
- Python and C++ code examples

Please refer to [GRIPPER_CONTROL_GUIDE.md](GRIPPER_CONTROL_GUIDE.md).

### Usage Examples

#### Example 1: Launch with Trajectory Controller (MoveIt)

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=joint_trajectory_controller \
    control_mode:=mit
```

#### Example 2: Launch with Position Controller (Teleoperation Follower)

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    robot_controller:=forward_position_controller \
    control_mode:=mit \
    right_can_interface:=can2 \
    left_can_interface:=can3
```

#### Example 3: Launch with CSP Position Mode

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    control_mode:=csp
```

#### Example 4: Send Position Commands (Forward Position Controller)

```bash
# Send position to left arm (7 joints + 1 gripper)
ros2 topic pub /left_forward_position_controller/commands \
    std_msgs/msg/Float64MultiArray \
    "data: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.02]" --once
```

---

## License

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## Author

- **Zhang Li** (张力)
- Company: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- Website: https://openarmx.com/

## Version

**Current Version**: 1.0.0

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
