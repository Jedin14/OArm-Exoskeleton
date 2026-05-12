# 02_RViz界面操作

## RViz可视化界面详细操作指南

深入了解RViz界面各组件的使用方法。

---

## 1. RViz界面概览

### 1.1 主界面布局

```
┌─────────────────────────────────────────────────────────┐
│  File  Panels  View  Help                               │
├────────────┬────────────────────────────────────────────┤
│            │                                             │
│  Displays  │                                             │
│  面板      │          3D 视图区域                         │
│            │                                             │
│  MotionPlanning                                          │
│  面板      │          [机器人模型]                        │
│            │                                             │
│  - Planning│                                             │
│  - Scene   │                                             │
│  - Joints  │                                             │
│            │                                             │
│            │                                             │
├────────────┴────────────────────────────────────────────┤
│  Console: [log消息]                                      │
└─────────────────────────────────────────────────────────┘
```

### 1.2 主要区域功能

| 区域 | 功能 |
|------|------|
| **Displays面板** | 显示项管理(开关、配置) |
| **MotionPlanning面板** | 运动规划控制 |
| **3D视图** | 机器人和场景可视化 |
| **Console** | 日志和状态输出 |
| **工具栏** | 视图控制、交互工具 |

---

## 2. Displays面板详解

### 2.1 显示项结构

```
Displays
├─ Global Options
│  └─ Fixed Frame: base_link
├─ Grid
│  ├─ Plane: XY
│  └─ Cell Size: 0.5m
├─ RobotModel
│  ├─ Robot Description: robot_description
│  ├─ Visual Enabled: ✓
│  └─ Collision Enabled: □
├─ TF
│  ├─ Frames: [显示所有坐标系]
│  └─ Show Names: ✓
└─ MotionPlanning
   ├─ Planning Request: ✓
   ├─ Planned Path: ✓
   │  ├─ Trail: ✓ (显示轨迹)
   │  └─ Loop Animation: ✓
   ├─ Robot Interaction: ✓
   └─ Scene Robot
      ├─ Show Robot Visual: ✓
      └─ Alpha: 1.0
```

### 2.2 常用显示项配置

**Grid (网格):**
```
作用: 提供空间参考
配置:
- Plane Cell Count: 20
- Cell Size: 0.5 m
- Color: 浅灰色
- Line Style: Lines
```

**RobotModel (机器人模型):**
```
作用: 显示机器人当前状态
配置:
- Robot Description: robot_description
- Visual Enabled: ✓ (显示外观)
- Collision Enabled: □ (隐藏碰撞体)
- Alpha: 1.0 (不透明)
```

**TF (坐标系):**
```
作用: 显示坐标变换关系
配置:
- Show Axes: ✓ (显示坐标轴)
- Show Names: ✓ (显示名称)
- Marker Scale: 0.3 (轴长度)
- Update Interval: 0.05 s

重要坐标系:
- base_link: 机器人基座
- left_link_7: 左臂末端
- right_link_7: 右臂末端
- world: 世界坐标系
```

**MotionPlanning:**
```
作用: 运动规划可视化
重要子项:
- Planned Path: 显示规划的轨迹(橙色)
- Planning Request: 显示目标姿态
- Robot Interaction: 交互标记(球和箭头)
```

### 2.3 显示项开关

**快速开关:**
- 点击显示项左侧的复选框
- ✓ 启用
- □ 禁用

**推荐配置:**
```
正常使用:
✓ Grid
✓ RobotModel
✓ MotionPlanning
□ TF (初次使用可开启学习)

调试时:
✓ Grid
✓ RobotModel
✓ MotionPlanning
✓ TF (查看坐标系)
□ Grid (可选关闭,减少干扰)
```

---

## 3. MotionPlanning面板详解

### 3.1 Planning标签

#### 3.1.1 Planning Group

```
┌─────────────────────────────┐
│ Planning Group:             │
│ [left_arm ▼]                │
└─────────────────────────────┘

可选项:
- left_arm: 左臂7关节
- right_arm: 右臂7关节
- left_gripper: 左夹爪
- right_gripper: 右夹爪
```

**作用:** 选择要规划的关节组

#### 3.1.2 Query区域

```
┌──────────────────────────────────┐
│ Query                            │
│                                  │
│ ┌─ Goal State ────────────────┐ │
│ │ <current> ▼                 │ │
│ │ [Update] [Random Valid Goal]│ │
│ └─────────────────────────────┘ │
│                                  │
│ ┌─ Start State ───────────────┐ │
│ │ <current>                   │ │
│ │ [Update]                    │ │
│ └─────────────────────────────┘ │
└──────────────────────────────────┘
```

**Goal State:** 目标状态
- 下拉菜单选择预定义姿态
- 或使用交互标记手动设置

**Start State:** 起始状态
- 通常使用 `<current>` (当前位置)

**按钮:**
- `Update`: 更新状态
- `Random Valid Goal`: 生成随机有效目标

#### 3.1.3 Planning Library

```
┌──────────────────────┐
│ Planning Library     │
│ [OMPL ▼]             │
│                      │
│ Planning Algorithm:  │
│ [RRTConnect ▼]       │
└──────────────────────┘

OMPL算法:
- RRTConnect (默认,快速)
- RRT
- RRTstar (优化路径)
- PRM
- BKPIECE
- LBKPIECE
- EST
- KPIECE
```

**推荐:**
- **RRTConnect**: 快速,适合大部分场景
- **RRTstar**: 路径更优,但速度慢
- **PRM**: 多次规划,环境不变

#### 3.1.4 Planning参数

```
┌────────────────────────────────┐
│ Planning Time (s): 5.0         │
│ [滑块: 1.0 ─────●──── 30.0]    │
│                                │
│ Planning Attempts: 10          │
│                                │
│ Velocity Scaling: 0.1          │
│ [滑块: 0.0 ●────────── 1.0]    │
│                                │
│ Acceleration Scaling: 0.1      │
│ [滑块: 0.0 ●────────── 1.0]    │
└────────────────────────────────┘
```

**参数说明:**
- **Planning Time**: 规划超时(秒)
  - 简单场景: 1-5秒
  - 复杂场景: 10-30秒

- **Planning Attempts**: 规划尝试次数
  - 默认: 10
  - 增加可提高成功率,但耗时更长

- **Velocity Scaling**: 速度缩放
  - 0.1: 10%速度(安全测试)
  - 0.5: 50%速度(常规)
  - 1.0: 100%速度(最大)

- **Acceleration Scaling**: 加速度缩放
  - 建议与Velocity Scaling同步

**⚠️ 安全建议:**
```
首次使用: Velocity=0.1, Acceleration=0.1
熟悉后: Velocity=0.3, Acceleration=0.3
验证安全后: Velocity=0.5-1.0
```

#### 3.1.5 控制按钮

```
┌──────────────────────────┐
│ [Plan]                   │
│ [Execute]                │
│ [Plan & Execute]         │
└──────────────────────────┘
```

**按钮功能:**
- **Plan**: 仅规划轨迹,不执行
  - 显示橙色轨迹预览
  - 可以检查是否合理

- **Execute**: 执行已规划的轨迹
  - 需要先Plan成功

- **Plan & Execute**: 规划并立即执行
  - 一键操作
  - ⚠️ 谨慎使用,确保安全

**使用流程:**
```
1. 调整目标位置(拖动交互标记)
   ↓
2. 点击 [Plan]
   ↓
3. 观察橙色轨迹
   ↓
4. 确认无问题
   ↓
5. 点击 [Execute]
```

### 3.2 Scene标签

**场景编辑功能:**

```
┌──────────────────────────────┐
│ Scene Objects                │
│                              │
│ [Add Object ▼]               │
│  ├─ Box                      │
│  ├─ Sphere                   │
│  ├─ Cylinder                 │
│  └─ Mesh                     │
│                              │
│ 已添加对象:                   │
│ □ table (Box)                │
│ □ obstacle_1 (Cylinder)      │
│                              │
│ [Publish Scene]              │
│ [Save Scene]                 │
│ [Load Scene]                 │
└──────────────────────────────┘
```

**添加障碍物示例:**

1. **添加桌子:**
```
点击 [Add Object] → Box
Name: table
Size: X=1.0, Y=0.6, Z=0.05 m
Position: X=0.5, Y=0.0, Z=0.4 m
点击 [Add]
```

2. **添加圆柱障碍物:**
```
点击 [Add Object] → Cylinder
Name: obstacle_1
Radius: 0.1 m
Height: 0.5 m
Position: X=0.4, Y=0.3, Z=0.5 m
点击 [Add]
```

3. **发布场景:**
```
点击 [Publish Scene]
# MoveIt会自动避开障碍物规划
```

**场景保存/加载:**
```bash
# 保存场景
点击 [Save Scene]
# 保存为 .scene 文件

# 加载场景
点击 [Load Scene]
# 选择 .scene 文件
```

### 3.3 Joints标签

**关节滑块控制:**

```
┌────────────────────────────────────┐
│ left_joint_1                       │
│ [-3.14 ───●──────────── 3.14] rad  │
│                                    │
│ left_joint_2                       │
│ [-1.57 ──●────────── 1.57] rad     │
│                                    │
│ left_joint_3                       │
│ [-3.14 ─────────●──── 3.14] rad    │
│                                    │
│ ... (其他关节)                      │
│                                    │
│ [Plan to Target]                   │
└────────────────────────────────────┘
```

**使用方法:**
```
1. 拖动各关节滑块到期望位置
2. 点击 [Plan to Target]
3. 规划到该关节配置
4. 点击 [Execute] 执行
```

**适用场景:**
- 精确关节位置控制
- 避免逆运动学奇异
- 学习关节空间规划

---

## 4. 3D视图操作

### 4.1 视角控制

**鼠标操作:**
```
旋转视角:
- 左键拖动: 绕中心旋转

平移视角:
- 中键(滚轮)拖动: 平移视图
- Shift + 左键拖动: 平移视图

缩放:
- 滚轮滚动: 缩放
- Ctrl + 左键拖动: 缩放
```

**键盘快捷键:**
```
F: Focus on Selection (聚焦到选中物体)
G: Grid开关
R: 重置视角
Z: 切换到俯视图
```

### 4.2 交互标记使用

**标记类型:**

```
┌──────────────────────┐
│                      │
│    [球形标记]         │
│   位置控制            │
│                      │
│   ↑ [箭头标记]       │
│   轴向移动            │
│                      │
│   ⟳ [环形标记]       │
│   旋转控制            │
│                      │
└──────────────────────┘
```

**操作方法:**

1. **位置移动:**
```
拖动球形标记:
- 自由移动末端位置
- 逆运动学自动计算关节角度
```

2. **轴向移动:**
```
拖动红色箭头: X轴移动
拖动绿色箭头: Y轴移动
拖动蓝色箭头: Z轴移动
```

3. **旋转控制:**
```
拖动红色环: 绕X轴旋转
拖动绿色环: 绕Y轴旋转
拖动蓝色环: 绕Z轴旋转
```

**技巧:**
```
精确控制:
- 拖动时观察状态栏数值
- 小幅度拖动,逐步调整
- 配合不同视角操作

避免奇异位形:
- 不要将关节拖到极限位置
- 避免肘部完全伸直
- 保持冗余自由度
```

### 4.3 轨迹可视化

**已规划轨迹显示:**
```
颜色:
- 橙色: 规划的路径
- 绿色: 当前位置
- 半透明: 轨迹上的中间状态

轨迹播放:
- 自动循环播放(如果启用Loop Animation)
- 显示运动过程

配置:
Displays → MotionPlanning → Planned Path
  ├─ Show Trail: ✓ (显示轨迹)
  ├─ Trail Step Size: 1 (轨迹点间隔)
  ├─ Loop Animation: ✓ (循环播放)
  └─ Robot Alpha: 0.5 (透明度)
```

---

## 5. 实用操作技巧

### 5.1 快速规划流程

**标准流程:**
```
1. 选择Planning Group (如left_arm)
2. 拖动交互标记到目标位置
3. 观察机器人实时预览(白色/半透明)
4. 点击 [Plan]
5. 检查橙色轨迹
6. 确认无碰撞无异常
7. 点击 [Execute]
```

**快捷流程(熟练后):**
```
1. 选择Group
2. 拖动标记
3. 点击 [Plan & Execute]
```

### 5.2 避免规划失败

**常见失败原因及对策:**

| 失败原因 | 现象 | 解决方法 |
|---------|------|---------|
| 目标超出工作空间 | "No IK solution" | 将目标移近基座 |
| 关节超限 | "Joint limits violated" | 检查Joint标签限位 |
| 自碰撞 | "State in collision" | 调整姿态避免碰撞 |
| 逆运动学无解 | "IK failed" | 旋转末端姿态或改变接近角度 |
| 超时 | "Timeout" | 增加Planning Time或简化目标 |

**提高成功率:**
```
□ 使用Random Valid Goal测试系统
□ 从当前位置附近开始
□ 避免极限位置
□ 增加Planning Attempts
□ 使用不同的规划算法
```

### 5.3 保存和恢复配置

**保存RViz配置:**
```
File → Save Config As...
保存为: openarmx_moveit.rviz

下次启动:
rviz -d openarmx_moveit.rviz
```

**保存常用姿态:**
```
MotionPlanning → Planning → Goal State
当前姿态 → [Save as named pose]
输入名称: "pick_pose"
下次可直接选择使用
```

---

## 6. 高级功能

### 6.1 多规划组协同

**场景:** 同时规划左右臂

**方法:**
```
1. 规划左臂:
   - Planning Group: left_arm
   - 设置目标
   - Plan & Execute

2. 规划右臂:
   - Planning Group: right_arm
   - 设置目标
   - Plan & Execute

注意:
- 每次只能规划一个Group
- 需要分别执行
```

**同步运动(需编程):**
```python
# 需要通过Python或C++ API实现
# 参考开发者指南
```

### 6.2 路径优化

**启用路径平滑:**
```
Planning → Planning Library → OMPL
勾选: Simplify Solution

效果:
- 移除冗余路径点
- 优化轨迹平滑度
- 减少执行时间
```

**使用优化算法:**
```
Planning Algorithm: RRTstar
或: PRMstar

特点:
- 路径更短
- 更平滑
- 但规划时间更长
```

### 6.3 碰撞检查配置

**调整碰撞检测:**
```
Scene → 右键某对象 → Properties
- Collision Enabled: ✓/□
- Collision Padding: 0.01 m (安全距离)

自碰撞:
Planning → Self-Collision Checking: ✓
```

---

## 7. 常见问题

### 7.1 交互标记不出现

**症状:** 3D视图中无交互球和箭头

**排查:**
```
□ 检查Planning Group是否选择
□ Displays → MotionPlanning → Robot Interaction: ✓
□ 点击Query区域的 [Update]
□ 重启RViz
```

### 7.2 规划的轨迹不显示

**症状:** Plan后无橙色轨迹

**排查:**
```
□ Displays → MotionPlanning → Planned Path: ✓
□ Displays → MotionPlanning → Planned Path → Show Trail: ✓
□ 检查Console是否有错误
□ 重新Plan
```

### 7.3 Execute无反应

**症状:** 点击Execute后机器人不动

**排查:**
```bash
# 1. 检查控制器
ros2 control list_controllers

# 应该看到:
# left_arm_controller [active]
# right_arm_controller [active]

# 2. 检查话题
ros2 topic echo /left_arm_controller/follow_joint_trajectory/goal

# 3. 检查硬件连接
python3 check_motor_status.py

# 4. 查看MoveIt日志
# Console窗口中的错误信息
```

---

## 8. 总结

### 8.1 RViz关键操作

```
基本操作:
✓ 选择Planning Group
✓ 拖动交互标记设置目标
✓ 点击Plan规划轨迹
✓ 检查橙色轨迹
✓ 点击Execute执行

安全原则:
✓ 首次使用低速度缩放(0.1)
✓ 总是先Plan再Execute
✓ 仔细观察规划的轨迹
✓ 手放在急停上
```

### 8.2 界面导航速查

| 想要... | 位置 |
|--------|------|
| 选择机械臂 | Planning → Planning Group |
| 设置目标 | 拖动3D视图中的交互标记 |
| 规划轨迹 | Planning → [Plan] |
| 执行运动 | Planning → [Execute] |
| 调速度 | Planning → Velocity Scaling |
| 添加障碍物 | Scene → Add Object |
| 手动关节控制 | Joints → 拖动滑块 |
| 保存姿态 | Planning → Save as named pose |

### 8.3 下一步

熟悉RViz界面后:
- **03_单臂运动规划** - 单臂轨迹规划技巧
- **04_双臂独立控制** - 双臂协调运动
- **06_轨迹录制与回放** - 复杂动作录制

---

*本文档版本: v1.0*
*最后更新: 2025年10月19日*
*成都长数机器人有限公司*
