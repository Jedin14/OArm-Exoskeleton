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
@File    :   script_finder.py
@Time    :   2026/01/05 18:50:49
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   脚本文件搜索工具
'''



import os
from pathlib import Path
from typing import List, Dict


class ScriptFinder:
    """脚本文件搜索工具"""

    @staticmethod
    def find_scripts(script_name: str, search_paths: List[str] = None, max_depth: int = 6) -> List[str]:
        """搜索指定名称的脚本文件

        Args:
            script_name: 脚本文件名
            search_paths: 搜索路径列表，默认为用户主目录
            max_depth: 最大搜索深度，默认为6层

        Returns:
            List[str]: 找到的脚本路径列表
        """
        if search_paths is None:
            # 默认搜索路径
            home = str(Path.home())
            search_paths = [
                os.path.join(home, 'openarm'),
                os.path.join(home, 'ros2_ws'),
                os.path.join(home, 'workspace'),
                '/opt/ros',
            ]

        found_scripts = []

        for base_path in search_paths:
            if not os.path.exists(base_path):
                continue

            # 使用 os.walk 递归搜索
            try:
                for root, dirs, files in os.walk(base_path):
                    # 计算当前深度
                    depth = root[len(base_path):].count(os.sep)
                    if depth > max_depth:
                        # 超过最大深度，跳过子目录
                        dirs[:] = []
                        continue

                    # 跳过隐藏目录和一些不需要搜索的目录
                    dirs[:] = [d for d in dirs if not d.startswith('.') and
                              d not in ['build', 'install', 'log', '__pycache__', 'node_modules']]

                    # 检查是否找到目标文件
                    if script_name in files:
                        script_path = os.path.join(root, script_name)
                        # 检查是否有执行权限或是shell脚本
                        if script_path.endswith('.sh') or os.access(script_path, os.X_OK):
                            found_scripts.append(script_path)

            except PermissionError:
                # 没有权限访问某些目录，跳过
                continue
            except Exception as e:
                print(f"搜索 {base_path} 时出错: {e}")
                continue

        # 去重并排序
        found_scripts = sorted(list(set(found_scripts)))
        return found_scripts

    @staticmethod
    def find_moveit_scripts() -> Dict[str, List[str]]:
        """搜索 MoveIt 启动脚本

        Returns:
            Dict[str, List[str]]: 包含两个键的字典
                - 'moveit_sim': 仿真版脚本路径列表
                - 'moveit_can': CAN版脚本路径列表
        """
        results = {
            'moveit_sim': ScriptFinder.find_scripts('run_bimanual_moveit_sim.sh'),
            'moveit_can': ScriptFinder.find_scripts('run_bimanual_moveit_with_can2.0.sh')
        }

        return results

    @staticmethod
    def get_script_display_name(script_path: str) -> str:
        """获取脚本的显示名称（用于UI展示）

        Args:
            script_path: 脚本完整路径

        Returns:
            str: 格式化的显示名称
        """
        # 提取相对于用户主目录的路径
        home = str(Path.home())
        if script_path.startswith(home):
            relative_path = '~' + script_path[len(home):]
        else:
            relative_path = script_path

        # 提取包含工作空间名称的路径
        parts = Path(script_path).parts
        if len(parts) > 3:
            # 显示最后3-4层目录
            display_parts = parts[-4:]
            display_name = os.path.join(*display_parts)
        else:
            display_name = script_path

        return display_name

    @staticmethod
    def validate_script(script_path: str) -> bool:
        """验证脚本是否有效

        Args:
            script_path: 脚本路径

        Returns:
            bool: 脚本是否有效（存在且可执行）
        """
        if not os.path.exists(script_path):
            return False

        if not os.path.isfile(script_path):
            return False

        # 检查是否是shell脚本
        if not script_path.endswith('.sh'):
            return False

        return True
