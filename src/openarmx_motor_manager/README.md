# OpenArmX Motor Manager

English | [简体中文](README_CN.md)

OpenArmX Multi-Robot Motor Management System - A PySide6-based graphical control tool for dual-arm robots.

## Overview

`openarmx_motor_manager` is a desktop application for managing and controlling OpenArmX dual-arm robots. The system supports simultaneous management of multiple robots, providing an intuitive graphical interface for motor control, status monitoring, and CAN interface management.

## Features

### Multi-Robot Management
- Support for simultaneous connection and management of multiple dual-arm robots
- Each robot displayed in an independent tab
- Automatic detection and pairing of CAN interfaces (can0-can1, can2-can3, ...)
- Manual CAN channel configuration support

### CAN Interface Management
- One-click enable/disable all CAN interfaces
- Automatic detection of real CAN interfaces (filtering virtual interfaces)
- View CAN interface status and bitrate
- Support for automatic sudo password input

### Motor Control
- **Enable All Motors** - Batch enable all motors on both arms
- **Disable All Motors** - Batch stop all motors on both arms
- **Go Home** - Command all motors to return to zero position
- **Set Zero** - Set current position as motor zero point
- **Single Motor Test** - Precise control of individual motors in MIT mode
- **Test All Motors** - Execute simple motion tests

### Motor Status Monitoring
- Real-time display of motor status (position, velocity, torque, temperature)
- Mode status indication (Motor mode/Reset mode/Cali mode)
- Fault status monitoring

### Multi-Language Support
- 中文 (zh_CN)
- English (en_US)
- 日本語 (ja_JP)
- Русский (ru_RU)

## Project Structure

```
openarmx_motor_manager/
├── GUI_MultiRobot.py          # Program entry point
├── __init__.py                # Package initialization
├── requirements.txt           # Dependency list
├── config/
│   ├── config.yaml            # Configuration file
│   ├── config_manager.py      # Configuration manager
│   └── script_finder.py       # Script finder
├── ui/
│   ├── MainUI_MultiRobot.py   # Main interface
│   ├── RobotPage.py           # Robot control page
│   ├── RobotWorker.py         # Worker thread
│   ├── SingleMotorTestDialog.py  # Single motor test dialog
│   ├── SettingsDialog.py      # Settings dialog
│   ├── ConfigDialog.py        # Configuration dialog
│   ├── translations.yaml      # Multi-language translation file
│   ├── ui/                    # Qt Designer UI files
│   │   ├── MainUI.ui
│   │   ├── TestMotorUI.ui
│   │   ├── ui_MainUI.py
│   │   └── ui_TestMotorUI.py
│   └── texture/               # Icon resources
│       ├── icon.ico
│       └── icon.png
├── utils/
│   └── can_detector.py        # CAN interface detector
└── scripts/                   # Command-line scripts
    ├── en_all_can.py          # Enable all CAN interfaces
    ├── dis_all_can.py         # Disable all CAN interfaces
    ├── en_all_motors.py       # Enable all motors
    ├── dis_all_motors.py      # Stop all motors
    ├── check_motor_status.py  # Check motor status
    ├── control_motor_gohome.py  # Motor go home
    ├── set_motor_zero.py      # Set zero point
    ├── test_motor_one_CSP.py  # Single motor CSP mode test
    ├── test_motor_one_MIT.py  # Single motor MIT mode test
    ├── test_motor_one_by_one.py  # Test motors one by one
    └── test_motor_all_random.py  # Test all motors randomly
```

## Installation

### Dependencies

```bash
pip install -r requirements.txt
```

Main dependencies:
- PySide6 >= 6.5.0
- PyYAML >= 6.0
- openarmx_arm_driver >= 1.1.5
- python-can >= 4.0.0

### System Requirements
- Linux operating system (CAN interface support required)
- Python 3.8+
- CAN hardware device (e.g., USB-CAN adapter)

## Usage

### Launch GUI Application

```bash
cd /path/to/openarmx_motor_manager
python3 GUI_MultiRobot.py
```

### Quick Start

1. **Enable CAN Interfaces**
   - Menu bar → CAN → Enable CAN Interfaces
   - First-time use requires sudo password input

2. **Add Robot**
   - Menu bar → Robot → Add Robot
   - Choose automatic or manual CAN channel configuration
   - System requires at least 2 CAN interfaces to control one dual-arm robot

3. **Control Motors**
   - Use motor control buttons in the robot page
   - Check output panel for operation results

### Command-Line Scripts

You can also use command-line scripts directly:

```bash
# Enable all CAN interfaces
python scripts/en_all_can.py

# Enable all motors
python scripts/en_all_motors.py

# Check motor status
python scripts/check_motor_status.py

# Motor go home
python scripts/control_motor_gohome.py

# Disable all motors
python scripts/dis_all_motors.py

# Disable all CAN interfaces
python scripts/dis_all_can.py
```

## Configuration

Configuration file is located at `config/config.yaml` with the following settings:

```yaml
version: 2.0.0
first_run: false              # Whether it's the first run
language: zh_CN               # Interface language
sudo_password: ""             # sudo password (stored in plaintext, ensure security)
last_can_channels:            # Last used CAN channels
  right: can0
  left: can1
scripts:                      # MoveIt script paths
  moveit_sim: ""
  moveit_can: ""
```

## Safety Notice

When using single motor test function, please note:

1. Ensure motors are securely mounted and no personnel are nearby
2. Operator's hand should hover over the emergency stop button
3. For initial testing, parameter values should be less than 10% of maximum
4. Press emergency stop button immediately if any abnormality is detected

## API Dependencies

This system is based on the `openarmx_arm_driver` package, mainly using the following features:

- `Robot` - Dual-arm robot control class
- `get_all_can_interfaces()` - Get all CAN interfaces
- `get_available_can_interfaces()` - Get enabled CAN interfaces
- `enable_can_interface()` - Enable CAN interface
- `disable_can_interface()` - Disable CAN interface
- `check_can_interface_type()` - Check interface type (real/virtual)
- `verify_can_interface()` - Verify interface status

## Author

- **Wei Lindong** (魏林栋)
- Company: Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)
- Website: https://openarmx.com/

## Version

v2.0.0

## License

Copyright (c) Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

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
