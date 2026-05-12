# OpenArmX ROS 2 核心库

[English](README.md) | 简体中文

OpenArmX 的基础 ROS 2 包，搭配 `openarmx_description`（URDF/xacro/mesh）即可完成机械臂的基础建模与运动控制。

## 包含内容
- `openarmx`：元包，聚合核心组件。
- `openarmx_hardware`：`ros2_control` 硬件插件 `openarmx_hardware/OpenArm_v10HW`，通过 `openarmx_can` 驱动机械臂与夹爪。
- `openarmx_bringup`：启动文件、RViz 配置、夹爪操作指南。
- `openarmx_bimanual_moveit_config`：双臂 MoveIt 配置，依赖 `openarmx_description`。
- 额外仓库：`openarmx_minimal.repos`（仅描述包）或 `openarmx.repos`（包含遥操作、工具、参数管理器）。

## 环境要求
- Ubuntu 22.04，ROS 2 Humble。
- 构建：`colcon`、`ament_cmake`、C++17 工具链。
- ROS 依赖：`rclcpp`、`pluginlib`、`hardware_interface`/`ros2_control`，以及 MoveIt（用于 MoveIt 配置）。
- 系统：支持 SocketCAN（建议安装 `can-utils`，可选 `python-can` 用于脚本）。
- 实机：Robstride 电机可通过 CAN（默认 `can0`）访问。

## 工作区准备
```bash
# 安装 vcs 工具
sudo apt-get install python3-vcstool -y

mkdir -p ~/openarmx_ws/src && cd ~/openarmx_ws/src
git clone git@github.com:openarmx/openarmx_ros2.git
# 拉取必须的描述包或完整可选包
vcs import < openarmx_ros2/openarmx_minimal.repos
# 或者：vcs import < openarmx_ros2/openarmx.repos
rosdep install --from-paths . --ignore-src -r -y
```

## 编译
```bash
cd ~/openarmx_ws
colcon build
source install/setup.bash
```

## 运行示例
```bash
# 实机启动，一键启动脚本
/home/openarmx/openarmx_ws/src/openarmx_ros2/openarmx_bimanual_moveit_config/run_bimanual_moveit_with_can2.0.sh
# 仿真模式
/home/openarmx/openarmx_ws/src/openarmx_ros2/openarmx_bimanual_moveit_config/run_bimanual_moveit_sim.sh
```

## 许可证

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

## 作者

- **Zhang Li** (张力)
- 公司: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- 网站: https://openarmx.com/

## 版本

**当前版本**：6.0.0

## 致谢

本包是 OpenArmX 机器人平台生态系统的一部分，专为协作机器人领域的研究和工业应用而开发。

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