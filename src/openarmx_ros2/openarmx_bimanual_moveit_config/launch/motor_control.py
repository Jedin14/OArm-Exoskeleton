#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
@File    :   motor_control.py
@Time    :   2025/10/01 11:49:13
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   None
'''

import can
import time
import struct
import subprocess
from motor_config_loader import MotorConfigLoader

motor_conf = MotorConfigLoader()

def float_to_uint16(float_data, float_data_min, float_data_max):
    """有符号浮点型转十六进制(0~65535)无符号型"""
    if float_data > float_data_max:
        float_data_s = float_data_max
    elif float_data < float_data_min:
        float_data_s = float_data_min
    else:
        float_data_s = float_data

    return int((float_data_s - float_data_min) / (float_data_max - float_data_min) * 65535)


def uint16_to_float(uint16_data, float_data_min, float_data_max):
    """十六进制(0~65535)无符号型转有符号浮点型"""
    return float(uint16_data / 65535) * (float_data_max - float_data_min) + float_data_min

#**************************************************************************
#有符号浮点型转四位十六进制(输出小端模式：低在左高在右) 适合单个参数写函数
#**************************************************************************
def float_to_P4hex(float_data):
    # 将十进制浮点数转换为十六进制浮点数
    byte_representation = struct.pack('f', float_data)
    return byte_representation


#**************************************************************************
# 四位十六进制转有符号浮点型转(输入小端模式：低在左高在右) 适合单个参数写函数
#**************************************************************************
def P4hex_to_float(P4hex_data):
    bytes_obj = P4hex_data.to_bytes(4, byteorder='big') 
    float_value = struct.unpack('f', bytes_obj)[0]

    return float_value


def send_extended_frame_main(bus, arbitration_id, data, block_receive=1, timeout=1.0, verbose=False):
    """
    标准CAN扩展帧收发接口

    参数:
        bus (can.Bus): CAN总线对象
        arbitration_id (int): 29位扩展仲裁ID
        data (list): CAN数据字段，长度0-8字节
        block_receive (int): 是否阻塞接收 0=不等待接收, 1=等待接收 (默认: 1)
        timeout (float): 接收超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        tuple: (state, rx_data, rx_arbitration_id)
            - state (int): 状态码 0=成功, 1=超时/错误
            - rx_data (list): 接收到的数据(8字节)
            - rx_arbitration_id (int): 接收到的仲裁ID (如果未接收则为0)

    示例:
        # 发送并等待响应
        state, rx_data, rx_id = send_extended_frame_main(bus, 0x01000005, [0]*8, block_receive=1)

        # 只发送不等待
        state, rx_data, rx_id = send_extended_frame_main(bus, 0x03000005, [0]*8, block_receive=0)
    """
    # 初始化返回值
    state = 0
    rx_data = [0 for _ in range(8)]
    rx_arbitration_id = 0

    # 创建CAN消息
    message = can.Message(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=True
    )

    # 发送CAN消息
    try:
        bus.send(message)
        if verbose:
            print(f"[TX] ID: 0x{arbitration_id:08X}, Data: {[f'{b:02X}' for b in data]}")
    except can.CanError as e:
        print(f"[ERROR] 发送失败: {e}")
        return (1, rx_data, rx_arbitration_id)

    # 如果需要阻塞接收
    if block_receive == 1:
        time_start = time.time()

        while True:
            # 计算剩余超时时间
            elapsed = time.time() - time_start
            remaining_timeout = timeout - elapsed

            if remaining_timeout <= 0:
                # 超时
                if verbose:
                    print(f"[TIMEOUT] 仲裁号: 0x{arbitration_id:08X} 未收到回馈 (超时: {timeout}s)")
                else:
                    print(f"仲裁号: 0x{arbitration_id:08X} 未收到回馈-error")
                state = 1
                break

            # 尝试接收消息
            message_rx = bus.recv(timeout=min(0.1, remaining_timeout))

            if message_rx is not None:
                # 成功接收到消息
                rx_data = list(message_rx.data)
                rx_arbitration_id = message_rx.arbitration_id

                if verbose:
                    print(f"[RX] ID: 0x{rx_arbitration_id:08X}, Data: {[f'{b:02X}' for b in rx_data]}")

                    # 解析通信类型
                    comm_type = (rx_arbitration_id >> 24) & 0x3F
                    motor_id = (rx_arbitration_id >> 8) & 0xFF
                    print(f"     通信类型: {comm_type}, 电机ID: {motor_id}")

                state = 0
                break

    return (state, rx_data, rx_arbitration_id)


def send_extended_frame_no_wait(bus, arbitration_id, data, verbose=False):
    """
    发送CAN扩展帧(不等待响应)

    参数:
        bus (can.Bus): CAN总线对象
        arbitration_id (int): 29位扩展仲裁ID
        data (list): CAN数据字段，长度0-8字节
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    state, _, _ = send_extended_frame_main(bus, arbitration_id, data, block_receive=0, verbose=verbose)
    return state


def send_extended_frame_with_retry(bus, arbitration_id, data, max_retries=3, timeout=1.0, verbose=False):
    """
    发送CAN扩展帧并重试(如果失败)

    参数:
        bus (can.Bus): CAN总线对象
        arbitration_id (int): 29位扩展仲裁ID
        data (list): CAN数据字段，长度0-8字节
        max_retries (int): 最大重试次数 (默认: 3)
        timeout (float): 每次尝试的超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        tuple: (state, rx_data, rx_arbitration_id)
    """
    for attempt in range(max_retries):
        state, rx_data, rx_arbitration_id = send_extended_frame_main(
            bus, arbitration_id, data,
            block_receive=1,
            timeout=timeout,
            verbose=verbose
        )

        if state == 0:
            # 成功
            if verbose and attempt > 0:
                print(f"[SUCCESS] 第 {attempt + 1} 次尝试成功")
            return (state, rx_data, rx_arbitration_id)

        # 失败，准备重试
        if attempt < max_retries - 1:
            if verbose:
                print(f"[RETRY] 第 {attempt + 1} 次尝试失败，准备重试...")
            time.sleep(0.1)  # 重试前短暂延迟

    # 所有重试都失败
    if verbose:
        print(f"[FAILED] 所有 {max_retries} 次尝试均失败")

    return (1, rx_data, rx_arbitration_id)

def get_motor_status(bus, motorID):
    """获取电机状态信息"""

    data_s = [0 for i in range(8)]

    # 发送状态查询指令 (0扭矩, 0角度, 0速度, 小KP, 小KD)
    # 转化扭矩参数
    T_MIN,T_MAX = motor_conf.get_torque_limits(motorID)
    data_int16 = (float_to_uint16(0, T_MIN, T_MAX)) << 8
    arbitration_id = 0x01000000 | data_int16 | motorID

    # 转化角度(弧度)参数载入
    P_MIN,P_MAX = motor_conf.get_position_limits(motorID)
    data_int16 = (float_to_uint16(0, P_MIN, P_MAX))
    data_s[0] = data_int16 >> 8
    data_s[1] = data_int16 & 0x00ff

    # 转化载入速度参数
    V_MIN,V_MAX = motor_conf.get_velocity_limits(motorID)
    data_int16 = (float_to_uint16(0, V_MIN, V_MAX))
    data_s[2] = data_int16 >> 8
    data_s[3] = data_int16 & 0x00ff

    # 转化载入KP参数 (使用较小的KP值避免震荡)
    KP_MIN,KP_MAX = motor_conf.get_kp_limits(motorID)
    data_int16 = (float_to_uint16(1.0, KP_MIN, KP_MAX))
    data_s[4] = data_int16 >> 8
    data_s[5] = data_int16 & 0x00ff

    # 转化载入KD参数
    KD_MIN,KD_MAX = motor_conf.get_kd_limits(motorID)
    data_int16 = (float_to_uint16(0.1, KD_MIN, KD_MAX))
    data_s[6] = data_int16 >> 8
    data_s[7] = data_int16 & 0x00ff

    # 发送指令并等待反馈 (block_receive=1表示等待反馈)
    (state, rx_data, rx_arbitration_id) = send_extended_frame_main(bus, arbitration_id, data_s, 1)

    return (state, rx_data, rx_arbitration_id)

def parse_motor_feedback(rx_data, motorID):
    """解析电机反馈数据"""
    if len(rx_data) < 8:
        return None

    # 解析当前角度 (Byte0~1)
    angle_raw = (rx_data[0] << 8) | rx_data[1]
    P_MIN,P_MAX = motor_conf.get_position_limits(motorID)
    current_angle = uint16_to_float(angle_raw, P_MIN, P_MAX)

    # 解析当前角速度 (Byte2~3)
    velocity_raw = (rx_data[2] << 8) | rx_data[3]
    V_MIN,V_MAX = motor_conf.get_velocity_limits(motorID)
    current_velocity = uint16_to_float(velocity_raw, V_MIN, V_MAX)

    # 解析当前力矩 (Byte4~5)
    torque_raw = (rx_data[4] << 8) | rx_data[5]
    T_MIN,T_MAX = motor_conf.get_torque_limits(motorID)
    current_torque = uint16_to_float(torque_raw, T_MIN, T_MAX)

    # 解析当前温度 (Byte6~7)
    temp_raw = (rx_data[6] << 8) | rx_data[7]
    current_temp = temp_raw / 10.0  # 温度×10

    return {
        'angle': current_angle,
        'velocity': current_velocity,
        'torque': current_torque,
        'temperature': current_temp
    }

def parse_control_mode_and_status(arbitration_id, rx_data):
    """解析控制模式和使能状态信息
    根据电机说明书通信类型2的返回格式解析
    """
    if len(rx_data) < 8:
        return None

    # 从仲裁号中提取信息 (bit21~16:故障信息, bit22~23:模式状态)
    # 注意：这里的arbitration_id已经是扩展帧格式
    fault_info = (arbitration_id >> 16) & 0x3F  # bit21~16: 故障信息
    mode_status = (arbitration_id >> 22) & 0x03  # bit22~23: 模式状态

    # 模式状态解析
    mode_status_desc = {
        0: "Reset模式[复位]",
        1: "Cali模式[标定]",
        2: "Motor模式[运行]",
        3: "未知模式"
    }.get(mode_status, f"未知模式({mode_status})")

    # 故障信息解析
    fault_flags = []
    if fault_info & (1 << 5):  # bit21
        fault_flags.append("未标定")
    if fault_info & (1 << 4):  # bit20
        fault_flags.append("堵转过载故障")
    if fault_info & (1 << 3):  # bit19
        fault_flags.append("磁编码故障")
    if fault_info & (1 << 2):  # bit18
        fault_flags.append("过温")
    if fault_info & (1 << 1):  # bit17
        fault_flags.append("驱动故障")
    if fault_info & (1 << 0):  # bit16
        fault_flags.append("欠压故障")

    fault_status = "正常" if not fault_flags else ", ".join(fault_flags)

    return {
        'mode_status': mode_status_desc,
        'fault_status': fault_status,
        'fault_code': fault_info
    }


def set_motion_control(bus, motor_id, position=0.0, velocity=0.0, torque=0.0, kp=0.0, kd=0.0,
                       wait_response=False, timeout=1.0, verbose=False):
    """
    标准运动控制函数 (MIT模式/运控模式)

    发送运动控制命令到指定电机，使用电机配置自动获取对应的限制参数。

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        position (float): 目标位置 (弧度, rad) (默认: 0.0)
        velocity (float): 目标速度 (弧度/秒, rad/s) (默认: 0.0)
        torque (float): 前馈扭矩 (牛米, Nm) (默认: 0.0)
        kp (float): 位置增益 KP (默认: 0.0)
        kd (float): 速度增益 KD (默认: 0.0)
        wait_response (bool): 是否等待电机响应 (默认: False)
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        tuple: (state, rx_data, rx_arbitration_id)
            - state (int): 0=成功, 1=失败
            - rx_data (list): 接收到的数据 (如果 wait_response=True)
            - rx_arbitration_id (int): 接收到的仲裁ID (如果 wait_response=True)

    示例:
        # 基本使用：移动到位置 0.5 弧度
        state, _, _ = set_motion_control(bus, motor_id=5, position=0.5, kp=20, kd=2)

        # 速度控制
        state, _, _ = set_motion_control(bus, motor_id=5, velocity=5.0, kp=0, kd=1)

        # 扭矩控制
        state, _, _ = set_motion_control(bus, motor_id=5, torque=2.0, kp=0, kd=0)

        # 完整的位置-速度-扭矩复合控制
        state, _, _ = set_motion_control(
            bus, motor_id=5,
            position=1.0, velocity=2.0, torque=0.5,
            kp=30, kd=3, verbose=True
        )
    """
    # 创建数据缓冲区
    data = [0 for _ in range(8)]

    # 1. 获取扭矩限制并编码到CAN ID中
    t_min, t_max = motor_conf.get_torque_limits(motor_id)
    torque_uint16 = float_to_uint16(torque, t_min, t_max)
    arbitration_id = 0x01000000 | (torque_uint16 << 8) | motor_id

    if verbose:
        print(f"[Motion Control] 电机ID: {motor_id}")
        print(f"  扭矩限制: [{t_min}, {t_max}] Nm, 扭矩值: {torque} Nm")

    # 2. 编码位置参数 (Byte 0-1)
    
    p_min, p_max = motor_conf.get_position_limits(motor_id)
    pos_uint16 = float_to_uint16(position, p_min, p_max)
    data[0] = (pos_uint16 >> 8) & 0xFF
    data[1] = pos_uint16 & 0xFF

    if verbose:
        print(f"  位置限制: [{p_min}, {p_max}] rad, 位置值: {position} rad")

    # 3. 编码速度参数 (Byte 2-3)
    v_min, v_max = motor_conf.get_velocity_limits(motor_id)
    vel_uint16 = float_to_uint16(velocity, v_min, v_max)
    data[2] = (vel_uint16 >> 8) & 0xFF
    data[3] = vel_uint16 & 0xFF

    if verbose:
        print(f"  速度限制: [{v_min}, {v_max}] rad/s, 速度值: {velocity} rad/s")

    # 4. 编码KP参数 (Byte 4-5)
    kp_min, kp_max = motor_conf.get_kp_limits(motor_id)
    kp_uint16 = float_to_uint16(kp, kp_min, kp_max)
    data[4] = (kp_uint16 >> 8) & 0xFF
    data[5] = kp_uint16 & 0xFF

    if verbose:
        print(f"  KP限制: [{kp_min}, {kp_max}], KP值: {kp}")

    # 5. 编码KD参数 (Byte 6-7)
    kd_min, kd_max = motor_conf.get_kd_limits(motor_id)
    kd_uint16 = float_to_uint16(kd, kd_min, kd_max)
    data[6] = (kd_uint16 >> 8) & 0xFF
    data[7] = kd_uint16 & 0xFF

    if verbose:
        print(f"  KD限制: [{kd_min}, {kd_max}], KD值: {kd}")

    # 6. 发送CAN消息
    block_receive = 1 if wait_response else 0
    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=block_receive,
        timeout=timeout,
        verbose=verbose
    )

    return (state, rx_data, rx_arbitration_id)


def set_motion_control_simple(bus, motor_id, position, kp=20.0, kd=2.0, verbose=False):
    """
    简化的位置控制函数

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        position (float): 目标位置 (弧度)
        kp (float): 位置增益 (默认: 20.0)
        kd (float): 速度增益 (默认: 2.0)
        verbose (bool): 调试输出 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    state, _, _ = set_motion_control(
        bus, motor_id,
        position=position,
        velocity=0.0,
        torque=0.0,
        kp=kp,
        kd=kd,
        wait_response=False,
        verbose=verbose
    )
    return state


def set_zero_position(bus, motor_id, kp=3.0, kd=0.3, verbose=False):
    """
    将电机移动到零位

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        kp (float): 位置增益 (默认: 5.0 - 较小值以平稳归零)
        kd (float): 速度增益 (默认: 0.5)
        verbose (bool): 调试输出 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    return set_motion_control_simple(bus, motor_id, position=0.0, kp=kp, kd=kd, verbose=verbose)


def hold_position(bus, motor_id, kp=30.0, kd=3.0, verbose=False):
    """
    保持当前位置 (发送当前位置=0的命令，配合高KP/KD锁定位置)

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        kp (float): 位置增益 (默认: 30.0 - 高增益以锁定)
        kd (float): 速度增益 (默认: 3.0)
        verbose (bool): 调试输出 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    return set_motion_control_simple(bus, motor_id, position=0.0, kp=kp, kd=kd, verbose=verbose)


def set_velocity_control(bus, motor_id, velocity, kd=2.0, verbose=False):
    """
    速度控制函数

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        velocity (float): 目标速度 (弧度/秒)
        kd (float): 速度增益 (默认: 2.0)
        verbose (bool): 调试输出 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    state, _, _ = set_motion_control(
        bus, motor_id,
        position=0.0,
        velocity=velocity,
        torque=0.0,
        kp=0.0,  # 位置控制关闭
        kd=kd,
        wait_response=False,
        verbose=verbose
    )
    return state


def set_torque_control(bus, motor_id, torque, verbose=False):
    """
    扭矩控制函数 (开环力矩控制)

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        torque (float): 目标扭矩 (牛米)
        verbose (bool): 调试输出 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败
    """
    state, _, _ = set_motion_control(
        bus, motor_id,
        position=0.0,
        velocity=0.0,
        torque=torque,
        kp=0.0,
        kd=0.0,
        wait_response=False,
        verbose=verbose
    )
    return state



def enable_motor(bus, motor_id, timeout=1.0, verbose=False):
    """
    使能电机

    发送使能命令到指定电机，启动电机。

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败

    示例:
        # 使能单个电机
        state = enable_motor(bus, motor_id=5)
        if state == 0:
            print("电机使能成功")

        # 使能多个电机
        for motor_id in range(1, 9):
            enable_motor(bus, motor_id)
    """
    # 功能码 0x0300
    # 仲裁ID: 0x0300fd00 + motorID
    arbitration_id = 0x0300fd00 + motor_id
    data = [0] * 8

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if verbose or state == 0:
        if state == 0:
            print(f"电机ID {motor_id}: 使能成功")
        else:
            print(f"电机ID {motor_id}: 使能失败")

    return state


def disable_motor(bus, motor_id, timeout=1.0, verbose=False):
    """
    停止电机

    发送停止命令到指定电机。

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败

    示例:
        # 停止单个电机
        state = disable_motor(bus, motor_id=5)

        # 停止所有电机
        for motor_id in range(1, 9):
            disable_motor(bus, motor_id)
    """
    # 功能码 0x0400
    # 仲裁ID: 0x0400fd00 + motorID
    arbitration_id = 0x0400fd00 + motor_id
    data = [0] * 8  # 全0数据

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if verbose or state == 0:
        if state == 0:
            print(f"电机ID {motor_id}: 停止成功")
        else:
            print(f"电机ID {motor_id}: 停止失败")

    return state


def set_control_mode(bus, motor_id, mode='motion', timeout=1.0, verbose=False):
    """
    设置电机控制模式

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        mode (str): 控制模式，可选值:
            - 'motion' / 0: 运控模式 (MIT模式)
            - 'position' / 1: 位置模式
            - 'velocity' / 2: 速度模式
            - 'current' / 3: 电流模式
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败

    示例:
        # 设置为运控模式（MIT模式）
        set_control_mode(bus, motor_id=5, mode='motion')

        # 设置为位置模式
        set_control_mode(bus, motor_id=5, mode='position')

        # 设置为速度模式
        set_control_mode(bus, motor_id=5, mode='velocity')
    """
    # 功能码 0x1200 - 设置单个参数/控制模式
    # 仲裁ID: 0x1200fd00 + motorID
    arbitration_id = 0x1200fd00 + motor_id

    # 模式映射
    mode_map = {
        'motion': 0,
        'position': 1,
        'velocity': 2,
        'speed': 2,  # 别名
        'current': 3,
        'torque': 3,  # 别名
        0: 0,
        1: 1,
        2: 2,
        3: 3
    }

    if mode not in mode_map:
        print(f"错误: 无效的控制模式 '{mode}'")
        print(f"可选值: 'motion', 'position', 'velocity', 'current' 或 0-3")
        return 1

    mode_value = mode_map[mode]

    # 数据位第[4]位表示模式: 0=运控, 1=位置, 2=速度, 3=电流
    # 但运控模式时全部为0
    data = [0] * 8
    if mode_value != 0:
        data[4] = mode_value

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    mode_names = {0: '运控模式', 1: '位置模式', 2: '速度模式', 3: '电流模式'}
    if verbose or state == 0:
        if state == 0:
            print(f"电机ID {motor_id}: 设置为 {mode_names[mode_value]} 成功")
        else:
            print(f"电机ID {motor_id}: 设置为 {mode_names[mode_value]} 失败")

    return state


def enable_all_motors(bus, motor_ids=None, verbose=False):
    """
    批量使能电机

    参数:
        bus (can.Bus): CAN总线对象
        motor_ids (list): 电机ID列表，默认 None (使能ID 1-8 所有电机)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        dict: {motor_id: state} 每个电机的状态

    示例:
        # 使能所有电机 (ID 1-8)
        results = enable_all_motors(bus)

        # 使能指定电机
        results = enable_all_motors(bus, motor_ids=[1, 2, 5, 6])

        # 检查结果
        for motor_id, state in results.items():
            if state == 0:
                print(f"电机 {motor_id}: OK")
            else:
                print(f"电机 {motor_id}: 失败")
    """
    if motor_ids is None:
        motor_ids = list(range(1, 9))  # 默认 1-8

    results = {}
    for motor_id in motor_ids:
        state = enable_motor(bus, motor_id, verbose=verbose)
        results[motor_id] = state
        time.sleep(0.01)  # 短暂延迟避免总线拥塞

    return results


# ====================== 位置模式 (CSP) 标准接口 ======================

# 依据《灵足时代 RS03 说明书》与本仓库 Robstride 协议实现：
# - 进入位置模式需要通过通信类型 0x1200 设置控制模式为 1
# - 位置目标、速度/电流限制通过通信类型 0x1200 写入单个参数：
#   - 0x7016 LOC_REF   位置模式角度指令 (float, 单位: rad)
#   - 0x7017 LIMIT_SPD 位置模式速度限制 (float, 单位: rad/s)
#   - 0x7018 LIMIT_CUR 位置/速度模式电流限制 (float, 单位: N·m)
# - 本实现使用 socketcan + python-can 的扩展帧发送


def _write_motor_parameter(bus, motor_id, param_index, value, *, value_type='float',
                           timeout=0.2, verbose=False, wait_response=True):
    """
    写单个参数的通用函数 (通信类型 0x1200)

    参数:
        bus (can.Bus): CAN 总线
        motor_id (int): 电机ID
        param_index (int): 参数索引 (如 0x7016/0x7017/0x7018)
        value: 写入的数值
        value_type (str): 'float' 或 'u8'
        timeout (float): 超时
        verbose (bool): 调试输出
        wait_response (bool): 是否等待电机回包

    返回:
        (state, rx_data, rx_arbitration_id)
    """
    arbitration_id = 0x1200fd00 + motor_id
    data = [0] * 8

    # 索引采用现有实现习惯：低字节在 data[0]，高字节在 data[1]
    data[0] = param_index & 0xFF
    data[1] = (param_index >> 8) & 0xFF

    if value_type == 'float':
        # 按小端写入到 data[4:8]，与 read_motor_parameter 的解析相匹配
        b = struct.pack('<f', float(value))
        data[4] = b[0]
        data[5] = b[1]
        data[6] = b[2]
        data[7] = b[3]
    elif value_type == 'u8':
        data[4] = int(value) & 0xFF
    else:
        raise ValueError("Unsupported value_type, use 'float' or 'u8'")

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1 if wait_response else 0,
        timeout=timeout,
        verbose=verbose
    )

    if verbose:
        if state == 0:
            print(f"电机ID {motor_id}: 写参数 0x{param_index:04X} = {value} 成功")
        else:
            print(f"电机ID {motor_id}: 写参数 0x{param_index:04X} 失败")

    return state, rx_data, rx_arbitration_id


def csp_set_mode(bus, motor_id, *, timeout=1.0, verbose=False):
    """
    切换到位置模式 (CSP)

    依据 RS03 说明书，使用通信类型 0x1200 写运行模式参数 RUN_MODE(0x7005)=5。
    注意：手册要求“切换模式需在失能状态下”，请在上层流程里先停机再调用。
    """
    # 禁用电机，修改电机运动模式时必须先失能电机
    disable_motor(bus, motor_id, timeout=timeout, verbose=verbose)
    # RUN_MODE = 0x7005, 5 = CSP
    return _write_motor_parameter(
        bus, motor_id, 0x7005, 5,
        value_type='u8', timeout=timeout,
        verbose=verbose, wait_response=True
    )[0]


def csp_set_limits(bus, motor_id, *, speed_limit=None, current_limit=None,
                   timeout=0.2, verbose=False, wait_response=True):
    """
    设置 CSP 模式下的速度/电流限制。

    - speed_limit 写 0x7017 (rad/s)
    - current_limit 写 0x7018 (N·m)
    若为 None 则跳过对应设置。
    """
    # 取各项限制，做范围裁剪
    v_min, v_max = motor_conf.get_velocity_limits(motor_id)
    t_min, t_max = motor_conf.get_torque_limits(motor_id)

    state_agg = 0

    if speed_limit is not None:
        v_cmd = max(min(float(speed_limit), v_max), v_min)
        s, _, _ = _write_motor_parameter(bus, motor_id, 0x7017, v_cmd,
                                         value_type='float', timeout=timeout,
                                         verbose=verbose, wait_response=wait_response)
        state_agg |= s

    if current_limit is not None:
        c_cmd = max(min(float(current_limit), t_max), t_min)
        s, _, _ = _write_motor_parameter(bus, motor_id, 0x7018, c_cmd,
                                         value_type='float', timeout=timeout,
                                         verbose=verbose, wait_response=wait_response)
        state_agg |= s

    return state_agg


def csp_set_target_position(bus, motor_id, position, *,
                            speed_limit=None, current_limit=None,
                            ensure_mode=False, wait_response=False,
                            timeout=0.2, verbose=False):
    """
    CSP 位置目标下发（循环同步位置）标准接口

    参数:
        bus (can.Bus): CAN 总线
        motor_id (int): 电机ID
        position (float): 目标位置 (rad)
        speed_limit (float|None): 可选，位置模式速度限制 (rad/s)
        current_limit (float|None): 可选，位置/速度模式电流限制 (N·m)
        ensure_mode (bool): 若为 True，在发送前强制切换到位置模式
        wait_response (bool): 是否等待单包回包（循环控制通常设为 False）
        timeout (float): 超时
        verbose (bool): 调试输出

    返回:
        int: 0 成功, 非 0 失败（如有多次写入则为各次按位或）

    用法示例:
        # 一次性设置目标位姿
        csp_set_mode(bus, 5)
        csp_set_limits(bus, 5, speed_limit=5.0, current_limit=8.0)
        csp_set_target_position(bus, 5, position=1.2)

        # 循环发送（简要示意）
        for p in traj:  # traj 为离散轨迹
            csp_set_target_position(bus, 5, p)
            time.sleep(0.002)  # 2ms 周期
    """
    # 可选：确保处于位置模式
    if ensure_mode:
        mode_state = csp_set_mode(bus, motor_id, timeout=timeout, verbose=verbose)
        if mode_state != 0 and verbose:
            print(f"电机ID {motor_id}: 切换位置模式失败，仍尝试下发位置指令")

    # 可选：设置限制
    limit_state = csp_set_limits(bus, motor_id,
                                 speed_limit=speed_limit,
                                 current_limit=current_limit,
                                 timeout=timeout, verbose=verbose,
                                 wait_response=wait_response)

    # 位置裁剪
    p_min, p_max = motor_conf.get_position_limits(motor_id)
    p_cmd = max(min(float(position), p_max), p_min)

    # 写 LOC_REF (0x7016)
    state, _, _ = _write_motor_parameter(bus, motor_id, 0x7016, p_cmd,
                                         value_type='float', timeout=timeout,
                                         verbose=verbose, wait_response=wait_response)

    return (state | limit_state)
    

def csp_move_to(bus, motor_id, position, 
                speed_limit=None, current_limit=None,
                timeout=1.0, verbose=False):
    """
    严格按说明书的 CSP 流程执行，以保证进入 CSP 并转到目标位置。

    步骤:
      1) 在失能状态写 RUN_MODE=5 (CSP)
      2) 写 LIMIT_CUR(0x7018)、LIMIT_SPD(0x7017)
      3) 使能
      4) 写 LOC_REF(0x7016)
    """
    state = 0

    s = csp_set_limits(bus, motor_id,
                       speed_limit=speed_limit,
                       current_limit=current_limit,
                       timeout=timeout, verbose=verbose,
                       wait_response=True)
    state |= s
    time.sleep(0.005)

    s = csp_set_target_position(bus, motor_id, position,
                                wait_response=True,
                                timeout=timeout, verbose=verbose)
    state |= s

    return 0 if state == 0 else 1


def csp_move_to_flowwork(bus, motor_id, position, *,
                         speed_limit=None, current_limit=None,
                         require_disabled=True,
                         timeout=1.0, verbose=False):
    """
    严格按说明书的 CSP 流程执行，以保证进入 CSP 并转到目标位置。

    步骤:
      1) 在失能状态写 RUN_MODE=5 (CSP)
      2) 写 LIMIT_CUR(0x7018)、LIMIT_SPD(0x7017)
      3) 使能
      4) 写 LOC_REF(0x7016)
    """
    state = 0

    if require_disabled:
        s = disable_motor(bus, motor_id, timeout=timeout, verbose=verbose)
        state |= s
        time.sleep(0.01)

    s = csp_set_mode(bus, motor_id, timeout=timeout, verbose=verbose)
    state |= s
    time.sleep(0.005)

    s = csp_set_limits(bus, motor_id,
                       speed_limit=speed_limit,
                       current_limit=current_limit,
                       timeout=timeout, verbose=verbose,
                       wait_response=True)
    state |= s
    time.sleep(0.005)

    s = enable_motor(bus, motor_id, timeout=timeout, verbose=verbose)
    state |= s
    time.sleep(0.005)

    s = csp_set_target_position(bus, motor_id, position,
                                wait_response=True,
                                timeout=timeout, verbose=verbose)
    state |= s

    return 0 if state == 0 else 1


def disable_all_motors(bus, motor_ids=None, verbose=False):
    """
    批量停止电机

    参数:
        bus (can.Bus): CAN总线对象
        motor_ids (list): 电机ID列表，默认 None (停止ID 1-8 所有电机)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        dict: {motor_id: state} 每个电机的状态

    示例:
        # 停止所有电机
        results = disable_all_motors(bus)

        # 紧急停止指定电机
        results = disable_all_motors(bus, motor_ids=[1, 2, 3])
    """
    if motor_ids is None:
        motor_ids = list(range(1, 9))

    results = {}
    for motor_id in motor_ids:
        state = disable_motor(bus, motor_id, verbose=verbose)
        results[motor_id] = state
        time.sleep(0.01)

    return results


def initialize_motor(bus, motor_id, verbose=False):
    """
    初始化电机（使能 + 设置运控模式）

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        bool: True=成功, False=失败

    示例:
        # 初始化单个电机
        if initialize_motor(bus, motor_id=5):
            print("初始化成功，可以开始控制")

        # 初始化所有电机
        for motor_id in range(1, 9):
            initialize_motor(bus, motor_id)
    """
    if verbose:
        print(f"\n=== 初始化电机 ID {motor_id} ===")

    # 1. 使能电机
    state1 = enable_motor(bus, motor_id, verbose=verbose)
    if state1 != 0:
        if verbose:
            print(f"  使能失败")
        return False

    time.sleep(0.01)

    # 2. 设置为运控模式
    state2 = set_control_mode(bus, motor_id, mode='motion', verbose=verbose)
    if state2 != 0:
        if verbose:
            print(f"  设置控制模式失败")
        return False

    if verbose:
        print(f"  初始化成功!")

    return True


# ====================== CAN接口管理函数 ======================

def enable_can_interface(interface="can0", bitrate=1000000, verbose=False):
    """
    启用单个CAN接口

    参数:
        interface (str): CAN接口名称 (默认: "can0")
        bitrate (int): CAN波特率 (默认: 1000000 = 1Mbps)
        verbose (bool): 是否显示详细信息 (默认: False)

    返回:
        bool: True=成功, False=失败

    示例:
        # 启用can0接口
        if enable_can_interface("can0"):
            print("CAN0启用成功")

        # 启用can1接口，显示详细信息
        enable_can_interface("can1", verbose=True)
    """
    if verbose:
        print(f"正在启用CAN接口: {interface} (波特率: {bitrate})")

    commands = [
        f"sudo ip link set down {interface}",
        f"sudo ip link set {interface} type can bitrate {bitrate} loopback off",
        f"sudo ip link set up {interface}"
    ]

    for cmd in commands:
        if verbose:
            print(f"  执行: {cmd}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                check=True,
                capture_output=True,
                text=True
            )
            if verbose:
                print(f"  ✓ 成功")
        except subprocess.CalledProcessError as e:
            if verbose:
                print(f"  ✗ 失败: {e}")
                if e.stderr:
                    print(f"  错误信息: {e.stderr.strip()}")
            return False

    if verbose:
        print(f"✓ {interface} 启用成功!")

    return True


def disable_can_interface(interface="can0", verbose=False):
    """
    禁用单个CAN接口

    参数:
        interface (str): CAN接口名称 (默认: "can0")
        verbose (bool): 是否显示详细信息 (默认: False)

    返回:
        bool: True=成功, False=失败

    示例:
        # 禁用can0接口
        if disable_can_interface("can0"):
            print("CAN0禁用成功")

        # 禁用can1接口，显示详细信息
        disable_can_interface("can1", verbose=True)
    """
    if verbose:
        print(f"正在禁用CAN接口: {interface}")

    # 检查接口是否存在
    try:
        result = subprocess.run(
            f"ip link show {interface}",
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError:
        if verbose:
            print(f"  ⚠ {interface} 不存在，跳过")
        return True

    # 关闭接口
    cmd = f"sudo ip link set down {interface}"
    if verbose:
        print(f"  执行: {cmd}")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )
        if verbose:
            print(f"  ✓ 成功")
    except subprocess.CalledProcessError as e:
        if verbose:
            print(f"  ✗ 失败: {e}")
            if e.stderr:
                print(f"  错误信息: {e.stderr.strip()}")
        return False

    if verbose:
        print(f"✓ {interface} 禁用成功!")

    return True


def enable_all_can_interfaces(interfaces=None, bitrate=1000000, verbose=False):
    """
    启用所有CAN接口

    参数:
        interfaces (list): CAN接口列表，默认 None (启用 can0 和 can1)
        bitrate (int): CAN波特率 (默认: 1000000 = 1Mbps)
        verbose (bool): 是否显示详细信息 (默认: False)

    返回:
        dict: {interface: success} 每个接口的启用结果

    示例:
        # 启用默认接口 (can0, can1)
        results = enable_all_can_interfaces(verbose=True)

        # 启用指定接口
        results = enable_all_can_interfaces(
            interfaces=["can0", "can1", "can2"],
            bitrate=500000,
            verbose=True
        )

        # 检查结果
        for interface, success in results.items():
            if success:
                print(f"{interface}: 成功")
            else:
                print(f"{interface}: 失败")
    """
    if interfaces is None:
        interfaces = ["can0", "can1"]

    if verbose:
        print("=" * 60)
        print(f"启用CAN接口: {', '.join(interfaces)}")
        print("=" * 60)

    results = {}
    for interface in interfaces:
        success = enable_can_interface(interface, bitrate, verbose)
        results[interface] = success
        if verbose and len(interfaces) > 1:
            print()  # 空行分隔

    if verbose:
        success_count = sum(results.values())
        total_count = len(results)
        print("=" * 60)
        print(f"完成: {success_count}/{total_count} 接口启用成功")
        print("=" * 60)

    return results


def disable_all_can_interfaces(interfaces=None, verbose=False):
    """
    禁用所有CAN接口

    参数:
        interfaces (list): CAN接口列表，默认 None (禁用 can0 和 can1)
        verbose (bool): 是否显示详细信息 (默认: False)

    返回:
        dict: {interface: success} 每个接口的禁用结果

    示例:
        # 禁用默认接口 (can0, can1)
        results = disable_all_can_interfaces(verbose=True)

        # 禁用指定接口
        results = disable_all_can_interfaces(
            interfaces=["can0", "can1", "can2", "can3"],
            verbose=True
        )

        # 检查结果
        for interface, success in results.items():
            if success:
                print(f"{interface}: 成功")
            else:
                print(f"{interface}: 失败")
    """
    if interfaces is None:
        interfaces = ["can0", "can1"]

    if verbose:
        print("=" * 60)
        print(f"禁用CAN接口: {', '.join(interfaces)}")
        print("=" * 60)

    results = {}
    for interface in interfaces:
        success = disable_can_interface(interface, verbose)
        results[interface] = success
        if verbose and len(interfaces) > 1:
            print()  # 空行分隔

    if verbose:
        success_count = sum(results.values())
        total_count = len(results)
        print("=" * 60)
        print(f"完成: {success_count}/{total_count} 接口禁用成功")
        print("=" * 60)

    return results


def check_can_interface_status(interface="can0", verbose=False):
    """
    检查CAN接口状态

    参数:
        interface (str): CAN接口名称 (默认: "can0")
        verbose (bool): 是否显示详细信息 (默认: False)

    返回:
        str: "UP", "DOWN", "NOT_FOUND"

    示例:
        status = check_can_interface_status("can0", verbose=True)
        if status == "UP":
            print("CAN0 已启用")
        elif status == "DOWN":
            print("CAN0 已禁用")
        else:
            print("CAN0 不存在")
    """
    try:
        result = subprocess.run(
            f"ip link show {interface}",
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )

        if "UP" in result.stdout:
            status = "UP"
        else:
            status = "DOWN"

        if verbose:
            print(f"{interface}: {status}")

        return status

    except subprocess.CalledProcessError:
        if verbose:
            print(f"{interface}: NOT_FOUND")
        return "NOT_FOUND"


# ====================== 电机零点设置函数 ======================

def set_motor_zero(bus, motor_id, timeout=1.0, verbose=False):
    """
    设置电机机械零点

    将当前电机位置设置为负载端零角度。此操作会将当前电机的机械位置标记为零点。

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败

    示例:
        # 设置单个电机的零点
        state = set_motor_zero(bus, motor_id=5, verbose=True)
        if state == 0:
            print("零点设置成功")

        # 设置所有电机的零点
        for motor_id in range(1, 9):
            set_motor_zero(bus, motor_id)
            time.sleep(0.01)
    """
    # 功能码 0x0600
    # 仲裁ID: 0x0600fd00 + motorID
    arbitration_id = 0x0600fd00 + motor_id
    data = [0] * 8
    data[0] = 0x01  # 第一个字节设置为0x01表示设置零点

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if verbose:
        if state == 0:
            print(f"电机ID {motor_id}: 设置负载端零角度成功")
        else:
            print(f"电机ID {motor_id}: 设置负载端零角度失败")

    return state


def set_zero_sta_parameter(bus, motor_id, zero_sta, timeout=1.0, verbose=False):
    """
    设置零点标志位参数 (zero_sta)

    该参数控制电机角度表示范围:
    - zero_sta = 0: 角度范围为 0 到 2π
    - zero_sta = 1: 角度范围为 -π 到 π

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        zero_sta (int): 零点标志位，0代表0~2π, 1代表-π~π
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        int: 状态码 0=成功, 1=失败

    示例:
        # 设置为 -π~π 范围
        state = set_zero_sta_parameter(bus, motor_id=5, zero_sta=1, verbose=True)

        # 设置为 0~2π 范围
        state = set_zero_sta_parameter(bus, motor_id=5, zero_sta=0, verbose=True)
    """
    # 使用通信类型18写入单个参数
    # index: 0x7029 (zero_sta参数)

    # 仲裁帧ID - 通信类型18
    arbitration_id = 0x1200fd00 + motor_id
    data = [0] * 8

    # 设置index (0x7029)
    param_index = 0x7029
    data[0] = param_index & 0xFF        # 低字节
    data[1] = (param_index >> 8) & 0xFF # 高字节
    data[2] = 0x00
    data[3] = 0x00

    # 设置参数值 (zero_sta)
    data[4] = zero_sta & 0xFF
    data[5] = 0x00
    data[6] = 0x00
    data[7] = 0x00

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if verbose:
        if state == 0:
            zero_range = "-π~π" if zero_sta == 1 else "0~2π"
            print(f"电机ID {motor_id}: 设置zero_sta={zero_sta} ({zero_range}) 成功")
        else:
            print(f"电机ID {motor_id}: 设置zero_sta={zero_sta} 失败")

    return state


def read_motor_parameter(bus, motor_id, param_index, timeout=1.0, verbose=False):
    """
    读取电机单个参数值

    使用通信类型17读取电机内部参数。

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        param_index (int): 参数索引，常用值:
            - 0x7019: mechPos (负载端计圈机械角度, float)
            - 0x7029: zero_sta (零点标志位, uint8)
        timeout (float): 响应超时时间(秒) (默认: 1.0)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        tuple: (state, param_value)
            - state (int): 状态码 0=成功, 1=失败
            - param_value: 参数值 (类型取决于参数, 失败时为None)

    示例:
        # 读取机械角度
        state, mech_pos = read_motor_parameter(bus, motor_id=5, param_index=0x7019)
        if state == 0:
            print(f"机械角度: {mech_pos} rad")

        # 读取zero_sta参数
        state, zero_sta = read_motor_parameter(bus, motor_id=5, param_index=0x7029)
        if state == 0:
            print(f"zero_sta: {zero_sta}")
    """
    # 仲裁帧ID - 通信类型17
    arbitration_id = 0x1100fd00 + motor_id
    data = [0] * 8

    # 设置参数索引
    data[0] = param_index & 0xFF        # 低字节
    data[1] = (param_index >> 8) & 0xFF # 高字节
    data[2] = 0x00
    data[3] = 0x00
    data[4] = 0x00
    data[5] = 0x00
    data[6] = 0x00
    data[7] = 0x00

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if state == 0:
        # 解析返回的参数值 (Byte4-7包含参数数据)
        if param_index == 0x7029:  # zero_sta 是 uint8 类型
            param_value = rx_data[4]
        elif param_index == 0x7019:  # mechPos 是 float 类型 (大端序)
            param_bytes = bytes([rx_data[7], rx_data[6], rx_data[5], rx_data[4]])
            param_value = struct.unpack('>f', param_bytes)[0]
        else:
            # 默认按uint8处理
            param_value = rx_data[4]

        if verbose:
            print(f"电机ID {motor_id}: 读取参数 0x{param_index:04X} = {param_value}")
    else:
        param_value = None
        if verbose:
            print(f"电机ID {motor_id}: 读取参数 0x{param_index:04X} 失败")

    return state, param_value


def initialize_motor_zero(bus, motor_id, zero_sta=1, verbose=False):
    """
    完整的电机零点初始化流程

    该函数会执行完整的零点设置流程:
    1. 设置zero_sta参数 (角度范围)
    2. 设置当前位置为机械零点
    3. 设置电机为运控模式

    参数:
        bus (can.Bus): CAN总线对象
        motor_id (int): 电机ID (1-8)
        zero_sta (int): 零点标志位 0=0~2π, 1=-π~π (默认: 1)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        bool: True=成功, False=失败

    示例:
        # 初始化单个电机的零点 (使用-π~π范围)
        if initialize_motor_zero(bus, motor_id=5, zero_sta=1, verbose=True):
            print("零点初始化成功")

        # 初始化所有电机
        for motor_id in range(1, 9):
            initialize_motor_zero(bus, motor_id, zero_sta=1)
            time.sleep(0.01)
    """
    if verbose:
        print(f"\n=== 初始化电机ID {motor_id} 的零点 ===")

    # 1. 设置zero_sta参数
    state1 = set_zero_sta_parameter(bus, motor_id, zero_sta, verbose=verbose)
    if state1 != 0:
        if verbose:
            print(f"  设置zero_sta失败")
        return False
    time.sleep(0.01)

    # 2. 设置机械零点
    state2 = set_motor_zero(bus, motor_id, verbose=verbose)
    if state2 != 0:
        if verbose:
            print(f"  设置机械零点失败")
        return False
    time.sleep(0.01)

    # 3. 设置为运控模式
    state3 = set_control_mode(bus, motor_id, mode='motion', verbose=verbose)
    if state3 != 0:
        if verbose:
            print(f"  设置运控模式失败")
        return False

    if verbose:
        print(f"  零点初始化成功!")

    return True


def batch_initialize_motor_zeros(can_channels=None, motor_ids=None, zero_sta=1, verbose=False):
    """
    批量初始化多个CAN通道上的所有电机零点

    参数:
        can_channels (list): CAN通道列表，默认 None (使用 ['can0', 'can1'])
        motor_ids (list): 电机ID列表，默认 None (使用 1-8)
        zero_sta (int): 零点标志位 0=0~2π, 1=-π~π (默认: 1)
        verbose (bool): 是否打印详细调试信息 (默认: False)

    返回:
        dict: {(can_channel, motor_id): success} 每个电机的初始化结果

    示例:
        # 初始化所有电机的零点
        results = batch_initialize_motor_zeros(verbose=True)

        # 检查结果
        for (channel, motor_id), success in results.items():
            if success:
                print(f"{channel} 电机{motor_id}: 成功")
            else:
                print(f"{channel} 电机{motor_id}: 失败")
    """
    if can_channels is None:
        can_channels = ['can0', 'can1']
    if motor_ids is None:
        motor_ids = list(range(1, 9))

    if verbose:
        print("=" * 80)
        print(f"批量初始化电机零点 (zero_sta={zero_sta})")
        print(f"CAN通道: {can_channels}, 电机ID: {motor_ids}")
        print("=" * 80)

    results = {}

    for can_channel in can_channels:
        if verbose:
            print(f"\n处理 {can_channel}...")

        bus = None
        try:
            bus = can.interface.Bus(interface='socketcan', channel=can_channel, bitrate=1000000)

            for motor_id in motor_ids:
                success = initialize_motor_zero(bus, motor_id, zero_sta, verbose=verbose)
                results[(can_channel, motor_id)] = success
                time.sleep(0.01)

        except Exception as e:
            if verbose:
                print(f"{can_channel} 初始化失败: {str(e)}")
            # 标记该通道所有电机为失败
            for motor_id in motor_ids:
                if (can_channel, motor_id) not in results:
                    results[(can_channel, motor_id)] = False

        finally:
            if bus:
                bus.shutdown()

    if verbose:
        success_count = sum(results.values())
        total_count = len(results)
        print("=" * 80)
        print(f"完成: {success_count}/{total_count} 个电机零点初始化成功")
        print("=" * 80)

    return results


# ====================== 只读电机状态接口 ======================

def get_motor_status_readonly(bus, motor_id, timeout=1.0, verbose=False):
    """
    只读查询电机状态（不改变电机控制状态）

    按 RS03 手册与本仓库协议，使用通信类型 0x0200 (MOTOR_REQUEST) 请求电机上报当前状态。
    - 不发送任何位置/速度/增益等控制参数，不会改变电机当前控制模式或控制量。

    返回:
        tuple: (state, info)
            - state: 0=成功, 1=失败/超时
            - info: dict 或 None，包含以下字段（成功时）：
                angle, velocity, torque, temperature
                mode_status, fault_status, fault_code
                raw: {rx_data, rx_id}

    示例:
        state, info = get_motor_status_readonly(bus, 5, verbose=True)
        if state == 0:
            print(info)
    """
    # 功能码 0x0200: 电机状态请求
    arbitration_id = 0x0200fd00 + motor_id
    data = [0] * 8
    data[0] = 0x01  # 请求帧 data[0]=0x01

    state, rx_data, rx_arbitration_id = send_extended_frame_main(
        bus, arbitration_id, data,
        block_receive=1,
        timeout=timeout,
        verbose=verbose
    )

    if state != 0:
        if verbose:
            print(f"电机ID {motor_id}: 状态请求失败/超时")
        return state, None

    # 解析反馈
    feedback = parse_motor_feedback(rx_data, motor_id) or {}
    mode_info = parse_control_mode_and_status(rx_arbitration_id, rx_data) or {}

    info = {}
    info.update(feedback)
    info.update(mode_info)
    info['raw'] = {
        'rx_data': rx_data,
        'rx_id': rx_arbitration_id
    }

    if verbose:
        print(f"电机ID {motor_id} 状态: {info}")

    return 0, info


def get_motor_basic_telemetry(bus, motor_id, timeout=1.0, verbose=False):
    """
    只读方式逐项读取关键遥测（通过通信类型 0x1100 逐个参数读取）

    当设备不支持 0x0200 状态回传时，可使用该函数按参数索引读取：
      - 0x7019: 负载端计圈机械角度 (float, rad)
      - 0x701B: 负载端转速 (float, rad/s)
      - 0x701A: iq 滤波值 (float) – 可近似表征力矩/电流
      - 0x701C: 母线电压 (float, V)

    返回:
        tuple: (state, info)
            - state: 0=成功（至少读取成功一项），1=全部失败
            - info: dict，包含成功读取到的键
    """
    ok = 0
    info = {}

    # 角度
    s, val = read_motor_parameter(bus, motor_id, 0x7019, timeout=timeout, verbose=verbose)
    if s == 0 and val is not None:
        info['mech_pos'] = float(val)
        ok |= 1

    # 速度
    s, val = read_motor_parameter(bus, motor_id, 0x701B, timeout=timeout, verbose=verbose)
    if s == 0 and val is not None:
        info['mech_vel'] = float(val)
        ok |= 1

    # iq 滤波值（近似电流/力矩指标）
    s, val = read_motor_parameter(bus, motor_id, 0x701A, timeout=timeout, verbose=verbose)
    if s == 0 and val is not None:
        info['iqf'] = float(val)
        ok |= 1

    # 母线电压
    s, val = read_motor_parameter(bus, motor_id, 0x701C, timeout=timeout, verbose=verbose)
    if s == 0 and val is not None:
        info['vbus'] = float(val)
        ok |= 1

    if verbose:
        if ok:
            print(f"电机ID {motor_id} 基本遥测: {info}")
        else:
            print(f"电机ID {motor_id} 基本遥测读取失败")

    return (0 if ok else 1), (info if ok else None)
