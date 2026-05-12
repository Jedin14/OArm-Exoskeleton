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
        default_value='0.003',
        description='夹爪位置变化的最小阈值 (m)'
    )
    
    gripper_scaling_factor_arg = DeclareLaunchArgument(
        'gripper_scaling_factor',
        default_value='0.02',
        description='夹爪数值缩放因子'
    )

    blend_time_arg = DeclareLaunchArgument(
        'blend_time',
        default_value='3.0',
        description='启动插值时间(秒)'
    )

    smooth_alpha_arg = DeclareLaunchArgument(
        'smooth_alpha',
        default_value='0.15',
        description='EMA平滑系数'
    )

    # 桥接节点
    bridge_node = Node(
        package='qnbot_teleoperator',
        executable='exoskeleton_bridge_node',
        name='exoskeleton_bridge_node',
        output='screen',
        parameters=[{
            'gripper_threshold':      LaunchConfiguration('gripper_threshold'),
            'gripper_scaling_factor': LaunchConfiguration('gripper_scaling_factor'),
            'blend_time':             LaunchConfiguration('blend_time'),
            'smooth_alpha':           LaunchConfiguration('smooth_alpha'),
        }]
    )

    return LaunchDescription([
        gripper_threshold_arg,
        gripper_scaling_factor_arg,
        blend_time_arg,
        smooth_alpha_arg,
        bridge_node
    ])
