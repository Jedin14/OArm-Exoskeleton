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

# OpenArmX Bimanual MoveIt Demo - Simulation Mode
# This script launches the MoveIt demo in simulation mode without hardware dependencies

set -e  # Exit on any error

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
WORKSPACE_ROOT="$SCRIPT_DIR/../../.."

echo "=========================================="
echo "  OpenArmX Bimanual MoveIt - Simulation"
echo "=========================================="
echo ""
echo "Script directory: $SCRIPT_DIR"
echo "Workspace root: $WORKSPACE_ROOT"
echo ""

# Source ROS2 environment
echo "Sourcing ROS2 environment..."
if [ -f "/opt/ros/humble/setup.bash" ]; then
    source /opt/ros/humble/setup.bash
    echo "✓ ROS2 Humble environment sourced"
else
    echo "✗ Error: ROS2 Humble not found at /opt/ros/humble/"
    exit 1
fi

# Source workspace environment
if [ -f "$WORKSPACE_ROOT/install/local_setup.bash" ]; then
    source "$WORKSPACE_ROOT/install/local_setup.bash"
    echo "✓ Workspace environment sourced"
else
    echo "✗ Error: Workspace not built. Please run 'colcon build' first."
    echo "  Expected file: $WORKSPACE_ROOT/install/local_setup.bash"
    exit 1
fi

echo ""
echo "Starting MoveIt demo in SIMULATION mode..."
echo "  - No hardware required"
echo "  - No CAN interfaces needed"
echo "  - Fake hardware controllers will be used"
echo ""
echo "Launch command: ros2 launch openarmx_bimanual_moveit_config demo_sim.launch.py"
echo ""

# Check if package exists
if ros2 pkg prefix openarmx_bimanual_moveit_config &>/dev/null; then
    echo "✓ Package openarmx_bimanual_moveit_config found"
    # Launch MoveIt demo in simulation mode
    ros2 launch openarmx_bimanual_moveit_config demo_sim.launch.py
else
    echo "✗ Error: Package 'openarmx_bimanual_moveit_config' not found"
    echo "  Available packages:"
    ros2 pkg list 2>/dev/null | grep openarmx || echo "  No openarmx packages found"
    exit 1
fi
