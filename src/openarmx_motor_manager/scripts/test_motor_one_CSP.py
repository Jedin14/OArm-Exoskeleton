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
@File    :   move_to_pos_position_CSP.py
@Time    :   2025/10/02 18:33:41
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   测试单电机CSP模式
'''

import time
from openarmx_arm_driver import Robot


if __name__ == "__main__":
    print("="*60)
    print("测试单电机CSP模式")
    print("="*60)

    side = 'right'  # 测试电机所在机械臂侧 'right' 或 'left'
    motor_id = 1
    position = 0.3  # 电机运动目标位置

    # Robot 会自动检测并启用 CAN 接口
    # 如果 can0 或 can1 未启用，会自动尝试启用
    with Robot(right_can_channel='can0', left_can_channel='can1') as robot:

        # 设置为 MIT 模式
        robot.set_mode_all('csp')

        # 使能所有电机
        robot.enable_all()

        robot.set_csp_limits_all(speed_limit=1.0)

        # 单电机运动
        robot.move_one_joint_csp(arm=side, 
                                 motor_id=motor_id, 
                                 position=0.3)
        time.sleep(3)

        # 回到零位
        robot.move_one_joint_csp(arm=side, 
                                 motor_id=motor_id, 
                                 position=0.0)
        time.sleep(2)

        # 停止所有电机
        robot.disable_all()

        robot.shutdown()
