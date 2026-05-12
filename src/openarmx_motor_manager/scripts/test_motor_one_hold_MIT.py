#!/usr/bin/env python
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


'''
@File    :   test_motor_one_hold.py
@Time    :   2025/12/23 18:57:37
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   测试单电机保持
'''

import time
from openarmx_arm_driver import Robot


if __name__ == "__main__":
    print("="*60)
    print("测试单电机")
    print("="*60)

    side = 'right'  # 测试电机所在机械臂侧 'right' 或 'left'
    motor_id = 1
    position = 0.3  # 电机运动目标位置

    # Robot 会自动检测并启用 CAN 接口
    # 如果 can0 或 can1 未启用，会自动尝试启用
    with Robot(right_can_channel='can0', left_can_channel='can1') as robot:

        # 设置为 MIT 模式
        robot.set_mode_all('mit')

        # 使能所有电机
        robot.enable_all()

        # 单电机运动
        robot.move_one_joint_mit(
            arm=side,
            motor_id=motor_id,
            position=position,
            kp=10.0,
            kd=1.0
        )

        robot.shutdown()