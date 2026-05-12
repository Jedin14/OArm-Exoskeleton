#!/bin/bash

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

# OpenArmX Bimanual MoveIt Demo with CAN 2.0 Setup Script
# This script configures CAN interfaces and launches the MoveIt demo

set -e  # Exit on any error

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
WORKSPACE_ROOT="$SCRIPT_DIR/../../.."

echo "Script directory: $SCRIPT_DIR"
echo "Workspace root: $WORKSPACE_ROOT"

# Source ROS2 environment
echo "Sourcing ROS2 environment..."
if [ -f "/opt/ros/humble/setup.bash" ]; then
    source /opt/ros/humble/setup.bash
    echo "ROS2 Humble environment sourced"
else
    echo "Error: ROS2 Humble not found at /opt/ros/humble/"
    exit 1
fi

# Source workspace environment
if [ -f "$WORKSPACE_ROOT/install/local_setup.bash" ]; then
    source "$WORKSPACE_ROOT/install/local_setup.bash"
    echo "Workspace environment sourced"
else
    echo "Error: Workspace not built. Please run 'colcon build' first."
    echo "Expected file: $WORKSPACE_ROOT/install/local_setup.bash"
    exit 1
fi

echo "Setting up CAN interfaces for OpenArmX Bimanual MoveIt Demo..."

# Store sudo password
SUDO_PASSWORD="ff"

# Function to configure CAN interface
configure_can_interface() {
    local interface=$1
    echo "Configuring $interface interface..."

    # Check if interface exists first
    if ! ip link show "$interface" &>/dev/null; then
        echo "Warning: $interface interface not found, creating virtual CAN interface..."
        echo "$SUDO_PASSWORD" | sudo -S modprobe vcan 2>/dev/null || true
        echo "$SUDO_PASSWORD" | sudo -S ip link add dev "$interface" type vcan 2>/dev/null || true
    fi

    # Configure the interface
    echo "$SUDO_PASSWORD" | sudo -S ip link set "$interface" down 2>/dev/null || true
    if ip link show "$interface" &>/dev/null; then
        echo "$SUDO_PASSWORD" | sudo -S ip link set "$interface" type can bitrate 1000000 2>/dev/null || \
        echo "$SUDO_PASSWORD" | sudo -S ip link set "$interface" up 2>/dev/null || true
        echo "$SUDO_PASSWORD" | sudo -S ip link set "$interface" up 2>/dev/null || true
    fi
}

# Configure CAN interfaces
configure_can_interface "can0"
configure_can_interface "can1"

# Verify CAN interfaces
echo "Verifying CAN interfaces..."
if ip link show can0 &>/dev/null; then
    ip link show can0 | grep -q "UP" && echo "CAN0 is UP" || echo "Warning: CAN0 is not UP but exists"
else
    echo "Warning: CAN0 interface not available"
fi

if ip link show can1 &>/dev/null; then
    ip link show can1 | grep -q "UP" && echo "CAN1 is UP" || echo "Warning: CAN1 is not UP but exists"
else
    echo "Warning: CAN1 interface not available"
fi

echo ""
echo "CAN interfaces configured. Starting MoveIt demo with CAN 2.0 (non-FD)..."
echo "Launch command: ros2 launch openarmx_bimanual_moveit_config demo.launch.py can_fd:=false"
echo ""

# Check if launch file exists
if ros2 pkg list 2>/dev/null | grep -q "openarmx_bimanual_moveit_config"; then
    echo "Package openarmx_bimanual_moveit_config found"
    # Launch MoveIt demo with CAN 2.0 configuration
    ros2 launch openarmx_bimanual_moveit_config demo.launch.py can_fd:=false
else
    echo "Error: Package 'openarmx_bimanual_moveit_config' not found"
    echo "Available packages:"
    ros2 pkg list 2>/dev/null | grep openarmx || echo "No openarmx packages found"
    exit 1
fi
