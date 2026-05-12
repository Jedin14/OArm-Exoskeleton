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
@File    :   RobotWorker.py
@Time    :   2026/01/05 18:46:58
@Author  :   Wei Lindong 
@Version :   2.0
@Desc    :   单个机器人控制Worker - 支持多机器人系统
'''



import time
from PySide6.QtCore import QThread, Signal
from openarmx_arm_driver import Robot, OpenArmXError


class RobotWorker(QThread):
    """单个机器人控制Worker线程"""

    # 定义信号
    log_signal = Signal(str, str)  # (message, level)
    finished_signal = Signal(bool, str)  # (success, message)
    motor_status_signal = Signal(int, int, dict)  # (can_channel, motor_id, info)
    error_signal = Signal(str, str, str)  # (arm_name, operation, error_details) - 电机错误信号

    def __init__(self, task, robot=None, **kwargs):
        super().__init__()
        self.task = task
        self.robot = robot  # 使用已创建的Robot实例
        self.task_params = kwargs
        self._is_running = True
        self.failed_motors = []  # 记录失败的电机列表

    def run(self):
        """线程主函数"""
        try:
            if self.task == 'enable_all':
                self._enable_all()
            elif self.task == 'disable_all':
                self._disable_all()
            elif self.task == 'go_home':
                self._go_home()
            elif self.task == 'set_zero':
                self._set_zero()
            elif self.task == 'test_all':
                self._test_all()
            elif self.task == 'check_status':
                self._check_status()
            elif self.task == 'single_motor_mit':
                self._single_motor_mit()
            else:
                self.log_signal.emit(f"未知任务类型: {self.task}", "ERROR")
                self.finished_signal.emit(False, "未知任务")
        except Exception as e:
            self.log_signal.emit(f"任务执行出错: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def stop(self):
        """停止线程"""
        self._is_running = False
        # Robot实例由RobotPage管理，不在这里关闭

    # ==================== 电机控制任务 ====================

    def _enable_all(self):
        """启用所有电机"""
        self.log_signal.emit("启用所有电机...", "INFO")
        self.failed_motors = []  # 清空失败记录

        def progress_callback(arm_name, motor_id, success, error_msg, exec_time):
            """实时进度回调 - 每个电机操作完成时调用"""
            arm_cn = "右臂" if arm_name == "right" else "左臂"

            if success:
                self.log_signal.emit(
                    f"✓ {arm_cn} 电机 {motor_id}: 使能成功 ({exec_time:.3f}s)",
                    "SUCCESS"
                )
            else:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 使能失败 - {error_msg} ({exec_time:.3f}s)",
                    "ERROR"
                )
                # 记录失败的电机
                self.failed_motors.append(f"{arm_cn} 电机{motor_id}")
                # 立即发送错误信号弹窗
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} 使能失败",
                    error_msg
                )

        try:
            # 调用 enable_all 并传入 progress_callback
            results = self.robot.enable_all(
                verbose=True,
                timeout=2.0,
                progress_callback=progress_callback
            )

            # 输出总结信息
            summary = results['summary']
            total = summary['total']
            success_count = summary['success']
            failed_count = summary['failed']
            total_time = summary['total_time']

            if failed_count == 0:
                self.log_signal.emit(
                    f"所有电机已启用 ({success_count}/{total} 成功，耗时 {total_time:.3f}s)",
                    "SUCCESS"
                )
                self.finished_signal.emit(True, "所有电机已启用")
            else:
                self.log_signal.emit(
                    f"电机启用完成，但有 {failed_count} 个失败 ({success_count}/{total} 成功，耗时 {total_time:.3f}s)",
                    "WARNING"
                )
                self.finished_signal.emit(
                    False,
                    f"{failed_count} 个电机启用失败: {', '.join(self.failed_motors)}"
                )

        except Exception as e:
            self.log_signal.emit(f"启用失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _disable_all(self):
        """禁用所有电机"""
        self.log_signal.emit("禁用所有电机...", "INFO")
        self.failed_motors = []  # 清空失败记录

        def progress_callback(arm_name, motor_id, success, error_msg, exec_time):
            """实时进度回调 - 每个电机操作完成时调用"""
            arm_cn = "右臂" if arm_name == "right" else "左臂"

            if success:
                self.log_signal.emit(
                    f"✓ {arm_cn} 电机 {motor_id}: 停止成功 ({exec_time:.3f}s)",
                    "SUCCESS"
                )
            else:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 停止失败 - {error_msg} ({exec_time:.3f}s)",
                    "ERROR"
                )
                # 记录失败的电机
                self.failed_motors.append(f"{arm_cn} 电机{motor_id}")
                # 立即发送错误信号弹窗
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} 停止失败",
                    error_msg
                )

        try:
            if self.robot:
                # 调用 disable_all 并传入 progress_callback
                results = self.robot.disable_all(
                    verbose=True,
                    timeout=2.0,
                    progress_callback=progress_callback
                )

                # 输出总结信息
                summary = results['summary']
                total = summary['total']
                success_count = summary['success']
                failed_count = summary['failed']
                total_time = summary['total_time']

                if failed_count == 0:
                    self.log_signal.emit(
                        f"所有电机已停止 ({success_count}/{total} 成功，耗时 {total_time:.3f}s)",
                        "SUCCESS"
                    )
                    self.finished_signal.emit(True, "所有电机已停止")
                else:
                    self.log_signal.emit(
                        f"电机停止完成，但有 {failed_count} 个失败 ({success_count}/{total} 成功，耗时 {total_time:.3f}s)",
                        "WARNING"
                    )
                    self.finished_signal.emit(
                        False,
                        f"{failed_count} 个电机停止失败: {', '.join(self.failed_motors)}"
                    )

        except Exception as e:
            self.log_signal.emit(f"停止失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _go_home(self):
        """电机回零"""
        self.log_signal.emit("电机回零中...", "INFO")
        self.failed_motors = []  # 清空失败记录

        def progress_callback(arm_name, motor_id, success, error_msg, exec_time):
            """实时进度回调"""
            arm_cn = "右臂" if arm_name == "right" else "左臂"

            if success:
                self.log_signal.emit(
                    f"✓ {arm_cn} 电机 {motor_id}: 操作成功 ({exec_time:.3f}s)",
                    "SUCCESS"
                )
            else:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 操作失败 - {error_msg} ({exec_time:.3f}s)",
                    "ERROR"
                )
                self.failed_motors.append(f"{arm_cn} 电机{motor_id}")
                # 发送错误信号
                self.error_signal.emit(arm_cn, f"电机 {motor_id} 操作失败", error_msg)

        try:
            # 设置为MIT模式
            self.log_signal.emit("设置MIT模式...", "INFO")
            self.robot.set_mode_all('mit', verbose=True, progress_callback=progress_callback)

            # 启用所有电机
            self.log_signal.emit("启用所有电机...", "INFO")
            self.robot.enable_all(verbose=True, progress_callback=progress_callback)

            # 回到零位
            self.log_signal.emit("移动到零位...", "INFO")
            results = self.robot.move_all_to_zero(
                kp=10.0, kd=1.0,
                verbose=True,
                progress_callback=progress_callback
            )

            # 检查结果
            if results['summary']['failed'] == 0:
                self.log_signal.emit("回零完毕！", "SUCCESS")
                self.finished_signal.emit(True, "回零完成")
            else:
                failed_count = results['summary']['failed']
                self.log_signal.emit(
                    f"回零完成，但有 {failed_count} 个电机失败",
                    "WARNING"
                )
                self.finished_signal.emit(
                    False,
                    f"{failed_count} 个电机回零失败: {', '.join(self.failed_motors)}"
                )

        except Exception as e:
            self.log_signal.emit(f"回零失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _set_zero(self):
        """设置零点"""
        self.log_signal.emit("设置零点...", "INFO")
        self.failed_motors = []  # 清空失败记录

        def progress_callback(arm_name, motor_id, success, error_msg, exec_time):
            """实时进度回调"""
            arm_cn = "右臂" if arm_name == "right" else "左臂"

            if success:
                self.log_signal.emit(
                    f"✓ {arm_cn} 电机 {motor_id}: 零点设置成功 ({exec_time:.3f}s)",
                    "SUCCESS"
                )
            else:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 零点设置失败 - {error_msg} ({exec_time:.3f}s)",
                    "ERROR"
                )
                self.failed_motors.append(f"{arm_cn} 电机{motor_id}")
                # 发送错误信号
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} 零点设置失败",
                    error_msg
                )

        try:
            # 调用SDK设置零点
            results = self.robot.set_zero_all(
                verbose=True,
                timeout=2.0,
                progress_callback=progress_callback
            )

            # 检查结果
            summary = results['summary']
            if summary['failed'] == 0:
                self.log_signal.emit(
                    f"零点设置完成 ({summary['success']}/{summary['total']} 成功，耗时 {summary['total_time']:.3f}s)",
                    "SUCCESS"
                )
                self.finished_signal.emit(True, "零点设置完成")
            else:
                self.log_signal.emit(
                    f"零点设置完成，但有 {summary['failed']} 个失败",
                    "WARNING"
                )
                self.finished_signal.emit(
                    False,
                    f"{summary['failed']} 个电机零点设置失败: {', '.join(self.failed_motors)}"
                )

        except Exception as e:
            self.log_signal.emit(f"零点设置失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _test_all(self):
        """测试全部电机"""
        self.log_signal.emit("测试全部电机...", "INFO")

        try:
            # 设置为MIT模式
            self.robot.set_mode_all('mit')

            # 启用所有电机
            self.robot.enable_all()

            # 简单的测试运动
            self.log_signal.emit("执行测试运动...", "INFO")

            # 小幅度运动测试
            test_angle = 0.3  # 0.3 rad 约 17.2 度
            self.robot.test_motor_one_by_one([test_angle]*8, [test_angle]*8, kp=10.0, kd=2.0)

            self.log_signal.emit("电机测试完成", "SUCCESS")
            self.finished_signal.emit(True, "测试完成")
        except OpenArmXError as e:
            self.log_signal.emit(f"测试失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _check_status(self):
        """检查电机状态"""
        self.log_signal.emit("检查电机状态...", "INFO")

        try:
            right_channel = self.task_params.get('right_channel', 'can0')
            left_channel = self.task_params.get('left_channel', 'can1')

            total_count = 0

            # 检查右臂
            self.log_signal.emit(f"检查右臂 ({right_channel})...", "INFO")
            for motor_id in range(1, 9):
                try:
                    status = self.robot.right_arm.get_status(motor_id)
                    if status:
                        status_dict = {
                            'angle': status.get('angle', 0.0),
                            'velocity': status.get('velocity', 0.0),
                            'torque': status.get('torque', 0.0),
                            'temperature': status.get('temperature', 0.0),
                            'mode_status': status.get('mode_status', 'Unknown'),
                            'fault_status': status.get('fault_status', 'Unknown')
                        }
                        self.motor_status_signal.emit(0, motor_id, status_dict)
                        total_count += 1
                except Exception as e:
                    self.log_signal.emit(f"{right_channel} | motor ID:{motor_id:2d} | 读取失败: {str(e)}", 'WARNING')

            # 检查左臂
            self.log_signal.emit(f"检查左臂 ({left_channel})...", "INFO")
            for motor_id in range(1, 9):
                try:
                    status = self.robot.left_arm.get_status(motor_id)
                    if status:
                        status_dict = {
                            'angle': status.get('angle', 0.0),
                            'velocity': status.get('velocity', 0.0),
                            'torque': status.get('torque', 0.0),
                            'temperature': status.get('temperature', 0.0),
                            'mode_status': status.get('mode_status', 'Unknown'),
                            'fault_status': status.get('fault_status', 'Unknown')
                        }
                        self.motor_status_signal.emit(1, motor_id, status_dict)
                        total_count += 1
                except Exception as e:
                    self.log_signal.emit(f"{left_channel} | motor ID:{motor_id:2d} | 读取失败: {str(e)}", 'WARNING')

            self.log_signal.emit(f"电机状态检查完成，共检测到 {total_count} 个电机", "SUCCESS")
            self.finished_signal.emit(True, f"检测到 {total_count} 个电机")

        except OpenArmXError as e:
            self.log_signal.emit(f"状态检查失败: {str(e)}", "ERROR")
            self.finished_signal.emit(False, str(e))

    def _single_motor_mit(self):
        """单电机MIT模式控制"""
        # 获取参数
        arm_name = self.task_params.get('arm_name')
        motor_id = self.task_params.get('motor_id')
        params = self.task_params.get('params', {})

        arm_cn = "右臂" if arm_name == "right" else "左臂"

        # 检查是否是失能命令
        if params.get('disable', False):
            self.log_signal.emit(
                f"失能 {arm_cn} 电机 {motor_id}...",
                "INFO"
            )

            try:
                # 获取对应的Arm对象
                arm = self.robot.right_arm if arm_name == "right" else self.robot.left_arm

                # 失能电机
                state = arm.disable(motor_id, timeout=1.0, verbose=True)

                if state == 0:
                    self.log_signal.emit(
                        f"✓ {arm_cn} 电机 {motor_id}: 失能成功",
                        "SUCCESS"
                    )
                    self.finished_signal.emit(True, f"{arm_cn} 电机 {motor_id} 已失能")
                else:
                    self.log_signal.emit(
                        f"✗ {arm_cn} 电机 {motor_id}: 失能失败 (state={state})",
                        "ERROR"
                    )
                    self.error_signal.emit(
                        arm_cn,
                        f"电机 {motor_id} 失能失败",
                        f"返回状态码: {state}"
                    )
                    self.finished_signal.emit(False, f"{arm_cn} 电机 {motor_id} 失能失败")

            except Exception as e:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 失能出错 - {str(e)}",
                    "ERROR"
                )
                self.error_signal.emit(arm_cn, f"电机 {motor_id} 失能出错", str(e))
                self.finished_signal.emit(False, str(e))

            return

        # 正常的MIT模式控制流程
        self.log_signal.emit(
            f"控制 {arm_cn} 电机 {motor_id} (MIT模式)...",
            "INFO"
        )

        try:
            # 获取对应的Arm对象
            arm = self.robot.right_arm if arm_name == "right" else self.robot.left_arm

            # 步骤1: 切换为MIT模式
            self.log_signal.emit(f"步骤1: 设置 {arm_cn} 电机 {motor_id} 为MIT模式...", "INFO")
            state = arm.set_mode('mit', motor_id, timeout=1.0, verbose=True)
            if state != 0:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: MIT模式设置失败 (state={state})",
                    "ERROR"
                )
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} MIT模式设置失败",
                    f"返回状态码: {state}"
                )
                self.finished_signal.emit(False, f"{arm_cn} 电机 {motor_id} MIT模式设置失败")
                return
            self.log_signal.emit(f"✓ MIT模式设置成功", "SUCCESS")

            # 步骤2: 使能电机
            self.log_signal.emit(f"步骤2: 使能 {arm_cn} 电机 {motor_id}...", "INFO")
            state = arm.enable(motor_id, timeout=1.0, verbose=True)
            if state != 0:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 使能失败 (state={state})",
                    "ERROR"
                )
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} 使能失败",
                    f"返回状态码: {state}"
                )
                self.finished_signal.emit(False, f"{arm_cn} 电机 {motor_id} 使能失败")
                return
            self.log_signal.emit(f"✓ 电机使能成功", "SUCCESS")

            # 步骤3: 控制电机运动
            self.log_signal.emit(f"步骤3: 发送运动控制命令...", "INFO")
            state = self.robot.move_one_joint_mit(
                arm=arm_name,
                motor_id=motor_id,
                position=params.get('position', 0.0),
                velocity=params.get('velocity', 0.0),
                torque=params.get('torque', 0.0),
                kp=params.get('kp', 10.0),
                kd=params.get('kd', 1.0),
                wait_response=False,
                timeout=1.0,
                verbose=True
            )

            if state == 0:
                self.log_signal.emit(
                    f"✓ {arm_cn} 电机 {motor_id}: 控制命令发送成功",
                    "SUCCESS"
                )
                self.finished_signal.emit(True, f"{arm_cn} 电机 {motor_id} 控制成功")
            else:
                self.log_signal.emit(
                    f"✗ {arm_cn} 电机 {motor_id}: 控制命令发送失败 (state={state})",
                    "ERROR"
                )
                self.error_signal.emit(
                    arm_cn,
                    f"电机 {motor_id} 控制失败",
                    f"返回状态码: {state}"
                )
                self.finished_signal.emit(False, f"{arm_cn} 电机 {motor_id} 控制失败")

        except Exception as e:
            self.log_signal.emit(
                f"✗ {arm_cn} 电机 {motor_id}: 控制出错 - {str(e)}",
                "ERROR"
            )
            self.error_signal.emit(
                arm_cn,
                f"电机 {motor_id} 控制出错",
                str(e)
            )
            self.finished_signal.emit(False, str(e))
