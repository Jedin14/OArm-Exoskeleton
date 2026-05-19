import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    # 声明启动参数
    gripper_threshold_arg = DeclareLaunchArgument(
        'gripper_threshold',
        default_value='0.005',
        description='夹爪位置变化的最小阈值 (m)，用于减少Action请求频率'
    )
    
    gripper_scaling_factor_arg = DeclareLaunchArgument(
        'gripper_scaling_factor',
        default_value='0.02',
        description='夹爪数值缩放因子：外骨骼归一化值(0-1) -> 机械臂物理值(米)。默认0.02表示外骨骼1.0对应机械臂2cm'
    )

    left_joint_multipliers_arg = DeclareLaunchArgument(
        'left_joint_multipliers',
        default_value='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]',
        description='左臂7个关节倍率（列表字符串），用于单侧关节方向修正'
    )

    right_joint_multipliers_arg = DeclareLaunchArgument(
        'right_joint_multipliers',
        default_value='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]',
        description='右臂7个关节倍率（列表字符串），用于单侧关节方向修正'
    )

    left_gripper_reverse_arg = DeclareLaunchArgument(
        'left_gripper_reverse',
        default_value='false',
        description='是否反向左夹爪归一化输入（x -> 1-x）'
    )

    right_gripper_reverse_arg = DeclareLaunchArgument(
        'right_gripper_reverse',
        default_value='false',
        description='是否反向右夹爪归一化输入（x -> 1-x）'
    )

    # 桥接节点
    bridge_node = Node(
        package='qnbot_teleoperator',
        executable='exoskeleton_bridge_node',
        name='exoskeleton_bridge_node',
        output='screen',
        parameters=[{
            'gripper_threshold': LaunchConfiguration('gripper_threshold'),
            'gripper_scaling_factor': LaunchConfiguration('gripper_scaling_factor'),
            'left_joint_multipliers': LaunchConfiguration('left_joint_multipliers'),
            'right_joint_multipliers': LaunchConfiguration('right_joint_multipliers'),
            'left_gripper_reverse': LaunchConfiguration('left_gripper_reverse'),
            'right_gripper_reverse': LaunchConfiguration('right_gripper_reverse')
        }]
    )

    return LaunchDescription([
        gripper_threshold_arg,
        gripper_scaling_factor_arg,
        left_joint_multipliers_arg,
        right_joint_multipliers_arg,
        left_gripper_reverse_arg,
        right_gripper_reverse_arg,
        bridge_node
    ])

