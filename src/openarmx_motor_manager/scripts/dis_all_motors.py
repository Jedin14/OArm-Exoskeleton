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
@File    :   dis_all_motors.py
@Time    :   2025/12/23 15:00:57
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   None
'''



from openarmx_arm_driver import Robot, get_available_can_interfaces, pair_can_channels


def main():
    """主函数 - 失能所有Robot的电机"""

    print("=" * 60)
    print("失能所有电机工具 (Robot版)")
    print("=" * 60)

    # 步骤1: 检测CAN接口
    print("\n步骤1: 检测可用的CAN接口...")
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
    print("\n步骤2: 配对CAN通道...")
    valid_pairs = pair_can_channels(can_interfaces)

    if not valid_pairs:
        print("\n✗ 没有找到有效的配对组！")
        print("提示: 机器人CAN通道需要成对插入 (0-1, 2-3, 4-5...)")
        return 1

    print(f"\n找到 {len(valid_pairs)} 个有效的配对组")

    # 步骤3: 为每个配对组创建Robot实例并失能电机
    print("\n步骤3: 失能每个Robot的所有电机...")
    print("=" * 60)

    robots = []
    success_count = 0
    fail_count = 0

    for idx, (right_can, left_can) in enumerate(valid_pairs, 1):
        print(f"\n【Robot #{idx}】CAN通道: {right_can}-{left_can}")
        print("-" * 60)

        try:
            # 实例化Robot
            robot = Robot(right_can_channel=right_can, left_can_channel=left_can)
            robots.append(robot)

            # 失能所有电机
            print(f"正在失能 Robot #{idx} 的所有电机...")
            robot.disable_all()
            print(f"✓ Robot #{idx} ({right_can}-{left_can}) 所有电机已失能")
            success_count += 1

        except Exception as e:
            print(f"✗ Robot #{idx} ({right_can}-{left_can}) 失能失败: {e}")
            fail_count += 1

    # 步骤4: 清理资源
    print("\n" + "=" * 60)
    print("清理资源...")
    for robot in robots:
        try:
            robot.shutdown()
        except:
            pass

    # 显示总结
    print("\n" + "=" * 60)
    print("失能操作完成！")
    print(f"  成功: {success_count} 个Robot")
    print(f"  失败: {fail_count} 个Robot")
    print("=" * 60)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    try:
        exit_code = main()
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断操作 (Ctrl+C)")
        exit(130)
    except Exception as e:
        print(f"\n✗ 程序异常: {str(e)}")
        import traceback
        traceback.print_exc()
        exit(1)
