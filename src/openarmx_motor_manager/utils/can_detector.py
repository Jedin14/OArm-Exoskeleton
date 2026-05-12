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
@File    :   can_detector.py
@Time    :   2026/01/05 18:45:26
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   CAN接口检测器
'''



import os
import subprocess
import re
from typing import List, Tuple, Dict


class CANDetector:
    """CAN接口检测器"""

    @staticmethod
    def detect_can_interfaces() -> List[str]:
        """检测系统中所有可用的CAN接口

        Returns:
            List[str]: CAN接口列表，如 ['can0', 'can1', 'can2', 'can3']
        """
        can_interfaces = []

        try:
            # 方法1: 使用 ip link show
            result = subprocess.run(
                ['ip', 'link', 'show'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # 匹配 can0, can1, can2 等
                can_pattern = re.compile(r'\d+:\s+(can\d+):')
                matches = can_pattern.findall(result.stdout)
                can_interfaces.extend(matches)

        except Exception as e:
            print(f"检测CAN接口失败 (ip link): {e}")

        # 方法2: 检查 /sys/class/net/ 目录
        try:
            net_dir = '/sys/class/net/'
            if os.path.exists(net_dir):
                for interface in os.listdir(net_dir):
                    if interface.startswith('can'):
                        if interface not in can_interfaces:
                            can_interfaces.append(interface)
        except Exception as e:
            print(f"检测CAN接口失败 (sys/class/net): {e}")

        # 排序并去重
        can_interfaces = sorted(list(set(can_interfaces)))

        return can_interfaces

    @staticmethod
    def check_can_status(interface: str) -> Dict[str, any]:
        """检查CAN接口状态

        Args:
            interface: CAN接口名称，如 'can0'

        Returns:
            Dict: 状态信息
                {
                    'exists': bool,
                    'state': str,  # 'UP', 'DOWN', 'UNKNOWN'
                    'bitrate': int or None,
                    'error_count': int or None
                }
        """
        status = {
            'exists': False,
            'state': 'UNKNOWN',
            'bitrate': None,
            'error_count': None
        }

        try:
            # 检查接口是否存在
            result = subprocess.run(
                ['ip', 'link', 'show', interface],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                status['exists'] = True

                # 检查状态
                if 'state UP' in result.stdout:
                    status['state'] = 'UP'
                elif 'state DOWN' in result.stdout:
                    status['state'] = 'DOWN'

                # 提取比特率
                bitrate_match = re.search(r'bitrate\s+(\d+)', result.stdout)
                if bitrate_match:
                    status['bitrate'] = int(bitrate_match.group(1))

        except Exception as e:
            print(f"检查CAN状态失败: {e}")

        return status

    @staticmethod
    def auto_pair_can_interfaces(interfaces: List[str]) -> List[Tuple[str, str]]:
        """自动配对CAN接口

        按照顺序配对：(can0, can1), (can2, can3), (can4, can5) ...

        Args:
            interfaces: CAN接口列表

        Returns:
            List[Tuple[str, str]]: 配对列表，如 [('can0', 'can1'), ('can2', 'can3')]
        """
        pairs = []

        # 按数字排序
        sorted_interfaces = sorted(interfaces, key=lambda x: int(re.findall(r'\d+', x)[0]) if re.findall(r'\d+', x) else 0)

        # 两两配对
        for i in range(0, len(sorted_interfaces), 2):
            if i + 1 < len(sorted_interfaces):
                pairs.append((sorted_interfaces[i], sorted_interfaces[i + 1]))

        return pairs

    @staticmethod
    def get_unpaired_interfaces(interfaces: List[str], pairs: List[Tuple[str, str]]) -> List[str]:
        """获取未配对的CAN接口

        Args:
            interfaces: 所有CAN接口
            pairs: 已配对的接口

        Returns:
            List[str]: 未配对的接口
        """
        paired_interfaces = set()
        for pair in pairs:
            paired_interfaces.add(pair[0])
            paired_interfaces.add(pair[1])

        unpaired = [iface for iface in interfaces if iface not in paired_interfaces]
        return unpaired

    @staticmethod
    def validate_can_pair(right_channel: str, left_channel: str) -> Tuple[bool, str]:
        """验证CAN配对是否有效

        Args:
            right_channel: 右臂CAN通道
            left_channel: 左臂CAN通道

        Returns:
            Tuple[bool, str]: (是否有效, 错误信息)
        """
        # 检查是否为空
        if not right_channel or not left_channel:
            return False, "CAN通道不能为空"

        # 检查是否相同
        if right_channel == left_channel:
            return False, "左右CAN通道不能相同"

        # 检查格式
        can_pattern = re.compile(r'^can\d+$')
        if not can_pattern.match(right_channel):
            return False, f"无效的CAN通道格式: {right_channel}"
        if not can_pattern.match(left_channel):
            return False, f"无效的CAN通道格式: {left_channel}"

        # 检查是否存在
        available = CANDetector.detect_can_interfaces()
        if right_channel not in available:
            return False, f"CAN接口不存在: {right_channel}"
        if left_channel not in available:
            return False, f"CAN接口不存在: {left_channel}"

        return True, ""


# 测试代码
if __name__ == '__main__':
    print("=" * 60)
    print("CAN接口检测")
    print("=" * 60)

    # 检测所有CAN接口
    interfaces = CANDetector.detect_can_interfaces()
    print(f"\n检测到的CAN接口: {interfaces}")

    # 自动配对
    if interfaces:
        pairs = CANDetector.auto_pair_can_interfaces(interfaces)
        print(f"\n自动配对结果: {pairs}")

        # 检查未配对的接口
        unpaired = CANDetector.get_unpaired_interfaces(interfaces, pairs)
        if unpaired:
            print(f"未配对的接口: {unpaired}")

        # 检查每个接口的状态
        print("\nCAN接口状态:")
        for iface in interfaces:
            status = CANDetector.check_can_status(iface)
            print(f"  {iface}: {status}")

    else:
        print("\n未检测到任何CAN接口")
