# OpenArmX ROS 2 Core

English | [简体中文](README_CN.md)

Core ROS 2 packages for OpenArmX. Combined with `openarmx_description` (URDF/xacro/meshes), they provide the baseline hardware bringup and motion control stack for OpenArmX V10.

## Contents
- `openarmx` – metapackage that pulls the core components below.
- `openarmx_hardware` – `ros2_control` `SystemInterface` plugin (`openarmx_hardware/OpenArm_v10HW`) that drives the arm/gripper via `openarmx_can`.
- `openarmx_bringup` – launch files, RViz config, gripper control guides for running the robot.
- `openarmx_bimanual_moveit_config` – MoveIt config (bimanual example) that depends on `openarmx_description`.
- Additional repos: see `openarmx_minimal.repos` (required `openarmx_description`) or `openarmx.repos` (adds teleop/tools/motor manager).

## Requirements
- Ubuntu 22.04, ROS 2 Humble.
- Build: `colcon`, `ament_cmake`, C++17 toolchain.
- ROS deps: `rclcpp`, `pluginlib`, `hardware_interface`/`ros2_control`, MoveIt stack for the MoveIt config.
- System: SocketCAN enabled (`can-utils`), optional `python-can` for setup scripts.
- Hardware use: Robstride motors reachable on a CAN interface (default `can0`).

## Workspace Setup
```bash
# Install vcs
sudo apt-get install python3-vcstool -y

mkdir -p ~/openarmx_ws/src && cd ~/openarmx_ws/src
git clone git@github.com:openarmx/openarmx_ros2.git
# Fetch the required robot description (minimal) or full optional set
vcs import < openarmx_ros2/openarmx_minimal.repos
# or: vcs import < openarmx_ros2/openarmx.repos
rosdep install --from-paths . --ignore-src -r -y
```

## Build
```bash
cd ~/openarmx_ws
colcon build
source install/setup.bash
```

## Run (examples)
```bash
# real robot, one key run script
/home/openarmx/openarmx_ws/src/openarmx_ros2/openarmx_bimanual_moveit_config/run_bimanual_moveit_with_can2.0.sh
# Simulation mode
/home/openarmx/openarmx_ws/src/openarmx_ros2/openarmx_bimanual_moveit_config/run_bimanual_moveit_sim.sh
```

## License

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## Author

- **Zhang Li** (张力)
- Company: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- Website: https://openarmx.com/

## Version

**Current Version**: 6.0.0

## Acknowledgments

This package is part of the OpenArmX robotic platform ecosystem, developed for research and industrial applications in collaborative robotics.

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