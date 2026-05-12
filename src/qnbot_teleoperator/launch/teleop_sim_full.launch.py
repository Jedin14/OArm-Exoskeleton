#!/usr/bin/env python3
"""
Full Exoskeleton Teleoperation Stack (Simulation Mode)
======================================================
Launches all components in the correct order:

  1. openarm_bringup  — ros2_control (fake hardware) + RViz
  2. websocket_teleoperator — receives exo data on ws://localhost:19091
  3. exo_retargeting  — maps 16 exo joints → left/right arm JointState
  4. exoskeleton_bridge — routes retargeted joints → forward_position_controller

Usage:
  ros2 launch qnbot_teleoperator teleop_sim_full.launch.py

Optional arguments:
  use_fake_hardware:=false   Switch to real CAN hardware
  can_fd:=true               Enable CAN-FD (default false for compatibility)
  gripper_scaling_factor:=0.05
  websocket_port:=19091
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    TimerAction,
    LogInfo,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():

    # ── Launch arguments ────────────────────────────────────────────────────
    declared_args = [
        DeclareLaunchArgument(
            "use_fake_hardware",
            default_value="true",
            description="true = simulation, false = real CAN hardware",
        ),
        DeclareLaunchArgument(
            "can_fd",
            default_value="false",
            description="Enable CAN-FD mode (set true only for real hardware with FD support)",
        ),
        DeclareLaunchArgument(
            "arm_type",
            default_value="v10",
            description="OpenArm arm type",
        ),
        DeclareLaunchArgument(
            "gripper_scaling_factor",
            default_value="0.05",
            description="Exo trigger (0-1) → gripper metres. 0.05 ≈ 5 cm max open",
        ),
        DeclareLaunchArgument(
            "gripper_threshold",
            default_value="0.003",
            description="Min gripper position change before sending an action",
        ),
        DeclareLaunchArgument(
            "robot_type",
            default_value="OpenArm",
            description="Retargeting config name (loads retargeting_<robot_type>.yaml)",
        ),
    ]

    # ── 1. OpenArm ros2_control + RViz ──────────────────────────────────────
    openarm_bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare("openarm_bringup"),
                "launch",
                "openarm.bimanual.launch.py",
            ])
        ]),
        launch_arguments={
            "arm_type":              LaunchConfiguration("arm_type"),
            "robot_controller":      "forward_position_controller",
            "can_fd":                LaunchConfiguration("can_fd"),
            "use_fake_hardware":     LaunchConfiguration("use_fake_hardware"),
        }.items(),
    )

    # ── 2. WebSocket receiver (port 19091) ──────────────────────────────────
    # Give ros2_control 3 s to fully initialise before starting the data chain
    websocket_node = TimerAction(
        period=3.0,
        actions=[
            LogInfo(msg="[teleop_sim] Starting WebSocket receiver on ws://localhost:19091 ..."),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    PathJoinSubstitution([
                        FindPackageShare("qnbot_teleoperator"),
                        "launch",
                        "websocket_teleoperator.launch.py",
                    ])
                ]),
            ),
        ],
    )

    # ── 3. Exo retargeting (exo joints → arm JointState) ───────────────────
    retargeting_node = TimerAction(
        period=4.0,
        actions=[
            LogInfo(msg="[teleop_sim] Starting exo retargeting node ..."),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    PathJoinSubstitution([
                        FindPackageShare("qnbot_teleoperator"),
                        "launch",
                        "exo_retargeting.launch.py",
                    ])
                ]),
                launch_arguments={
                    "robot_type": LaunchConfiguration("robot_type"),
                }.items(),
            ),
        ],
    )

    # ── 4. Bridge (arm JointState → forward_position_controller) ───────────
    bridge_node = TimerAction(
        period=5.0,
        actions=[
            LogInfo(msg="[teleop_sim] Starting exoskeleton bridge node ..."),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    PathJoinSubstitution([
                        FindPackageShare("qnbot_teleoperator"),
                        "launch",
                        "exoskeleton_bridge.launch.py",
                    ])
                ]),
                launch_arguments={
                    "gripper_scaling_factor": LaunchConfiguration("gripper_scaling_factor"),
                    "gripper_threshold":      LaunchConfiguration("gripper_threshold"),
                }.items(),
            ),
        ],
    )

    return LaunchDescription(
        declared_args + [
            LogInfo(msg="========================================"),
            LogInfo(msg=" OpenArm Exo Teleoperation (Sim Mode)  "),
            LogInfo(msg="========================================"),
            LogInfo(msg="Step 1/4: Launching ros2_control + RViz"),
            openarm_bringup,
            websocket_node,
            retargeting_node,
            bridge_node,
            LogInfo(msg="All components scheduled. HMI: add ws://localhost:19091"),
        ]
    )
