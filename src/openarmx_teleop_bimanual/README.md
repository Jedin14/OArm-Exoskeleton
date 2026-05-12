# OpenArm Teleoperation Package (openarmx_teleop_bimanual)

English | [简体中文](README_CN.md)

This is a ROS 2 package designed to implement teleoperation for the OpenArmX robotic arm. It provides two operating modes:

1. **Non-gravity-compensation mode**:
   Supports **bimanual teleoperation**. The leader arm motors are **disabled**, allowing direct manual dragging.
2. **Gravity-compensation mode**:
   Supports **single-arm or bimanual teleoperation**. The leader arm actively compensates for its own gravity, providing a near *“weightless”* manipulation experience.

---

## Mode 1: Bimanual Teleoperation (Without Gravity Compensation)

In this mode, both left and right arms can be controlled simultaneously. The leader arm motors are **not enabled**, allowing you to freely drag the arms by hand, while the follower arms replicate the motion in real time.

### Features

✅ **Bimanual control**
✅ **High real-time performance**: 200 Hz control loop
✅ **Manual drag**: leader motors disabled, zero resistance
✅ **ROS 2 integrated**
✅ **8 DOF**: 7 joints + 1 gripper

### System Architecture

```
Leader Side                         Follower Side
┌─────────────────┐                ┌──────────────────┐
│  Right Arm      │                │  Right Arm       │
│  (can0)         │                │  (can2)          │
│  7 Joints +     │ ─── ROS2 ────→ │  8-DOF           │
│  Gripper        │                │  Controller      │
└─────────────────┘                └──────────────────┘
┌─────────────────┐                ┌──────────────────┐
│  Left Arm       │                │  Left Arm        │
│  (can1)         │ ─── ROS2 ────→ │  (can3)          │
│  7 Joints +     │                │  8-DOF           │
│  Gripper        │                │  Controller      │
└─────────────────┘                └──────────────────┘
```

### Usage

#### Step 1: Launch the Follower Robot

**Important: The follower must be started first!**

The follower robot must run in `forward_position_controller` mode:

```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller
```

#### Step 2: Launch the Teleoperation Node

Start the teleoperation node in another terminal:

```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual.launch.py
```

**Launching with custom parameters:**

```bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual.launch.py \
    leader_right_can:=can0 \
    leader_left_can:=can1 \
    follower_right_prefix:=right \
    follower_left_prefix:=left \
    control_rate_hz:=200
```

---

## Mode 2: Single / Bimanual Teleoperation (With Gravity Compensation)

In this mode, the leader arm motors are enabled. Gravity torques are computed in real time based on the URDF model and actively compensated. This results in a near *“zero-gravity”* dragging experience, significantly improving smoothness and precision.

### Features

✅ **Gravity compensation** for weightless manipulation
✅ **Active damping** (configurable) for improved stability
✅ **Position holding** when the arm is stationary
✅ **URDF-based dynamics computation**

---

### Usage (Single Arm Example)

#### Step 1: Launch the Follower Robot (Single Arm)

The follower must run in `forward_position_controller` mode:

```bash
# Launch right arm as the follower
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller
```

#### Step 2: Launch the Teleoperation Node with Gravity Compensation

In another terminal, start the gravity-compensated teleoperation node.
**Note:** This mode requires a valid URDF file.

---

### Generate the URDF File

Execute the following command:

```bash
cd {your_workspace_path}
xacro ./src/openarmx_description/urdf/robot/v10.urdf.xacro \
      arm_type:=v10 bimanual:=true > /tmp/v10_bimanual.urdf
```

---

### Launch Commands

* **Bimanual teleoperation with gravity compensation (experimental)**

Simplified launch command:

```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual_with_gravitycomp.launch.py
```

Custom launch parameters:

```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual_with_gravitycomp.launch.py \
    mode:=bimanual \
    leader_urdf_path:="/tmp/v10_bimanual.urdf"
```

---

### Gravity Compensation Parameters

| Parameter          | Type   | Default                  | Description                                                          |
| ------------------ | ------ | ------------------------ | -------------------------------------------------------------------- |
| `mode`             | string | `bimanual`               | Control mode: `bimanual`, `left_only`, `right_only`                  |
| `leader_urdf_path` | string | `/tmp/v10_bimanual.urdf` | **Required**, path to the leader URDF                                |
| `g_scale`          | double | `0.9`                    | Gravity scaling factor (<1.0 feels lighter, >1.0 feels upward drift) |
| `kd_damp`          | double | `0.0`                    | Damping coefficient to suppress oscillations                         |
| `kp_hold`          | double | `0.0`                    | Position-hold stiffness when stationary                              |
| `vel_hold_thresh`  | double | `0.02`                   | Velocity threshold to activate position holding (rad/s)              |
| `gdir`             | array  | `[0.0, -9.81, 0.0]`      | Gravity vector                                                       |
| `verbose`          | bool   | `false`                  | Enable detailed torque and joint debug output                        |

---

## General Information

### Build

```bash
cd ~/openarmx_ws
colcon build --packages-select openarmx_teleop_bimanual
source install/setup.bash
```

### Dependencies

* ROS 2 Humble
* `rclcpp`, `std_msgs`
* `openarmx_can`
* `openarmx_bringup` (follower side)

---

### Topics

#### Published Topics

| Topic Name                                    | Message Type                 | Description                                  |
| --------------------------------------------- | ---------------------------- | -------------------------------------------- |
| `/right_forward_position_controller/commands` | `std_msgs/Float64MultiArray` | Follower right arm position commands (8 DOF) |
| `/left_forward_position_controller/commands`  | `std_msgs/Float64MultiArray` | Follower left arm position commands (8 DOF)  |

---

## Safety Notes

⚠️ **Please read carefully before use:**

1. **Workspace clearance**: Ensure sufficient space for both leader and follower arms.
2. **Emergency stop**: Always be ready to trigger the E-stop.
3. **Start follower first**: The follower robot must be running before starting teleoperation.
4. **Move slowly**: Perform initial tests at low speed.

---

## License

Copyright (c) Chengdu Changshu Robot Co., Ltd.

---

## Authors

* **Zhang Li**
* Organization: Chengdu Changshu Robot Co., Ltd.
* Website: [https://openarmx.com/](https://openarmx.com/)

---

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
