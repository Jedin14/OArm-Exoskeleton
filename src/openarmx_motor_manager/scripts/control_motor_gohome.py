
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
@File    :   control_motor_gohome.py
@Time    :   2025/12/23 14:41:42
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   机器人回零
'''

from openarmx_arm_driver import Robot, get_available_can_interfaces


def pair_can_channels(available_channels):
    """
    将可用的CAN通道按固定规则配对
    配对规则：0-1, 2-3, 4-5, 6-7...
    只有当一组中的两个通道都可用时，才保留该组

    参数：
        available_channels: 可用的CAN通道列表（字符串格式，如 ['can0', 'can1']）

    返回：
        有效的配对组列表，例如：[(0, 1), (2, 3), (4, 5)]
    """
    # 从字符串中提取通道号（'can0' -> 0）
    channel_numbers = []
    for channel in available_channels:
        # 提取 'can' 后面的数字部分
        if channel.startswith('can'):
            try:
                num = int(channel[3:])  # 提取 'can' 之后的数字
                channel_numbers.append(num)
            except ValueError:
                continue

    # 将可用通道号转换为集合，方便查找
    available_set = set(channel_numbers)

    # 存储有效的配对组
    valid_pairs = []

    # 查找最大的通道号，以确定需要检查的配对范围
    if channel_numbers:
        max_channel = max(channel_numbers)

        # 按固定规则检查配对：0-1, 2-3, 4-5...
        for i in range(0, max_channel + 2, 2):
            left = i
            right = i + 1

            # 只有当两个通道都可用时，才保留该组
            if left in available_set and right in available_set:
                valid_pairs.append((f'can{left}', f'can{right}'))
                print(f"✓ 发现有效配对组: can{left}-can{right}")
            elif left in available_set or right in available_set:
                # 如果只有一个通道可用，给出提示
                available_one = left if left in available_set else right
                missing_one = right if left in available_set else left
                print(f"✗ 配对组 can{left}-can{right} 不完整 (can{available_one}可用，can{missing_one}缺失)，已剔除")

    return valid_pairs


def main():
    """主函数 - 自动扫描所有CAN接口上的电机状态"""

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

    # 步骤3: 为每个配对组创建Robot实例并查看状态
    print("=" * 60)
    print("Robot回零...")
    print("=" * 60)

    robots = []

    for idx, (left_can, right_can) in enumerate(valid_pairs, 1):
        print(f"\n【Robot #{idx}】CAN通道: {left_can}-{right_can}")
        print("-" * 60)

        try:
            # 实例化Robot
            robot = Robot(left_can_channel=left_can, right_can_channel=right_can)
            robots.append(robot)

            # 使能所有电机
            robot.enable_all()

            # 回零
            robot.move_all_to_zero(kp=10, kd=1.0)

        except Exception as e:
            print(f"✗ Robot #{idx} (can{left_can}-can{right_can}) 初始化或查询失败: {e}")

    # 步骤4: 清理资源
    print("\n" + "=" * 60)
    print("清理资源...")
    for robot in robots:
        try:
            robot.close()
        except:
            pass

    print(f"\n✓ 完成！共检查了 {len(robots)} 个Robot")
    return 0


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