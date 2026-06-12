#!/bin/bash
set -e

echo "========================================"
echo "Configuring can0 (Right Arm)..."
sudo ip link set can0 down || true
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up

echo "Configuring can1 (Left Arm)..."
sudo ip link set can1 down || true
sudo ip link set can1 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can1 up
echo "CAN interfaces configured."
echo "========================================"

echo ""
echo "Moving grippers to recorded closed position..."
if ! python3 move_grippers_to_home.py; then
    echo "========================================"
    echo "ABORTING CALIBRATION: Gripper home position missing or motor error."
    echo "Run 'python3 save_gripper_home.py' first!"
    exit 1
fi

echo ""
echo "Starting right arm calibration on can0..."
openarm-can-zero-position-calibration --canport can0 --arm-side right_arm

echo ""
echo "Starting left arm calibration on can1..."
openarm-can-zero-position-calibration --canport can1 --arm-side left_arm

echo ""
echo "========================================"
echo "Calibration sequence complete!"
