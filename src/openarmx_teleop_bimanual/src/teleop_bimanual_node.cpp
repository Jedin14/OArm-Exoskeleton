// OpenArmX Research and Education License
//
// Copyright (c) 2026 Chengdu Changshu Robot Co., Ltd.
// https://www.openarmx.com
//
// This software is licensed for non-commercial research, academic,
// and educational use only.
// Commercial use is strictly prohibited without prior written permission.
//
// Redistribution for permitted non-commercial purposes must retain
// this notice.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

#include <chrono>
#include <memory>
#include <vector>
#include <string>
#include <csignal>
#include <thread>

#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/float64_multi_array.hpp"

#include <openarmx/can/socket/openarmx.hpp>
#include <openarmx/robstride_motor/rs_motor_constants.hpp>

using namespace std::chrono_literals;

class TeleopLeaderNode : public rclcpp::Node
{
public:
    // 夹爪坐标转换常量（与hardware包保持一致）
    static constexpr double GRIPPER_JOINT_0_POSITION = 0.044;  // 夹爪完全打开的距离(m)
    static constexpr double GRIPPER_MOTOR_1_RADIANS = 1.0472;  // 对应的电机角度(rad, 60度)

    // 方向反转配置
    // 注意：如果主动端和从动端机械臂安装方向不同（如镜像安装），可能需要反转关节方向
    static constexpr bool INVERT_JOINTS = true;   // 是否反转7个关节方向（乘以-1）
    static constexpr bool INVERT_GRIPPER = false; // 是否反转夹爪方向（乘以-1）

    TeleopLeaderNode()
    : Node("teleop_bimanual_node")
    {
        // 声明参数
        this->declare_parameter<std::string>("leader_right_can", "can0");
        this->declare_parameter<std::string>("leader_left_can", "can1");
        this->declare_parameter<std::string>("follower_right_prefix", "right");
        this->declare_parameter<std::string>("follower_left_prefix", "left");
        this->declare_parameter<int>("control_rate_hz", 200);

        // 获取参数
        std::string leader_right_can = this->get_parameter("leader_right_can").as_string();
        std::string leader_left_can = this->get_parameter("leader_left_can").as_string();
        std::string follower_right_prefix = this->get_parameter("follower_right_prefix").as_string();
        std::string follower_left_prefix = this->get_parameter("follower_left_prefix").as_string();
        int control_rate_hz = this->get_parameter("control_rate_hz").as_int();

        RCLCPP_INFO(this->get_logger(), "=== OpenArmX 遥操作节点初始化 ===");
        RCLCPP_INFO(this->get_logger(), "主动端右臂CAN: %s", leader_right_can.c_str());
        RCLCPP_INFO(this->get_logger(), "主动端左臂CAN: %s", leader_left_can.c_str());
        RCLCPP_INFO(this->get_logger(), "从动端右臂前缀: %s", follower_right_prefix.c_str());
        RCLCPP_INFO(this->get_logger(), "从动端左臂前缀: %s", follower_left_prefix.c_str());
        RCLCPP_INFO(this->get_logger(), "控制频率: %d Hz", control_rate_hz);
        RCLCPP_INFO(this->get_logger(), "关节方向反转: %s", INVERT_JOINTS ? "是" : "否");
        RCLCPP_INFO(this->get_logger(), "夹爪方向反转: %s", INVERT_GRIPPER ? "是" : "否");

        // 初始化主动端右臂
        try {
            RCLCPP_INFO(this->get_logger(), "初始化主动端右臂...");
            leader_right_arm_ = std::make_unique<openarmx::can::socket::OpenArmX>(leader_right_can, false);

            // 初始化7个关节电机
            std::vector<openarmx::robstride_motor::MotorType> arm_motor_types = {
                openarmx::robstride_motor::MotorType::RS04,  // Joint 1
                openarmx::robstride_motor::MotorType::RS04,  // Joint 2
                openarmx::robstride_motor::MotorType::RS03,  // Joint 3
                openarmx::robstride_motor::MotorType::RS03,  // Joint 4
                openarmx::robstride_motor::MotorType::RS00,  // Joint 5
                openarmx::robstride_motor::MotorType::RS00,  // Joint 6
                openarmx::robstride_motor::MotorType::RS00   // Joint 7
            };
            std::vector<uint32_t> arm_ids = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07};
            leader_right_arm_->init_arm_motors(arm_motor_types, arm_ids, arm_ids);

            // 初始化夹爪（电机ID 8）
            leader_right_arm_->init_gripper_motor(openarmx::robstride_motor::MotorType::RS00, 0x08, 0x08);

            // 设置回调模式为STATE以读取位置
            leader_right_arm_->set_callback_mode_all(openarmx::robstride_motor::CallbackMode::STATE);

            // 关键：对齐Python，不调用enable也不调用disable，让电机保持初始未使能状态
            if (!leader_right_arm_->disable_all_complete()) {
                RCLCPP_WARN(this->get_logger(), "部分右臂电机禁用失败");
            }
            // Python版本注释掉了 enable_all_motors(leader_bus, "leader")
            RCLCPP_INFO(this->get_logger(), "主动端右臂初始化成功（7关节+夹爪，保持未使能状态）");
        } catch (const std::exception& e) {
            RCLCPP_ERROR(this->get_logger(), "初始化主动端右臂失败: %s", e.what());
            throw;
        }

        // 初始化主动端左臂
        try {
            RCLCPP_INFO(this->get_logger(), "初始化主动端左臂...");
            leader_left_arm_ = std::make_unique<openarmx::can::socket::OpenArmX>(leader_left_can, false);

            // 初始化7个关节电机
            std::vector<openarmx::robstride_motor::MotorType> arm_motor_types = {
                openarmx::robstride_motor::MotorType::RS04,  // Joint 1
                openarmx::robstride_motor::MotorType::RS04,  // Joint 2
                openarmx::robstride_motor::MotorType::RS03,  // Joint 3
                openarmx::robstride_motor::MotorType::RS03,  // Joint 4
                openarmx::robstride_motor::MotorType::RS00,  // Joint 5
                openarmx::robstride_motor::MotorType::RS00,  // Joint 6
                openarmx::robstride_motor::MotorType::RS00   // Joint 7
            };
            std::vector<uint32_t> arm_ids = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07};
            leader_left_arm_->init_arm_motors(arm_motor_types, arm_ids, arm_ids);

            // 初始化夹爪（电机ID 8）
            leader_left_arm_->init_gripper_motor(openarmx::robstride_motor::MotorType::RS00, 0x08, 0x08);

            // 设置回调模式为STATE以读取位置
            leader_left_arm_->set_callback_mode_all(openarmx::robstride_motor::CallbackMode::STATE);

            // 关键：对齐Python，不调用enable也不调用disable，让电机保持初始未使能状态
            if (!leader_left_arm_->disable_all_complete()) {
                RCLCPP_WARN(this->get_logger(), "部分左臂电机禁用失败");
            }
            // Python版本注释掉了 enable_all_motors(leader_bus, "leader")
            RCLCPP_INFO(this->get_logger(), "主动端左臂初始化成功（7关节+夹爪，保持未使能状态）");
        } catch (const std::exception& e) {
            RCLCPP_ERROR(this->get_logger(), "初始化主动端左臂失败: %s", e.what());
            throw;
        }

        // 创建发布器
        std::string right_topic = "/" + follower_right_prefix + "_forward_position_controller/commands";
        std::string left_topic = "/" + follower_left_prefix + "_forward_position_controller/commands";

        follower_right_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(right_topic, 10);
        follower_left_pub_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(left_topic, 10);

        RCLCPP_INFO(this->get_logger(), "从动端右臂话题: %s", right_topic.c_str());
        RCLCPP_INFO(this->get_logger(), "从动端左臂话题: %s", left_topic.c_str());

        // 创建定时器，200Hz控制循环
        auto control_period = std::chrono::microseconds(1000000 / control_rate_hz);
        control_timer_ = this->create_wall_timer(
            control_period, std::bind(&TeleopLeaderNode::control_callback, this));

        RCLCPP_INFO(this->get_logger(), "遥操作节点就绪，开始控制循环...");
    }

    ~TeleopLeaderNode()
    {
        RCLCPP_INFO(this->get_logger(), "关闭遥操作节点...");

        // 对齐Python的finally块：禁用所有电机
        if (leader_right_arm_) {
            try {
                RCLCPP_INFO(this->get_logger(), "释放主动端右臂电机保持力...");
                // 关键：根据说明书，发送kp=0, kd=0的运控命令可以完全放松电机
                release_motor_torque(leader_right_arm_.get());
                std::this_thread::sleep_for(std::chrono::milliseconds(10));

                // 然后再disable
                if (!leader_right_arm_->disable_all_complete()) {
                    RCLCPP_WARN(this->get_logger(), "部分右臂电机禁用失败");
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            } catch (const std::exception& e) {
                RCLCPP_ERROR(this->get_logger(), "禁用右臂失败: %s", e.what());
            }
        }
        if (leader_left_arm_) {
            try {
                RCLCPP_INFO(this->get_logger(), "释放主动端左臂电机保持力...");
                // 关键：发送kp=0, kd=0的运控命令
                release_motor_torque(leader_left_arm_.get());
                std::this_thread::sleep_for(std::chrono::milliseconds(10));

                // 然后再disable
                if (!leader_left_arm_->disable_all_complete()) {
                    RCLCPP_WARN(this->get_logger(), "部分左臂电机禁用失败");
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            } catch (const std::exception& e) {
                RCLCPP_ERROR(this->get_logger(), "禁用左臂失败: %s", e.what());
            }
        }
        RCLCPP_INFO(this->get_logger(), "遥操作节点已安全关闭");
    }

private:
    void control_callback()
    {
        try {
            // 读取右臂位置
            std::vector<double> right_positions = read_arm_positions(leader_right_arm_.get(), "右");

            // 读取左臂位置
            std::vector<double> left_positions = read_arm_positions(leader_left_arm_.get(), "左");

            // 如果需要，反转关节和/或夹爪方向
            if (INVERT_JOINTS || INVERT_GRIPPER) {
                invert_joint_positions(right_positions);
                invert_joint_positions(left_positions);
            }

            // 发布到从动端（8DOF：7关节+1夹爪）
            if (right_positions.size() == 8) {
                auto cmd_msg = std_msgs::msg::Float64MultiArray();
                cmd_msg.data = right_positions;
                follower_right_pub_->publish(cmd_msg);
            } else if (!right_positions.empty()) {
                RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                    "右臂关节数量错误: %zu (期望8个)", right_positions.size());
            }

            if (left_positions.size() == 8) {
                auto cmd_msg = std_msgs::msg::Float64MultiArray();
                cmd_msg.data = left_positions;
                follower_left_pub_->publish(cmd_msg);
            } else if (!left_positions.empty()) {
                RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                    "左臂关节数量错误: %zu (期望8个)", left_positions.size());
            }

            // 定期打印位置信息（1Hz）
            static auto last_log_time = this->now();
            auto current_time = this->now();
            if ((current_time - last_log_time).seconds() >= 1.0) {
                log_positions("右臂", right_positions);
                log_positions("左臂", left_positions);
                last_log_time = current_time;
            }

        } catch (const std::exception& e) {
            RCLCPP_ERROR_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                "控制循环错误: %s", e.what());
        }
    }

    std::vector<double> read_arm_positions(openarmx::can::socket::OpenArmX* arm, const std::string& name)
    {
        std::vector<double> positions;

        if (!arm) {
            RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                "%s臂未初始化", name.c_str());
            return positions;
        }

        try {
            // 刷新电机状态（需要refresh才能获取到数据）
            arm->refresh_all();
            // 关键修复：8个电机(7关节+夹爪)回复需要约1040us(8×130us)
            // 原来300us不够，导致夹爪数据丢失或延迟
            arm->recv_all(1500);  // 等待最多1500us，确保收到所有电机回复

            // 获取7个关节位置（保持原始弧度值）
            for (auto* motor : arm->get_arm().get_motors()) {
                positions.push_back(motor->get_position());
            }

            // 获取夹爪位置（第8个自由度）
            // 关键修复：将电机角度(弧度)转换为关节距离(米)，与从动端hardware包保持一致
            for (auto* motor : arm->get_gripper().get_motors()) {
                double motor_radians = motor->get_position();
                double joint_meters = motor_radians_to_joint(motor_radians);
                positions.push_back(joint_meters);
            }
        } catch (const std::exception& e) {
            RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                "读取%s臂位置失败: %s", name.c_str(), e.what());
        }

        return positions;
    }

    void log_positions(const std::string& arm_name, const std::vector<double>& positions)
    {
        if (positions.empty()) return;

        std::string pos_str = arm_name + " 位置: [";
        for (size_t i = 0; i < positions.size(); ++i) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.3f", positions[i]);
            pos_str += buf;
            if (i < positions.size() - 1) pos_str += ", ";
        }
        pos_str += "]";
        RCLCPP_INFO(this->get_logger(), "%s", pos_str.c_str());
    }

    void invert_joint_positions(std::vector<double>& positions)
    {
        if (positions.size() < 7) return;

        // 反转前7个关节（如果启用）
        if (INVERT_JOINTS) {
            for (size_t i = 0; i < 7 && i < positions.size(); ++i) {
                positions[i] = -positions[i];
            }
        }

        // 反转夹爪（第8个，如果存在且启用）
        if (INVERT_GRIPPER && positions.size() >= 8) {
            positions[7] = -positions[7];
        }
    }

    // 夹爪坐标转换：电机角度(弧度) -> 关节距离(米)
    // 与hardware包中的motor_radians_to_joint()保持一致
    double motor_radians_to_joint(double motor_radians) const
    {
        // 电机 0 rad=关闭 -> 关节 0m，电机 1.0472 rad=打开 -> 关节 0.044m
        return GRIPPER_JOINT_0_POSITION * (motor_radians / GRIPPER_MOTOR_1_RADIANS);
    }

    // 释放电机保持力：根据RS04说明书，发送kp=0, kd=0的运控命令可以完全放松电机
    void release_motor_torque(openarmx::can::socket::OpenArmX* arm)
    {
        if (!arm) return;

        try {
            // 准备所有电机的kp=0, kd=0命令（7关节）
            std::vector<openarmx::robstride_motor::MotionControlParam> arm_commands;
            for (auto* motor : arm->get_arm().get_motors()) {
                openarmx::robstride_motor::MotionControlParam param;
                param.position = motor->get_position();  // 保持当前位置
                param.velocity = 0.0;
                param.torque = 0.0;
                param.kp = 0.0;  // 关键：kp=0，无位置控制
                param.kd = 0.0;  // 关键：kd=0，无阻尼控制
                arm_commands.push_back(param);
            }
            arm->get_arm().send_motion_control_commands(arm_commands);

            // 准备夹爪的kp=0, kd=0命令
            std::vector<openarmx::robstride_motor::MotionControlParam> gripper_commands;
            for (auto* motor : arm->get_gripper().get_motors()) {
                openarmx::robstride_motor::MotionControlParam param;
                param.position = motor->get_position();
                param.velocity = 0.0;
                param.torque = 0.0;
                param.kp = 0.0;
                param.kd = 0.0;
                gripper_commands.push_back(param);
            }
            arm->get_gripper().send_motion_control_commands(gripper_commands);

        } catch (const std::exception& e) {
            RCLCPP_ERROR(this->get_logger(), "释放电机保持力失败: %s", e.what());
        }
    }

    // 主动端OpenArmX对象
    std::unique_ptr<openarmx::can::socket::OpenArmX> leader_right_arm_;
    std::unique_ptr<openarmx::can::socket::OpenArmX> leader_left_arm_;

    // 从动端发布器
    rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr follower_right_pub_;
    rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr follower_left_pub_;

    // 控制定时器
    rclcpp::TimerBase::SharedPtr control_timer_;
};

// 信号处理
static std::shared_ptr<TeleopLeaderNode> g_node;

void signal_handler(int signum)
{
    (void)signum;
    RCLCPP_INFO(rclcpp::get_logger("teleop_bimanual_node"), "接收到Ctrl+C，正在关闭...");

    // 在shutdown之前先禁用主动端电机
    if (g_node) {
        RCLCPP_INFO(rclcpp::get_logger("teleop_bimanual_node"), "禁用主动端电机...");
        try {
            // 通过g_node访问私有成员需要friend，这里直接在析构函数中处理
            // 但为了确保执行，先触发shutdown
            rclcpp::shutdown();
        } catch (...) {
            RCLCPP_ERROR(rclcpp::get_logger("teleop_bimanual_node"), "禁用电机时出错");
        }
    }
}

int main(int argc, char** argv)
{
    rclcpp::init(argc, argv);

    // 设置信号处理
    std::signal(SIGINT, signal_handler);

    try {
        g_node = std::make_shared<TeleopLeaderNode>();
        rclcpp::spin(g_node);
    } catch (const std::exception& e) {
        RCLCPP_ERROR(rclcpp::get_logger("teleop_bimanual_node"),
            "致命错误: %s", e.what());
        return 1;
    }

    rclcpp::shutdown();
    return 0;
}
