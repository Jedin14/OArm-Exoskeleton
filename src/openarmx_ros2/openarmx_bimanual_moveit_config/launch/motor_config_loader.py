#!/usr/bin/python3
# -*- coding: UTF-8 -*-
'''
@File    :   motor_config_loader.py
@Time    :   2025/10/01 11:49:13
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   None
'''

import yaml
import os

class MotorConfigLoader:
    """电机配置加载器"""

    def __init__(self, config_file_path="motor_config.yaml"):
        """
        初始化配置加载器
        :param config_file_path: yaml配置文件路径
        """
        self.config_file_path = config_file_path
        self.config = self._load_config()
        self.motor_dict = {
            '1': 'RS04',
            '2': 'RS04',
            '3': 'RS03',
            '4': 'RS03',
            '5': 'RS00',
            '6': 'RS00',
            '7': 'RS00',
            '8': 'RS00',
        }

    def _load_config(self):
        """加载yaml配置文件"""
        try:
            # 获取当前脚本所在目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            full_path = os.path.join(current_dir, self.config_file_path)

            with open(full_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
                return config
        except FileNotFoundError:
            print(f"配置文件 {self.config_file_path} 未找到")
            return None
        except yaml.YAMLError as e:
            print(f"解析yaml文件出错: {e}")
            return None

    def get_motor_config(self, motor_id):
        """
        获取指定电机ID的配置
        :param motor_id: 电机ID (可以是int或str)
        :return: 电机配置字典
        """
        if self.config is None:
            return None

        # 将motor_id转换为字符串
        motor_id_str = str(motor_id)

        # 通过motor_id获取motor_type (RS03/RS06/RS00)
        motor_type = self.motor_dict.get(motor_id_str)

        if motor_type is None:
            # 如果motor_id不在映射中，使用默认配置
            motor_config = self.config.get('default')
            print(f"电机ID {motor_id} 未找到映射关系，使用默认配置")
            return motor_config

        # 根据motor_type获取配置
        motor_config = self.config.get('motors', {}).get(motor_type)

        # 如果没有找到对应motor_type的配置，使用默认配置
        if motor_config is None:
            motor_config = self.config.get('default')
            print(f"电机类型 {motor_type} 未找到配置，使用默认配置")

        return motor_config

    def get_position_limits(self, motor_id):
        """获取位置限制"""
        config = self.get_motor_config(motor_id)
        if config:
            return config['position']['min'], config['position']['max']
        return -12.57, 12.57  # 默认值

    def get_torque_limits(self, motor_id):
        """获取扭矩限制"""
        config = self.get_motor_config(motor_id)
        if config:
            return config['torque']['min'], config['torque']['max']
        return -12.0, 12.0  # 默认值

    def get_velocity_limits(self, motor_id):
        """获取速度限制"""
        config = self.get_motor_config(motor_id)
        if config:
            return config['velocity']['min'], config['velocity']['max']
        return -30.0, 30.0  # 默认值

    def get_kp_limits(self, motor_id):
        """获取KP限制"""
        config = self.get_motor_config(motor_id)
        if config:
            return config['kp']['min'], config['kp']['max']
        return 0.0, 500.0  # 默认值

    def get_kd_limits(self, motor_id):
        """获取KD限制"""
        config = self.get_motor_config(motor_id)
        if config:
            return config['kd']['min'], config['kd']['max']
        return 0.0, 5.0  # 默认值

    def get_all_limits(self, motor_id):
        """获取所有限制参数，返回字典格式"""
        config = self.get_motor_config(motor_id)
        if config:
            return {
                'P_MIN': config['position']['min'],
                'P_MAX': config['position']['max'],
                'T_MIN': config['torque']['min'],
                'T_MAX': config['torque']['max'],
                'V_MIN': config['velocity']['min'],
                'V_MAX': config['velocity']['max'],
                'KP_MIN': config['kp']['min'],
                'KP_MAX': config['kp']['max'],
                'KD_MIN': config['kd']['min'],
                'KD_MAX': config['kd']['max'],
            }
        return {
            'P_MIN': -12.57, 'P_MAX': 12.57,
            'T_MIN': -12.0, 'T_MAX': 12.0,
            'V_MIN': -30.0, 'V_MAX': 30.0,
            'KP_MIN': 0.0, 'KP_MAX': 500.0,
            'KD_MIN': 0.0, 'KD_MAX': 5.0,
        }


# 使用示例
if __name__ == "__main__":
    # 创建配置加载器
    loader = MotorConfigLoader()

    # 测试获取不同电机的配置
    for motor_id in range(1, 9):
        print(f"\n电机ID {motor_id} 的配置:")
        limits = loader.get_all_limits(motor_id)
        for key, value in limits.items():
            print(f"  {key}: {value}")

    # 测试获取特定参数
    print("\n单独获取参数示例:")
    p_min, p_max = loader.get_position_limits(5)
    print(f"电机5的位置限制: {p_min} ~ {p_max}")

    t_min, t_max = loader.get_torque_limits(8)
    print(f"电机8的扭矩限制: {t_min} ~ {t_max}")