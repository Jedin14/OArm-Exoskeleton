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
@File    :   ConfigDialog.py
@Time    :   2026/01/05 18:46:06
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   首次运行配置对话框
'''



import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QButtonGroup,
                               QProgressBar, QGroupBox, QScrollArea, QWidget,
                               QMessageBox, QFileDialog)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from config.script_finder import ScriptFinder
from config.config_manager import ConfigManager


class ScriptSearchWorker(QThread):
    """脚本搜索工作线程"""

    # 信号
    progress_signal = Signal(str)  # 进度信息
    finished_signal = Signal(dict)  # 搜索完成，传递结果字典

    def run(self):
        """执行搜索"""
        self.progress_signal.emit("正在搜索 MoveIt 脚本...")

        # 搜索脚本
        results = ScriptFinder.find_moveit_scripts()

        self.progress_signal.emit(f"找到 {len(results['moveit_sim'])} 个仿真脚本")
        self.progress_signal.emit(f"找到 {len(results['moveit_can'])} 个CAN脚本")

        # 发送结果
        self.finished_signal.emit(results)


class ConfigDialog(QDialog):
    """首次运行配置对话框"""

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)

        self.config_manager = config_manager
        self.search_results = None
        self.selected_paths = {
            'moveit_sim': None,
            'moveit_can': None
        }

        self.setWindowTitle("首次运行配置")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self.setModal(True)

        self.setup_ui()
        self.start_search()

    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()

        # 标题
        title_label = QLabel("欢迎使用 OpenArmX 电机管理系统")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # 说明
        desc_label = QLabel("首次运行需要配置 MoveIt 启动脚本路径\n正在搜索您的系统...")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet("color: gray; padding: 10px;")
        layout.addWidget(desc_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 不确定进度
        layout.addWidget(self.progress_bar)

        # 进度信息
        self.progress_label = QLabel("正在搜索...")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_label)

        # 搜索结果容器（初始隐藏）
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout()
        self.results_widget.setLayout(self.results_layout)
        self.results_widget.setVisible(False)
        layout.addWidget(self.results_widget)

        # 按钮
        button_layout = QHBoxLayout()

        self.manual_button = QPushButton("手动选择")
        self.manual_button.setEnabled(False)
        self.manual_button.clicked.connect(self.on_manual_select)
        button_layout.addWidget(self.manual_button)

        button_layout.addStretch()

        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        self.ok_button = QPushButton("确定")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self.on_confirm)
        button_layout.addWidget(self.ok_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def start_search(self):
        """启动搜索"""
        self.search_worker = ScriptSearchWorker()
        self.search_worker.progress_signal.connect(self.on_search_progress)
        self.search_worker.finished_signal.connect(self.on_search_finished)
        self.search_worker.start()

    def on_search_progress(self, message):
        """搜索进度更新"""
        self.progress_label.setText(message)

    def on_search_finished(self, results):
        """搜索完成"""
        self.search_results = results

        # 隐藏进度条
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)

        # 显示结果
        self.show_results()

    def show_results(self):
        """显示搜索结果"""
        # 清空之前的内容
        for i in reversed(range(self.results_layout.count())):
            self.results_layout.itemAt(i).widget().setParent(None)

        # 仿真脚本选择
        self.create_script_selection_group(
            "仿真版 MoveIt 脚本",
            'moveit_sim',
            self.search_results['moveit_sim']
        )

        # CAN脚本选择
        self.create_script_selection_group(
            "CAN版 MoveIt 脚本",
            'moveit_can',
            self.search_results['moveit_can']
        )

        # 显示结果容器
        self.results_widget.setVisible(True)

        # 启用按钮
        self.manual_button.setEnabled(True)

        # 检查是否可以自动配置
        self.check_auto_config()

    def create_script_selection_group(self, title, script_type, paths):
        """创建脚本选择组

        Args:
            title: 组标题
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')
            paths: 脚本路径列表
        """
        group_box = QGroupBox(title)
        group_layout = QVBoxLayout()

        if not paths:
            # 未找到脚本
            no_result_label = QLabel("❌ 未找到脚本")
            no_result_label.setStyleSheet("color: red;")
            group_layout.addWidget(no_result_label)

            # 添加手动选择按钮
            manual_btn = QPushButton("手动选择文件...")
            manual_btn.clicked.connect(lambda: self.manual_select_script(script_type))
            group_layout.addWidget(manual_btn)

        elif len(paths) == 1:
            # 只找到一个脚本，自动选择
            path = paths[0]
            self.selected_paths[script_type] = path

            label = QLabel(f"✓ 已找到脚本:")
            label.setStyleSheet("color: green; font-weight: bold;")
            group_layout.addWidget(label)

            path_label = QLabel(f"  {path}")
            path_label.setWordWrap(True)
            path_label.setStyleSheet("color: #333; padding-left: 20px;")
            group_layout.addWidget(path_label)

        else:
            # 找到多个脚本，让用户选择
            label = QLabel(f"找到 {len(paths)} 个脚本，请选择:")
            label.setStyleSheet("font-weight: bold;")
            group_layout.addWidget(label)

            # 创建单选按钮组
            button_group = QButtonGroup(self)
            button_group.setExclusive(True)

            # 创建滚动区域
            scroll_area = QScrollArea()
            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout()

            for i, path in enumerate(paths):
                radio = QRadioButton(path)
                radio.setProperty('script_type', script_type)
                radio.setProperty('script_path', path)
                radio.toggled.connect(self.on_script_selected)

                button_group.addButton(radio)
                scroll_layout.addWidget(radio)

                # 默认选择第一个
                if i == 0:
                    radio.setChecked(True)
                    self.selected_paths[script_type] = path

            scroll_widget.setLayout(scroll_layout)
            scroll_area.setWidget(scroll_widget)
            scroll_area.setWidgetResizable(True)
            scroll_area.setMaximumHeight(150)
            group_layout.addWidget(scroll_area)

        group_box.setLayout(group_layout)
        self.results_layout.addWidget(group_box)

    def on_script_selected(self, checked):
        """脚本选择改变"""
        if checked:
            sender = self.sender()
            script_type = sender.property('script_type')
            script_path = sender.property('script_path')
            self.selected_paths[script_type] = script_path
            self.check_auto_config()

    def manual_select_script(self, script_type):
        """手动选择脚本文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择脚本文件",
            os.path.expanduser("~"),
            "Shell脚本 (*.sh);;所有文件 (*)"
        )

        if file_path:
            self.selected_paths[script_type] = file_path
            # 重新显示结果
            self.show_results()

    def on_manual_select(self):
        """手动选择所有脚本"""
        QMessageBox.information(
            self,
            "手动选择",
            "请为每个脚本类型选择对应的文件"
        )

        # 选择仿真脚本
        sim_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择仿真版 MoveIt 脚本 (run_bimanual_moveit_sim.sh)",
            os.path.expanduser("~"),
            "Shell脚本 (*.sh);;所有文件 (*)"
        )

        if sim_path:
            self.selected_paths['moveit_sim'] = sim_path

        # 选择CAN脚本
        can_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择CAN版 MoveIt 脚本 (run_bimanual_moveit_with_can2.0.sh)",
            os.path.expanduser("~"),
            "Shell脚本 (*.sh);;所有文件 (*)"
        )

        if can_path:
            self.selected_paths['moveit_can'] = can_path

        # 重新显示结果
        self.show_results()

    def check_auto_config(self):
        """检查是否可以启用确定按钮"""
        # 至少需要配置一个脚本
        has_sim = self.selected_paths['moveit_sim'] is not None
        has_can = self.selected_paths['moveit_can'] is not None

        self.ok_button.setEnabled(has_sim or has_can)

    def on_confirm(self):
        """确认配置"""
        # 保存配置
        if self.selected_paths['moveit_sim']:
            self.config_manager.set_script_path('moveit_sim', self.selected_paths['moveit_sim'])
            # 同时保存为默认路径
            self.config_manager.set_default_script_path('moveit_sim', self.selected_paths['moveit_sim'])

        if self.selected_paths['moveit_can']:
            self.config_manager.set_script_path('moveit_can', self.selected_paths['moveit_can'])
            # 同时保存为默认路径
            self.config_manager.set_default_script_path('moveit_can', self.selected_paths['moveit_can'])

        # 标记首次运行完成
        self.config_manager.set_first_run_completed()

        # 保存配置
        if self.config_manager.save_config():
            QMessageBox.information(
                self,
                "配置成功",
                "配置已保存！\n\n您可以稍后在设置中修改这些路径。"
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "保存失败",
                "配置保存失败，请检查权限。"
            )
