# 01_启动MoveIt

## MoveIt运动规划系统启动

本文档说明如何启动MoveIt进行可视化运动规划。

---

## 1. MoveIt简介

### 1.1 什么是MoveIt

MoveIt是ROS生态中最流行的机器人运动规划框架,提供:
- ✅ 可视化操作界面(RViz)
- ✅ 逆运动学求解
- ✅ 路径规划(OMPL)
- ✅ 碰撞检测
- ✅ 轨迹执行

### 1.2 OpenArmX的MoveIt配置

```
openarm_moveit_config/
├── config/
│   ├── openarm_v10.srdf           # 语义机器人描述
│   ├── kinematics.yaml            # 运动学配置
│   ├── joint_limits.yaml          # 关节限制
│   ├── ompl_planning.yaml         # OMPL规划器
│   └── moveit_controllers.yaml    # 控制器配置
└── launch/
    └── demo.launch.py             # 演示启动文件
```

---

## 2. 启动MoveIt Demo

### 2.1 基本启动

```bash
# 确保已source工作空间
source ~/openarmx_robotstride_ws-cc/install/setup.bash

# 启动MoveIt Demo
ros2 launch openarm_moveit_config demo.launch.py
```

**预期现象:**
- RViz窗口打开
- 显示OpenArmX双臂机器人模型
- 左侧有MoveIt控制面板

### 2.2 启动参数

```bash
# 使用fake硬件(仅仿真,不连接实际电机)
ros2 launch openarm_moveit_config demo.launch.py use_fake_hardware:=true

# 连接实际硬件
ros2 launch openarm_moveit_config demo.launch.py use_fake_hardware:=false
```

### 2.3 启动检查

```bash
# 检查MoveIt节点
ros2 node list | grep move_group

# 应该看到:
# /move_group

# 检查话题
ros2 topic list | grep -E "joint_states|trajectory"

# 检查服务
ros2 service list | grep move_group
```

---

## 3. RViz界面布局

### 3.1 主要组件

```
┌────────────────────────────────────────────────┐
│  RViz - OpenArmX MoveIt                        │
├──────────┬─────────────────────────────────────┤
│          │                                     │
│ MoveIt   │        3D 显示区域                  │
│ 控制面板  │                                     │
│          │     [机器人模型]                     │
│ Planning │                                     │
│ Scene    │                                     │
│ Joints   │                                     │
│          │                                     │
├──────────┴─────────────────────────────────────┤
│  Terminal / Console Output                     │
└────────────────────────────────────────────────┘
```

### 3.2 MoveIt控制面板

**主要选项卡:**
- **Planning**: 运动规划
- **Scene**: 场景编辑
- **Joints**: 关节控制

### 3.3 3D显示设置

**显示项(Displays):**
```
□ RobotModel        - 机器人模型
□ MotionPlanning    - 运动规划
  ├─ Planned Path   - 规划路径
  ├─ Planning Request - 规划请求
  └─ Robot Interaction - 交互标记
□ TF                - 坐标系
□ Grid              - 网格
```

---

## 4. Planning Group选择

### 4.1 可用的Planning Group

OpenArmX定义了以下规划组:

| Group名称 | 包含关节 | 用途 |
|----------|---------|------|
| `left_arm` | left_joint_1~7 | 左臂运动规划 |
| `right_arm` | right_joint_1~7 | 右臂运动规划 |
| `left_gripper` | left_gripper_joint | 左夹爪控制 |
| `right_gripper` | right_gripper_joint | 右夹爪控制 |

### 4.2 选择Planning Group

**在MoveIt面板中:**
```
Planning -> Planning Group: [下拉菜单]
选择: left_arm 或 right_arm
```

---

## 5. 基本运动规划

### 5.1 使用交互标记

**步骤:**
```
1. 在Planning面板选择Planning Group (如left_arm)

2. 在3D视图中会出现交互球和箭头标记

3. 拖动标记到目标位置:
   - 拖动球: 移动末端
   - 拖动箭头: 沿轴移动
   - 拖动环: 旋转

4. 点击"Plan"按钮生成轨迹

5. 检查规划的路径(橙色显示)

6. 如果满意,点击"Execute"执行
```

### 5.2 使用随机目标

```
1. 点击"Planning"标签下的"Select Random Valid Goal"

2. 系统自动生成一个有效的随机目标

3. 点击"Plan"规划

4. 点击"Execute"执行
```

### 5.3 使用预定义姿态

```
1. 在"Goal State"下拉菜单选择预定义姿态
   - <current>: 当前位置
   - home: 零位姿态
   - (其他自定义姿态)

2. 点击"Update"更新目标

3. 点击"Plan"规划轨迹
```

---

## 6. 参数调整

### 6.1 规划器选择

```bash
# 在Planning面板:
Planning Library: OMPL

# 选择不同的规划算法:
- RRTConnect (默认,快速)
- RRT
- PRM
- BKPIECE
- etc.
```

### 6.2 速度缩放

```
Planning -> Velocity Scaling Factor: [滑块]

范围: 0.0 ~ 1.0
- 0.1: 10%速度(安全测试)
- 0.5: 50%速度(常规)
- 1.0: 100%速度(最快)
```

**⚠️ 建议:**
- 首次测试使用0.1-0.3
- 验证轨迹安全后提高速度

### 6.3 加速度缩放

```
Planning -> Acceleration Scaling Factor: [滑块]

范围: 0.0 ~ 1.0
建议与速度缩放同步
```

---

## 7. 安全注意事项

### 7.1 规划前检查

```
□ 确认Planning Group正确
□ 检查当前机器人状态
□ 观察目标位置是否合理
□ 确认速度缩放设置保守
□ 周围无障碍物
```

### 7.2 执行前检查

```
□ 仔细查看规划的轨迹(橙色路径)
□ 确认无碰撞风险
□ 确认运动幅度合理
□ 手放在急停按钮上
□ 准备随时中断
```

### 7.3 执行中监控

```
□ 观察实际运动轨迹
□ 监听异常声音
□ 注意温度变化
□ 发现异常立即急停
```

---

## 8. 常见问题

### 8.1 MoveIt无法启动

**症状:** launch失败

**排查:**
```bash
# 1. 检查工作空间编译
cd ~/openarmx_robotstride_ws-cc
colcon build --packages-select openarm_moveit_config

# 2. 重新source
source install/setup.bash

# 3. 检查依赖
rosdep install --from-paths src --ignore-src -y

# 4. 查看错误日志
ros2 launch openarm_moveit_config demo.launch.py --debug
```

### 8.2 规划失败

**症状:** 点击"Plan"后显示失败

**可能原因:**
- 目标位置超出关节限制
- 目标位置导致自碰撞
- 逆运动学无解

**解决:**
```
1. 调整目标位置(拖动标记)
2. 尝试不同的随机目标
3. 检查关节限制配置
4. 尝试不同的规划器
```

### 8.3 执行失败

**症状:** 规划成功但执行失败

**排查:**
```bash
# 1. 检查控制器状态
ros2 control list_controllers

# 应该看到:
# left_arm_controller[active]
# right_arm_controller[active]

# 2. 检查硬件连接
# - CAN接口是否启动
# - 电机是否使能
# - 电源是否正常

# 3. 查看日志
ros2 topic echo /move_group/result
```

---

## 9. Demo模式 vs 实际硬件

### 9.1 Demo模式(仿真)

```bash
# 启动Demo模式
ros2 launch openarm_moveit_config demo.launch.py use_fake_hardware:=true
```

**特点:**
- ✅ 不需要实际硬件
- ✅ 快速测试规划算法
- ✅ 安全学习操作
- ❌ 无法验证实际运动

### 9.2 实际硬件模式

```bash
# 启动实际硬件模式
ros2 launch openarm_moveit_config demo.launch.py use_fake_hardware:=false
```

**前提条件:**
```
□ CAN接口已启动
□ 电机已使能
□ 零位已设置
□ ros2_control已加载
```

**启动流程:**
```bash
# Terminal 1: 启动CAN和电机
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com
python3 en_all_can.py
python3 en_all_motors.py

# Terminal 2: 启动MoveIt
ros2 launch openarm_moveit_config demo.launch.py use_fake_hardware:=false
```

---

## 10. 高级选项

### 10.1 添加场景对象

```
1. 切换到"Scene"标签

2. 点击"Scene Objects"

3. 添加碰撞对象:
   - Box: 长方体
   - Sphere: 球体
   - Cylinder: 圆柱体
   - Mesh: 网格模型

4. 设置位置和尺寸

5. MoveIt会自动避障规划
```

### 10.2 保存规划轨迹

```
# 在Terminal中记录话题
ros2 bag record /move_group/display_planned_path

# 回放
ros2 bag play <bag文件>
```

### 10.3 自定义配置

```bash
# 编辑运动学配置
vim ~/openarmx_robotstride_ws-cc/src/openarm_moveit_config/config/kinematics.yaml

# 编辑关节限制
vim ~/openarmx_robotstride_ws-cc/src/openarm_moveit_config/config/joint_limits.yaml

# 重新编译
colcon build --packages-select openarm_moveit_config
```

---

## 11. 总结

### 11.1 快速启动流程

```bash
# 1. Source工作空间
source ~/openarmx_robotstride_ws-cc/install/setup.bash

# 2. 启动MoveIt (Demo模式)
ros2 launch openarm_moveit_config demo.launch.py

# 3. 选择Planning Group

# 4. 拖动交互标记

# 5. Plan -> Execute
```

### 11.2 最佳实践

```
✓ 从Demo模式开始熟悉
✓ 首次使用低速度缩放(0.1-0.3)
✓ 仔细检查规划轨迹
✓ 逐步提高速度
✓ 手放在急停按钮上
```

### 11.3 下一步

学习完MoveIt启动后,继续:
- **02_RViz界面操作** - 详细的界面使用
- **03_单臂运动规划** - 单臂轨迹规划技巧
- **04_双臂独立控制** - 协调左右臂运动

---

*本文档版本: v1.0*
*最后更新: 2025年10月19日*
*成都长数机器人有限公司*
