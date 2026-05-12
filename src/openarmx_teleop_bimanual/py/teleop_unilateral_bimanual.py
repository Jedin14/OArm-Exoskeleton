#!/usr/bin/env python3
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


"""
一键启动左右臂单向遥操作 (Python 版)

- 右臂: leader=can0, follower=can2
- 左臂: leader=can1, follower=can3

内部启动两个 teleop_unilateral_single.py 子进程，并在 Ctrl+C 时统一退出。
"""

import subprocess
import sys
import signal
import os

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), 'teleop_unilateral_single.py')

procs = []


def signal_handler(sig, frame):
    print("\n[INFO] Ctrl+C detected, terminating teleop processes...")
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    # 再给一点时间让子进程退出
    try:
        for p in procs:
            p.wait(timeout=2)
    except Exception:
        pass
    sys.exit(0)


def main():
    global procs

    # 注册 Ctrl+C 处理
    signal.signal(signal.SIGINT, signal_handler)

    print(f"[INFO] Using teleop script: {SCRIPT_PATH}")

    # 启动右臂 (can0 -> can2)
    print("[INFO] Starting RIGHT arm teleop: leader=can0, follower=can2")
    p_right = subprocess.Popen([
        sys.executable,
        SCRIPT_PATH,
        'can0', 'can2'
    ])
    procs.append(p_right)

    # 启动左臂 (can1 -> can3)
    print("[INFO] Starting LEFT arm teleop: leader=can1, follower=can3")
    p_left = subprocess.Popen([
        sys.executable,
        SCRIPT_PATH,
        'can1', 'can3'
    ])
    procs.append(p_left)

    print("[INFO] Bimanual teleop started. Press Ctrl+C to stop both arms.")

    # 等待子进程结束
    try:
        while True:
            ret_right = p_right.poll()
            ret_left = p_left.poll()
            if ret_right is not None or ret_left is not None:
                print("[INFO] One of teleop processes exited, shutting down...")
                signal_handler(None, None)
            signal.pause()
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()
