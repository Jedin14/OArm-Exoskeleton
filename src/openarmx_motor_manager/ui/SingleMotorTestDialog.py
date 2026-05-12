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
@File    :   SingleMotorTestDialog.py
@Time    :   2026/01/05 18:47:27
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   单电机测试对话框 - MIT模式参数控制
'''



from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
                               QLabel, QComboBox, QDoubleSpinBox, QPushButton,
                               QGridLayout, QMessageBox, QTextEdit)
from PySide6.QtCore import Qt, Signal
import yaml
from pathlib import Path


class SingleMotorTestDialog(QDialog):
    """单电机测试对话框 - MIT模式"""

    # 定义信号：发送控制命令
    command_signal = Signal(str, int, dict)  # (arm_name, motor_id, params)

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)

        # 翻译系统
        self.config_manager = config_manager
        self.current_language = config_manager.get_language() if config_manager else 'zh_CN'
        self.translations = self.load_translations()

        self.setWindowTitle(self.tr("single_motor_test_title"))
        self.setMinimumWidth(500)
        self.setup_ui()

    def load_translations(self):
        """加载翻译文件"""
        translations_file = Path(__file__).parent / 'translations.yaml'
        try:
            with open(translations_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"加载翻译文件失败: {e}")
            return {}

    def tr(self, key):
        """翻译文本"""
        if self.current_language in self.translations:
            return self.translations[self.current_language].get(key, key)
        return key

    def setup_ui(self):
        """设置UI"""
        main_layout = QVBoxLayout()

        # 安全提示区
        safety_warning = self.create_safety_warning()
        main_layout.addWidget(safety_warning)

        # 电机选择组
        motor_select_group = self.create_motor_select_group()
        main_layout.addWidget(motor_select_group)

        # MIT参数设置组
        mit_params_group = self.create_mit_params_group()
        main_layout.addWidget(mit_params_group)

        # 按钮组
        button_layout = self.create_button_layout()
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def create_safety_warning(self):
        """创建安全提示区"""
        warning_label = QLabel()
        warning_text = self.tr("single_motor_safety_warning")
        warning_label.setText(warning_text)
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("""
            QLabel {
                background-color: #fff3cd;
                border: 2px solid #dc3545;
                border-radius: 5px;
                padding: 10px;
                font-weight: bold;
            }
        """)
        return warning_label

    def create_motor_select_group(self):
        """创建电机选择组"""
        group = QGroupBox(self.tr("single_motor_select_group"))
        layout = QGridLayout()

        # 机械臂选择
        arm_label = QLabel(self.tr("single_motor_arm_label"))
        self.arm_combo = QComboBox()
        self.arm_combo.addItems([
            self.tr("single_motor_right_arm"),
            self.tr("single_motor_left_arm")
        ])
        layout.addWidget(arm_label, 0, 0)
        layout.addWidget(self.arm_combo, 0, 1)

        # 电机ID选择
        motor_id_label = QLabel(self.tr("single_motor_motor_id_label"))
        self.motor_id_spinbox = QDoubleSpinBox()
        self.motor_id_spinbox.setDecimals(0)
        self.motor_id_spinbox.setRange(1, 8)
        self.motor_id_spinbox.setValue(1)
        layout.addWidget(motor_id_label, 0, 2)
        layout.addWidget(self.motor_id_spinbox, 0, 3)

        group.setLayout(layout)
        return group

    def create_mit_params_group(self):
        """创建MIT参数设置组"""
        group = QGroupBox(self.tr("single_motor_mit_params"))
        layout = QGridLayout()

        # 位置 (Position)
        position_label = QLabel(self.tr("single_motor_position_label"))
        position_label.setToolTip(self.tr("single_motor_position_tooltip"))
        self.position_spinbox = QDoubleSpinBox()
        self.position_spinbox.setRange(-12.56, 12.56)  # -4π to 4π
        self.position_spinbox.setSingleStep(0.1)
        self.position_spinbox.setDecimals(3)
        self.position_spinbox.setValue(0.0)
        layout.addWidget(position_label, 0, 0)
        layout.addWidget(self.position_spinbox, 0, 1)

        # 速度 (Velocity)
        velocity_label = QLabel(self.tr("single_motor_velocity_label"))
        velocity_label.setToolTip(self.tr("single_motor_velocity_tooltip"))
        self.velocity_spinbox = QDoubleSpinBox()
        self.velocity_spinbox.setRange(-30.0, 30.0)
        self.velocity_spinbox.setSingleStep(0.1)
        self.velocity_spinbox.setDecimals(3)
        self.velocity_spinbox.setValue(0.0)
        layout.addWidget(velocity_label, 0, 2)
        layout.addWidget(self.velocity_spinbox, 0, 3)

        # 扭矩 (Torque)
        torque_label = QLabel(self.tr("single_motor_torque_label"))
        torque_label.setToolTip(self.tr("single_motor_torque_tooltip"))
        self.torque_spinbox = QDoubleSpinBox()
        self.torque_spinbox.setRange(-30.0, 30.0)
        self.torque_spinbox.setSingleStep(0.1)
        self.torque_spinbox.setDecimals(3)
        self.torque_spinbox.setValue(0.0)
        layout.addWidget(torque_label, 1, 0)
        layout.addWidget(self.torque_spinbox, 1, 1)

        # Kp (位置增益)
        kp_label = QLabel(self.tr("single_motor_kp_label"))
        kp_label.setToolTip(self.tr("single_motor_kp_tooltip"))
        self.kp_spinbox = QDoubleSpinBox()
        self.kp_spinbox.setRange(0.0, 500.0)
        self.kp_spinbox.setSingleStep(0.5)
        self.kp_spinbox.setDecimals(2)
        self.kp_spinbox.setValue(10.0)
        layout.addWidget(kp_label, 1, 2)
        layout.addWidget(self.kp_spinbox, 1, 3)

        # Kd (速度增益)
        kd_label = QLabel(self.tr("single_motor_kd_label"))
        kd_label.setToolTip(self.tr("single_motor_kd_tooltip"))
        self.kd_spinbox = QDoubleSpinBox()
        self.kd_spinbox.setRange(0.0, 50.0)
        self.kd_spinbox.setSingleStep(0.1)
        self.kd_spinbox.setDecimals(2)
        self.kd_spinbox.setValue(1.0)
        layout.addWidget(kd_label, 2, 0)
        layout.addWidget(self.kd_spinbox, 2, 1)

        group.setLayout(layout)
        return group

    def create_button_layout(self):
        """创建按钮布局"""
        layout = QHBoxLayout()

        layout.addStretch()

        # 执行按钮
        self.btn_send = QPushButton(self.tr("single_motor_send_button"))
        self.btn_send.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; }")
        self.btn_send.clicked.connect(self.send_command)

        self.btn_stop = QPushButton(self.tr("single_motor_disable_button"))
        self.btn_stop.setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; padding: 8px; }")
        self.btn_stop.clicked.connect(self.stop_motor)

        layout.addWidget(self.btn_send)
        layout.addWidget(self.btn_stop)

        return layout

    # ==================== 命令发送 ====================

    def send_command(self):
        """发送控制命令"""
        # 获取机械臂选择
        arm_index = self.arm_combo.currentIndex()
        arm_name = "right" if arm_index == 0 else "left"

        # 获取电机ID
        motor_id = int(self.motor_id_spinbox.value())

        # 获取MIT参数
        params = {
            'position': self.position_spinbox.value(),
            'velocity': self.velocity_spinbox.value(),
            'torque': self.torque_spinbox.value(),
            'kp': self.kp_spinbox.value(),
            'kd': self.kd_spinbox.value()
        }

        # 安全确认对话框
        arm_display = self.arm_combo.currentText()

        # 构建确认消息
        msg = self.tr("single_motor_confirm_message").format(
            arm=arm_display,
            motor_id=motor_id,
            position=params['position'],
            velocity=params['velocity'],
            torque=params['torque'],
            kp=params['kp'],
            kd=params['kd']
        )

        msgBox = QMessageBox(self)
        msgBox.setIcon(QMessageBox.Icon.Warning)
        msgBox.setWindowTitle(self.tr("single_motor_confirm_title"))
        msgBox.setText(msg)
        msgBox.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msgBox.setDefaultButton(QMessageBox.StandardButton.No)  # 默认选择"否"

        reply = msgBox.exec()

        if reply == QMessageBox.StandardButton.Yes:
            # 发送信号
            self.command_signal.emit(arm_name, motor_id, params)

    def stop_motor(self):
        """停止电机 - 失能电机"""
        # 获取机械臂选择
        arm_index = self.arm_combo.currentIndex()
        arm_name = "right" if arm_index == 0 else "left"

        # 获取电机ID
        motor_id = int(self.motor_id_spinbox.value())

        # 停止参数：使用特殊标记表示这是失能命令
        params = {
            'disable': True  # 标记这是失能命令
        }

        # 确认对话框
        arm_display = self.arm_combo.currentText()
        msg = self.tr("single_motor_disable_confirm").format(
            arm=arm_display,
            motor_id=motor_id
        )
        reply = QMessageBox.warning(
            self,
            self.tr("single_motor_disable_title"),
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 发送信号
            self.command_signal.emit(arm_name, motor_id, params)
