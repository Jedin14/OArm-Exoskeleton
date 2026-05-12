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
@File    :   en_all_can.py
@Time    :   2025/12/10 17:12:38
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   None
'''


"""
自动CAN接口启用脚本 - 智能检测并启用所有可用的CAN通道
功能：
  - 自动检测系统中所有的CAN接口（can0, can1, can2...）
  - 跳过不存在或虚拟的接口
  - 配置1Mbps波特率，禁用loopback
  - 友好的错误处理和进度显示
"""

import sys

try:
    from openarmx_arm_driver import (
        get_all_can_interfaces,
        check_can_interface_type,
        enable_can_interface,
        verify_can_interface
    )
except ImportError:
    print("错误: 无法导入 openarmx_arm_driver 库")
    print("请确保在正确的目录下运行此脚本")
    sys.exit(1)


def main():
    """主函数"""

    # 1. 检测所有可用的CAN接口
    can_interfaces = get_all_can_interfaces()

    if not can_interfaces:
        print("✗ 未检测到任何CAN接口！")
        print("\n可能的原因:")
        print("  1. 系统没有CAN硬件设备")
        print("  2. CAN驱动未加载")
        print("  3. 需要检查硬件连接")
        print("\n提示: 运行 'ip link show' 查看所有网络接口")
        sys.exit(1)


    # 2. 过滤虚拟接口
    real_interfaces = []
    for iface in can_interfaces:
        if check_can_interface_type(iface):
            real_interfaces.append(iface)

    if not real_interfaces:
        print("\n✗ 没有找到真实的CAN接口！")
        sys.exit(1)

    print(f"\n找到 {len(real_interfaces)} 个真实CAN接口")

    # 3. 逐个启用CAN接口
    success_count = 0
    failed_interfaces = []

    for iface in real_interfaces:
        if enable_can_interface(iface, bitrate=1000000, loopback=False, verbose=True):
            success_count += 1
        else:
            failed_interfaces.append(iface)

    # 4. 验证启用结果
    # print('\n')
    # print("="*50)
    # print("接口状态")
    # print("="*50)
    # for iface in real_interfaces:
    #     status = verify_can_interface(iface)
    #     status_str = "✓ UP" if status else "✗ DOWN"
    #     print(f"  {iface}: {status_str}")

    


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断操作")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
