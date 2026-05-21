#!/usr/bin/env bash
#
# OpenArm exoskeleton teleoperation — single-script launcher
#
# Starts the full pipeline from "Exoskeleton teleoperation Openarm.pdf":
#   1. WebSocket server (receives exoskeleton data on port 19091)
#   2. Exoskeleton retargeting (exo -> OpenArm joint commands)
#   3. OpenArm bimanual hardware / simulation (ros2_control)
#   4. Exoskeleton bridge (joint commands -> forward_position_controller)
#
# Usage:
#   ./openarm_teleop.sh              # simulation (default, safe)
#   ./openarm_teleop.sh --real       # real hardware (CAN required)
#   ./openarm_teleop.sh --help
#
# Before teleop (on the exoskeleton host PC):
#   - Add forwarding address: ws://<this-pc-ip>:19091  (or ws://localhost:19091)
#   - Stop forwarding while donning the suit; start forwarding only when ready.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_DIR="${OPENARM_WS:-$SCRIPT_DIR}"

# Defaults (match PDF / 外骨骼遥操作步骤)
USE_FAKE_HARDWARE="true"
ARM_TYPE="v10"
ROBOT_CONTROLLER="forward_position_controller"
RIGHT_CAN_FD="true"
LEFT_CAN_FD="true"
RIGHT_RECV_CAN_ID_OFFSET="16"   # 0x10 = standard DM firmware (recv_id = send_id + 16)
LEFT_RECV_CAN_ID_OFFSET="16"
SC_INTERFACES=""          # comma-separated list of interfaces forced to classic SocketCAN
CAN_FD_OVERRIDE=""        # set to "true" by --can-fd to override --sc
RIGHT_CAN="can0"
LEFT_CAN="can1"
CAN_BITRATE="1000000"
GRIPPER_SCALING="0.05"
LEFT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]'
RIGHT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]'
LEFT_GRIPPER_REVERSE="false"
RIGHT_GRIPPER_REVERSE="false"
WEBSOCKET_HOST="0.0.0.0"
WEBSOCKET_PORT="19091"
STARTUP_DELAY=3

ROS_DISTRO="${ROS_DISTRO:-humble}"

PIDS=()
LOG_DIR=""
SKIP_WEBSOCKET_STEP="false"

usage() {
    cat <<'EOF'
OpenArm exoskeleton teleoperation launcher

Usage:
  openarm_teleop.sh [OPTIONS]

Options:
  --sim                 Use fake hardware (default, recommended first)
  --real                Use real OpenArm hardware over CAN
  --sc                  Use classic SocketCAN for specific interfaces (disables
                        CAN-FD on those interfaces). Most USB-CAN adapters
                        (e.g. CANable, PCAN) use plain SocketCAN.
                        Without an argument: disables CAN-FD on ALL interfaces.
                        With a comma-separated list: only those interfaces use
                        classic SocketCAN, others keep CAN-FD.
                        Examples:
                          --sc              (both arms: classic SocketCAN)
                          --sc can0         (right arm: SocketCAN, left: CAN-FD)
                          --sc can0,can1    (both arms: classic SocketCAN)
  --can-fd              Force CAN-FD on all interfaces (overrides --sc)
  --right-can IF        Right arm CAN interface (default: can0)
  --left-can IF         Left arm CAN interface (default: can1)
  --can-bitrate N       CAN bitrate used if interfaces need setup (default: 1000000)
  --gripper-scale F     Gripper scaling factor (default: 0.05)
  --invert-left-j6-j7   Multiply left joint6/joint7 commands by -1
  --reverse-left-grip   Reverse left gripper command (x -> 1-x)
  --ws-host HOST        WebSocket bind host (default: 0.0.0.0)
  --ws-port PORT        WebSocket port (default: 19091)
  --delay SEC           Seconds between launch steps (default: 3)
  -h, --help            Show this help

Environment:
  OPENARM_WS            Workspace root (default: script directory)
  ROS_DISTRO            ROS 2 distro to source (default: humble)

Exoskeleton host setup:
  1. Connect exoskeleton PC to this machine on the network.
  2. Add data-forwarding URL: ws://<this-machine-ip>:19091
  3. Stop forwarding while putting on the suit; enable only when ready.

EOF
}

log() { echo "[openarm_teleop] $*"; }
err() { echo "[openarm_teleop] ERROR: $*" >&2; }

run_privileged() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
        return $?
    fi

    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo -n "$@"
        return $?
    fi

    return 1
}

ensure_openarm_hardware_plugin_registry() {
    local pkg_prefix resource_file build_resource xml_file expected_rel

    expected_rel="share/openarm_hardware/openarm_hardware.xml"

    if ! pkg_prefix="$(ros2 pkg prefix openarm_hardware 2>/dev/null)"; then
        err "Package 'openarm_hardware' not found. Build the workspace first."
        return 1
    fi

    resource_file="$pkg_prefix/share/ament_index/resource_index/hardware_interface__pluginlib__plugin/openarm_hardware"
    build_resource="$WS_DIR/build/openarm_hardware/ament_cmake_index/share/ament_index/resource_index/hardware_interface__pluginlib__plugin/openarm_hardware"
    xml_file="$pkg_prefix/share/openarm_hardware/openarm_hardware.xml"

    if [[ -f "$resource_file" ]] && grep -aqF "$expected_rel" "$resource_file" 2>/dev/null; then
        return 0
    fi

    log "Repairing openarm_hardware plugin registry ..."
    mkdir -p "$(dirname "$resource_file")"

    if [[ -f "$build_resource" ]] && grep -aqF "$expected_rel" "$build_resource" 2>/dev/null; then
        cp "$build_resource" "$resource_file"
        return 0
    fi

    if [[ -f "$xml_file" ]]; then
        printf '%s\n' "$expected_rel" > "$resource_file"
        return 0
    fi

    err "openarm_hardware plugin metadata is missing."
    err "Rebuild it with:"
    err "  cd $WS_DIR && colcon build --packages-select openarm_hardware --allow-overriding openarm_hardware"
    return 1
}

ensure_can_interface_ready() {
    local ifname="$1"
    local link_state

    if ! command -v ip >/dev/null 2>&1; then
        err "'ip' command not found; cannot validate CAN interface $ifname."
        return 1
    fi

    if ! ip link show "$ifname" >/dev/null 2>&1; then
        err "CAN interface '$ifname' does not exist."
        err "Create it with:"
        err "  sudo ip link set $ifname type can bitrate $CAN_BITRATE"
        err "  sudo ip link set up $ifname"
        return 1
    fi

    if ! ip -details link show "$ifname" 2>/dev/null | grep -q 'link/can'; then
        if run_privileged ip link set "$ifname" type can bitrate "$CAN_BITRATE"; then
            log "Configured CAN interface $ifname with bitrate $CAN_BITRATE."
        else
            err "CAN interface '$ifname' is not configured as SocketCAN."
            err "Run:"
            err "  sudo ip link set $ifname type can bitrate $CAN_BITRATE"
            return 1
        fi
    fi

    link_state="$(ip -br link show "$ifname" 2>/dev/null | awk '{print $2}')"
    if [[ "$link_state" != "UP" ]]; then
        if run_privileged ip link set "$ifname" up; then
            log "Brought CAN interface $ifname up."
        else
            err "CAN interface '$ifname' is down and this script could not elevate privileges."
            err "Run:"
            err "  sudo ip link set up $ifname"
            return 1
        fi
    fi

    # Warn if slcand was started with -o (one-shot / no-retransmit).
    # In one-shot mode the enable command may not be retried if unACK'd,
    # leaving motors permanently red even though the bus is up.
    local slcand_cmd
    slcand_cmd="$(ps -eo args 2>/dev/null | grep "slcand" | grep "$ifname" | grep -v grep | head -1 || true)"
    if echo "$slcand_cmd" | grep -qw '\-o'; then
        log "WARNING: slcand for $ifname was started with -o (one-shot mode)."
        log "  Motors may stay red because enable frames are not retransmitted."
        log "  Restart slcand WITHOUT -o:"
        log "    sudo ip link set $ifname down"
        log "    sudo killall slcand"
        log "    sudo slcand -c -f -s8 <device> $ifname"
        log "    sudo ip link set $ifname up"
    fi
}

ensure_websocket_port_available() {
    local listeners

    if ! command -v ss >/dev/null 2>&1; then
        return 0
    fi

    listeners="$(ss -ltnp "( sport = :$WEBSOCKET_PORT )" 2>/dev/null | tail -n +2 || true)"
    if [[ -z "$listeners" ]]; then
        return 0
    fi

    # Reuse an already-running teleoperator websocket service on the same port.
    if echo "$listeners" | grep -q 'websocket_teleo'; then
        SKIP_WEBSOCKET_STEP="true"
        log "WebSocket port $WEBSOCKET_PORT already served by websocket_teleoperator; reusing it."
        return 0
    fi

    err "TCP port $WEBSOCKET_PORT is already in use."
    err "Stop existing listener(s) before launching teleop:"
    err "  ss -tlnp | grep :$WEBSOCKET_PORT"
    err "  kill <pid>   # or: pkill -f websocket_teleoperator"
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sim) USE_FAKE_HARDWARE="true" ;;
        --real) USE_FAKE_HARDWARE="false" ;;
        --sc)
            # Optional next arg: comma-separated interface list (e.g. can0 or can0,can1)
            # If next token starts with '-' or is empty, treat as "all interfaces"
            if [[ $# -gt 1 && "${2:0:1}" != "-" ]]; then
                SC_INTERFACES="$2"; shift
            else
                SC_INTERFACES="__ALL__"
            fi
            ;;
        --can-fd) CAN_FD_OVERRIDE="true" ;;
        --right-can) RIGHT_CAN="${2:?--right-can requires an argument}"; shift ;;
        --left-can) LEFT_CAN="${2:?--left-can requires an argument}"; shift ;;
        --can-bitrate) CAN_BITRATE="${2:?--can-bitrate requires an argument}"; shift ;;
        --gripper-scale) GRIPPER_SCALING="${2:?--gripper-scale requires an argument}"; shift ;;
        --invert-left-j6-j7) LEFT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, -1.0, -1.0]' ;;
        --reverse-left-grip) LEFT_GRIPPER_REVERSE="true" ;;
        --ws-host) WEBSOCKET_HOST="${2:?--ws-host requires an argument}"; shift ;;
        --ws-port) WEBSOCKET_PORT="${2:?--ws-port requires an argument}"; shift ;;
        --delay) STARTUP_DELAY="${2:?--delay requires an argument}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown option: $1"; usage >&2; exit 1 ;;
    esac
    shift
done

# Resolve per-arm CAN-FD flags now that RIGHT_CAN / LEFT_CAN are final.
# --can-fd always wins; --sc narrows which interfaces are classic SocketCAN.
iface_is_socketcan() {
    local iface="$1"
    [[ -z "$SC_INTERFACES" ]] && return 1          # --sc never passed
    [[ "$SC_INTERFACES" == "__ALL__" ]] && return 0 # --sc (no arg) → all
    # Check if iface appears as a word in the comma-separated list
    IFS=',' read -ra _sc_list <<< "$SC_INTERFACES"
    for _sc_if in "${_sc_list[@]}"; do
        [[ "${_sc_if// /}" == "$iface" ]] && return 0
    done
    return 1
}

if [[ -n "$CAN_FD_OVERRIDE" ]]; then
    RIGHT_CAN_FD="true"
    LEFT_CAN_FD="true"
    RIGHT_RECV_CAN_ID_OFFSET="16"
    LEFT_RECV_CAN_ID_OFFSET="16"
else
    if iface_is_socketcan "$RIGHT_CAN"; then
        RIGHT_CAN_FD="false"
        RIGHT_RECV_CAN_ID_OFFSET="0"   # motors on slcand/plain adapters use recv_id == send_id
    else
        RIGHT_CAN_FD="true"
        RIGHT_RECV_CAN_ID_OFFSET="16"
    fi
    if iface_is_socketcan "$LEFT_CAN"; then
        LEFT_CAN_FD="false"
        LEFT_RECV_CAN_ID_OFFSET="0"
    else
        LEFT_CAN_FD="true"
        LEFT_RECV_CAN_ID_OFFSET="16"
    fi
fi

if [[ ! -f "$WS_DIR/install/setup.bash" ]]; then
    err "Workspace not built. Run from $WS_DIR:"
    err "  source /opt/ros/$ROS_DISTRO/setup.bash && colcon build --symlink-install"
    exit 1
fi

if [[ ! -f "/opt/ros/$ROS_DISTRO/setup.bash" ]]; then
    err "ROS 2 $ROS_DISTRO not found at /opt/ros/$ROS_DISTRO"
    exit 1
fi

# ROS setup scripts reference optional vars; disable nounset while sourcing.
set +u
# shellcheck source=/dev/null
source "/opt/ros/$ROS_DISTRO/setup.bash"
# shellcheck source=/dev/null
source "$WS_DIR/install/setup.bash"
set -u

for pkg in qnbot_teleoperator openarm_bringup; do
    if ! ros2 pkg prefix "$pkg" &>/dev/null; then
        err "Package '$pkg' not found. Build the workspace first."
        exit 1
    fi
done

if [[ "$USE_FAKE_HARDWARE" == "false" ]]; then
    ensure_openarm_hardware_plugin_registry
    ensure_can_interface_ready "$RIGHT_CAN"
    ensure_can_interface_ready "$LEFT_CAN"
fi

# colcon sometimes installs console_scripts without +x; ros2 launch requires executable bit
QNBOT_LIBEXEC="$(ros2 pkg prefix qnbot_teleoperator)/lib/qnbot_teleoperator"
if [[ -d "$QNBOT_LIBEXEC" ]]; then
    chmod +x "$QNBOT_LIBEXEC"/* 2>/dev/null || true
fi

# RViz needs real mesh files (empty/LFS placeholders show a red RobotModel error)
MESH_CHECK="$WS_DIR/src/openarm_description/meshes/arm/v10/visual/link1.dae"
MESH_SRC="${OPENARM_MESH_SOURCE:-$HOME/openarm_ros2_ws/src/openarm_description}"
if [[ -f "$MESH_CHECK" ]] && ! grep -aq 'COLLADA' "$MESH_CHECK" 2>/dev/null; then
    if [[ -f "$MESH_SRC/meshes/arm/v10/visual/link1.dae" ]] \
        && grep -aq 'COLLADA' "$MESH_SRC/meshes/arm/v10/visual/link1.dae" 2>/dev/null; then
        log "Restoring meshes from $MESH_SRC ..."
        rsync -a "$MESH_SRC/meshes/" "$WS_DIR/src/openarm_description/meshes/"
        if [[ -f "$MESH_SRC/package.xml" ]] && file -b "$WS_DIR/src/openarm_description/package.xml" 2>/dev/null | grep -qv XML; then
            cp "$MESH_SRC/package.xml" "$WS_DIR/src/openarm_description/package.xml"
        fi
        rm -rf "$WS_DIR/build/openarm_description"
        (cd "$WS_DIR" && colcon build --packages-select openarm_description \
            --allow-overriding openarm_description) || {
            err "Failed to rebuild openarm_description after mesh restore."
            exit 1
        }
        # shellcheck source=/dev/null
        source "$WS_DIR/install/setup.bash"
    else
        err "openarm_description meshes are corrupt (link1.dae is not valid COLLADA)."
        err "Your git repo may be damaged (git lfs pull failed). Copy meshes from a good checkout:"
        err "  rsync -a ~/openarm_ros2_ws/src/openarm_description/meshes/ \\"
        err "    $WS_DIR/src/openarm_description/meshes/"
        err "  rm -rf $WS_DIR/build/openarm_description"
        err "  cd $WS_DIR && colcon build --packages-select openarm_description --allow-overriding openarm_description"
        exit 1
    fi
fi

LOG_DIR="$(mktemp -d /tmp/openarm_teleop.XXXXXX)"
cleanup() {
    local pid
    log "Shutting down (logs: $LOG_DIR)..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    sleep 1
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

launch_step() {
    local name="$1"
    shift
    local logfile="$LOG_DIR/${name}.log"
    log "Starting: $name  (log: $logfile)"
    "$@" >"$logfile" 2>&1 &
    PIDS+=("$!")
    sleep "$STARTUP_DELAY"
}

MODE_LABEL="simulation (fake hardware)"
if [[ "$USE_FAKE_HARDWARE" == "false" ]]; then
    MODE_LABEL="REAL HARDWARE"
fi

log "Workspace: $WS_DIR"
log "Mode: $MODE_LABEL"
log "CAN interfaces: right=$RIGHT_CAN ($([ "$RIGHT_CAN_FD" == "true" ] && echo 'CAN-FD' || echo 'SocketCAN'))  left=$LEFT_CAN ($([ "$LEFT_CAN_FD" == "true" ] && echo 'CAN-FD' || echo 'SocketCAN'))"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LAN_IP="${LAN_IP:-127.0.0.1}"
log "WebSocket (configure on exoskeleton host PC): ws://${LAN_IP}:${WEBSOCKET_PORT}"
log "  Same machine as ROS: ws://127.0.0.1:${WEBSOCKET_PORT}"
log "  Cursor/VS Code port forwarding is NOT required if the exo PC is on your LAN."
log "  Allow firewall: sudo ufw allow ${WEBSOCKET_PORT}/tcp"
log "  After start, verify: ss -tlnp | grep ${WEBSOCKET_PORT}"
log ""
log "Safety: stop exoskeleton data forwarding until the suit is worn correctly."
if [[ "$USE_FAKE_HARDWARE" == "true" ]]; then
    log "Running in simulation — use --real only after verifying motion in sim."
fi
log ""

ensure_websocket_port_available

# Step 1: WebSocket teleoperator
if [[ "$SKIP_WEBSOCKET_STEP" == "true" ]]; then
    log "Skipping websocket launch step (already running on :$WEBSOCKET_PORT)."
else
    launch_step websocket \
        ros2 launch qnbot_teleoperator websocket_teleoperator.launch.py \
        websocket_host:="$WEBSOCKET_HOST" \
        websocket_port:="$WEBSOCKET_PORT"
fi

# Step 2: Exoskeleton retargeting -> OpenArm
launch_step exo_retargeting \
    ros2 launch qnbot_teleoperator exo_retargeting.launch.py \
    robot_type:=OpenArm

# Step 3: OpenArm bimanual (ros2_control + RViz)
launch_step openarm_bringup \
    ros2 launch openarm_bringup openarm.bimanual.launch.py \
    arm_type:="$ARM_TYPE" \
    robot_controller:="$ROBOT_CONTROLLER" \
    right_can_fd:="$RIGHT_CAN_FD" \
    left_can_fd:="$LEFT_CAN_FD" \
    right_recv_can_id_offset:="$RIGHT_RECV_CAN_ID_OFFSET" \
    left_recv_can_id_offset:="$LEFT_RECV_CAN_ID_OFFSET" \
    use_fake_hardware:="$USE_FAKE_HARDWARE" \
    right_can_interface:="$RIGHT_CAN" \
    left_can_interface:="$LEFT_CAN"

# Step 4: Bridge retargeted commands to controllers
launch_step exoskeleton_bridge \
    ros2 launch qnbot_teleoperator exoskeleton_bridge.launch.py \
    gripper_scaling_factor:="$GRIPPER_SCALING" \
    left_joint_multipliers:="$LEFT_JOINT_MULTIPLIERS" \
    right_joint_multipliers:="$RIGHT_JOINT_MULTIPLIERS" \
    left_gripper_reverse:="$LEFT_GRIPPER_REVERSE" \
    right_gripper_reverse:="$RIGHT_GRIPPER_REVERSE"

log "All components started. Press Ctrl+C to stop."
log "Tail logs: tail -f $LOG_DIR/*.log"

wait -n 2>/dev/null || wait
