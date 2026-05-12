#!/usr/bin/env bash
# ============================================================
#  OpenArm Exoskeleton Teleoperation — Quick Launch Script
#  Usage:
#    ./teleop_sim.sh            # simulation (fake hardware)
#    ./teleop_sim.sh real       # real CAN hardware (can_fd=false)
#    ./teleop_sim.sh real_fd    # real CAN hardware (can_fd=true)
# ============================================================



SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Source workspaces ────────────────────────────────────────
source /opt/ros/humble/setup.bash
source /home/jed/openarm_ws/install/setup.bash
source /home/jed/openarmx_ws/install/setup.bash

# ── Parse mode ──────────────────────────────────────────────
MODE="${1:-sim}"

case "$MODE" in
  real)
    USE_FAKE="false" #For now = Fake
    CAN_FD="false"
    echo "🤖  Mode: REAL HARDWARE (CAN 2.0)"
    echo "⚠️   Ensure CAN interfaces are up and arm is powered!"
    ;;
  real_fd)
    USE_FAKE="false"
    CAN_FD="true"
    echo "🤖  Mode: REAL HARDWARE (CAN-FD)"
    echo "⚠️   Ensure CAN interfaces are up and arm is powered!"
    ;;
  sim|*)
    USE_FAKE="true"
    CAN_FD="false"
    echo "🖥️   Mode: SIMULATION (fake hardware)"
    ;;
esac

echo ""
echo "════════════════════════════════════════"
echo "  OpenArm Exo Teleoperation Stack"
echo "════════════════════════════════════════"
echo "  ros2_control : use_fake_hardware=$USE_FAKE"
echo "  CAN-FD       : $CAN_FD"
echo "  WebSocket    : ws://localhost:19091"
echo "════════════════════════════════════════"
echo ""

# ── CAN interface setup (real hardware only) ─────────────────
if [ "$USE_FAKE" = "false" ]; then
    echo "🔌  Bringing up CAN interfaces..."

    if [ "$CAN_FD" = "true" ]; then
        # CAN-FD: 1 Mbps nominal, 5 Mbps data
        sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on 2>/dev/null || true
        sudo ip link set can1 type can bitrate 1000000 dbitrate 5000000 fd on 2>/dev/null || true
    else
        # Standard CAN: 1 Mbps
        sudo ip link set can0 type can bitrate 1000000 2>/dev/null || true
        sudo ip link set can1 type can bitrate 1000000 2>/dev/null || true
    fi

    sudo ip link set can0 up 2>/dev/null || true
    sudo ip link set can1 up 2>/dev/null || true

    # Verify interfaces are up
    for IFACE in can0 can1; do
        STATE=$(ip link show "$IFACE" 2>/dev/null | grep -oP '(?<=state )\w+' || echo "NOT FOUND")
        if [ "$STATE" = "UP" ]; then
            echo "   ✅  $IFACE — UP"
        else
            echo "   ❌  $IFACE — $STATE  (check USB CAN adapter is connected)"
        fi
    done
    echo ""
fi

echo "📋  HMI setup:"
echo "   1. Open Qnbot HMI AppImage"
echo "   2. Add forwarding address: ws://localhost:19091"
echo "   3. STOP forwarding → wear exoskeleton → START forwarding"
echo ""
echo "Launching in 3 seconds... (Ctrl+C to abort)"
sleep 3

ros2 launch qnbot_teleoperator teleop_sim_full.launch.py \
  use_fake_hardware:="$USE_FAKE" \
  can_fd:="$CAN_FD" \
  arm_type:=v10 \
  gripper_scaling_factor:=0.05 \
  robot_type:=OpenArm
