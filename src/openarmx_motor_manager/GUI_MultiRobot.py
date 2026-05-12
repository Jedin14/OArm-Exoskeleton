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
@File    :   GUI_MultiRobot.py
@Time    :   2026/01/05 18:43:53
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   电机管理系统入口
'''



import sys
from PySide6.QtWidgets import QApplication

from ui.MainUI_MultiRobot import MainUI_MultiRobot


def main():
    """主函数"""
    app = QApplication(sys.argv)

    # 创建并显示主窗口
    window = MainUI_MultiRobot()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
