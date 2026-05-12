# CAN接口配置

## SocketCAN接口配置详解

本文档详细说明如何在Linux下配置和管理SocketCAN接口。


##  SocketCAN简介

### 什么是SocketCAN

SocketCAN是Linux内核中的CAN总线协议族,将CAN设备抽象为网络设备。

**优势:**
- ✅ 统一的Socket API
- ✅ 与网络工具兼容(ip命令)
- ✅ 内核空间处理,高性能
- ✅ 支持多种CAN硬件

### 工作原理

```
应用程序
    ↕ Socket API
SocketCAN内核模块
    ↕ 驱动接口
硬件驱动(PCAN, USBCAN等)
    ↕ USB/SPI/...
CAN硬件设备
```


##  CAN模块加载

### 检查内核支持

```bash
# 检查内核是否支持CAN
zcat /proc/config.gz | grep CAN

# 应该看到:
# CONFIG_CAN=m
# CONFIG_CAN_RAW=m
# CONFIG_CAN_BCM=m
# ...
```

### 加载CAN模块

```bash
# 加载CAN核心模块
sudo modprobe can

# 加载CAN原始协议
sudo modprobe can_raw

# 加载CAN设备驱动
sudo modprobe can_dev

# 加载虚拟CAN(测试用)
sudo modprobe vcan

# 验证模块已加载
lsmod | grep can
```

**输出示例:**
```
can_raw                20480  0
can                    24576  1 can_raw
can_dev                28672  0
vcan                   16384  0
```

### 开机自动加载

```bash
# 编辑modules文件
sudo vim /etc/modules

# 添加以下行:
can
can_raw
can_dev

# 保存退出
# 重启后自动加载
```


##  CAN接口配置命令

### 基本命令格式

**设置CAN参数:**
```bash
sudo ip link set <device> type can <options>
```

**启动接口:**
```bash
sudo ip link set <device> up
```

**关闭接口:**
```bash
sudo ip link set <device> down
```

### 常用参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `bitrate` | 波特率(bps) | `bitrate 1000000` |
| `sample-point` | 采样点位置(0.0-1.0) | `sample-point 0.875` |
| `sjw` | 同步跳转宽度 | `sjw 1` |
| `restart-ms` | 自动重启延迟(ms) | `restart-ms 100` |
| `fd on` | 启用CAN FD | `fd on dbitrate 5000000` |


##  配置can0和can1

### 基本配置

**配置can0:**
```bash
# 设置参数
sudo ip link set can0 type can bitrate 1000000

# 启动接口
sudo ip link set can0 up

# 检查状态
ip link show can0
```

**配置can1:**
```bash
sudo ip link set can1 type can bitrate 1000000
sudo ip link set can1 up
ip link show can1
```

### 使用Python脚本配置(推荐)

**OpenArmX提供的配置脚本:**

```bash
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com

# 启动所有CAN接口
python3 en_all_can.py

# 查看脚本内容
cat en_all_can.py
```

**脚本内容示例:**
```python
#!/usr/bin/env python3
import os

# 配置can0
os.system('sudo ip link set can0 type can bitrate 1000000')
os.system('sudo ip link set can0 up')

# 配置can1
os.system('sudo ip link set can1 type can bitrate 1000000')
os.system('sudo ip link set can1 up')

print("CAN接口已启动")
print("can0: 1 Mbps")
print("can1: 1 Mbps")
```

### 高级配置选项

**设置采样点:**
```bash
sudo ip link set can0 type can bitrate 1000000 sample-point 0.875
```

**启用自动重启:**
```bash
# Bus-Off后自动重启(延迟100ms)
sudo ip link set can0 type can bitrate 1000000 restart-ms 100
```

**设置三次采样:**
```bash
sudo ip link set can0 type can bitrate 1000000 triple-sampling on
```


##  查看CAN接口状态

### 基本状态查看

```bash
# 查看接口列表
ip link show | grep can

# 查看特定接口
ip link show can0

# 详细状态
ip -details link show can0
```

**输出示例:**
```
3: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP mode DEFAULT group default qlen 10
    link/can
    can state ERROR-ACTIVE (berr-counter tx 0 rx 0) restart-ms 0
	  bitrate 1000000 sample-point 0.875
	  tq 62 prop-seg 6 phase-seg1 7 phase-seg2 2 sjw 1
	  pcan_usb: tseg1 1..16 tseg2 1..8 sjw 1..4 brp 1..64 brp-inc 1
	  clock 16000000
```

**关键字段解释:**
- `<NOARP,UP,LOWER_UP,ECHO>`: 接口标志
  - `UP`: 接口已启动
  - `LOWER_UP`: 物理层连接正常
- `state ERROR-ACTIVE`: CAN状态(正常)
- `berr-counter tx 0 rx 0`: 错误计数器(应为0或很小)
- `bitrate 1000000`: 波特率1Mbps
- `sample-point 0.875`: 采样点位置

### 统计信息查看

```bash
# 查看收发统计
ip -s link show can0

# 详细统计
ip -s -s link show can0
```

**输出示例:**
```
3: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP mode DEFAULT group default qlen 10
    link/can
    RX: bytes  packets  errors  dropped overrun mcast
    1248       156      0       0       0       0
    TX: bytes  packets  errors  dropped carrier collsns
    1040       130      0       0       0       0
```

**关键指标:**
- `RX packets`: 接收的CAN帧数
- `TX packets`: 发送的CAN帧数
- `errors`: 错误帧数(应该很小)
- `dropped`: 丢弃帧数


##  CAN接口管理

### 重启CAN接口

```bash
# 方法1: 先down再up
sudo ip link set can0 down
sudo ip link set can0 up

# 方法2: 修改参数会自动重启
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
```

### 手动重启(Bus-Off恢复)

```bash
# 当接口处于Bus-Off状态时
sudo ip link set can0 type can restart
```

### 关闭CAN接口

```bash
# 使用Python脚本(推荐)
python3 ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com/dis_all_can.py

# 或手动关闭
sudo ip link set can0 down
sudo ip link set can1 down
```


##  波特率配置

### 标准波特率

OpenArmX使用**1 Mbps**波特率:

```bash
sudo ip link set can0 type can bitrate 1000000
```

**CAN 2.0 常用波特率:**
| 波特率 | 最大总线长度 | 适用场景 |
|--------|-------------|---------|
| 1 Mbps | <40m | 短距离高速(OpenArmX使用) |
| 500 kbps | <100m | 中速应用 |
| 250 kbps | <200m | 汽车CAN |
| 125 kbps | <500m | 长距离低速 |

### 降低波特率(故障排查)

如果通信不稳定,可尝试降低波特率:

```bash
# 关闭接口
sudo ip link set can0 down

# 设置为500kbps
sudo ip link set can0 type can bitrate 500000

# 重新启动
sudo ip link set can0 up
```

**⚠️ 注意:** 降低波特率需要电机也支持,Robstride电机默认1Mbps。

### 自定义位时序

**高级用户:**

```bash
# 手动设置位时序参数
sudo ip link set can0 type can \
    tq 62 \
    prop-seg 6 \
    phase-seg1 7 \
    phase-seg2 2 \
    sjw 1
```

**一般不需要手动设置,使用bitrate参数即可**


##  错误处理

### CAN状态机

CAN控制器有三种状态:

```
ERROR-ACTIVE (正常)
    ↓ 错误增加
ERROR-PASSIVE (被动错误)
    ↓ 错误继续增加
BUS-OFF (总线关闭)
```

**查看当前状态:**
```bash
ip -details link show can0 | grep state

# 正常: state ERROR-ACTIVE
# 警告: state ERROR-PASSIVE
# 严重: state BUS-OFF
```

### 错误计数器

```bash
# 查看错误计数器
ip -details link show can0 | grep berr

# 输出示例:
# berr-counter tx 0 rx 0

# tx: 发送错误计数
# rx: 接收错误计数
```

**正常范围:**
- 0-95: ERROR-ACTIVE
- 96-127: ERROR-PASSIVE
- \>127: BUS-OFF

### Bus-Off恢复

**自动恢复(推荐):**
```bash
# 设置自动重启
sudo ip link set can0 type can bitrate 1000000 restart-ms 100
sudo ip link set can0 up

# 发生Bus-Off后,100ms自动重启
```

**手动恢复:**
```bash
# 检测到Bus-Off
ip link show can0
# can0: ... state BUS-OFF

# 手动重启
sudo ip link set can0 type can restart
```


##  虚拟CAN(vcan)

### 创建虚拟CAN接口

**用于测试和开发:**

```bash
# 加载vcan模块
sudo modprobe vcan

# 创建vcan0
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up

# 查看
ip link show vcan0
```

### 使用vcan测试

```bash
# 终端1: 监听
candump vcan0

# 终端2: 发送
cansend vcan0 123#DEADBEEF

# 终端1会显示接收到的帧
```

### 删除vcan

```bash
sudo ip link delete vcan0
```


##  网络命名空间(高级)

### 隔离CAN接口

```bash
# 创建网络命名空间
sudo ip netns add can_test

# 将can0移到命名空间
sudo ip link set can0 netns can_test

# 在命名空间中配置
sudo ip netns exec can_test ip link set can0 type can bitrate 1000000
sudo ip netns exec can_test ip link set can0 up

# 在命名空间中运行程序
sudo ip netns exec can_test candump can0
```

**一般用户无需使用,仅高级测试场景**


##  性能优化

### 接收缓冲区大小

```bash
# 增加接收队列长度
sudo ip link set can0 txqueuelen 1000
```

### CPU亲和性

```bash
# 将CAN中断绑定到特定CPU核心
# 查看中断号
cat /proc/interrupts | grep can

# 设置CPU亲和性(假设中断号为45,绑定到CPU2)
echo 4 | sudo tee /proc/irq/45/smp_affinity
```

### 实时优先级

```bash
# 提升CAN处理线程优先级(需要RT内核)
sudo chrt -f 80 -p $(pgrep irq/.*can)
```


##  故障排查

### 设备不存在

**症状:**
```bash
$ sudo ip link set can0 up
Cannot find device "can0"
```

**解决:**
```bash
#  检查USB设备
lsusb

#  检查dmesg
dmesg | tail -50 | grep -i can

#  重新插拔USB

#  检查驱动模块
lsmod | grep peak  # PCAN设备
lsmod | grep gs_usb  # 其他设备
```

### 权限不足

**症状:**
```bash
$ ip link set can0 up
RTNETLINK answers: Operation not permitted
```

**解决:**
```bash
# 方法1: 使用sudo
sudo ip link set can0 up

# 方法2: 添加用户到netdev组
sudo usermod -a -G netdev $USER
# 重新登录生效
```

### 波特率设置失败

**症状:**
```bash
$ sudo ip link set can0 type can bitrate 1000000
RTNETLINK answers: Invalid argument
```

**解决:**
```bash
#  检查接口是否已关闭
sudo ip link set can0 down

#  重新设置
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

#  检查硬件是否支持该波特率
ip -details link show can0
```


##  监控和调试

### 实时监控脚本

```bash
#!/bin/bash
# 保存为 monitor_can.sh

while true; do
    clear
    echo "========== CAN接口状态 =========="
    date
    echo ""

    echo "can0:"
    ip -s link show can0 | grep -A 4 "can0:"
    echo ""

    echo "can1:"
    ip -s link show can1 | grep -A 4 "can1:"
    echo ""

    sleep 1
done
```

**使用:**
```bash
chmod +x monitor_can.sh
./monitor_can.sh
```

### 错误日志记录

```bash
# 记录CAN统计到文件
while true; do
    echo "$(date): $(ip -s link show can0)" >> can0_stats.log
    sleep 60
done &
```


##  自动化配置

### 创建systemd服务

```bash
# 创建服务文件
sudo vim /etc/systemd/system/openarmx-can.service
```

**内容:**
```ini
[Unit]
Description=OpenArmX CAN Interface Setup
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'ip link set can0 type can bitrate 1000000 && ip link set can0 up'
ExecStart=/bin/bash -c 'ip link set can1 type can bitrate 1000000 && ip link set can1 up'
ExecStop=/bin/bash -c 'ip link set can0 down'
ExecStop=/bin/bash -c 'ip link set can1 down'

[Install]
WantedBy=multi-user.target
```

**启用服务:**
```bash
# 重新加载systemd
sudo systemctl daemon-reload

# 启用服务(开机自启)
sudo systemctl enable openarmx-can.service

# 启动服务
sudo systemctl start openarmx-can.service

# 查看状态
sudo systemctl status openarmx-can.service
```

### udev规则(自动配置)

```bash
# 创建udev规则
sudo vim /etc/udev/rules.d/99-openarmx-can.rules
```

**内容:**
```
# PCAN-USB设备插入时自动配置
ACTION=="add", SUBSYSTEM=="net", KERNEL=="can*", RUN+="/bin/bash -c 'sleep 1 && ip link set $name type can bitrate 1000000 && ip link set $name up'"
```

**重新加载udev:**
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```


##  总结

### 常用命令速查

```bash
# 配置CAN接口
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

# 查看状态
ip link show can0
ip -s link show can0

# 重启接口
sudo ip link set can0 down
sudo ip link set can0 up

# 关闭接口
sudo ip link set can0 down

# 监听总线
candump can0

# 查看错误
ip -details link show can0 | grep -E 'state|berr'
```

### 推荐配置

**OpenArmX标准配置:**
```bash
# can0和can1都使用:
bitrate: 1000000 (1 Mbps)
sample-point: 0.875 (自动)
restart-ms: 100 (自动恢复)
```

### 下一步

完成CAN接口配置后,请继续:
- **03_电机ID配置.md** - 设置电机ID和零位
- **04_硬件故障排查.md** - 硬件问题诊断


*本文档版本: v1.0*
*最后更新: 2025年10月19日*
*成都长数机器人有限公司*
