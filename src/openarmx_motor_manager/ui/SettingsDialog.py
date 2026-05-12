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
@File    :   SettingsDialog.py
@Time    :   2026/01/05 18:47:14
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   设置对话框 - 配置脚本路径
'''



import os
import yaml
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QGroupBox, QFileDialog,
                               QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from config.config_manager import ConfigManager


class SettingsDialog(QDialog):
    """设置对话框"""

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)

        self.config_manager = config_manager
        self.changes_made = False  # 标记是否有修改

        # 翻译系统
        self.current_language = self.config_manager.get_language()
        self.translations = self.load_translations()

        self.setWindowTitle(self.tr("settings_dialog_title"))
        self.setMinimumWidth(700)
        self.setMinimumHeight(300)
        self.setModal(True)

        self.setup_ui()
        self.load_current_settings()

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
        layout = QVBoxLayout()

        # 标题
        title_label = QLabel(self.tr("settings_title"))
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        # 说明
        desc_label = QLabel(self.tr("settings_description"))
        desc_label.setStyleSheet("color: gray; padding-bottom: 10px;")
        layout.addWidget(desc_label)

        # 仿真脚本配置
        sim_group = self.create_script_config_group(
            self.tr("settings_sim_group"),
            "run_bimanual_moveit_sim.sh",
            'moveit_sim'
        )
        layout.addWidget(sim_group)

        # CAN脚本配置
        can_group = self.create_script_config_group(
            self.tr("settings_can_group"),
            "run_bimanual_moveit_with_can2.0.sh",
            'moveit_can'
        )
        layout.addWidget(can_group)

        # 密码配置
        password_group = self.create_password_config_group()
        layout.addWidget(password_group)

        layout.addStretch()

        # 按钮
        button_layout = QHBoxLayout()

        reset_button = QPushButton(self.tr("settings_reset_button"))
        reset_button.clicked.connect(self.on_reset)
        button_layout.addWidget(reset_button)

        button_layout.addStretch()

        cancel_button = QPushButton(self.tr("settings_cancel_button"))
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        save_button = QPushButton(self.tr("settings_save_button"))
        save_button.setStyleSheet("background-color: #4CAF50; color: white;")
        save_button.clicked.connect(self.on_save)
        button_layout.addWidget(save_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def create_script_config_group(self, title, script_name, script_type):
        """创建脚本配置组

        Args:
            title: 组标题
            script_name: 脚本文件名（用于显示）
            script_type: 脚本类型 ('moveit_sim' 或 'moveit_can')

        Returns:
            QGroupBox: 配置组控件
        """
        group_box = QGroupBox(title)
        layout = QVBoxLayout()

        # 脚本名称提示
        name_label = QLabel(self.tr("settings_script_name").format(script_name=script_name))
        name_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(name_label)

        # 路径输入框和浏览按钮
        path_layout = QHBoxLayout()

        # 路径输入框
        path_edit = QLineEdit()
        path_edit.setPlaceholderText(self.tr("settings_path_placeholder"))
        path_edit.setReadOnly(True)  # 只读，通过浏览按钮选择
        path_layout.addWidget(path_edit)

        # 浏览按钮
        browse_button = QPushButton(self.tr("settings_browse_button"))
        browse_button.clicked.connect(lambda: self.on_browse(script_type, path_edit))
        path_layout.addWidget(browse_button)

        # 清除按钮
        clear_button = QPushButton(self.tr("settings_clear_button"))
        clear_button.clicked.connect(lambda: self.on_clear(script_type, path_edit))
        path_layout.addWidget(clear_button)

        layout.addLayout(path_layout)

        # 状态标签
        status_label = QLabel()
        status_label.setStyleSheet("font-size: 10px; padding-top: 5px;")
        layout.addWidget(status_label)

        group_box.setLayout(layout)

        # 保存引用
        if script_type == 'moveit_sim':
            self.sim_path_edit = path_edit
            self.sim_status_label = status_label
        else:
            self.can_path_edit = path_edit
            self.can_status_label = status_label

        return group_box

    def create_password_config_group(self):
        """创建密码配置组

        Returns:
            QGroupBox: 密码配置组控件
        """
        group_box = QGroupBox(self.tr("password_config_group"))
        layout = QVBoxLayout()

        # 说明标签
        desc_label = QLabel(self.tr("password_desc"))
        desc_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(desc_label)

        # 密码输入框和按钮
        password_layout = QHBoxLayout()

        # 密码输入框
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText(self.tr("password_placeholder"))
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)  # 显示为 *
        password_layout.addWidget(self.password_edit)

        # 显示/隐藏密码按钮
        self.show_password_button = QPushButton(self.tr("password_show"))
        self.show_password_button.setCheckable(True)
        self.show_password_button.clicked.connect(self.on_toggle_password_visibility)
        password_layout.addWidget(self.show_password_button)

        # 清除密码按钮
        clear_password_button = QPushButton(self.tr("password_clear"))
        clear_password_button.clicked.connect(self.on_clear_password)
        password_layout.addWidget(clear_password_button)

        layout.addLayout(password_layout)

        # 提示标签
        hint_label = QLabel(self.tr("password_hint"))
        hint_label.setStyleSheet("color: orange; font-size: 9px; padding-top: 5px;")
        layout.addWidget(hint_label)

        group_box.setLayout(layout)
        return group_box

    def on_toggle_password_visibility(self):
        """切换密码可见性"""
        if self.show_password_button.isChecked():
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_password_button.setText(self.tr("password_hide"))
        else:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_password_button.setText(self.tr("password_show"))

    def on_clear_password(self):
        """清除密码"""
        self.password_edit.clear()
        self.changes_made = True

    def load_current_settings(self):
        """加载当前配置"""
        # 加载仿真脚本路径
        sim_path = self.config_manager.get_script_path('moveit_sim')
        if sim_path:
            self.sim_path_edit.setText(sim_path)
            self.update_status_label(self.sim_status_label, sim_path)

        # 加载CAN脚本路径
        can_path = self.config_manager.get_script_path('moveit_can')
        if can_path:
            self.can_path_edit.setText(can_path)
            self.update_status_label(self.can_status_label, can_path)

        # 加载密码
        password = self.config_manager.get_sudo_password()
        if password:
            self.password_edit.setText(password)

    def update_status_label(self, label, path):
        """更新状态标签

        Args:
            label: 状态标签控件
            path: 脚本路径
        """
        if not path:
            label.setText("")
            return

        if os.path.exists(path):
            if os.access(path, os.X_OK) or path.endswith('.sh'):
                label.setText(self.tr("settings_status_valid"))
                label.setStyleSheet("color: green; font-size: 10px; padding-top: 5px;")
            else:
                label.setText(self.tr("settings_status_no_permission"))
                label.setStyleSheet("color: orange; font-size: 10px; padding-top: 5px;")
        else:
            label.setText(self.tr("settings_status_not_exist"))
            label.setStyleSheet("color: red; font-size: 10px; padding-top: 5px;")

    def on_browse(self, script_type, path_edit):
        """浏览并选择脚本文件

        Args:
            script_type: 脚本类型
            path_edit: 路径输入框控件
        """
        # 获取起始目录
        current_path = path_edit.text()
        start_dir = os.path.dirname(current_path) if current_path else os.path.expanduser("~")

        # 打开文件对话框
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("settings_select_script_title"),
            start_dir,
            self.tr("settings_file_filter")
        )

        if file_path:
            path_edit.setText(file_path)
            # 更新状态
            if script_type == 'moveit_sim':
                self.update_status_label(self.sim_status_label, file_path)
            else:
                self.update_status_label(self.can_status_label, file_path)

            self.changes_made = True

    def on_clear(self, script_type, path_edit):
        """清除脚本路径

        Args:
            script_type: 脚本类型
            path_edit: 路径输入框控件
        """
        path_edit.clear()

        # 更新状态
        if script_type == 'moveit_sim':
            self.sim_status_label.setText("")
        else:
            self.can_status_label.setText("")

        self.changes_made = True

    def on_reset(self):
        """恢复为首次配置时的所有设置"""
        reply = QMessageBox.question(
            self,
            self.tr("settings_reset_confirm_title"),
            self.tr("settings_reset_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 获取默认设置
            default_sim_path = self.config_manager.get_default_script_path('moveit_sim')
            default_can_path = self.config_manager.get_default_script_path('moveit_can')
            default_password = self.config_manager.get_default_password()

            # 恢复为默认路径
            if default_sim_path:
                self.sim_path_edit.setText(default_sim_path)
                self.update_status_label(self.sim_status_label, default_sim_path)
            else:
                self.sim_path_edit.clear()
                self.sim_status_label.setText("")

            if default_can_path:
                self.can_path_edit.setText(default_can_path)
                self.update_status_label(self.can_status_label, default_can_path)
            else:
                self.can_path_edit.clear()
                self.can_status_label.setText("")

            # 恢复为默认密码
            if default_password:
                self.password_edit.setText(default_password)
            else:
                self.password_edit.clear()

            # 使用 ConfigManager 的 reset_to_defaults 方法
            self.config_manager.reset_to_defaults()
            self.changes_made = True

            QMessageBox.information(
                self,
                self.tr("settings_reset_complete_title"),
                self.tr("settings_reset_complete_msg")
            )

    def on_save(self):
        """保存配置"""
        # 获取输入的路径
        sim_path = self.sim_path_edit.text().strip()
        can_path = self.can_path_edit.text().strip()

        # 验证至少有一个路径
        if not sim_path and not can_path:
            QMessageBox.warning(
                self,
                self.tr("settings_incomplete_title"),
                self.tr("settings_incomplete_msg")
            )
            return

        # 验证路径有效性
        error_messages = []

        if sim_path and not os.path.exists(sim_path):
            error_messages.append(self.tr("settings_sim_path_not_exist").format(sim_path=sim_path))

        if can_path and not os.path.exists(can_path):
            error_messages.append(self.tr("settings_can_path_not_exist").format(can_path=can_path))

        if error_messages:
            QMessageBox.warning(
                self,
                self.tr("settings_invalid_path_title"),
                "\n\n".join(error_messages) + self.tr("settings_check_path_msg")
            )
            return

        # 保存到配置
        if sim_path:
            self.config_manager.set_script_path('moveit_sim', sim_path)
        else:
            self.config_manager.set_script_path('moveit_sim', '')

        if can_path:
            self.config_manager.set_script_path('moveit_can', can_path)
        else:
            self.config_manager.set_script_path('moveit_can', '')

        # 保存密码
        password = self.password_edit.text().strip()
        self.config_manager.set_sudo_password(password)

        # 保存配置文件
        if self.config_manager.save_config():
            QMessageBox.information(
                self,
                self.tr("settings_save_success_title"),
                self.tr("settings_save_success_msg")
            )
            self.accept()
        else:
            QMessageBox.critical(
                self,
                self.tr("settings_save_failed_title"),
                self.tr("settings_save_failed_msg")
            )

    def has_changes(self):
        """检查是否有修改

        Returns:
            bool: 是否有修改
        """
        return self.changes_made
