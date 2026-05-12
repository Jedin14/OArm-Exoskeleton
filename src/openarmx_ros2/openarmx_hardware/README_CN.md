# openarmx_hardware 说明

[English](README.md) | 简体中文

面向 OpenArmX V10（Robstride 电机版本）的 ROS 2 `hardware_interface::SystemInterface` 插件，依赖 `openarmx_can` 完成底层 CAN 通信。

## 功能概览
- 插件类 `openarmx_hardware/OpenArmX_v10HW`（见 `openarmx_hardware.xml`），可直接在 `ros2_control` 硬件块中引用。
- 支持 7 关节手臂 + 可选夹爪（默认开启）；关节名自动生成：`openarmx_<prefix>joint{1..7}` 与 `openarmx_<prefix>finger_joint1`。
- 支持 MIT 运动控制模式（默认）与 CiA402 CSP 模式，可针对每个关节设置 KP/KD。
- 动态参数节点 `openarmx_<prefix>hardware_params` 暴露 `kp_joint1..8`、`kd_joint1..8`，可运行时调节（第 8 个为夹爪）。
- 默认使用 CAN Socket（可选 CAN-FD），已内置 Robstride 电机型号与 CAN ID 映射，电机方向系数统一为 -1.0。
- 夹爪在关节位移 0–0.044 m 与电机弧度 0–1.0472 之间完成映射。

## 编译
依赖：ROS 2（rclcpp、rclcpp_lifecycle、hardware_interface、pluginlib）与 `openarmx_can`。

```bash
colcon build --packages-select openarmx_hardware
source install/setup.bash
```

**注意：** openarmx_hardware 依赖 openarmx_can ，所以需要先编译 openarmx_can 再编译 openarmx_hardware。


## 在 ros2_control 中使用
在 URDF/xacro 的硬件配置中声明插件：

```xml
<ros2_control name="openarmx" type="system">
  <hardware>
    <plugin>openarmx_hardware/OpenArmX_v10HW</plugin>
    <param name="can_interface">can0</param>
    <param name="arm_prefix"></param>        <!-- 如双臂可设 left_ / right_ -->
    <param name="hand">true</param>          <!-- 是否启用夹爪 -->
    <param name="can_fd">false</param>       <!-- 总线支持时可开启 CAN-FD -->
    <param name="control_mode">mit</param>   <!-- mit（默认）或 csp -->
  </hardware>
  <!-- 此处补充关节定义 -->
</ros2_control>
```

硬件参数（hardware 标签）：
- `can_interface`（字符串，默认 `can0`）：CAN 口名称。
- `arm_prefix`（字符串，默认空）：关节名前缀，便于多机械臂区分。
- `hand`（布尔，默认 `true`）：是否包含夹爪。
- `can_fd`（布尔，默认 `false`）：是否以 CAN-FD 初始化。
- `control_mode`（字符串，默认 `mit`）：`mit` 走运动控制，`csp` 发送 CiA402 位置参考。

运行时 KP/KD 参数（节点 `openarmx_<prefix>hardware_params`）：
- 默认：KP = `[50, 50, 50, 50, 10, 10, 10, 50]`，KD = `[2.5, 2.5, 2.5, 2.5, 0.5, 0.5, 0.5, 2.5]`。
- 调参示例：`ros2 param set openarmx_hardware_params kp_joint1 80.0`。

## 运行特性
- 激活时：切换回调模式到 STATE，启用电机并回零（夹爪也回到初始位）。CSP 模式会先设置速度/电流限制再启用。
- 状态接口：为每个关节导出位置/速度/力矩；夹爪的速度与力矩当前填 0。
- 指令接口：为每个关节导出位置/速度/力矩；MIT 使用运动控制发送，CSP 发送目标位置帧。
- 调试输出：激活后的前 ~50 次 `read()` 会打印原始电机值与发布的关节值，便于检查方向/零偏。

## 备注
- 方向系数固定为 -1，留意关节符号方向；夹爪默认使用 Robstride（RS00）且 CAN ID 为 0x08，若硬件不同需在上游适配。

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