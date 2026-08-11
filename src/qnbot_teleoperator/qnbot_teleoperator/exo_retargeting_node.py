#!/usr/bin/env python3
"""
外骨骼数据重定向节点（Exo Retargeting Node）
从 WebSocket 外骨骼数据进行重定向映射到目标机器人关节

功能特点：
1. 订阅 /exo/joint_command 话题（16个关节数据）
2. 通过配置化方式映射到目标机器人的关节
3. 支持多种机器人适配
4. 进程内优化，减少内存复制
5. 实时retargeting变换

数据流：
/exo/joint_command (16关节) -> retargeting -> /target_robot/joint_command (7+7关节)

配置化设计：
- 源关节名称映射
- 目标关节名称映射  
- retargeting参数配置
- 发布话题配置
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
import yaml
import os
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup

from sensor_msgs.msg import JointState
from std_msgs.msg import Header


def _soft_limit(x: float, lower: float, upper: float, knee: float = 0.85) -> float:
    """
    平滑限位：在 knee 以内保持线性，超过后用 tanh 压缩，渐近但永不到达限位。

    Why this exists: np.clip writes the exact limit value into the recorded
    action whenever the operator over-reaches. Repeated across an episode that
    creates a large point mass at the rail — a confident, scene-independent
    label that teaches a policy "this joint is often exactly at the limit".
    On OpenArm v10, joint6 (+/-45deg, the narrowest joint) saturated in 15% of
    recorded episodes this way.

    Soft limiting keeps the joint inside its mechanical range while mapping the
    over-reach onto a continuum, so the action distribution stays smooth and no
    frame has to be masked out of training.

    Linear below `knee` (fraction of the half-range) and C1-continuous at the
    knee, since d/du[tanh(u/(1-k))*(1-k)] = 1 at u = 0.
    """
    half = 0.5 * (upper - lower)
    if half <= 0.0:
        return float(np.clip(x, lower, upper))
    center = 0.5 * (lower + upper)
    u = (x - center) / half                      # +/-1 at the limits
    k = min(max(knee, 0.0), 0.999)
    if abs(u) > k:
        sign = math.copysign(1.0, u)
        over = abs(u) - k
        # The (1 - 1e-6) factor matters: for a large enough over-reach
        # tanh() reaches exactly 1.0 in float64, which would put the value
        # back precisely on the limit — reintroducing the point mass this
        # function exists to remove. Asymptote just inside instead.
        u = sign * (k + (1.0 - k) * (1.0 - 1e-6) * math.tanh(over / (1.0 - k)))
    return float(center + u * half)


_JOINT_LABELS = ['joint1', 'joint2', 'joint3', 'joint4', 'joint5', 'joint6', 'joint7']


class ExoRetargetingNode(Node):
    """
    外骨骼重定向节点
    
    功能：
    - 订阅外骨骼关节数据 /exo/joint_command
    - 通过retargeting算法映射到目标机器人关节
    - 发布到目标机器人控制话题
    
    配置化设计：
    - 支持多种目标机器人
    - 关节名称映射配置
    - retargeting参数配置
    """
    
    def __init__(self):
        super().__init__('exo_retargeting_node')
        
        # 参数声明 - 只保留最基本的参数
        self.declare_parameter('robot_type', 'OpenArm')
        self.declare_parameter('enable_left_arm_retargeting', True)
        self.declare_parameter('enable_right_arm_retargeting', True)
        
        # 获取参数
        self.robot_type = self.get_parameter('robot_type').value
        self.enable_left_arm_retargeting = self.get_parameter('enable_left_arm_retargeting').value
        self.enable_right_arm_retargeting = self.get_parameter('enable_right_arm_retargeting').value

        # 限位饱和统计（每个 arm/joint 累计次数与最大超出量）
        # Saturation bookkeeping: {(arm, joint_idx): [count, max_overshoot]}.
        # Without this the operator has no way to know a joint is railing —
        # clipping is silent and only shows up later as corrupted training data.
        self._sat_stats: Dict[Tuple[str, int], List[float]] = {}
        self._sat_last_warn = 0.0
        
        # 根据机器人类型自动构造配置文件路径
        self._setup_config_file_path()
        
        # 加载配置 - 话题配置也在这里获取
        self.load_retargeting_config()
        
        # 在配置加载完成后创建ROS通信
        self._setup_ros_communication()
        
        # 状态变量
        self.last_exo_data = None
        self.retargeting_stats = {
            'total_received': 0,
            'total_published_left': 0,
            'total_published_right': 0,
            'retargeting_errors': 0,
            'last_process_time': 0.0,
        }
        # 记录上一次肩关节(pitch, roll)解析解，用于连续性分支选择，消除小扰动下的大跳变
        self._last_shoulder_solution = {
            'left_arm': None,
            'right_arm': None,
        }
        
        self.get_logger().info(
            f'外骨骼重定向节点已启动'
            f'\n  目标机器人: {self.robot_info["name"]} ({self.robot_info["description"]})'
            f'\n  配置文件: {self.config_file_path}'
            f'\n  外骨骼数据话题: {self.exo_joint_topic}'
            f'\n  左臂目标话题: {self.target_left_arm_topic} ({"启用" if self.enable_left_arm_retargeting else "禁用"})'
            f'\n  右臂目标话题: {self.target_right_arm_topic} ({"启用" if self.enable_right_arm_retargeting else "禁用"})'
            f'\n  📡 等待外骨骼数据...'
        )
    
    def _setup_config_file_path(self):
        """根据机器人类型自动设置配置文件路径"""
        from ament_index_python.packages import get_package_share_directory
        
        try:
            # 获取包路径
            package_dir = get_package_share_directory('qnbot_teleoperator')
            config_dir = os.path.join(package_dir, 'config')
            
            # 构造配置文件名：retargeting_{robot_type}.yaml
            config_filename = f'retargeting_{self.robot_type}.yaml'
            self.config_file_path = os.path.join(config_dir, config_filename)
            
            self.get_logger().info(f'🔧 自动设置配置文件路径: {self.config_file_path}')
            
        except Exception as e:
            # 如果获取包路径失败，尝试相对路径
            current_dir = os.path.dirname(os.path.dirname(__file__))
            config_dir = os.path.join(current_dir, 'config')
            config_filename = f'retargeting_{self.robot_type}.yaml'
            self.config_file_path = os.path.join(config_dir, config_filename)
            
            self.get_logger().warn(f'⚠️ 使用相对路径配置文件: {self.config_file_path}')
    
    def load_retargeting_config(self):
        """加载重定向配置 - 完全从YAML配置文件加载"""
        if not os.path.exists(self.config_file_path):
            raise FileNotFoundError(f"配置文件不存在: {self.config_file_path}")
        
        try:
            # 加载YAML配置文件
            with open(self.config_file_path, 'r', encoding='utf-8') as f:
                self.current_robot_config = yaml.safe_load(f)
                
            if not self.current_robot_config:
                raise ValueError("配置文件为空或格式错误")
                
            self.get_logger().info(f'✅ 已从YAML文件加载配置: {self.config_file_path}')
            
            # 验证配置完整性
            self._validate_robot_config()
            
            # 加载机器人信息
            self._load_robot_info()
            
            # 从YAML配置中获取话题配置
            self._load_topic_config()
            
            self.get_logger().info(f'✅ 成功加载 {self.robot_info["name"]} 机器人配置')
            
        except Exception as e:
            self.get_logger().error(f'加载配置文件失败: {e}')
            raise
    
    def _validate_robot_config(self):
        """验证机器人配置的完整性"""
        required_keys = ['robot_info', 'topics', 'exo_joint_mapping', 'target_joint_mapping', 'retargeting_params']
        
        for key in required_keys:
            if key not in self.current_robot_config:
                raise ValueError(f'配置文件缺少必需的配置项: {key}')
        
        # 验证机器人信息
        robot_info = self.current_robot_config['robot_info']
        required_info = ['name', 'description']
        for info in required_info:
            if info not in robot_info:
                raise ValueError(f'robot_info 缺少必需信息: {info}')
        
        # 验证话题配置
        topics = self.current_robot_config['topics']
        required_topics = ['exo_joint_topic', 'target_left_arm_topic', 'target_right_arm_topic']
        for topic in required_topics:
            if topic not in topics:
                raise ValueError(f'topics 配置缺少必需的话题: {topic}')
        
        # 验证外骨骼关节映射
        exo_mapping = self.current_robot_config['exo_joint_mapping']
        if 'left_arm' not in exo_mapping or 'right_arm' not in exo_mapping:
            raise ValueError('exo_joint_mapping 必须包含 left_arm 和 right_arm 配置')
        
        # 验证目标机器人关节映射
        target_mapping = self.current_robot_config['target_joint_mapping']
        if 'left_arm' not in target_mapping or 'right_arm' not in target_mapping:
            raise ValueError('target_joint_mapping 必须包含 left_arm 和 right_arm 配置')
        
        # 验证retargeting参数
        params = self.current_robot_config['retargeting_params']
        required_params = ['scaling_factors', 'offset_angles', 'joint_limits']
        for param in required_params:
            if param not in params:
                raise ValueError(f'retargeting_params 缺少必需参数: {param}')

        # 允许 offset_angles 为长度为7的列表或包含左右臂的字典
        offset_cfg = params['offset_angles']
        if isinstance(offset_cfg, dict):
            for side in ['left_arm', 'right_arm']:
                if side not in offset_cfg:
                    raise ValueError('retargeting_params.offset_angles 需要包含 left_arm 和 right_arm')
                if not isinstance(offset_cfg[side], list) or len(offset_cfg[side]) != 7:
                    raise ValueError('retargeting_params.offset_angles 每个手臂需要7个偏移角')
        elif not isinstance(offset_cfg, list) or len(offset_cfg) != 7:
            raise ValueError('retargeting_params.offset_angles 需要为长度为7的列表，或包含 left_arm/right_arm 的字典')

        # 允许 scaling_factors 为长度为7的列表或包含左右臂的字典
        scaling_cfg = params['scaling_factors']
        if isinstance(scaling_cfg, dict):
            for side in ['left_arm', 'right_arm']:
                if side not in scaling_cfg:
                    raise ValueError('retargeting_params.scaling_factors 需要包含 left_arm 和 right_arm')
                if not isinstance(scaling_cfg[side], list) or len(scaling_cfg[side]) != 7:
                    raise ValueError('retargeting_params.scaling_factors 每个手臂需要7个缩放因子')
        elif not isinstance(scaling_cfg, list) or len(scaling_cfg) != 7:
            raise ValueError('retargeting_params.scaling_factors 需要为长度为7的列表，或包含 left_arm/right_arm 的字典')

        # 独立左右臂限位
        joint_limits_cfg = params['joint_limits']
        if not isinstance(joint_limits_cfg, dict):
            raise ValueError('retargeting_params.joint_limits 必须为字典格式')
        for side in ['left_arm', 'right_arm']:
            if side not in joint_limits_cfg:
                raise ValueError(f'retargeting_params.joint_limits 必须包含 {side}')
            arm_limits = joint_limits_cfg[side]
            if 'lower' not in arm_limits or 'upper' not in arm_limits:
                raise ValueError(f'retargeting_params.joint_limits.{side} 需要包含 lower 和 upper')
            if not isinstance(arm_limits['lower'], list) or len(arm_limits['lower']) != 7:
                raise ValueError(f'retargeting_params.joint_limits.{side}.lower 需要为长度为7的列表')
            if not isinstance(arm_limits['upper'], list) or len(arm_limits['upper']) != 7:
                raise ValueError(f'retargeting_params.joint_limits.{side}.upper 需要为长度为7的列表')

        # 可选：输入关节补偿（用于外骨骼佩戴姿态偏置修正）
        input_offsets_cfg = params.get('input_joint_offsets')
        if input_offsets_cfg is not None:
            if not isinstance(input_offsets_cfg, dict):
                raise ValueError('retargeting_params.input_joint_offsets 必须为字典格式')
            for side in ['left_arm', 'right_arm']:
                if side not in input_offsets_cfg:
                    raise ValueError(f'retargeting_params.input_joint_offsets 必须包含 {side}')
                if not isinstance(input_offsets_cfg[side], list) or len(input_offsets_cfg[side]) != 7:
                    raise ValueError(f'retargeting_params.input_joint_offsets.{side} 需要为长度为7的列表')

        # 可选：腕部joint6/joint7二维轴同步补偿（90度旋转）
        # 允许值：none / cw / ccw
        wrist_sync_cfg = params.get('wrist_axis_sync_6_7')
        if wrist_sync_cfg is not None:
            if not isinstance(wrist_sync_cfg, dict):
                raise ValueError('retargeting_params.wrist_axis_sync_6_7 必须为字典格式')
            allowed = {'none', 'cw', 'ccw'}
            for side in ['left_arm', 'right_arm']:
                mode = wrist_sync_cfg.get(side, 'none')
                if mode not in allowed:
                    raise ValueError(
                        f'retargeting_params.wrist_axis_sync_6_7.{side} 仅支持 {sorted(list(allowed))}'
                    )
        
        self.get_logger().info('✅ 配置文件验证通过')
    
    def _load_robot_info(self):
        """加载机器人基本信息"""
        self.robot_info = self.current_robot_config['robot_info']
        
        self.get_logger().info(f'🤖 机器人信息:')
        self.get_logger().info(f'   名称: {self.robot_info["name"]}')
        self.get_logger().info(f'   描述: {self.robot_info["description"]}')
        if 'manufacturer' in self.robot_info:
            self.get_logger().info(f'   制造商: {self.robot_info["manufacturer"]}')
        if 'version' in self.robot_info:
            self.get_logger().info(f'   版本: {self.robot_info["version"]}')
    
    def _load_topic_config(self):
        """从YAML配置加载话题配置"""
        topics_config = self.current_robot_config['topics']
        
        # 从YAML配置获取话题名称
        self.exo_joint_topic = topics_config['exo_joint_topic']
        self.target_left_arm_topic = topics_config['target_left_arm_topic']
        self.target_right_arm_topic = topics_config['target_right_arm_topic']
        
        # 可选话题
        self.status_topic = topics_config.get('status_topic', '/exo_retargeting/status')
        
        self.get_logger().info(f'📡 话题配置加载完成:')
        self.get_logger().info(f'   订阅: {self.exo_joint_topic}')
        self.get_logger().info(f'   发布左臂: {self.target_left_arm_topic}')
        self.get_logger().info(f'   发布右臂: {self.target_right_arm_topic}')
        self.get_logger().info(f'   状态话题: {self.status_topic}')
    
    def _setup_ros_communication(self):
        """创建ROS订阅器和发布器"""
        # 创建回调组（进程内优化）
        self.callback_group = ReentrantCallbackGroup()
        
        # 创建订阅器（进程内优化，减少内存复制）
        self.exo_joint_sub = self.create_subscription(
            JointState,
            self.exo_joint_topic,
            self.exo_joint_callback,
            10,
            callback_group=self.callback_group
        )
        
        # 创建发布器
        if self.enable_left_arm_retargeting:
            self.left_arm_pub = self.create_publisher(
                JointState,
                self.target_left_arm_topic,
                10
            )
        
        if self.enable_right_arm_retargeting:
            self.right_arm_pub = self.create_publisher(
                JointState,
                self.target_right_arm_topic,
                10
            )
        
        # 可选：创建状态发布器
        self.status_pub = self.create_publisher(
            JointState,  # 可以后续改为自定义消息
            self.status_topic,
            10
        )
        
        self.get_logger().info('✅ ROS通信设置完成')

    def _map_trigger_to_gripper(self, trigger_raw: float) -> float:
        """
        将外骨骼扳机原始值映射为夹爪归一化开合量 [0, 1]
        0.0 = 完全夹紧, 1.0 = 完全松开
        """
        trig_cfg = self.current_robot_config.get('exo_joint_mapping', {}).get('triggers', {})

        trigger_open_value = float(trig_cfg.get('open_value', 0.067))
        trigger_close_value = float(trig_cfg.get('close_value', 0.0))
        trigger_close_deadzone = float(trig_cfg.get('close_deadzone', 0.05))
        trigger_open_deadzone = float(trig_cfg.get('open_deadzone', 0.0))

        # 防止配置错误导致除零
        span = max(1e-6, trigger_open_value - trigger_close_value)
        normalized = (float(trigger_raw) - trigger_close_value) / span
        normalized = max(0.0, min(1.0, normalized))

        # 扳机端点吸附，确保“完全夹紧/完全松开”能到达端点
        if normalized <= trigger_close_deadzone:
            return 0.0
        if normalized >= (1.0 - trigger_open_deadzone):
            return 1.0
        return normalized
    
    def exo_joint_callback(self, msg: JointState):
        """处理外骨骼关节数据回调（进程内优化版本）"""
        try:
            # 更新统计信息
            self.retargeting_stats['total_received'] += 1
            
            # 存储最新数据（避免内存复制，直接引用）
            self.last_exo_data = msg
            
            # 立即进行retargeting处理
            self.process_retargeting(msg)
            
            # 定期输出统计信息（降低频率）
            if self.retargeting_stats['total_received'] % 2000 == 0:
                self.get_logger().info(
                    f'重定向统计 - 接收: {self.retargeting_stats["total_received"]}, '
                    f'左臂发布: {self.retargeting_stats["total_published_left"]}, '
                    f'右臂发布: {self.retargeting_stats["total_published_right"]}, '
                    f'错误: {self.retargeting_stats["retargeting_errors"]}'
                )
        
        except Exception as e:
            self.retargeting_stats['retargeting_errors'] += 1
            self.get_logger().error(f'处理外骨骼数据失败: {e}')
    
    def process_retargeting(self, exo_msg: JointState):
        """处理retargeting变换"""
        try:
            start_time = self.get_clock().now().nanoseconds
            
            # 提取外骨骼关节位置数据并处理不同长度的情况
            exo_positions = list(exo_msg.position)
            if len(exo_positions) == 14:
                # 如果是14个关节，补全后面两个为0.0
                exo_positions.extend([0.0, 0.0])
                if self.retargeting_stats['total_received'] % 1000 == 1:  # 偶尔提示一次，避免日志过多
                    self.get_logger().info('收到14个关节数据，已自动补全为16个（后两个设为0.0）')
            elif len(exo_positions) == 16:
                # 16个关节数据，直接使用
                pass
            else:
                # 除了14和16之外的其他长度都是错误
                self.get_logger().warn(f'外骨骼关节数量错误，期望14或16个关节，收到{len(exo_positions)}个，跳过处理')
                return
            
            # 进行左臂retargeting
            if self.enable_left_arm_retargeting:
                left_arm_result = self.retarget_left_arm(exo_positions)
                if left_arm_result is not None:
                    left_arm_joints, left_gripper_position = left_arm_result
                    self.publish_left_arm_command(left_arm_joints, left_gripper_position, exo_msg.header.stamp)
                    self.retargeting_stats['total_published_left'] += 1
            
            # 进行右臂retargeting
            if self.enable_right_arm_retargeting:
                right_arm_result = self.retarget_right_arm(exo_positions)
                if right_arm_result is not None:
                    right_arm_joints, right_gripper_position = right_arm_result
                    self.publish_right_arm_command(right_arm_joints, right_gripper_position, exo_msg.header.stamp)
                    self.retargeting_stats['total_published_right'] += 1
            
            # 更新处理时间统计
            end_time = self.get_clock().now().nanoseconds
            self.retargeting_stats['last_process_time'] = (end_time - start_time) / 1e6  # ms
            
        except Exception as e:
            self.retargeting_stats['retargeting_errors'] += 1
            self.get_logger().error(f'Retargeting处理失败: {e}')
    
    def retarget_left_arm(self, exo_positions: List[float]) -> Optional[Tuple[List[float], float]]:
        """
        左臂数据提取层 - 负责从16个外骨骼关节中提取左臂7个关节数据和夹爪数据
        
        Args:
            exo_positions: 外骨骼16个关节位置
            
        Returns:
            (变换后的7个左臂关节位置, 夹爪开合程度)，失败返回None
        """
        try:
            # 获取配置的关节索引映射
            left_arm_config = self.current_robot_config['exo_joint_mapping']['left_arm']
            left_arm_indices = left_arm_config['indices']
            
            # 提取左臂7个关节数据
            exo_left_arm = [exo_positions[i] for i in left_arm_indices]
            
            # 提取左臂夹爪数据（从triggers中获取）
            triggers_config = self.current_robot_config['exo_joint_mapping']['triggers']
            left_trigger_index = triggers_config['indices'][0]  # 左扳机索引14
            left_gripper_raw = exo_positions[left_trigger_index] if left_trigger_index < len(exo_positions) else 0.0
            
            # 将扳机数据转换为夹爪开合程度（0.0=夹紧, 1.0=松开）
            left_gripper_position = self._map_trigger_to_gripper(left_gripper_raw)
            
            # 每1000次处理输出一次夹爪调试信息
            if self.retargeting_stats['total_received'] % 1000 == 1:
                self.get_logger().info(
                    f'左臂夹爪数据转换: 原始扳机值={left_gripper_raw:.4f} -> 夹爪位置={left_gripper_position:.3f}'
                )
            
            # 调用算法层进行retargeting变换
            retargeted_joints = self.apply_retargeting_transform(exo_left_arm, 'left_arm')
            
            return retargeted_joints, left_gripper_position
            
        except Exception as e:
            self.get_logger().error(f'左臂数据提取失败: {e}')
            return None
    
    def retarget_right_arm(self, exo_positions: List[float]) -> Optional[Tuple[List[float], float]]:
        """
        右臂数据提取层 - 负责从16个外骨骼关节中提取右臂7个关节数据和夹爪数据
        
        Args:
            exo_positions: 外骨骼16个关节位置
            
        Returns:
            (变换后的7个右臂关节位置, 夹爪开合程度)，失败返回None
        """
        try:
            # 获取配置的关节索引映射
            right_arm_config = self.current_robot_config['exo_joint_mapping']['right_arm']
            right_arm_indices = right_arm_config['indices']
            
            # 提取右臂7个关节数据
            exo_right_arm = [exo_positions[i] for i in right_arm_indices]
            
            # 提取右臂夹爪数据（从triggers中获取）
            triggers_config = self.current_robot_config['exo_joint_mapping']['triggers']
            right_trigger_index = triggers_config['indices'][1]  # 右扳机索引15
            right_gripper_raw = exo_positions[right_trigger_index] if right_trigger_index < len(exo_positions) else 0.0
            
            # 将扳机数据转换为夹爪开合程度（0.0=夹紧, 1.0=松开）
            right_gripper_position = self._map_trigger_to_gripper(right_gripper_raw)
            
            # 每1000次处理输出一次夹爪调试信息
            if self.retargeting_stats['total_received'] % 1000 == 1:
                self.get_logger().info(
                    f'右臂夹爪数据转换: 原始扳机值={right_gripper_raw:.4f} -> 夹爪位置={right_gripper_position:.3f}'
                )
            
            # 调用算法层进行retargeting变换
            retargeted_joints = self.apply_retargeting_transform(exo_right_arm, 'right_arm')
            
            return retargeted_joints, right_gripper_position
            
        except Exception as e:
            self.get_logger().error(f'右臂数据提取失败: {e}')
            return None
    
    def apply_retargeting_transform(self, joint_angles: List[float], arm_side: str) -> List[float]:
        """
        核心retargeting算法层 - 实现外骨骼到目标机器人的关节映射变换
        
        OpenArm处理：
        - 所有关节统一使用缩放和偏移映射
        
        支持新的左右臂分离配置格式：
        - scaling_factors.left_arm/right_arm: 左右臂专用缩放因子
        - offset_angles.left_arm/right_arm: 左右臂专用偏移角度
        - joint_limits.left_arm/right_arm: 左右臂专用关节限位
        - 向后兼容旧的统一配置格式
        
        Args:
            joint_angles: 输入关节角度（7个）
            arm_side: 'left_arm' 或 'right_arm'
            
        Returns:
            变换后的关节角度（7个）
        """
        # 复制输入角度以避免修改原始数据
        retargeted = list(joint_angles)
        
        # 所有关节统一处理
        start_idx = 0
        
        # 应用传统的retargeting逻辑
        params = self.current_robot_config['retargeting_params']
        
        # 获取缩放因子 - 支持左右臂分离配置
        scaling_cfg = params['scaling_factors']
        if isinstance(scaling_cfg, dict):
            scaling_factors = scaling_cfg.get(arm_side, [1.0] * 7)
        else:
            scaling_factors = scaling_cfg
            
        # 获取偏移角度 - 支持左右臂分离配置
        offset_cfg = params['offset_angles']
        if isinstance(offset_cfg, dict):
            offset_angles = offset_cfg.get(arm_side, [0.0] * 7)
        else:
            offset_angles = offset_cfg

        # 获取输入关节补偿（默认全0）：用于在进入现有映射前修正外骨骼佩戴姿态
        input_offsets_cfg = params.get('input_joint_offsets', {})
        if isinstance(input_offsets_cfg, dict):
            input_offsets = input_offsets_cfg.get(arm_side, [0.0] * 7)
        else:
            input_offsets = [0.0] * 7
            
        joint_limits = params['joint_limits']
        
        # 先完成标量变换（缩放、偏移）
        transformed = [0.0] * 7
        for i in range(start_idx, 7):
            compensated_input = retargeted[i] + input_offsets[i]
            transformed[i] = compensated_input * scaling_factors[i] + offset_angles[i]

        # 可选：腕部joint6/joint7坐标系同步（二维向量旋转90度）
        wrist_sync_cfg = params.get('wrist_axis_sync_6_7', {})
        wrist_mode = wrist_sync_cfg.get(arm_side, 'none') if isinstance(wrist_sync_cfg, dict) else 'none'
        if wrist_mode in ('cw', 'ccw'):
            j6 = transformed[5]
            j7 = transformed[6]
            if wrist_mode == 'cw':
                # [j6, j7] -> [j7, -j6]
                transformed[5] = j7
                transformed[6] = -j6
            else:
                # [j6, j7] -> [-j7, j6]
                transformed[5] = -j7
                transformed[6] = j6

        # 输出增益（可选）：在限位之前缩小行程，用于把操作者的活动范围压进
        # 关节硬限位内。主要用于腕部 joint6（±45°，是机械限位最窄的关节）。
        # Optional per-joint output gain, applied about the limit mid-point so
        # it also behaves sensibly on asymmetric joints. Default 1.0 = no-op.
        gain_cfg = params.get('output_gain', {})
        gains = gain_cfg.get(arm_side, [1.0] * 7) if isinstance(gain_cfg, dict) else [1.0] * 7

        # 限位模式：hard = 直接截断（旧行为）；soft = 平滑压缩，永不贴死限位
        limit_mode = str(params.get('limit_mode', 'hard')).lower()
        soft_knee = float(params.get('soft_limit_knee', 0.85))

        # 应用关节限位 - 支持新的左右臂分离结构
        if arm_side in joint_limits:
            arm_limits = joint_limits[arm_side]
            for i in range(start_idx, 7):
                lower_limit = arm_limits['lower'][i]
                upper_limit = arm_limits['upper'][i]

                value = transformed[i]
                gain = float(gains[i]) if i < len(gains) else 1.0
                if gain != 1.0:
                    center = 0.5 * (lower_limit + upper_limit)
                    value = center + (value - center) * gain

                # 记录“原始指令是否已超出硬限位”，用于录制期告警。
                # Record whether the *pre-limit* command exceeded the hard
                # limit. This is the signal that would otherwise be invisible:
                # np.clip silently writes the limit value into the dataset.
                overshoot = 0.0
                if value < lower_limit:
                    overshoot = lower_limit - value
                elif value > upper_limit:
                    overshoot = value - upper_limit
                if overshoot > 0.0:
                    self._note_saturation(arm_side, i, overshoot)

                if limit_mode == 'soft':
                    retargeted[i] = _soft_limit(value, lower_limit, upper_limit, soft_knee)
                else:
                    retargeted[i] = float(np.clip(value, lower_limit, upper_limit))
        else:
            raise RuntimeError("joint_limits 配置格式错误：未找到对应的左右臂分离结构，请检查配置文件。")

        return retargeted
    
    def _note_saturation(self, arm_side: str, joint_idx: int, overshoot: float) -> None:
        """
        记录一次限位饱和，并按节流频率告警。

        Recording a saturated joint is the whole point: np.clip would otherwise
        write the limit value into the dataset with no trace, and the damage
        only surfaces much later as a policy that cannot predict that joint.
        Warnings are throttled to once every 2 s so a sustained over-reach does
        not flood the log.
        """
        key = (arm_side, joint_idx)
        stat = self._sat_stats.setdefault(key, [0.0, 0.0])
        stat[0] += 1
        stat[1] = max(stat[1], overshoot)

        now = time.monotonic()
        if now - self._sat_last_warn < 2.0:
            return
        self._sat_last_warn = now

        label = _JOINT_LABELS[joint_idx] if joint_idx < len(_JOINT_LABELS) else f'idx{joint_idx}'
        self.get_logger().warn(
            f'⚠️  限位饱和 SATURATED: {arm_side}/{label} '
            f'超出 {math.degrees(overshoot):.1f}° beyond its limit '
            f'({int(stat[0])} samples so far). '
            f'Recorded data for this joint is being clipped — adjust your pose, '
            f'or set retargeting_params.output_gain / limit_mode: soft.'
        )

    def publish_left_arm_command(self, joint_positions: List[float], gripper_position: float, timestamp):
        """发布左臂关节命令"""
        try:
            # 创建关节命令消息
            joint_cmd = JointState()
            
            # 设置消息头
            joint_cmd.header = Header()
            joint_cmd.header.stamp = timestamp
            joint_cmd.header.frame_id = ''
            
            # 设置关节数据（创建新的列表，避免修改配置文件中的原始列表）
            target_config = self.current_robot_config['target_joint_mapping']['left_arm']
            joint_cmd.name = list(target_config['joint_names'])  # 创建副本
            joint_cmd.position = list(joint_positions)  # 创建副本
            joint_cmd.velocity = [0.0] * len(joint_positions)
            joint_cmd.effort = [0.0] * len(joint_positions)
            
            # 添加夹爪开合程度字段（从外骨骼扳机数据获取）
            # 将夹爪关节添加到关节列表中
            joint_cmd.name.append('left_gripper_joint')
            joint_cmd.position.append(gripper_position)
            joint_cmd.velocity.append(0.0)
            joint_cmd.effort.append(0.0)
            
            # 发布命令
            self.left_arm_pub.publish(joint_cmd)
            
        except Exception as e:
            self.get_logger().error(f'发布左臂命令失败: {e}')
    
    def publish_right_arm_command(self, joint_positions: List[float], gripper_position: float, timestamp):
        """发布右臂关节命令"""
        try:
            # 创建关节命令消息
            joint_cmd = JointState()
            
            # 设置消息头
            joint_cmd.header = Header()
            joint_cmd.header.stamp = timestamp
            joint_cmd.header.frame_id = ''
            
            # 设置关节数据（创建新的列表，避免修改配置文件中的原始列表）
            target_config = self.current_robot_config['target_joint_mapping']['right_arm']
            joint_cmd.name = list(target_config['joint_names'])  # 创建副本
            joint_cmd.position = list(joint_positions)  # 创建副本
            joint_cmd.velocity = [0.0] * len(joint_positions)
            joint_cmd.effort = [0.0] * len(joint_positions)
            
            # 添加夹爪开合程度字段（从外骨骼扳机数据获取）
            # 将夹爪关节添加到关节列表中
            joint_cmd.name.append('right_gripper_joint')
            joint_cmd.position.append(gripper_position)
            joint_cmd.velocity.append(0.0)
            joint_cmd.effort.append(0.0)
            
            # 发布命令
            self.right_arm_pub.publish(joint_cmd)
            
        except Exception as e:
            self.get_logger().error(f'发布右臂命令失败: {e}')
    
    def get_retargeting_statistics(self) -> Dict:
        """获取retargeting统计信息"""
        return {
            **self.retargeting_stats,
            'current_robot_config': self.robot_info["name"],
            'left_arm_enabled': self.enable_left_arm_retargeting,
            'right_arm_enabled': self.enable_right_arm_retargeting,
        }
    
    def destroy_node(self):
        """清理节点资源"""
        self.get_logger().info('正在关闭外骨骼重定向节点...')
        super().destroy_node()


def main(args=None):
    """主函数"""
    rclpy.init(args=args)
    
    try:
        # 创建节点
        node = ExoRetargetingNode()
        
        # 使用多线程执行器
        executor = MultiThreadedExecutor()
        executor.add_node(node)
        
        try:
            # 运行节点
            executor.spin()
        except KeyboardInterrupt:
            node.get_logger().info('收到中断信号，正在关闭...')
        finally:
            # 清理资源
            node.destroy_node()
            executor.shutdown()
            
    except Exception as e:
        print(f'重定向节点启动失败: {e}')
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main() 
