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
@File    :   control_arms_grippers_action.py
@Time    :   2026/01/05 18:48:54
@Author  :   Wei Lindong 
@Version :   1.0
@Desc    :   机器人双臂和夹爪控制脚本
             通过ROS2 Action控制左右手臂关节轨迹和左右夹爪
'''


import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from control_msgs.action import FollowJointTrajectory, GripperCommand
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from builtin_interfaces.msg import Duration
import time


class ArmGripperController(Node):
    """双臂和夹爪控制器"""

    def __init__(self):
        super().__init__('arm_gripper_controller')

        # 创建Action客户端
        self.left_arm_client = ActionClient(
            self,
            FollowJointTrajectory,
            '/left_joint_trajectory_controller/follow_joint_trajectory'
        )

        self.right_arm_client = ActionClient(
            self,
            FollowJointTrajectory,
            '/right_joint_trajectory_controller/follow_joint_trajectory'
        )

        self.left_gripper_client = ActionClient(
            self,
            GripperCommand,
            '/left_gripper_controller/gripper_cmd'
        )

        self.right_gripper_client = ActionClient(
            self,
            GripperCommand,
            '/right_gripper_controller/gripper_cmd'
        )

        self.get_logger().info('等待Action服务器连接...')

        # 等待所有Action服务器
        self.left_arm_client.wait_for_server()
        self.get_logger().info('左臂Action服务器已连接')

        self.right_arm_client.wait_for_server()
        self.get_logger().info('右臂Action服务器已连接')

        self.left_gripper_client.wait_for_server()
        self.get_logger().info('左夹爪Action服务器已连接')

        self.right_gripper_client.wait_for_server()
        self.get_logger().info('右夹爪Action服务器已连接')

        self.get_logger().info('所有Action服务器已就绪！')

    def send_arm_goal(self, client, joint_names, positions, duration_sec=2.0):
        """
        发送手臂轨迹目标

        Args:
            client: Action客户端
            joint_names: 关节名称列表
            positions: 目标位置列表 (弧度)
            duration_sec: 运动时间 (秒)
        """
        goal_msg = FollowJointTrajectory.Goal()

        # 创建轨迹
        trajectory = JointTrajectory()
        trajectory.joint_names = joint_names

        # 创建轨迹点
        point = JointTrajectoryPoint()
        point.positions = positions
        point.time_from_start = Duration(sec=int(duration_sec), nanosec=int((duration_sec % 1) * 1e9))

        trajectory.points = [point]
        goal_msg.trajectory = trajectory

        # 发送目标
        self.get_logger().info(f'发送手臂轨迹: 关节={joint_names}, 位置={positions}')
        future = client.send_goal_async(goal_msg)

        return future

    def send_gripper_goal(self, client, position, max_effort=10.0):
        """
        发送夹爪控制目标

        Args:
            client: Action客户端
            position: 夹爪位置 (通常0.0为闭合, 0.8为张开)
            max_effort: 最大力度
        """
        goal_msg = GripperCommand.Goal()
        goal_msg.command.position = position
        goal_msg.command.max_effort = max_effort

        self.get_logger().info(f'发送夹爪命令: 位置={position}, 力度={max_effort}')
        future = client.send_goal_async(goal_msg)

        return future

    def control_left_arm(self, positions, duration=2.0):
        """控制左臂"""
        joint_names = [
            'openarmx_left_joint1',
            'openarmx_left_joint2',
            'openarmx_left_joint3',
            'openarmx_left_joint4',
            'openarmx_left_joint5',
            'openarmx_left_joint6',
            'openarmx_left_joint7'
        ]
        return self.send_arm_goal(self.left_arm_client, joint_names, positions, duration)

    def control_right_arm(self, positions, duration=2.0):
        """控制右臂"""
        joint_names = [
            'openarmx_right_joint1',
            'openarmx_right_joint2',
            'openarmx_right_joint3',
            'openarmx_right_joint4',
            'openarmx_right_joint5',
            'openarmx_right_joint6',
            'openarmx_right_joint7'
        ]
        return self.send_arm_goal(self.right_arm_client, joint_names, positions, duration)

    def control_left_gripper(self, position=0.0, effort=10.0):
        """控制左夹爪 (0.0=闭合, 0.8=张开)"""
        return self.send_gripper_goal(self.left_gripper_client, position, effort)

    def control_right_gripper(self, position=0.0, effort=10.0):
        """控制右夹爪 (0.0=闭合, 0.8=张开)"""
        return self.send_gripper_goal(self.right_gripper_client, position, effort)


def main():
    rclpy.init()
    controller = ArmGripperController()

    try:
        # # 示例1: 控制左臂移动到指定关节位置 (单位: 弧度)
        # print("\n=== 示例1: 控制左臂 ===")
        # left_positions = [-0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 7个关节
        # future = controller.control_left_arm(left_positions, duration=3.0)
        # rclpy.spin_until_future_complete(controller, future, timeout_sec=5.0)
        # if future.result() is not None:
        #     goal_handle = future.result()
        #     if goal_handle.accepted:
        #         controller.get_logger().info('左臂目标已接受')
        #         # 等待执行完成
        #         result_future = goal_handle.get_result_async()
        #         rclpy.spin_until_future_complete(controller, result_future, timeout_sec=5.0)
        #         controller.get_logger().info('左臂运动完成')

        # time.sleep(1)

        # # 示例2: 控制右臂移动
        # print("\n=== 示例2: 控制右臂 ===")
        # right_positions = [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # 7个关节
        # future = controller.control_right_arm(right_positions, duration=3.0)
        # rclpy.spin_until_future_complete(controller, future, timeout_sec=5.0)
        # if future.result() is not None:
        #     goal_handle = future.result()
        #     if goal_handle.accepted:
        #         controller.get_logger().info('右臂目标已接受')
        #         result_future = goal_handle.get_result_async()
        #         rclpy.spin_until_future_complete(controller, result_future, timeout_sec=5.0)
        #         controller.get_logger().info('右臂运动完成')

        # time.sleep(1)

        # 示例3: 打开左夹爪
        print("\n=== 示例3: 打开左夹爪 ===")
        future = controller.control_left_gripper(position=0.01, effort=10.0)
        rclpy.spin_until_future_complete(controller, future, timeout_sec=5.0)
        if future.result() is not None:
            goal_handle = future.result()
            if goal_handle.accepted:
                controller.get_logger().info('左夹爪目标已接受')
                result_future = goal_handle.get_result_async()
                rclpy.spin_until_future_complete(controller, result_future, timeout_sec=5.0)
                controller.get_logger().info('左夹爪打开完成')

        time.sleep(1)

        # # 示例4: 打开右夹爪
        print("\n=== 示例4: 打开右夹爪 ===")
        future = controller.control_right_gripper(position=0.01, effort=10.0)
        rclpy.spin_until_future_complete(controller, future, timeout_sec=5.0)
        if future.result() is not None:
            goal_handle = future.result()
            if goal_handle.accepted:
                controller.get_logger().info('右夹爪目标已接受')
                result_future = goal_handle.get_result_async()
                rclpy.spin_until_future_complete(controller, result_future, timeout_sec=5.0)
                controller.get_logger().info('右夹爪打开完成')

        # time.sleep(1)

        # # 示例5: 闭合两个夹爪
        # print("\n=== 示例5: 闭合夹爪 ===")
        # future_left = controller.control_left_gripper(position=0.0, effort=10.0)
        # future_right = controller.control_right_gripper(position=0.0, effort=10.0)

        # rclpy.spin_until_future_complete(controller, future_left, timeout_sec=5.0)
        # rclpy.spin_until_future_complete(controller, future_right, timeout_sec=5.0)

        controller.get_logger().info('所有示例动作完成！')

    except KeyboardInterrupt:
        controller.get_logger().info('用户中断')
    except Exception as e:
        controller.get_logger().error(f'发生错误: {e}')
    finally:
        controller.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
