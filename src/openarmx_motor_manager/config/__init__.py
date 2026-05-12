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
@File    :   __init__.py
@Time    :   2026/01/05 18:50:35
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   None
'''



from .config_manager import ConfigManager
from .script_finder import ScriptFinder

__all__ = ['ConfigManager', 'ScriptFinder']
