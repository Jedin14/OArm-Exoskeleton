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
@File    :   MainUI_MultiRobot.py
@Time    :   2026/01/05 18:46:24
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   多机器人管理主界面
'''



import yaml
from pathlib import Path
from PySide6.QtWidgets import (QMainWindow, QTabWidget, QMessageBox, QDialog,
                               QInputDialog, QMenu, QWidget, QVBoxLayout, QLabel, QLineEdit)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon

from ui.ui.ui_MainUI import Ui_MainWindow
from ui.RobotPage import RobotPage
from ui.SettingsDialog import SettingsDialog
from config.config_manager import ConfigManager
from utils.can_detector import CANDetector

# 导入CAN管理函数
from openarmx_arm_driver import (
    get_all_can_interfaces,
    get_available_can_interfaces,
    check_can_interface_type,
    enable_can_interface,
    disable_can_interface,
    verify_can_interface
)


class MainUI_MultiRobot(QMainWindow):
    """多机器人管理主界面"""

    def __init__(self):
        super().__init__()

        # 配置管理器
        self.config_manager = ConfigManager()

        # 当前语言
        self.current_language = self.config_manager.get_language()

        # 翻译字典
        self.translations = self.load_translations()

        # sudo密码（内存中）
        self.sudo_password = None

        # 机器人页面列表
        self.robot_pages = []

        # 欢迎页面组件引用
        self.welcome_label = None
        self.welcome_tab_index = None

        # UI设置
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 隐藏原有的中央部件内容
        self.hide_original_widgets()

        # 创建标签页管理器
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.on_close_robot_tab)
        self.setCentralWidget(self.tab_widget)

        # 设置菜单
        self.setup_menus()

        # 应用翻译
        self._update_ui_texts()

        # 设置窗口图标
        self.load_icon()

        # 欢迎页面
        self.add_welcome_page()

        self.log_to_current_page(self.tr("log_startup"), "SUCCESS")
        self.log_to_current_page(self.tr("log_waiting"), "INFO")

        # 首次运行检测
        self.check_first_run()

    def hide_original_widgets(self):
        """隐藏原UI文件中的控件"""
        # 隐藏所有groupBox和控件
        if hasattr(self.ui, 'centralwidget'):
            for child in self.ui.centralwidget.findChildren(QWidget):
                child.setVisible(False)

    def add_welcome_page(self):
        """添加欢迎页面"""
        welcome_widget = QWidget()
        layout = QVBoxLayout()

        welcome_html = f"""
            <h1>{self.tr("welcome_title")}</h1>
            <p>{self.tr("welcome_greeting")}</p>
            <br>
            <h3>{self.tr("welcome_quick_start")}</h3>
            <ul>
                <li>{self.tr("welcome_step1")}</li>
                <li>{self.tr("welcome_step2")}</li>
                <li>{self.tr("welcome_step3")}</li>
            </ul>
        """

        self.welcome_label = QLabel(welcome_html)
        self.welcome_label.setWordWrap(True)
        self.welcome_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(self.welcome_label)
        layout.addStretch()

        welcome_widget.setLayout(layout)
        self.welcome_tab_index = self.tab_widget.addTab(welcome_widget, self.tr("welcome_tab_title"))

    def setup_menus(self):
        """设置菜单栏"""
        # 移除原有的"开始"菜单
        self.ui.menubar.removeAction(self.ui.menu.menuAction())

        # 1. 创建"机器人"菜单
        self.robot_menu = QMenu(self.tr("menu_robot"), self)
        self.ui.menubar.insertMenu(self.ui.menu_2.menuAction(), self.robot_menu)

        # 添加机器人
        self.action_add_robot = QAction(self.tr("action_add_robot"), self)
        self.action_add_robot.triggered.connect(self.on_add_robot)
        self.robot_menu.addAction(self.action_add_robot)

        # 2. 创建"CAN"菜单
        self.can_menu = QMenu(self.tr("menu_can"), self)
        self.ui.menubar.insertMenu(self.ui.menu_2.menuAction(), self.can_menu)

        # CAN接口操作
        self.action_start_can = QAction(self.tr("start_can"), self)
        self.action_stop_can = QAction(self.tr("stop_can"), self)
        self.action_check_can = QAction(self.tr("check_can"), self)

        self.action_start_can.triggered.connect(self.on_start_can)
        self.action_stop_can.triggered.connect(self.on_stop_can)
        self.action_check_can.triggered.connect(self.on_check_can_status)

        self.can_menu.addAction(self.action_start_can)
        self.can_menu.addAction(self.action_stop_can)
        self.can_menu.addAction(self.action_check_can)

        # 3. 创建"设置"菜单
        self.settings_menu = QMenu(self.tr("menu_settings"), self)
        self.ui.menubar.insertMenu(self.ui.menu_2.menuAction(), self.settings_menu)

        # 设置和退出
        self.action_settings = QAction(self.tr("action_preferences"), self)
        self.action_settings.triggered.connect(self.on_show_settings)
        self.settings_menu.addAction(self.action_settings)
        self.settings_menu.addSeparator()

        self.action_exit = QAction(self.tr("action_exit"), self)
        self.action_exit.triggered.connect(self.close)
        self.settings_menu.addAction(self.action_exit)

        # 语言菜单和帮助菜单保持原有连接
        self.ui.action_3.triggered.connect(self.on_show_about)
        self.ui.action_4.triggered.connect(self.on_set_chinese)
        self.ui.action_5.triggered.connect(self.on_set_english)
        self.ui.action_6.triggered.connect(self.on_set_japanese)
        self.ui.action_7.triggered.connect(self.on_set_russian)

    def load_icon(self):
        """加载窗口图标"""
        # 使用相对于当前文件的路径
        icon_path = Path(__file__).parent / 'texture' / 'icon.png'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
            self.log_to_current_page(self.tr("log_icon_loaded").format(icon_name=icon_path.name), "SUCCESS")
        else:
            # 如果找不到，尝试使用 .ico 格式
            icon_path = Path(__file__).parent / 'texture' / 'icon.ico'
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                self.log_to_current_page(self.tr("log_icon_loaded").format(icon_name=icon_path.name), "SUCCESS")

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

    def log_to_current_page(self, message: str, level: str = "INFO"):
        """输出日志到当前页面"""
        current_widget = self.tab_widget.currentWidget()
        if isinstance(current_widget, RobotPage):
            current_widget.log(message, level)
        else:
            # 如果当前不是机器人页面，输出到控制台
            print(f"[{level}] {message}")

    # ==================== 机器人管理 ====================

    def on_add_robot(self):
        """添加机器人"""
        # 检测CAN接口
        interfaces = CANDetector.detect_can_interfaces()

        if len(interfaces) < 2:
            QMessageBox.warning(
                self,
                self.tr("can_insufficient_title"),
                self.tr("can_insufficient_msg").format(
                    count=len(interfaces),
                    interfaces=', '.join(interfaces) if interfaces else '无'
                )
            )
            return

        # 显示配置选择对话框
        config_dialog = QMessageBox(self)
        config_dialog.setWindowTitle(self.tr("robot_add_config_title"))
        config_dialog.setText(self.tr("robot_add_config_prompt").format(
            count=len(interfaces),
            interfaces=', '.join(interfaces)
        ))
        config_dialog.setIcon(QMessageBox.Icon.Question)

        auto_btn = config_dialog.addButton(self.tr("robot_add_auto_config"), QMessageBox.ButtonRole.AcceptRole)
        manual_btn = config_dialog.addButton(self.tr("robot_add_manual_config"), QMessageBox.ButtonRole.RejectRole)
        config_dialog.addButton(QMessageBox.StandardButton.Cancel)

        config_dialog.exec()
        clicked_button = config_dialog.clickedButton()

        if clicked_button == auto_btn:
            # 自动配置
            self.add_robot_auto_config(interfaces)
        elif clicked_button == manual_btn:
            # 手动配置
            self.add_robot_manual_config(interfaces)

    def add_robot_auto_config(self, interfaces):
        """自动配置添加机器人"""
        # 自动配对CAN接口
        pairs = CANDetector.auto_pair_can_interfaces(interfaces)

        if not pairs:
            QMessageBox.warning(
                self,
                self.tr("can_config_failed_title"),
                self.tr("can_config_failed_msg")
            )
            return

        # 找出未使用的配对
        used_interfaces = set()
        for page in self.robot_pages:
            used_interfaces.add(page.right_channel)
            used_interfaces.add(page.left_channel)

        available_pair = None
        for pair in pairs:
            if pair[0] not in used_interfaces and pair[1] not in used_interfaces:
                available_pair = pair
                break

        if not available_pair:
            QMessageBox.warning(
                self,
                self.tr("can_no_available_pair_title"),
                self.tr("can_no_available_pair_msg")
            )
            return

        # 让用户输入机器人名称
        robot_index = len(self.robot_pages) + 1
        default_name = self.tr("robot_add_default_name").format(number=robot_index)
        robot_name, ok = QInputDialog.getText(
            self,
            self.tr("robot_add_title"),
            self.tr("robot_add_auto_pair_info").format(
                right=available_pair[0],
                left=available_pair[1]
            ),
            text=default_name
        )

        if not ok or not robot_name:
            return

        # 检查是否使用默认名称
        use_default_name = (robot_name == default_name)

        # 创建机器人页面
        self.add_robot_page(robot_name, available_pair[0], available_pair[1], robot_index, use_default_name)

    def add_robot_manual_config(self, interfaces):
        """手动配置添加机器人"""
        # 让用户输入机器人名称
        robot_index = len(self.robot_pages) + 1
        default_name = self.tr("robot_add_default_name").format(number=robot_index)
        robot_name, ok = QInputDialog.getText(
            self,
            self.tr("robot_add_title"),
            self.tr("robot_add_name_prompt"),
            text=default_name
        )

        if not ok or not robot_name:
            return

        # 检查是否使用默认名称
        use_default_name = (robot_name == default_name)

        # 让用户选择右臂CAN通道
        right_channel, ok = QInputDialog.getItem(
            self,
            self.tr("robot_add_select_right"),
            self.tr("robot_add_select_right_prompt"),
            interfaces,
            0,
            False
        )

        if not ok:
            return

        # 让用户选择左臂CAN通道
        available_left = [iface for iface in interfaces if iface != right_channel]
        if not available_left:
            QMessageBox.warning(
                self,
                self.tr("can_no_left_channel_title"),
                self.tr("can_no_left_channel_msg")
            )
            return

        left_channel, ok = QInputDialog.getItem(
            self,
            self.tr("robot_add_select_left"),
            self.tr("robot_add_select_left_prompt"),
            available_left,
            0,
            False
        )

        if not ok:
            return

        # 验证配对
        valid, error_msg = CANDetector.validate_can_pair(right_channel, left_channel)
        if not valid:
            QMessageBox.warning(
                self,
                self.tr("can_config_invalid_title"),
                error_msg
            )
            return

        # 创建机器人页面
        self.add_robot_page(robot_name, right_channel, left_channel, robot_index, use_default_name)

    def add_robot_page(self, robot_name: str, right_channel: str, left_channel: str, robot_index: int = None, use_default_name: bool = False):
        """添加机器人页面

        Args:
            robot_name: 机器人名称
            right_channel: 右臂CAN通道
            left_channel: 左臂CAN通道
            robot_index: 机器人编号（用于默认名称）
            use_default_name: 是否使用默认名称
        """
        # 创建页面
        robot_page = RobotPage(robot_name, right_channel, left_channel, self.config_manager, robot_index, use_default_name)
        robot_page.set_enabled(True)  # 启用控件

        # 添加到标签页
        index = self.tab_widget.addTab(robot_page, robot_name)
        self.tab_widget.setCurrentIndex(index)

        # 保存到列表
        self.robot_pages.append(robot_page)

        robot_page.log(self.tr("log_robot_added").format(robot_name=robot_name), "SUCCESS")

    def on_close_robot_tab(self, index: int):
        """关闭机器人标签页"""
        # 不允许关闭欢迎页面（索引0）
        if index == 0:
            return

        widget = self.tab_widget.widget(index)
        if isinstance(widget, RobotPage):
            reply = QMessageBox.question(
                self,
                self.tr("robot_close_confirm_title"),
                self.tr("robot_close_confirm_msg").format(name=widget.robot_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                # 清理资源
                widget.cleanup()

                # 从列表中移除
                if widget in self.robot_pages:
                    self.robot_pages.remove(widget)

                # 移除标签页
                self.tab_widget.removeTab(index)

    # ==================== CAN接口操作 ====================

    def on_start_can(self):
        """启动CAN接口"""
        # 获取sudo密码
        password = self.get_sudo_password()
        if password is None:
            self.log_to_current_page("未获取到密码，操作取消", "ERROR")
            return

        self.log_to_current_page("开始启动CAN接口...", "INFO")

        try:
            # 1. 检测所有CAN接口
            all_interfaces = get_all_can_interfaces()
            self.log_to_current_page(f"检测到 {len(all_interfaces)} 个CAN接口: {', '.join(all_interfaces) if all_interfaces else '无'}", "INFO")

            if not all_interfaces:
                QMessageBox.warning(
                    self,
                    self.tr("can_not_detected_title"),
                    self.tr("can_not_detected_msg")
                )
                self.log_to_current_page("未检测到CAN接口，请插上CAN盒", "ERROR")
                return

            # 2. 过滤虚拟接口
            real_interfaces = []
            for iface in all_interfaces:
                if check_can_interface_type(iface):
                    real_interfaces.append(iface)
                    self.log_to_current_page(f"{iface}: 真实CAN接口 ✓", "SUCCESS")
                else:
                    self.log_to_current_page(f"{iface}: 虚拟接口，跳过", "INFO")

            if not real_interfaces:
                QMessageBox.warning(
                    self,
                    self.tr("can_no_real_title"),
                    self.tr("can_no_real_msg")
                )
                self.log_to_current_page("没有找到真实的CAN接口", "ERROR")
                return

            self.log_to_current_page(f"找到 {len(real_interfaces)} 个真实CAN接口", "SUCCESS")

            # 3. 逐个启用CAN接口
            success_count = 0
            failed_interfaces = []

            for iface in real_interfaces:
                self.log_to_current_page(f"正在启用 {iface}...", "INFO")
                if enable_can_interface(iface, bitrate=1000000, loopback=False, password=password, verbose=False):
                    # 验证启用结果
                    if verify_can_interface(iface):
                        self.log_to_current_page(f"{iface}: 启用成功 ✓", "SUCCESS")
                        success_count += 1
                    else:
                        self.log_to_current_page(f"{iface}: 启用失败（验证未通过）✗", "WARNING")
                        failed_interfaces.append(iface)
                else:
                    self.log_to_current_page(f"{iface}: 启用失败 ✗", "ERROR")
                    failed_interfaces.append(iface)

            # 4. 显示总结
            if success_count == len(real_interfaces):
                self.log_to_current_page(f"所有CAN接口启用成功！({success_count}/{len(real_interfaces)})", "SUCCESS")
                QMessageBox.information(
                    self,
                    self.tr("can_enable_success_title"),
                    self.tr("can_enable_success_msg").format(
                        count=success_count,
                        interfaces=', '.join(real_interfaces)
                    )
                )
            elif success_count > 0:
                self.log_to_current_page(f"部分CAN接口启用成功 ({success_count}/{len(real_interfaces)})", "WARNING")
                QMessageBox.warning(
                    self,
                    self.tr("can_enable_partial_title"),
                    self.tr("can_enable_partial_msg").format(
                        success=success_count,
                        total=len(real_interfaces),
                        failed=', '.join(failed_interfaces)
                    )
                )
            else:
                self.log_to_current_page("所有CAN接口启用失败", "ERROR")
                QMessageBox.critical(
                    self,
                    self.tr("can_enable_failed_title"),
                    self.tr("can_enable_failed_msg").format(
                        failed=', '.join(failed_interfaces)
                    )
                )

        except Exception as e:
            self.log_to_current_page(f"CAN启动异常: {str(e)}", "ERROR")
            QMessageBox.critical(
                self,
                self.tr("can_enable_error_title"),
                self.tr("can_enable_error_msg").format(error=str(e))
            )

    def on_stop_can(self):
        """禁用CAN接口"""
        # 获取sudo密码
        password = self.get_sudo_password()
        if password is None:
            self.log_to_current_page("未获取到密码，操作取消", "ERROR")
            return

        self.log_to_current_page("开始禁用CAN接口...", "INFO")

        try:
            # 1. 检测所有已启用的CAN接口
            available_interfaces = get_available_can_interfaces()
            self.log_to_current_page(f"检测到 {len(available_interfaces)} 个已启用的CAN接口: {', '.join(available_interfaces) if available_interfaces else '无'}", "INFO")

            if not available_interfaces:
                QMessageBox.information(
                    self,
                    self.tr("can_disable_no_need_title"),
                    self.tr("can_disable_no_need_msg")
                )
                self.log_to_current_page("未检测到已启用的CAN接口", "INFO")
                return

            # 2. 过滤虚拟接口
            real_interfaces = []
            for iface in available_interfaces:
                if check_can_interface_type(iface):
                    real_interfaces.append(iface)
                    self.log_to_current_page(f"{iface}: 真实CAN接口，将禁用", "INFO")
                else:
                    self.log_to_current_page(f"{iface}: 虚拟接口，跳过", "INFO")

            if not real_interfaces:
                QMessageBox.information(
                    self,
                    self.tr("can_disable_no_real_title"),
                    self.tr("can_disable_no_real_msg")
                )
                self.log_to_current_page("没有找到真实的CAN接口需要禁用", "INFO")
                return

            self.log_to_current_page(f"找到 {len(real_interfaces)} 个真实CAN接口需要禁用", "INFO")

            # 3. 逐个禁用CAN接口
            success_count = 0
            failed_interfaces = []

            for iface in real_interfaces:
                self.log_to_current_page(f"正在禁用 {iface}...", "INFO")
                if disable_can_interface(iface, password=password, verbose=False):
                    # 验证禁用结果
                    if not verify_can_interface(iface):  # verify返回False表示已DOWN
                        self.log_to_current_page(f"{iface}: 禁用成功 ✓", "SUCCESS")
                        success_count += 1
                    else:
                        self.log_to_current_page(f"{iface}: 禁用失败（仍然UP）✗", "WARNING")
                        failed_interfaces.append(iface)
                else:
                    self.log_to_current_page(f"{iface}: 禁用失败 ✗", "ERROR")
                    failed_interfaces.append(iface)

            # 4. 显示总结
            if success_count == len(real_interfaces):
                self.log_to_current_page(f"所有CAN接口禁用成功！({success_count}/{len(real_interfaces)})", "SUCCESS")
                QMessageBox.information(
                    self,
                    self.tr("can_disable_success_title"),
                    self.tr("can_disable_success_msg").format(
                        count=success_count,
                        interfaces=', '.join(real_interfaces)
                    )
                )
            elif success_count > 0:
                self.log_to_current_page(f"部分CAN接口禁用成功 ({success_count}/{len(real_interfaces)})", "WARNING")
                QMessageBox.warning(
                    self,
                    self.tr("can_disable_partial_title"),
                    self.tr("can_disable_partial_msg").format(
                        success=success_count,
                        total=len(real_interfaces),
                        failed=', '.join(failed_interfaces)
                    )
                )
            else:
                self.log_to_current_page("所有CAN接口禁用失败", "ERROR")
                QMessageBox.critical(
                    self,
                    self.tr("can_disable_failed_title"),
                    self.tr("can_disable_failed_msg").format(
                        failed=', '.join(failed_interfaces)
                    )
                )

        except Exception as e:
            self.log_to_current_page(f"CAN禁用异常: {str(e)}", "ERROR")
            QMessageBox.critical(
                self,
                self.tr("can_disable_error_title"),
                self.tr("can_disable_error_msg").format(error=str(e))
            )

    def on_check_can_status(self):
        """检查CAN状态"""
        interfaces = CANDetector.detect_can_interfaces()
        self.log_to_current_page(f"检测到{len(interfaces)}个CAN接口", "INFO")

        status_info = []
        for iface in interfaces:
            status = CANDetector.check_can_status(iface)
            status_str = f"{iface}: {status['state']}"
            if status['bitrate']:
                status_str += f" (bitrate: {status['bitrate']})"
            status_info.append(status_str)
            self.log_to_current_page(status_str, "INFO")

        QMessageBox.information(
            self,
            self.tr("can_status_title"),
            "\n".join(status_info) if status_info else self.tr("can_status_no_interface")
        )

    def get_sudo_password(self):
        """获取sudo密码"""
        if self.sudo_password is not None:
            return self.sudo_password

        saved_password = self.config_manager.get_sudo_password()
        if saved_password:
            self.sudo_password = saved_password
            self.log_to_current_page("使用已保存的密码", "SUCCESS")
            return saved_password

        # 首次使用CAN操作，请求用户输入密码
        if self.check_and_request_password():
            # 密码已保存，重新获取
            saved_password = self.config_manager.get_sudo_password()
            if saved_password:
                self.sudo_password = saved_password
                return saved_password

        return None

    # ==================== 菜单操作 ====================

    def on_show_about(self):
        """显示关于对话框"""
        QMessageBox.about(self, self.tr("about_title"), self.tr("about_content"))

    def on_show_settings(self):
        """显示设置对话框"""
        dialog = SettingsDialog(self.config_manager, self)
        dialog.exec()

    def on_set_chinese(self):
        """设置中文"""
        if self.current_language != "zh_CN":
            self.current_language = "zh_CN"
            self.config_manager.set_language("zh_CN")
            self._update_ui_texts()
            self.log_to_current_page(self.tr("log_switch_to_chinese"), "SUCCESS")

    def on_set_english(self):
        """设置英语"""
        if self.current_language != "en_US":
            self.current_language = "en_US"
            self.config_manager.set_language("en_US")
            self._update_ui_texts()
            self.log_to_current_page(self.tr("log_switch_to_english"), "SUCCESS")

    def on_set_japanese(self):
        """设置日语"""
        if self.current_language != "ja_JP":
            self.current_language = "ja_JP"
            self.config_manager.set_language("ja_JP")
            self._update_ui_texts()
            self.log_to_current_page(self.tr("log_switch_to_japanese"), "SUCCESS")

    def on_set_russian(self):
        """设置俄语"""
        if self.current_language != "ru_RU":
            self.current_language = "ru_RU"
            self.config_manager.set_language("ru_RU")
            self._update_ui_texts()
            self.log_to_current_page(self.tr("log_switch_to_russian"), "SUCCESS")

    def _update_ui_texts(self):
        """更新界面文本"""
        # 窗口标题
        self.setWindowTitle(self.tr("window_title"))

        # 菜单标题
        if hasattr(self, 'robot_menu'):
            self.robot_menu.setTitle(self.tr("menu_robot"))
        if hasattr(self, 'can_menu'):
            self.can_menu.setTitle(self.tr("menu_can"))
        if hasattr(self, 'settings_menu'):
            self.settings_menu.setTitle(self.tr("menu_settings"))

        self.ui.menu_2.setTitle(self.tr("menu_language"))
        if hasattr(self.ui, 'menu_H'):
            self.ui.menu_H.setTitle(self.tr("menu_help"))

        # 机器人菜单项
        if hasattr(self, 'action_add_robot'):
            self.action_add_robot.setText(self.tr("action_add_robot"))

        # CAN菜单项
        if hasattr(self, 'action_start_can'):
            self.action_start_can.setText(self.tr("start_can"))
        if hasattr(self, 'action_stop_can'):
            self.action_stop_can.setText(self.tr("stop_can"))
        if hasattr(self, 'action_check_can'):
            self.action_check_can.setText(self.tr("check_can"))

        # 设置菜单项
        if hasattr(self, 'action_settings'):
            self.action_settings.setText(self.tr("action_preferences"))
        if hasattr(self, 'action_exit'):
            self.action_exit.setText(self.tr("action_exit"))

        # 帮助菜单项
        if hasattr(self.ui, 'action_3'):
            self.ui.action_3.setText(self.tr("action_about"))

        # 更新欢迎页面内容
        if self.welcome_label is not None:
            welcome_html = f"""
                <h1>{self.tr("welcome_title")}</h1>
                <p>{self.tr("welcome_greeting")}</p>
                <br>
                <h3>{self.tr("welcome_quick_start")}</h3>
                <ul>
                    <li>{self.tr("welcome_step1")}</li>
                    <li>{self.tr("welcome_step2")}</li>
                    <li>{self.tr("welcome_step3")}</li>
                </ul>
            """
            self.welcome_label.setText(welcome_html)

        # 更新欢迎标签页标题
        if self.welcome_tab_index is not None:
            self.tab_widget.setTabText(self.welcome_tab_index, self.tr("welcome_tab_title"))

        # 更新所有机器人页面
        for i, robot_page in enumerate(self.robot_pages):
            robot_page.update_ui_texts()
            # 更新标签页标题
            # 找到该页面在 tab_widget 中的索引
            page_index = self.tab_widget.indexOf(robot_page)
            if page_index != -1:
                self.tab_widget.setTabText(page_index, robot_page.robot_name)

    def check_first_run(self):
        """检查首次运行并自动配置"""
        if self.config_manager.is_first_run():
            self.log_to_current_page("检测到首次运行，正在自动检测脚本位置...", "INFO")

            # 自动检测脚本路径
            detected_scripts = self.detect_script_paths()

            script_types = {
                'moveit_sim': '仿真版 MoveIt 脚本',
                'moveit_can': '真机版 MoveIt 脚本 (CAN)'
            }

            found_any = False

            for script_type, type_name in script_types.items():
                paths = detected_scripts[script_type]

                if not paths:
                    continue

                found_any = True

                if len(paths) == 1:
                    # 只找到一个，直接使用
                    selected_path = paths[0]
                    self.log_to_current_page(f"✓ 已检测到{type_name}: {selected_path}", "SUCCESS")
                else:
                    # 找到多个，让用户选择
                    self.log_to_current_page(f"检测到 {len(paths)} 个{type_name}，请选择...", "INFO")

                    selected_path, ok = QInputDialog.getItem(
                        self,
                        f"选择{type_name}",
                        f"检测到多个{type_name}，请选择要使用的版本：",
                        paths,
                        0,
                        False
                    )

                    if not ok or not selected_path:
                        self.log_to_current_page(f"未选择{type_name}，跳过", "WARNING")
                        continue

                    self.log_to_current_page(f"✓ 已选择{type_name}: {selected_path}", "SUCCESS")

                # 保存选中的脚本路径
                self.config_manager.set_script_path(script_type, selected_path)
                self.config_manager.set_default_script_path(script_type, selected_path)

            if found_any:
                # 保存配置
                self.config_manager.save_config()
                self.log_to_current_page("脚本路径已保存到配置文件", "SUCCESS")
            else:
                self.log_to_current_page("未检测到脚本，请在设置中手动配置", "WARNING")

            # 标记首次运行已完成
            self.config_manager.set_first_run_completed()

    def detect_script_paths(self):
        """自动检测脚本路径

        Returns:
            dict: {'moveit_sim': [路径列表], 'moveit_can': [路径列表]}
        """
        import os

        result = {
            'moveit_sim': [],
            'moveit_can': []
        }

        # 常见的脚本位置
        base_paths = [
            os.path.expanduser("~/openarm/openarmx_robotstride_ws-cc/src/openarm_ros2/openarmx_bimanual_moveit_config"),
            os.path.expanduser("~/openarmx_ws/src/openarm_ros2/openarmx_bimanual_moveit_config"),
            os.path.expanduser("~/openarm_ros2/openarmx_bimanual_moveit_config"),
        ]

        script_names = {
            'moveit_sim': 'run_bimanual_moveit_sim.sh',
            'moveit_can': 'run_bimanual_moveit_with_can2.0.sh'
        }

        # 检测每个可能的位置，收集所有找到的脚本
        for base_path in base_paths:
            if not os.path.exists(base_path):
                continue

            for script_type, script_name in script_names.items():
                script_path = os.path.join(base_path, script_name)
                if os.path.exists(script_path) and script_path not in result[script_type]:
                    result[script_type].append(script_path)

        return result

    def check_and_request_password(self):
        """检查并请求 sudo 密码

        Returns:
            bool: 是否成功获取密码（如果已有密码或用户输入了密码返回True）
        """
        # 如果已经配置了密码，直接返回
        if self.config_manager.has_sudo_password():
            return True

        # 弹出对话框请求用户输入密码
        password, ok = QInputDialog.getText(
            self,
            self.tr("password_first_time_title"),
            self.tr("password_first_time_msg"),
            QLineEdit.EchoMode.Password
        )

        if ok and password:
            # 保存密码到配置
            self.config_manager.set_sudo_password(password)
            self.config_manager.set_default_password(password)
            self.config_manager.save_config()
            self.log_to_current_page(self.tr("password_saved"), "SUCCESS")
            return True
        else:
            self.log_to_current_page(self.tr("password_not_entered"), "WARNING")
            return False

    def closeEvent(self, event):
        """关闭事件"""
        # 清理所有机器人页面
        for robot_page in self.robot_pages:
            robot_page.cleanup()

        event.accept()
