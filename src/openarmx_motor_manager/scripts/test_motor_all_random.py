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
@File    :   test_motor_all_random.py
@Time    :   2025/12/23 18:41:11
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   电机随机运动测试
'''


import sys
import time
import random
from openarmx_arm_driver import Robot, get_available_can_interfaces, pair_can_channels


def test_robot_random_motion(robot, test_id, position_max=0.3, kp_list=10.0, kd_list=1.0):
    """
    让Robot的所有电机做随机幅度的往复运动

    参数：
        robot: Robot实例
        test_id: 测试轮次ID
        position_max: 最大运动角度（弧度）
    """
    print(f"\n----- 测试轮次 {test_id} -----")

    try:
        # 步骤1: 生成随机目标位置
        target_position = []

        for i in range(16):  # 电机1-7
            # 随机生成运动幅度 (20%-100% 的 position_max)
            random_amplitude = random.uniform(0.2 * position_max, position_max)

            target_position.append(random_amplitude)
        


        # 步骤2: 运动到随机位置
        print("\n>>> 运动到随机位置...")
        robot.move_joints_mit(target_position[:8], target_position[8:], kp=kp_list, kd=kd_list)

        time.sleep(1.0)  # 等待运动完成

        # 步骤3: 回零位
        print(">>> 回到零位...")
        robot.move_all_to_zero(kp=kp_list, kd=kd_list)

        time.sleep(0.5)  # 等待回零完成

        print(f"✓ 测试轮次 {test_id} 完成")

    except Exception as e:
        print(f"✗ 测试轮次 {test_id} 失败: {e}")
        raise


def main():
    """主函数 - 所有电机随机运动测试"""

    print("=" * 60)
    print("所有电机随机运动测试工具")
    print("=" * 60)

    # 步骤1: 检测CAN接口
    can_interfaces = get_available_can_interfaces()

    if not can_interfaces:
        print("✗ 未检测到已启用的CAN接口！")
        print("\n可能的原因:")
        print("  1. CAN接口未启用，请先运行: python3 en_all_can.py")
        print("  2. CAN硬件未连接")
        print("  3. 驱动未加载")
        print("\n提示: 运行 'ip link show' 查看所有网络接口")
        return 1

    print(f"检测到可用的CAN通道: {sorted(can_interfaces)}")

    # 步骤2: 配对CAN通道
    valid_pairs = pair_can_channels(can_interfaces)

    if not valid_pairs:
        print("\n✗ 没有找到有效的配对组！")
        print("提示: 机器人CAN通道需要成对插入 (0-1, 2-3, 4-5...)")
        return 1

    print(f"\n找到 {len(valid_pairs)} 个有效的配对组")

    # 步骤3: 为每个配对组创建Robot实例并测试
    print("\n步骤3: 开始随机运动测试...")
    print("提示: 按 Ctrl+C 停止测试")
    print("=" * 60)

    # 测试参数
    position_max = 0.3  # 最大运动角度（弧度）
    kp_list = [50, 50, 50, 50, 5, 5, 5, 5]
    kd_list = [5, 5, 5, 5, 0.25, 0.25, 0.25, 0.25]

    robots = []

    try:
        # 初始化所有Robot
        for idx, (right_can, left_can) in enumerate(valid_pairs, 1):
            print(f"\n【Robot #{idx}】CAN通道: {right_can}-{left_can}")
            print("-" * 60)

            try:
                # 实例化Robot
                robot = Robot(right_can_channel=right_can, left_can_channel=left_can)
                robots.append(robot)

                # 设置为MIT模式
                robot.set_mode_all('mit')

                # 使能所有电机 (1-7, 不包括电机8)
                robot.enable_all()

                print(f"✓ Robot #{idx} 初始化完成")

            except Exception as e:
                print(f"✗ Robot #{idx} ({right_can}-{left_can}) 初始化失败: {e}")
                raise

        # 循环测试
        test_id = 0
        while True:
            test_id += 1

            for idx, robot in enumerate(robots, 1):
                print(f"\n【Robot #{idx}】")
                
                test_robot_random_motion(robot, test_id, position_max, kp_list, kd_list)

    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断测试 (Ctrl+C)")
        print("正在让所有电机回零位...")

        # 先让所有电机回零
        for idx, robot in enumerate(robots):
            try:
                print(f"\n回零 Robot #{idx} 的电机...")
                robot.move_all_to_zero()
                print(f"✓ Robot #{idx} 已回零")
            except Exception as e:
                print(f"✗ Robot #{idx} 回零失败: {e}")

        time.sleep(0.5)  # 等待回零完成

    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 步骤4: 清理资源
        print("\n" + "=" * 60)
        print("清理资源...")
        for idx, robot in enumerate(robots):
            try:
                print(f"失能 Robot #{idx} 的所有电机...")
                robot.disable_all()
                robot.shutdown()
            except Exception as e:
                print(f"✗ Robot #{idx} 清理失败: {e}")

        print("\n✓ 测试结束！所有资源已清理")

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断操作 (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ 程序异常: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
