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
@File    :   RobotPage.py
@Time    :   2026/01/05 18:46:36
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   单个机器人控制页面
'''



import os
import yaml
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                               QPushButton, QPlainTextEdit, QLabel, QTableView, QGridLayout, QHeaderView, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor, QColor, QStandardItemModel
from datetime import datetime

from ui.RobotWorker import RobotWorker
from ui.SingleMotorTestDialog import SingleMotorTestDialog
from config.config_manager import ConfigManager
from openarmx_arm_driver import Robot


class RobotPage(QWidget):
    """单个机器人控制页面"""

    # 信号
    log_signal = Signal(str, str)  # 日志信号 (message, level)
    status_changed = Signal(str)  # 状态改变信号

    def __init__(self, robot_name: str, right_channel: str, left_channel: str, config_manager: ConfigManager, robot_index: int = None, use_default_name: bool = False, parent=None):
        """初始化机器人控制页面

        Args:
            robot_name: 机器人名称
            right_channel: 右臂CAN通道
            left_channel: 左臂CAN通道
            config_manager: 配置管理器
            robot_index: 机器人编号（用于默认名称）
            use_default_name: 是否使用默认名称
            parent: 父窗口
        """
        super().__init__(parent)

        self.robot_name = robot_name
        self.right_channel = right_channel
        self.left_channel = left_channel
        self.robot_index = robot_index
        self.use_default_name = use_default_name
        self.worker = None  # RobotWorker实例

        # 创建表格模型
        self.motor_status_model = QStandardItemModel()

        # 翻译系统
        self.config_manager = config_manager
        self.current_language = self.config_manager.get_language()
        self.translations = self.load_translations()

        # 先创建UI（因为Robot初始化时会产生日志）
        self.setup_ui()

        # 创建Robot实例（只创建一次，后续重复使用）
        self.robot = self._create_robot_instance()

        self.log(self.tr("robot_page_created").format(
            robot_name=robot_name,
            right_channel=right_channel,
            left_channel=left_channel
        ), "INFO")

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

    def update_ui_texts(self):
        """更新界面文本（用于语言切换）"""
        # 重新加载当前语言
        self.current_language = self.config_manager.get_language()

        # 如果使用默认名称，重新生成翻译后的名称
        if self.use_default_name and self.robot_index is not None:
            self.robot_name = self.tr("robot_add_default_name").format(number=self.robot_index)

        # 更新机器人信息标签
        if hasattr(self, 'info_label'):
            self.info_label.setText(f"<b>{self.robot_name}</b> - {self.tr('robot_page_info').format(right_channel=self.right_channel, left_channel=self.left_channel)}")

        # 更新电机控制组
        if hasattr(self, 'motor_control_group'):
            self.motor_control_group.setTitle(self.tr("motor_control_group"))
        if hasattr(self, 'btn_enable_all'):
            self.btn_enable_all.setText(self.tr("enable_all"))
        if hasattr(self, 'btn_disable_all'):
            self.btn_disable_all.setText(self.tr("disable_all"))
        if hasattr(self, 'btn_go_home'):
            self.btn_go_home.setText(self.tr("go_home"))
        if hasattr(self, 'btn_set_zero'):
            self.btn_set_zero.setText(self.tr("set_zero"))
        if hasattr(self, 'btn_test_single'):
            self.btn_test_single.setText(self.tr("test_single"))
        if hasattr(self, 'btn_test_all'):
            self.btn_test_all.setText(self.tr("test_all"))

        # 更新电机监控组
        if hasattr(self, 'motor_monitor_group'):
            self.motor_monitor_group.setTitle(self.tr("motor_monitor_group"))
        if hasattr(self, 'btn_check_status'):
            self.btn_check_status.setText(self.tr("check_status"))

        # 更新输出栏组
        if hasattr(self, 'output_group'):
            self.output_group.setTitle(self.tr("robot_page_output_group"))

    def _create_robot_instance(self):
        """创建Robot实例

        Returns:
            Robot: 机器人实例
        """
        # 获取密码
        password = None
        if self.config_manager:
            password = self.config_manager.get_sudo_password()

        # 创建Robot实例，传入密码和log回调
        if password:
            robot = Robot(
                right_can_channel=self.right_channel,
                left_can_channel=self.left_channel,
                password=password,
                log=self._robot_log
            )
        else:
            robot = Robot(
                right_can_channel=self.right_channel,
                left_can_channel=self.left_channel,
                log=self._robot_log
            )

        return robot

    def _robot_log(self, message, level='INFO'):
        """Robot日志回调函数"""
        self.log(message, level)

    def setup_ui(self):
        """设置UI - 左右布局"""
        main_layout = QVBoxLayout()

        # 机器人信息
        info_layout = QHBoxLayout()
        self.info_label = QLabel(f"<b>{self.robot_name}</b> - {self.tr('robot_page_info').format(right_channel=self.right_channel, left_channel=self.left_channel)}")
        info_layout.addWidget(self.info_label)
        info_layout.addStretch()
        main_layout.addLayout(info_layout)

        # 主体内容 - 左右分栏
        content_layout = QHBoxLayout()

        # 左侧：控制区域
        left_layout = QVBoxLayout()
        motor_control_group = self.create_motor_control_group()
        motor_monitor_group = self.create_motor_monitor_group()
        left_layout.addWidget(motor_control_group, 0)  # 固定大小
        left_layout.addWidget(motor_monitor_group, 1)  # 自动扩展填充剩余空间

        # 右侧：输出栏
        output_group = self.create_output_group()

        # 添加到主体布局（左:右 = 1:2）
        content_layout.addLayout(left_layout, 1)
        content_layout.addWidget(output_group, 1)

        main_layout.addLayout(content_layout)

        self.setLayout(main_layout)

        # 初始时禁用所有控件
        self.set_enabled(False)

    def create_motor_control_group(self):
        """创建电机控制组"""
        self.motor_control_group = QGroupBox(self.tr("motor_control_group"))
        layout = QGridLayout()

        # 创建按钮
        self.btn_enable_all = QPushButton(self.tr("enable_all"))
        self.btn_disable_all = QPushButton(self.tr("disable_all"))
        self.btn_go_home = QPushButton(self.tr("go_home"))
        self.btn_set_zero = QPushButton(self.tr("set_zero"))
        self.btn_test_single = QPushButton(self.tr("test_single"))
        self.btn_test_all = QPushButton(self.tr("test_all"))

        # 连接信号
        self.btn_enable_all.clicked.connect(self.on_enable_all_motors)
        self.btn_disable_all.clicked.connect(self.on_disable_all_motors)
        self.btn_go_home.clicked.connect(self.on_go_home)
        self.btn_set_zero.clicked.connect(self.on_set_zero)
        self.btn_test_single.clicked.connect(self.on_test_single_motor)
        self.btn_test_all.clicked.connect(self.on_test_all_motors)

        # 网格布局 (2列3行)
        # 第一行
        layout.addWidget(self.btn_enable_all, 0, 0)
        layout.addWidget(self.btn_disable_all, 0, 1)
        # 第二行
        layout.addWidget(self.btn_go_home, 1, 0)
        layout.addWidget(self.btn_set_zero, 1, 1)
        # 第三行
        layout.addWidget(self.btn_test_single, 2, 0)
        layout.addWidget(self.btn_test_all, 2, 1)

        self.motor_control_group.setLayout(layout)
        return self.motor_control_group

    def create_motor_monitor_group(self):
        """创建电机监控组"""
        self.motor_monitor_group = QGroupBox(self.tr("motor_monitor_group"))
        layout = QVBoxLayout()

        # 检查状态按钮
        self.btn_check_status = QPushButton(self.tr("check_status"))
        self.btn_check_status.clicked.connect(self.on_check_motor_status)
        layout.addWidget(self.btn_check_status, 0)  # 固定大小

        # 电机状态表格
        self.motor_status_table = QTableView()
        # self.motor_status_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.motor_status_table, 1)  # 自动扩展填充剩余空间

        # 设置列头（表头）
        self.motor_status_model.setHorizontalHeaderLabels(['CAN通道', '电机ID', '位置(rad)', '速度(rad/s)', '扭矩(Nm)', '温度(°C)', '模式', '状态'])

        # 绑定 model
        self.motor_status_table.setModel(self.motor_status_model)
        self.motor_status_table.verticalHeader().setVisible(False)


        self.motor_monitor_group.setLayout(layout)
        return self.motor_monitor_group

    def create_output_group(self):
        """创建输出栏组"""
        self.output_group = QGroupBox(self.tr("robot_page_output_group"))
        layout = QVBoxLayout()

        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMaximumBlockCount(1000)  # 限制最大行数

        layout.addWidget(self.output_text)

        self.output_group.setLayout(layout)
        return self.output_group

    def set_enabled(self, enabled: bool):
        """设置页面是否可用

        Args:
            enabled: 是否启用
        """
        self.btn_enable_all.setEnabled(enabled)
        self.btn_disable_all.setEnabled(enabled)
        self.btn_go_home.setEnabled(enabled)
        self.btn_set_zero.setEnabled(enabled)
        self.btn_test_single.setEnabled(enabled)
        self.btn_test_all.setEnabled(enabled)
        self.btn_check_status.setEnabled(enabled)

    def log(self, message: str, level: str = "INFO"):
        """输出日志

        Args:
            message: 日志消息
            level: 日志级别 (INFO, SUCCESS, WARNING, ERROR)
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"

        # 根据级别设置颜色
        color_map = {
            "INFO": QColor(100, 100, 100),
            "SUCCESS": QColor(0, 150, 0),
            "WARNING": QColor(200, 100, 0),
            "ERROR": QColor(200, 0, 0)
        }

        color = color_map.get(level, QColor(0, 0, 0))

        # 添加到输出栏
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.output_text.setTextCursor(cursor)

        format = cursor.charFormat()
        format.setForeground(color)
        cursor.setCharFormat(format)
        cursor.insertText(formatted_message + "\n")

        # 滚动到底部
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )

        # 发送信号
        self.log_signal.emit(message, level)

    def start_worker(self, task: str, **kwargs):
        """启动工作线程

        Args:
            task: 任务类型
            **kwargs: 任务参数
        """
        if self.worker is not None and self.worker.isRunning():
            self.log(self.tr("robot_page_task_running"), "WARNING")
            return

        # 创建并启动worker，传入robot实例
        self.worker = RobotWorker(task, robot=self.robot, **kwargs)
        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.motor_status_signal.connect(self.update_motor_status)
        self.worker.error_signal.connect(self.on_motor_error)  # 连接错误信号
        self.worker.start()

        self.log(self.tr("robot_page_task_start").format(task=task), "INFO")

    def on_worker_finished(self, success: bool, message: str):
        """Worker完成回调"""
        if success:
            self.log(message, "SUCCESS")
        else:
            self.log(message, "ERROR")

    def on_motor_error(self, arm_name: str, operation: str, error_details: str):
        """电机错误回调 - 弹窗告警

        Args:
            arm_name: 机械臂名称（右臂/左臂）
            operation: 操作描述
            error_details: 错误详情
        """
        # 弹出错误对话框
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("电机错误")
        msg_box.setText(f"<b>{arm_name}: {operation}</b>")
        msg_box.setInformativeText(f"错误详情: {error_details}")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def update_motor_status(self, can_channel: str, motor_id: int, info: dict):
        """更新电机状态"""
        channel_name = "右臂" if can_channel == 0 else "左臂"
        row = self.motor_status_model.rowCount()
        self.motor_status_model.insertRow(row)
        self.motor_status_model.setData(self.motor_status_model.index(row, 0), channel_name)
        self.motor_status_model.setData(self.motor_status_model.index(row, 1), motor_id)
        self.motor_status_model.setData(self.motor_status_model.index(row, 2), f"{info.get('angle', 0.0):.3f}")
        self.motor_status_model.setData(self.motor_status_model.index(row, 3), f"{info.get('velocity', 0.0):.3f}")
        self.motor_status_model.setData(self.motor_status_model.index(row, 4), f"{info.get('torque', 0.0):.3f}")
        self.motor_status_model.setData(self.motor_status_model.index(row, 5), f"{info.get('temperature', 0.0):.3f}")

        mode_status = info.get('mode_status', 'Unknown')
        if 'Motor模式' in mode_status or '运行' in mode_status:
            mode_icon = "🟢"
        elif 'Reset模式' in mode_status or '复位' in mode_status:
            mode_icon = "🔴"
        elif 'Cali模式' in mode_status or '标定' in mode_status:
            mode_icon = "🟡"
        else:
            mode_icon = "⚪"
        self.motor_status_table.setColumnWidth(6, 200)
        self.motor_status_model.setData(self.motor_status_model.index(row, 6), f'{mode_status:15s}{mode_icon}')
        self.motor_status_model.setData(self.motor_status_model.index(row, 7), info.get('fault_status', 'Unknown'))

    # ==================== 电机控制槽函数 ====================

    def on_enable_all_motors(self):
        """启用所有电机"""
        self.start_worker('enable_all')

    def on_disable_all_motors(self):
        """禁用所有电机"""
        self.start_worker('disable_all')

    def on_go_home(self):
        """回零"""
        self.start_worker('go_home')

    def on_set_zero(self):
        """设置零点"""
        self.start_worker('set_zero')

    def on_test_single_motor(self):
        """单电机测试"""
        # 创建并显示单电机测试对话框
        dialog = SingleMotorTestDialog(self, config_manager=self.config_manager)
        dialog.command_signal.connect(self.on_single_motor_command)
        dialog.exec()

    def on_test_all_motors(self):
        """测试全部电机"""
        self.start_worker('test_all')

    def on_single_motor_command(self, arm_name: str, motor_id: int, params: dict):
        """处理单电机控制命令

        Args:
            arm_name: 机械臂名称 ('right' 或 'left')
            motor_id: 电机ID
            params: MIT模式参数字典
        """
        arm_cn = "右臂" if arm_name == "right" else "左臂"

        # 检查是否是失能命令
        if params.get('disable', False):
            self.log(
                f"失能命令: {arm_cn} 电机 {motor_id}",
                "INFO"
            )
        else:
            # 正常控制命令，显示详细参数
            self.log(
                f"发送命令: {arm_cn} 电机 {motor_id} - "
                f"位置={params['position']:.3f}, "
                f"速度={params['velocity']:.3f}, "
                f"扭矩={params['torque']:.3f}, "
                f"Kp={params['kp']:.2f}, "
                f"Kd={params['kd']:.2f}",
                "INFO"
            )

        # 启动worker执行单电机控制
        self.start_worker(
            'single_motor_mit',
            arm_name=arm_name,
            motor_id=motor_id,
            params=params
        )

    def on_check_motor_status(self):
        """检查电机状态"""
        # 清空表格，重新添加
        self.motor_status_model.removeRows(0, self.motor_status_model.rowCount())
        # 传递通道信息用于日志显示
        self.start_worker('check_status', right_channel=self.right_channel, left_channel=self.left_channel)

    def cleanup(self):
        """清理资源"""
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        # 关闭Robot实例
        if self.robot is not None:
            try:
                self.robot.shutdown()
            except:
                pass
            self.robot = None

        self.log(self.tr("robot_page_cleanup").format(robot_name=self.robot_name), "INFO")
