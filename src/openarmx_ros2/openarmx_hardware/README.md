# openarmx_hardware

English | [简体中文](README_CN.md)

ROS 2 `hardware_interface::SystemInterface` plugin for the OpenArmX V10 arm that drives Robstride motors through the `openarmx_can` library.

## Features
- Plugin class `openarmx_hardware/OpenArmX_v10HW` (see `openarmx_hardware.xml`) for use with `ros2_control`.
- Supports a 7-DOF arm plus an optional gripper (enabled by default); joint names are generated as `openarmx_<prefix>joint{1..7}` and `openarmx_<prefix>finger_joint1`.
- MIT motion control mode (default) or CiA402 CSP mode, with per-joint KP/KD motion gains.
- Dynamic KP/KD parameters exposed via the node `openarmx_<prefix>hardware_params`; parameters `kp_joint1..8` and `kd_joint1..8` can be changed at runtime (index 8 is the gripper when present).
- CAN socket transport with optional CAN-FD, Robstride motor typing/IDs baked in, and direction multipliers set to -1.0 for all joints.
- Gripper mapping between joint range 0–0.044 m and motor radians (0 to 1.0472).

## Build
Prerequisites: ROS 2 (rclcpp, rclcpp_lifecycle, hardware_interface, pluginlib) and `openarmx_can`.

```bash
colcon build --packages-select openarmx_hardware
source install/setup.bash
```

**Note:** openarmx_hardware depends on openarmx_can, so you need to compile openarmx_can first and then compile openarmx_hardware.

## ros2_control usage
Declare the plugin in your URDF/xacro hardware block:

```xml
<ros2_control name="openarmx" type="system">
  <hardware>
    <plugin>openarmx_hardware/OpenArmX_v10HW</plugin>
    <param name="can_interface">can0</param>
    <param name="arm_prefix"></param>        <!-- e.g., left_ / right_ for bimanual -->
    <param name="hand">true</param>          <!-- enable gripper -->
    <param name="can_fd">false</param>       <!-- enable CAN-FD if the bus supports it -->
    <param name="control_mode">mit</param>   <!-- mit (default) or csp -->
  </hardware>
  <!-- joint definitions go here -->
</ros2_control>
```

Configuration parameters (hardware tag):
- `can_interface` (string, default `can0`): CAN socket name.
- `arm_prefix` (string, default empty): prepended to joint names for multi-arm setups.
- `hand` (bool, default `true`): include the gripper joint.
- `can_fd` (bool, default `false`): toggles CAN-FD initialization.
- `control_mode` (string, default `mit`): `mit` uses motion control with KP/KD; `csp` sends CiA402 target positions.

Runtime KP/KD parameters (node `openarmx_<prefix>hardware_params`):
- Defaults: KP = `[50, 50, 50, 50, 10, 10, 10, 50]`, KD = `[2.5, 2.5, 2.5, 2.5, 0.5, 0.5, 0.5, 2.5]`.
- Update example: `ros2 param set openarmx_hardware_params kp_joint1 80.0`.

## Runtime behavior
- On activation: switches callbacks to state mode, enables motors, and drives to zero pose (plus gripper home if enabled). CSP mode also sets speed/current limits before enabling.
- State interfaces: position/velocity/effort for each joint; gripper velocity/effort are currently reported as zero.
- Command interfaces: position/velocity/effort for each joint; MIT mode uses motion control, CSP sends target positions.
- Debug logging: first ~50 read cycles print raw motor vs. published joint values to help diagnose direction/offset issues.

## Notes
- Direction multipliers are `-1` for all arm joints to align with URDF axes; keep this in mind when interpreting signs.
- Gripper is assumed to be Robstride (`RS00`) with CAN ID 0x08; adjust upstream if hardware differs.

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