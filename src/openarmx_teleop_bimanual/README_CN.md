# OpenArm 遥操作包 (openarmx_teleop_bimanual)

[English](README.md) | 简体中文

这是一个ROS2包，用于实现OpenArmX机械臂的遥操作功能。它包含两种模式：

1.  **无重力补偿模式**：支持双臂遥操作，主控臂电机不使能，可直接手动拖动。
2.  **带重力补偿模式**：支持单臂遥操作，主控臂会主动补偿自身重力，实现“无重力”的拖动体验。

---

## 模式一：双臂遥操作（无重力补偿）

此模式下，您可以同时控制左右两条机械臂。主动端电机不使能，允许您轻松地手动拖动它们，从动端会实时跟随运动。

### 功能特性

✅ **双臂同时控制**
✅ **高实时性**：200Hz控制频率
✅ **手动拖动**：主动端电机不使能，无阻力
✅ **ROS2集成**
✅ **8自由度**：控制7个关节 + 1个夹爪

### 系统架构
```
主动端（Leader）                    从动端（Follower）
┌─────────────────┐                ┌──────────────────┐
│  右臂 (can0)     │                │  右臂 (can2)      │
│  7关节+夹爪      │  ─────ROS2────→│  8DOF控制器        │
└─────────────────┘                └──────────────────┘
┌─────────────────┐                ┌──────────────────┐
│  左臂 (can1)    │                │  左臂 (can3)      │
│  7关节+夹爪      │  ─────ROS2────→│  8DOF控制器       │
└─────────────────┘                └──────────────────┘
```

### 使用方法

#### 步骤1：启动从动端机器人

**重要：必须先启动从动端！**

从动端需要运行在 `forward_position_controller` 模式：
```bash
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller
```

#### 步骤2：启动遥操作节点

在另一个终端启动遥操作节点：
```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual.launch.py
```

**自定义参数启动：**
```bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual.launch.py \
    leader_right_can:=can0 \
    leader_left_can:=can1 \
    follower_right_prefix:=right \
    follower_left_prefix:=left \
    control_rate_hz:=200
```

---

## 模式二：单臂/双臂遥操作（带重力补偿）

此模式下，主动端机械臂会使能电机，并基于URDF模型实时计算和补偿自身的重力力矩。这使得拖动机械臂时感觉不到其本身的重量，大大提升了操作的平顺性和精确性。

### 功能特性

✅ **重力补偿**：实现“无重力”拖动体验
✅ **主动阻尼**：可配置阻尼以增加稳定性
✅ **位置保持**：当手臂静止时可锁定位置
✅ **URDF驱动**：基于精确的机器人模型进行计算

### 使用方法

#### 步骤1：启动从动端机器人

从动端需要运行在 `forward_position_controller` 模式：
```bash
# 启动右臂作为从动端
ros2 launch openarmx_bringup openarmx.bimanual.launch.py \
    right_can_interface:=can2 \
    left_can_interface:=can3 \
    control_mode:=mit \
    robot_controller:=forward_position_controller
```

#### 步骤2：启动带重力补偿的遥操作节点

在另一个终端启动遥操作节点。**注意：** 此模式需要一个URDF文件。

### 先生成 urdf 文件
执行指令：
```
cd {你的工作空间路径}
xacro ./src/openarmx_description/urdf/robot/v10.urdf.xacro  arm_type:=v10 bimanual:=true > /tmp/v10_bimanual.urdf
```

- **启动双臂（带重力补偿，实验性）**
简化启动命令
```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual_with_gravitycomp.launch.py
```

自定义启动参数
```bash
source ~/openarmx_ws/install/setup.bash
ros2 launch openarmx_teleop_bimanual teleop_bimanual_with_gravitycomp.launch.py mode:=bimanual leader_urdf_path:="/tmp/v10_bimanual.urdf"
```

### 重力补偿启动参数说明

| 参数名称 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mode` | string | `bimanual` | 控制模式: `bimanual`, `left_only`, `right_only` |
| `leader_urdf_path` | string | `/tmp/v10_bimanual.urdf` | **必需**，主动端URDF文件路径 |
| `g_scale` | double | `0.9` | 重力补偿缩放系数，<1.0会感觉轻，>1.0会感觉向上飘 |
| `kd_damp` | double | `0.0` | 阻尼系数，增加以抑制震动 |
| `kp_hold` | double | `0.0` | 位置保持刚度，增加以在静止时锁定位置 |
| `vel_hold_thresh` | double | `0.02` | 触发位置保持的速度阈值 (rad/s) |
| `gdir` | array | `[0.0, -9.81, 0.0]` | 重力向量 |
| `verbose` | bool | `false` | 是否打印详细的力矩和关节信息 |


---

## 通用信息

### 编译
```bash
cd ~/openarmx_ws
colcon build --packages-select openarmx_teleop_bimanual
source install/setup.bash
```

### 依赖项
- ROS2 Humble
- rclcpp, std_msgs
- openarmx_can
- openarmx_bringup (从动端)

### 话题说明

#### 发布的话题
| 话题名称 | 消息类型 | 说明 |
|---|---|---|
| `/right_forward_position_controller/commands` | `std_msgs/Float64MultiArray` | 从动端右臂位置命令 (8DOF) |
| `/left_forward_position_controller/commands` | `std_msgs/Float64MultiArray` | 从动端左臂位置命令 (8DOF) |

### 安全注意事项
⚠️ **使用前请注意：**
1. **运动空间**：确保主动端和从动端都有足够的运动空间。
2. **急停按钮**：随时准备按下急停按钮。
3. **从动端先启动**：务必先启动从动端，再启动遥操作节点。
4. **缓慢移动**：初次测试时缓慢移动主动端。

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