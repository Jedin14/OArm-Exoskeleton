# CAN总线管理

## CAN总线启动和管理

本文档说明如何使用OpenArmX提供的脚本管理CAN总线。


##  CAN管理脚本概述

### 脚本位置

```bash
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com

ls -l *can.py
# en_all_can.py    - 启动CAN接口
# dis_all_can.py   - 关闭CAN接口
```

### 脚本功能

| 脚本 | 功能 | 使用场景 |
|------|------|---------|
| `en_all_can.py` | 启动can0和can1 | 系统启动时 |
| `dis_all_can.py` | 关闭can0和can1 | 系统关闭时 |


##  启动CAN接口

### 使用en_all_can.py

```bash
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com

# 启动CAN接口
python3 en_all_can.py
```

**输出示例:**
```
启动CAN接口...
设置can0波特率为1Mbps...
启动can0...
设置can1波特率为1Mbps...
启动can1...
CAN接口启动成功!
```

### 脚本内部实现

```python
#!/usr/bin/env python3
import os
import sys

def enable_can():
    """启动CAN接口"""

    # 配置can0
    print("设置can0...")
    os.system('sudo ip link set can0 type can bitrate 1000000')
    os.system('sudo ip link set can0 up')

    # 配置can1
    print("设置can1...")
    os.system('sudo ip link set can1 type can bitrate 1000000')
    os.system('sudo ip link set can1 up')

    print("CAN接口启动成功!")

    # 显示状态
    os.system('ip link show can0 | grep can0')
    os.system('ip link show can1 | grep can1')

if __name__ == '__main__':
    enable_can()
```

### 验证启动成功

```bash
# 检查接口状态
ip link show can0 can1

# 应该看到:
# can0: <NOARP,UP,LOWER_UP,ECHO> ...
# can1: <NOARP,UP,LOWER_UP,ECHO> ...

# 关键字: UP, LOWER_UP
```


##  关闭CAN接口

### 使用dis_all_can.py

```bash
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com

# 关闭CAN接口
python3 dis_all_can.py
```

**输出:**
```
关闭can0...
关闭can1...
CAN接口已关闭
```

### 何时关闭CAN

```
推荐关闭时机:
□ 系统长时间不使用
□ 进行硬件维护
□ 重新配置CAN参数
□ 系统关机前

不建议频繁开关:
✗ 每次测试后关闭(不必要)
✗ 电机测试中间关闭
```


##  自动化管理

### 开机自动启动

**方法1: 添加到.bashrc**

```bash
# 编辑.bashrc
vim ~/.bashrc

# 添加到文件末尾(不推荐,每次终端都执行)
# alias start-can='python3 ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/en_all_can.py'
```

**方法2: 创建systemd服务(推荐)**

```bash
# 创建服务文件
sudo vim /etc/systemd/system/openarmx-can.service

# 内容:
[Unit]
Description=OpenArmX CAN Interface
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /home/你的用户名/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/en_all_can.py
ExecStop=/usr/bin/python3 /home/你的用户名/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/dis_all_can.py

[Install]
WantedBy=multi-user.target

# 启用服务
sudo systemctl enable openarmx-can.service
sudo systemctl start openarmx-can.service
```

### 快捷命令设置

```bash
# 添加alias到.bashrc
echo "alias can-on='python3 ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/en_all_can.py'" >> ~/.bashrc
echo "alias can-off='python3 ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/dis_all_can.py'" >> ~/.bashrc

source ~/.bashrc

# 使用
can-on   # 启动CAN
can-off  # 关闭CAN
```


##  故障排查

### 启动失败

**问题:** `Error: Cannot find device "can0"`

**解决:**
```bash
#  检查USB设备
lsusb | grep -i can

#  检查驱动
lsmod | grep can

#  重新插拔USB
#  重新运行脚本
```

### 权限问题

**问题:** `RTNETLINK answers: Operation not permitted`

**解决:**
```bash
# 方法1: 使用sudo
sudo python3 en_all_can.py

# 方法2: 添加用户到netdev组(一次性)
sudo usermod -a -G netdev $USER
# 重新登录后生效
```

### PCAN设备反向

**症状:** can0和can1顺序错误

**解决:**
```bash
# 重启PCAN模块
sudo rmmod peak_usb
sudo modprobe peak_usb

# 重新运行启动脚本
python3 en_all_can.py
```


##  监控CAN状态

### 实时状态监控

```bash
# 创建监控脚本
cat > ~/monitor_can.sh << 'EOF'
#!/bin/bash
while true; do
    clear
    echo "=== CAN接口状态 ==="
    date
    echo ""
    ip -s link show can0
    echo ""
    ip -s link show can1
    sleep 2
done
EOF

chmod +x ~/monitor_can.sh
./monitor_can.sh
```

### 检查通信

```bash
# 终端1: 监听can0
candump can0

# 终端2: 启动电机
python3 en_all_motors.py

# 终端1应该看到CAN帧流动
```


##  高级配置

### 修改波特率

如果需要使用其他波特率:

```python
# 修改en_all_can.py
# 将1000000改为500000(500kbps)
os.system('sudo ip link set can0 type can bitrate 500000')
os.system('sudo ip link set can1 type can bitrate 500000')
```

### 启用自动重启

```python
# 在en_all_can.py中添加
os.system('sudo ip link set can0 type can bitrate 1000000 restart-ms 100')
os.system('sudo ip link set can1 type can bitrate 1000000 restart-ms 100')

# restart-ms 100: Bus-Off后100ms自动重启
```


##  总结

### 常用命令

```bash
# 启动CAN
python3 en_all_can.py

# 关闭CAN
python3 dis_all_can.py

# 检查状态
ip link show can0 can1

# 查看统计
ip -s link show can0
```

### 最佳实践

```
✓ 使用脚本而非手动命令(一致性)
✓ 系统启动后首先启动CAN
✓ 关机前关闭CAN(可选)
✓ 定期检查错误计数器
✓ 记录异常情况
```


*本文档版本: v1.0*
*最后更新: 2025年10月19日*
*成都长数机器人有限公司*
