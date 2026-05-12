# 01_CAN通信问题

## CAN总线通信故障诊断与解决

解决OpenArmX系统中CAN通信相关的问题。

---

## 1. CAN通信概述

### 1.1 CAN总线基础

**OpenArmX CAN配置:**
```
can0: 左臂 (8个电机)
can1: 右臂 (8个电机)
波特率: 1 Mbps
拓扑: 菊花链
终端电阻: 120Ω (两端)
```

### 1.2 常见CAN问题

```
□ CAN接口未启动
□ 设备未识别
□ 通信超时
□ 数据丢失
□ 总线拥塞
□ 硬件故障
```

---

## 2. 问题诊断

### 2.1 检查CAN接口状态

```bash
# 查看CAN接口
ip link show can0
ip link show can1

# 正常输出示例:
# 3: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP mode DEFAULT group default qlen 10
#     link/can

# 异常状态:
# can0: <NOARP> mtu 16 qdisc noop state DOWN
```

**状态说明:**
```
UP: 接口已启动 ✓
DOWN: 接口未启动 ✗
LOWER_UP: 物理层连接 ✓
```

### 2.2 查看CAN统计信息

```bash
# 详细统计
ip -s -d link show can0

# 输出:
# RX: bytes  packets  errors  dropped overrun mcast
#     12345   678      0       0       0       0
# TX: bytes  packets  errors  dropped carrier collsns
#     23456   789      0       0       0       0
```

**关键指标:**
```
RX errors: 接收错误
TX errors: 发送错误
dropped: 丢包数
overrun: 缓冲区溢出

正常值: 全部为0
异常值: >0 表示有问题
```

### 2.3 监听CAN消息

```bash
# 监听can0上的消息
candump can0

# 正常输出示例:
# can0  001   [8]  01 02 03 04 05 06 07 08
# can0  002   [8]  11 12 13 14 15 16 17 18
# ...

# 异常情况:
# - 无输出: 没有通信
# - 错误帧: (ERROR)
```

**过滤特定ID:**
```bash
# 只看ID=1的电机
candump can0,001:7FF

# 只看ID=1-8
candump can0,001:7F8
```

---

## 3. 常见问题及解决

### 3.1 CAN接口未启动

**症状:**
```
ip link show can0
# can0: <NOARP> mtu 16 qdisc noop state DOWN
```

**原因:**
```
- 未执行启动脚本
- 启动脚本失败
- 驱动未加载
```

**解决:**

```bash
# 方法1: 使用脚本
cd ~/openarmx_robotstride_ws-cc/src/motor_tests_openarmx_com
python3 en_all_can.py

# 方法2: 手动启动
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

sudo ip link set can1 type can bitrate 1000000
sudo ip link set can1 up

# 验证
ip link show can0
# 应显示 UP,LOWER_UP
```

**检查驱动:**
```bash
# 检查CAN模块
lsmod | grep can

# 应看到:
# can_raw
# can
# can_dev

# 如未加载,手动加载
sudo modprobe can
sudo modprobe can_raw
sudo modprobe can_dev
```

### 3.2 设备未识别

**症状:**
```
ip link show can0
# Device "can0" does not exist.
```

**原因:**
```
- USB-CAN转换器未连接
- 设备驱动未安装
- USB权限问题
```

**解决:**

```bash
# 1. 检查USB设备
lsusb

# 应看到CAN转换器设备,例如:
# Bus 001 Device 005: ID 1d50:606f OpenMoko, Inc.

# 2. 检查内核日志
dmesg | grep -i can

# 3. 检查设备节点
ls -l /dev/tty* | grep USB

# 4. 安装驱动(如果需要)
# 根据具体CAN转换器型号安装驱动
```

**USB权限:**
```bash
# 添加用户到dialout组
sudo usermod -a -G dialout $USER

# 注销后重新登录生效
```

### 3.3 通信超时

**症状:**
```
python3 check_motor_status.py
# 超时错误: Motor 1 timeout
```

**原因:**
```
- 电机未上电
- CAN线缆连接问题
- 电机ID错误
- 波特率不匹配
```

**解决:**

```bash
# 1. 检查电机供电
# 确认48V电源已开启,电源指示灯亮

# 2. 检查CAN消息
candump can0 -t A

# 如果无消息:
#   - 检查线缆连接
#   - 检查终端电阻

# 3. 发送测试命令
cansend can0 001#0102030405060708

# 4. 检查波特率
ip -d link show can0 | grep bitrate
# 应显示: bitrate 1000000
```

**重置CAN接口:**
```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

### 3.4 数据丢失/丢帧

**症状:**
```
# CAN统计显示dropped > 0
ip -s link show can0
# RX: dropped 123
```

**原因:**
```
- CAN总线负载过高
- 接收缓冲区过小
- 处理速度慢
```

**解决:**

```bash
# 1. 增大接收缓冲区
sudo ip link set can0 txqueuelen 1000

# 2. 降低控制频率
# 修改 update_rate: 200 → 100

# 3. 减少同时通信的电机数量

# 4. 检查CPU使用率
top
# 如果CPU接近100%,优化代码
```

### 3.5 总线拥塞

**症状:**
```
# TX errors增加
ip -s link show can0
# TX: errors 50

# 或candump显示大量ERROR帧
```

**原因:**
```
- 发送频率过高
- 总线负载超过容量
- 冲突过多
```

**解决:**

```bash
# 1. 降低发送频率
# 控制循环中添加延迟
time.sleep(0.001)  # 每个电机间隔1ms

# 2. 计算总线负载
# 16电机 × 100Hz = 1600帧/秒
# 1 Mbps CAN约可支持10000帧/秒
# 负载率 = 16% (安全)

# 如果负载>70%,降低频率

# 3. 检查错误详情
candump can0 -e
```

### 3.6 硬件故障

**症状:**
```
- CAN接口频繁DOWN
- 无法稳定通信
- 错误率高
```

**诊断:**

```bash
# 1. 检查线缆
# - 目视检查是否破损
# - 测量线缆电阻
# - 更换线缆测试

# 2. 检查终端电阻
# 使用万用表测量CAN_H和CAN_L之间电阻
# 应为60Ω (两个120Ω并联)

# 3. 检查USB-CAN转换器
# - 更换USB端口
# - 更换转换器
# - 检查LED指示

# 4. 检查电机
# 逐个连接电机,找出故障电机
```

**线缆测试:**
```bash
# 断开所有设备,测量线缆
# CAN_H - CAN_L: 应为开路或高阻抗
# 安装终端电阻后: 60Ω

# 如果短路(<10Ω): 线缆损坏
# 如果开路(>1MΩ): 未连接终端电阻
```

---

## 4. 诊断工具

### 4.1 candump (监听)

```bash
# 基本监听
candump can0

# 带时间戳
candump can0 -t A

# 仅显示错误帧
candump can0 -e

# 保存到文件
candump -l can0
# 生成 candump-<date>.log
```

### 4.2 cansend (发送测试)

```bash
# 发送单条消息
cansend can0 001#0102030405060708

# 循环发送
while true; do cansend can0 001#01; sleep 0.1; done
```

### 4.3 cangen (生成测试流量)

```bash
# 生成随机CAN消息
cangen can0 -g 10 -I 1 -L 8

# 参数:
# -g 10: 间隔10ms
# -I 1: ID=1
# -L 8: 数据长度8字节
```

### 4.4 cansequence (测试序列)

```bash
# 测试发送/接收序列
cansequence -r can0
cansequence -s can0
```

### 4.5 Python诊断脚本

```python
#!/usr/bin/env python3
"""
CAN诊断工具
"""
import can
import time

def diagnose_can(channel='can0'):
    """CAN总线诊断"""
    print(f"诊断 {channel}...")

    try:
        # 尝试初始化
        bus = can.interface.Bus(
            channel=channel,
            bustype='socketcan',
            bitrate=1000000
        )
        print(f"✓ {channel} 初始化成功")

        # 监听5秒
        print("监听5秒...")
        start_time = time.time()
        msg_count = 0

        while (time.time() - start_time) < 5.0:
            msg = bus.recv(timeout=0.1)
            if msg:
                msg_count += 1
                print(f"  ID={msg.arbitration_id:03X}, 数据={msg.data.hex()}")

        print(f"✓ 收到 {msg_count} 条消息")

        if msg_count == 0:
            print("⚠️ 警告: 未收到任何消息")
            print("  可能原因:")
            print("  - 电机未上电")
            print("  - 线缆未连接")
            print("  - 电机未使能")

        bus.shutdown()

    except OSError as e:
        print(f"✗ 错误: {e}")
        print("  可能原因:")
        print("  - CAN接口未启动")
        print("  - 权限不足")
        print("  - 设备不存在")

    except Exception as e:
        print(f"✗ 未知错误: {e}")

# 运行
diagnose_can('can0')
diagnose_can('can1')
```

---

## 5. 高级故障排查

### 5.1 波形分析

**使用示波器:**
```
测量CAN_H和CAN_L波形
正常差分电压: 2V (显性) vs 0V (隐性)

异常情况:
- 电压幅度过小: 终端电阻问题
- 波形失真: 线缆过长或质量差
- 无波形: 未连接或短路
```

### 5.2 负载测试

```python
#!/usr/bin/env python3
"""
CAN总线负载测试
"""
import can
import time

bus = can.interface.Bus(channel='can0', bustype='socketcan')

# 测试不同发送频率
frequencies = [10, 50, 100, 200, 500]

for freq in frequencies:
    print(f"\n测试频率: {freq} Hz")

    error_count = 0
    send_count = 0
    interval = 1.0 / freq

    start_time = time.time()
    test_duration = 10.0  # 10秒测试

    while (time.time() - start_time) < test_duration:
        msg = can.Message(
            arbitration_id=0x001,
            data=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08],
            is_extended_id=False
        )

        try:
            bus.send(msg)
            send_count += 1
        except can.CanError:
            error_count += 1

        time.sleep(interval)

    error_rate = error_count / send_count if send_count > 0 else 0
    print(f"  发送: {send_count}, 错误: {error_count}, 错误率: {error_rate:.2%}")

    if error_rate > 0.01:
        print(f"  ⚠️ 错误率过高,频率{freq}Hz可能不稳定")

bus.shutdown()
```

### 5.3 电机逐一测试

```bash
#!/bin/bash
# 逐一测试电机连接

echo "逐一测试can0电机..."

for id in {1..8}; do
    echo "测试电机ID=$id"

    # 发送使能命令
    cansend can0 $(printf "%03X" $id)#0102030405060708

    # 监听响应(1秒)
    timeout 1 candump can0,$(printf "%03X" $id):7FF > /tmp/can_test.log

    if [ -s /tmp/can_test.log ]; then
        echo "  ✓ 电机$id 响应正常"
    else
        echo "  ✗ 电机$id 无响应"
    fi

    sleep 0.5
done

rm /tmp/can_test.log
```

---

## 6. 预防措施

### 6.1 日常检查

```
□ 定期检查CAN统计(每天)
□ 检查线缆连接(每周)
□ 测量终端电阻(每月)
□ 清洁连接器(每月)
□ 备份配置(持续)
```

### 6.2 最佳实践

```
✓ 使用高质量CAN线缆
✓ 保持线缆长度<5m
✓ 避免平行走线(与强电)
✓ 固定连接器,防松动
✓ 标记线缆(can0/can1)
✓ 备用USB-CAN转换器
```

### 6.3 监控脚本

```python
#!/usr/bin/env python3
"""
CAN健康监控
"""
import subprocess
import time

def check_can_health(interface='can0'):
    """检查CAN接口健康度"""
    try:
        # 获取统计
        result = subprocess.run(
            ['ip', '-s', 'link', 'show', interface],
            capture_output=True,
            text=True
        )

        output = result.stdout

        # 提取错误数
        for line in output.split('\n'):
            if 'RX:' in line:
                parts = line.split()
                rx_errors = int(parts[3])
            if 'TX:' in line:
                parts = line.split()
                tx_errors = int(parts[3])

        print(f"{interface}: RX错误={rx_errors}, TX错误={tx_errors}")

        if rx_errors > 0 or tx_errors > 0:
            print(f"  ⚠️ 警告: {interface} 有通信错误")
            return False

        return True

    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return False

# 持续监控
while True:
    print(f"\n[{time.strftime('%H:%M:%S')}] CAN健康检查")
    check_can_health('can0')
    check_can_health('can1')
    time.sleep(60)  # 每分钟检查
```

---

## 7. 故障速查表

| 症状 | 可能原因 | 解决方法 |
|------|---------|---------|
| CAN接口DOWN | 未启动 | `python3 en_all_can.py` |
| 设备不存在 | USB未连接 | 检查USB连接 |
| 通信超时 | 电机未上电 | 检查48V电源 |
| RX errors >0 | 线缆问题 | 检查/更换线缆 |
| TX errors >0 | 总线拥塞 | 降低发送频率 |
| dropped >0 | 缓冲区小 | 增大txqueuelen |
| 无消息 | 未使能电机 | `python3 en_all_motors.py` |
| 间歇通信 | 接触不良 | 重新插拔连接器 |

---

## 8. 总结

### 8.1 诊断流程

```
1. 检查CAN接口状态 (ip link show)
   ↓
2. 查看错误统计 (ip -s link show)
   ↓
3. 监听CAN消息 (candump)
   ↓
4. 检查硬件连接
   ↓
5. 逐一排查电机
   ↓
6. 必要时更换硬件
```

### 8.2 关键命令

```bash
# 状态检查
ip link show can0
ip -s link show can0

# 监听
candump can0

# 测试发送
cansend can0 001#01

# 重启接口
sudo ip link set can0 down
sudo ip link set can0 up
```

### 8.3 下一步

- **02_电机异常处理** - 电机故障诊断
- **03_控制器问题** - ros2_control故障
- **04_常用诊断命令** - 命令速查

---

*本文档版本: v1.0*
*最后更新: 2025年10月19日*
*成都长数机器人有限公司*
