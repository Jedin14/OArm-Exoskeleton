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
@File    :   config_manager.py
@Time    :   2025/12/24
@Author  :   Claude
@Version :   1.0
@Desc    :   配置文件管理器
'''

import os
import yaml
from pathlib import Path


class ConfigManager:
    """配置文件管理器"""

    def __init__(self, config_file='config.yaml'):
        """初始化配置管理器

        Args:
            config_file: 配置文件名，默认为 config.yaml
        """
        # 获取配置目录路径
        self.config_dir = Path(__file__).parent
        self.config_file = self.config_dir / config_file

        # 默认配置
        self.default_config = {
            'version': '2.0.0',
            'first_run': True,
            'scripts': {
                'moveit_sim': '',
                'moveit_can': ''
            },
            'default_scripts': {
                'moveit_sim': '',
                'moveit_can': ''
            },
            'language': 'zh_CN',
            'last_can_channels': {
                'right': 'can0',
                'left': 'can1'
            },
            'sudo_password': '',
            'default_password': ''
        }

        # 加载配置
        self.config = self.load_config()

    def load_config(self):
        """加载配置文件

        Returns:
            dict: 配置字典
        """
        if not self.config_file.exists():
            # 配置文件不存在，使用默认配置
            return self.default_config.copy()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            # 确保所有默认键都存在
            for key, value in self.default_config.items():
                if key not in config:
                    config[key] = value
                elif isinstance(value, dict):
                    # 递归合并字典
                    for sub_key, sub_value in value.items():
                        if sub_key not in config[key]:
                            config[key][sub_key] = sub_value

            return config

        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return self.default_config.copy()

    def save_config(self):
        """保存配置到文件

        Returns:
            bool: 保存是否成功
        """
        try:
            # 确保配置目录存在
            self.config_dir.mkdir(parents=True, exist_ok=True)

            with open(self.config_file, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)

            return True

        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False

    def is_first_run(self):
        """检查是否首次运行

        Returns:
            bool: 是否首次运行
        """
        return self.config.get('first_run', True)

    def set_first_run_completed(self):
        """标记首次运行已完成"""
        self.config['first_run'] = False
        self.save_config()

    def get_script_path(self, script_type):
        """获取脚本路径

        Args:
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')

        Returns:
            str: 脚本路径
        """
        return self.config['scripts'].get(script_type, '')

    def set_script_path(self, script_type, path):
        """设置脚本路径

        Args:
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')
            path: 脚本路径
        """
        self.config['scripts'][script_type] = path

    def get_language(self):
        """获取语言设置

        Returns:
            str: 语言代码
        """
        return self.config.get('language', 'zh_CN')

    def set_language(self, language):
        """设置语言

        Args:
            language: 语言代码
        """
        self.config['language'] = language
        self.save_config()

    def get_can_channels(self):
        """获取CAN通道配置

        Returns:
            dict: CAN通道配置
        """
        return self.config.get('last_can_channels', {'right': 'can0', 'left': 'can1'})

    def set_can_channels(self, right, left):
        """设置CAN通道

        Args:
            right: 右臂CAN通道
            left: 左臂CAN通道
        """
        self.config['last_can_channels'] = {'right': right, 'left': left}
        self.save_config()

    def get_default_script_path(self, script_type):
        """获取默认脚本路径（首次配置时的路径）

        Args:
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')

        Returns:
            str: 默认脚本路径
        """
        if 'default_scripts' not in self.config:
            self.config['default_scripts'] = {'moveit_sim': '', 'moveit_can': ''}
        return self.config['default_scripts'].get(script_type, '')

    def set_default_script_path(self, script_type, path):
        """设置默认脚本路径（首次配置时的路径）

        Args:
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')
            path: 脚本路径
        """
        if 'default_scripts' not in self.config:
            self.config['default_scripts'] = {'moveit_sim': '', 'moveit_can': ''}
        self.config['default_scripts'][script_type] = path

    def get_sudo_password(self):
        """获取sudo密码

        Returns:
            str: sudo密码
        """
        return self.config.get('sudo_password', '')

    def set_sudo_password(self, password):
        """设置sudo密码

        Args:
            password: sudo密码
        """
        self.config['sudo_password'] = password
        self.save_config()

    def has_sudo_password(self):
        """检查是否已保存sudo密码

        Returns:
            bool: 是否已保存密码
        """
        return bool(self.config.get('sudo_password', ''))

    def get_default_password(self):
        """获取默认密码（首次配置时的密码）

        Returns:
            str: 默认密码
        """
        return self.config.get('default_password', '')

    def set_default_password(self, password):
        """设置默认密码（首次配置时的密码）

        Args:
            password: 默认密码
        """
        self.config['default_password'] = password
        self.save_config()

    def reset_to_defaults(self):
        """重置为默认配置（恢复到首次配置时的所有设置）"""
        # 保存默认脚本路径
        default_scripts = self.config.get('default_scripts', {'moveit_sim': '', 'moveit_can': ''})

        # 保存默认密码
        default_password = self.config.get('default_password', '')

        # 将当前脚本路径恢复为默认路径
        self.config['scripts'] = default_scripts.copy()

        # 将当前密码恢复为默认密码
        self.config['sudo_password'] = default_password

        self.save_config()
