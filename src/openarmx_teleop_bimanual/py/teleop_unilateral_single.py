#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# OpenArmX Research and Education License
#
# Copyright (c) 2026 Chengdu Changshu Robot Co., Ltd.
# https://www.openarmx.com
#
# This software is licensed for non-commercial research, academic,
# and educational use only.
# Commercial use is strictly prohibited without prior written permission.
#
# Redistribution for permitted non-commercial purposes must retain
# this notice.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.


"""
简化版单向遥操作测试脚本（Python）
- 默认: leader 使用 can0, follower 使用 can2
- 直接读取 leader 7 关节 + 1 夹爪的角度, 以固定增益发送到 follower

注意: 这是一个原型调试脚本, 只做基本的"位置跟踪"测试.
"""

import time
import sys
import signal
import os
import can

# add motor_tests_openarmx_com to sys.path so 'lib' package is found
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOTOR_TESTS_PATH = os.path.join(BASE_DIR, 'motor_tests_openarmx_com')
if MOTOR_TESTS_PATH not in sys.path:
    sys.path.insert(0, MOTOR_TESTS_PATH)

from lib.motor_control import set_zero_position
from lib.motor_control import (
    get_motor_status,
    parse_motor_feedback,
    set_motion_control,
    enable_motor,
    disable_motor,
)

# 关节数量 (7 关节 + 1 夹爪)
NUM_JOINTS = 8

# leader/follower 使用相同的电机 ID 布局: 1..7 关节, 8 为夹爪
MOTOR_IDS = list(range(1, NUM_JOINTS + 1))

# 控制频率 (Hz)
CONTROL_HZ = 200.0
CONTROL_DT = 1.0 / CONTROL_HZ

# leader -> follower 位置增益 (更高=更紧跟, 更低=更柔软)
# KP_FOLLOW = 1.0
# KD_FOLLOW = 0.1
KP_FOLLOW = 100.0
KD_FOLLOW = 5.5

running = True


def signal_handler(sig, frame):
    global running
    print("\n[INFO] Ctrl+C detected, stopping teleop loop...")
    running = False


def create_bus(channel: str) -> can.Bus:
    """创建一个 socketcan 总线"""
    print(f"[INFO] Opening CAN bus {channel} @1Mbps")
    return can.interface.Bus(bustype="socketcan", channel=channel, bitrate=1000000)


def enable_all_motors(bus: can.Bus, name: str):
    print(f"[INFO] Enabling all motors on {name}...")
    for mid in MOTOR_IDS:
        try:
            enable_motor(bus, mid, verbose=False)
            time.sleep(0.01)
        except Exception as e:
            print(f"[WARN] enable_motor({name}, id={mid}) failed: {e}")


def disable_all_motors(bus: can.Bus, name: str):
    print(f"[INFO] Disabling all motors on {name}...")
    for mid in MOTOR_IDS:
        try:
            disable_motor(bus, mid, verbose=False)
            time.sleep(0.005)
        except Exception as e:
            print(f"[WARN] disable_motor({name}, id={mid}) failed: {e}")


def read_leader_positions(bus: can.Bus):
    """读取 leader 端所有电机的当前角度 (rad)"""
    positions = []
    for mid in MOTOR_IDS:
        try:
            state, rx_data, rx_id = get_motor_status(bus, mid)
            if state != 0:
                # 通信故障, 保持上一次值 (这里简单返回 0.0)
                positions.append(0.0)
                continue
            fb = parse_motor_feedback(rx_data, mid)
            positions.append(fb["angle"])
        except Exception as e:
            print(f"[WARN] read_leader_positions: motor {mid} error: {e}")
            positions.append(0.0)
    return positions



def teleop_unilateral_loop(leader_can: str = "can0", follower_can: str = "can2"):
    global running

    # 注册 Ctrl+C 信号
    signal.signal(signal.SIGINT, signal_handler)

    # 创建 CAN 总线
    leader_bus = create_bus(leader_can)
    follower_bus = create_bus(follower_can)

    try:
        # 使能 leader/follower 所有电机
        # enable_all_motors(leader_bus, "leader")
        enable_all_motors(follower_bus, "follower")

        # # 回零
        for motorID in range(1,9):                                 
            # set_zero_position(leader_bus,motorID,5,0.5,verbose=True)
            set_zero_position(follower_bus,motorID,5,0.5,verbose=True)

        print("[INFO] Starting unilateral teleop loop...")
        last_time = time.time()

        while running:
            t_start = time.time()

            # 1) 读取 leader 当前角度
            leader_pos = read_leader_positions(leader_bus)

            # 2) 将该角度作为 follower 目标位置
            for idx, mid in enumerate(MOTOR_IDS):
                target_pos = leader_pos[idx]
                try:
                    # 这里使用纯位置控制: 速度=0, 扭矩=0
                    set_motion_control(
                        follower_bus,
                        motor_id=mid,
                        position=target_pos,
                        velocity=0.0,
                        torque=0.0,
                        kp=KP_FOLLOW,
                        kd=KD_FOLLOW,
                        wait_response=False,
                        verbose=False,
                    )
                except Exception as e:
                    print(f"[WARN] set_motion_control follower motor {mid} error: {e}")

            # 3) 控制频率节拍
            elapsed = time.time() - t_start
            sleep_time = CONTROL_DT - elapsed
            if sleep_time > 0:
                # print(sleep_time)
                time.sleep(sleep_time)

        print("[INFO] Teleop loop exited.")

    finally:
        # 安全关闭
        disable_all_motors(leader_bus, "leader")
        disable_all_motors(follower_bus, "follower")
        try:
            leader_bus.shutdown()
            follower_bus.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    # 默认参数: right_arm, leader=can0, follower=can2
    leader_can = "can0"
    follower_can = "can2"

    if len(sys.argv) >= 3:
        leader_can = sys.argv[1]
        follower_can = sys.argv[2]

    print(f"[INFO] teleop_unilateral_test.py leader={leader_can}, follower={follower_can}")
    teleop_unilateral_loop(leader_can, follower_can)
