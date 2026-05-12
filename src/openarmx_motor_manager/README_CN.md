# OpenArmX Motor Manager

[English](README.md) | 简体中文

OpenArmX 多机器人电机管理系统 - 基于 PySide6 的图形化双臂机器人控制工具。

## 概述

`openarmx_motor_manager` 是一个用于管理和控制 OpenArmX 双臂机器人的桌面应用程序。该系统支持同时管理多个机器人,提供直观的图形界面来执行电机控制、状态监控和 CAN 接口管理等操作。

## 功能特性

### 多机器人管理
- 支持同时连接和管理多个双臂机器人
- 每个机器人以独立的标签页形式展示
- 自动检测和配对 CAN 接口（can0-can1, can2-can3, ...）
- 支持手动配置 CAN 通道

### CAN 接口管理
- 一键启动/禁用所有 CAN 接口
- 自动检测真实 CAN 接口（过滤虚拟接口）
- 查看 CAN 接口状态和比特率
- 支持 sudo 密码自动输入

### 电机控制
- **启用所有电机** - 批量使能双臂所有电机
- **禁用所有电机** - 批量停止双臂所有电机
- **回零** - 控制所有电机回到零位
- **设置零点** - 将当前位置设置为电机零点
- **单电机测试** - MIT 模式下精确控制单个电机
- **全部电机测试** - 执行简单的运动测试

### 电机状态监控
- 实时显示电机状态（位置、速度、扭矩、温度）
- 模式状态指示（Motor模式/Reset模式/Cali模式）
- 故障状态监控

### 多语言支持
- 中文 (zh_CN)
- English (en_US)
- 日本語 (ja_JP)
- Русский (ru_RU)

## 项目结构

```
openarmx_motor_manager/
├── GUI_MultiRobot.py          # 程序入口
├── __init__.py                # 包初始化
├── requirements.txt           # 依赖列表
├── config/
│   ├── config.yaml            # 配置文件
│   ├── config_manager.py      # 配置管理器
│   └── script_finder.py       # 脚本查找器
├── ui/
│   ├── MainUI_MultiRobot.py   # 主界面
│   ├── RobotPage.py           # 机器人控制页面
│   ├── RobotWorker.py         # 工作线程
│   ├── SingleMotorTestDialog.py  # 单电机测试对话框
│   ├── SettingsDialog.py      # 设置对话框
│   ├── ConfigDialog.py        # 配置对话框
│   ├── translations.yaml      # 多语言翻译文件
│   ├── ui/                    # Qt Designer UI 文件
│   │   ├── MainUI.ui
│   │   ├── TestMotorUI.ui
│   │   ├── ui_MainUI.py
│   │   └── ui_TestMotorUI.py
│   └── texture/               # 图标资源
│       ├── icon.ico
│       └── icon.png
├── utils/
│   └── can_detector.py        # CAN 接口检测器
└── scripts/                   # 命令行脚本
    ├── en_all_can.py          # 启用所有 CAN 接口
    ├── dis_all_can.py         # 禁用所有 CAN 接口
    ├── en_all_motors.py       # 使能所有电机
    ├── dis_all_motors.py      # 停止所有电机
    ├── check_motor_status.py  # 检查电机状态
    ├── control_motor_gohome.py  # 电机回零
    ├── set_motor_zero.py      # 设置零点
    ├── test_motor_one_CSP.py  # 单电机 CSP 模式测试
    ├── test_motor_one_MIT.py  # 单电机 MIT 模式测试
    ├── test_motor_one_by_one.py  # 逐个电机测试
    └── test_motor_all_random.py  # 全部电机随机测试
```

## 安装

### 依赖项

```bash
pip install -r requirements.txt
```

主要依赖：
- PySide6 >= 6.5.0
- PyYAML >= 6.0
- openarmx_arm_driver >= 1.1.5
- python-can >= 4.0.0

### 系统要求
- Linux 操作系统（需要 CAN 接口支持）
- Python 3.8+
- CAN 硬件设备（如 USB-CAN 适配器）

## 使用方法

### 启动 GUI 应用

```bash
cd /path/to/openarmx_motor_manager
python3 GUI_MultiRobot.py
```

### 快速开始

1. **启动 CAN 接口**
   - 菜单栏 → CAN → 启动 CAN 接口
   - 首次使用需要输入 sudo 密码

2. **添加机器人**
   - 菜单栏 → 机器人 → 添加机器人
   - 选择自动配置或手动配置 CAN 通道
   - 系统需要至少 2 个 CAN 接口来控制一个双臂机器人

3. **控制电机**
   - 在机器人页面中使用电机控制按钮
   - 查看输出栏了解操作结果

### 命令行脚本

也可以直接使用命令行脚本进行操作：

```bash
# 启用所有 CAN 接口
python scripts/en_all_can.py

# 使能所有电机
python scripts/en_all_motors.py

# 检查电机状态
python scripts/check_motor_status.py

# 电机回零
python scripts/control_motor_gohome.py

# 禁用所有电机
python scripts/dis_all_motors.py

# 禁用所有 CAN 接口
python scripts/dis_all_can.py
```

## 配置说明

配置文件位于 `config/config.yaml`，包含以下设置：

```yaml
version: 2.0.0
first_run: false              # 是否首次运行
language: zh_CN               # 界面语言
sudo_password: ""             # sudo 密码（明文存储，请确保安全）
last_can_channels:            # 上次使用的 CAN 通道
  right: can0
  left: can1
scripts:                      # MoveIt 脚本路径
  moveit_sim: ""
  moveit_can: ""
```

## 安全提示

在使用单电机测试功能时，请务必注意：

1. 确保电机安装牢固且周围无人员
2. 操作人员手悬停在紧急停止按钮上方
3. 初次测试时，参数值应小于最大值的 10%
4. 发现异常情况立即按下急停按钮

## API 依赖

本系统基于 `openarmx_arm_driver` 包，主要使用以下功能：

- `Robot` - 双臂机器人控制类
- `get_all_can_interfaces()` - 获取所有 CAN 接口
- `get_available_can_interfaces()` - 获取已启用的 CAN 接口
- `enable_can_interface()` - 启用 CAN 接口
- `disable_can_interface()` - 禁用 CAN 接口
- `check_can_interface_type()` - 检查接口类型（真实/虚拟）
- `verify_can_interface()` - 验证接口状态

## 作者

- **魏林栋** (Wei Lindong)
- 公司：成都长数机器人有限公司
- 网站：https://openarmx.com/

## 版本

v2.0.0

## 许可证

Copyright (c) 成都长数机器人有限公司 (Chengdu Changshu Robot Co., Ltd.)

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