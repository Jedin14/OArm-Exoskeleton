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
#   ./openarm_teleop.sh --lelab      # also launch leLab web UI (training/datasets)
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
CAN_FD="false"
USE_WAVESHARE="false"
RIGHT_CAN="can0"
LEFT_CAN="can1"
CAN_BITRATE="1000000"
CAN_DATA_BITRATE="5000000"
GRIPPER_SCALING="0.05"
LEFT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]'
RIGHT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]'
LEFT_GRIPPER_REVERSE="false"
RIGHT_GRIPPER_REVERSE="false"
WEBSOCKET_HOST="0.0.0.0"
WEBSOCKET_PORT="19091"
STARTUP_DELAY=3
CLEAN_START="true"

ROS_DISTRO="${ROS_DISTRO:-humble}"

PIDS=()
LOG_DIR=""
SKIP_WEBSOCKET_STEP="false"
LAUNCH_LELAB="false"
LELAB_PORT="8000"

usage() {
    cat <<'EOF'
OpenArm exoskeleton teleoperation launcher

Usage:
  openarm_teleop.sh [OPTIONS]

Options:
  --sim                 Use fake hardware (default, recommended first)
  --real                Use real OpenArm hardware over CAN
  --can                 Alias for --real (requested hardware mode)
  --waveshare           Configure SocketCAN as Waveshare USB-CAN-FD-B
  --right-can IF        Right arm CAN interface (default: can0)
  --left-can IF         Left arm CAN interface (default: can1)
  --can-bitrate N       CAN bitrate used if interfaces need setup (default: 1000000)
  --can-data-bitrate N  CAN-FD data bitrate for Waveshare mode (default: 5000000)
  --gripper-scale F     Gripper scaling factor (default: 0.05)
  --invert-left-j6-j7   Multiply left joint6/joint7 commands by -1
  --reverse-left-grip   Reverse left gripper command (x -> 1-x)
  --ws-host HOST        WebSocket bind host (default: 0.0.0.0)
  --ws-port PORT        WebSocket port (default: 19091)
  --delay SEC           Seconds between launch steps (default: 3)
  --clean-start         Kill previous teleop/ROS/RViz processes before launch (default)
  --no-clean-start      Do not kill previous processes before launch
  --lelab               Also launch leLab web UI (training/dataset viewer)
  --lelab-port PORT     leLab web UI port (default: 8000)
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
}

ensure_waveshare_ready() {
    local ifname="$1"
    local type_out

    if ! ip link show "$ifname" >/dev/null 2>&1; then
        err "Waveshare CAN interface '$ifname' does not exist."
        err "Install and load the Waveshare driver first so Linux creates can0/can1."
        return 1
    fi

    if ! run_privileged ip link set "$ifname" down; then
        err "Failed to bring '$ifname' down before CAN-FD config."
        return 1
    fi

    if ! run_privileged ip link set "$ifname" type can bitrate "$CAN_BITRATE" dbitrate "$CAN_DATA_BITRATE" fd on; then
        err "Failed to configure CAN-FD on '$ifname'."
        err "Try manually:"
        err "  sudo ip link set $ifname type can bitrate $CAN_BITRATE dbitrate $CAN_DATA_BITRATE fd on"
        return 1
    fi

    if ! run_privileged ip link set "$ifname" up; then
        err "Failed to bring '$ifname' up after CAN-FD config."
        return 1
    fi

    type_out="$(ip -details link show "$ifname" 2>/dev/null || true)"
    if ! grep -q "fd" <<<"$type_out"; then
        err "Interface '$ifname' is up but CAN-FD mode was not detected."
        return 1
    fi
}

maybe_fallback_to_vcan() {
    # For development: if real CAN is unavailable but VCAN exists, switch interfaces.
    if ip link show "$RIGHT_CAN" >/dev/null 2>&1 && ip link show "$LEFT_CAN" >/dev/null 2>&1; then
        return 0
    fi

    if ip link show vcan0 >/dev/null 2>&1; then
        RIGHT_CAN="vcan0"
    fi
    if ip link show vcan1 >/dev/null 2>&1; then
        LEFT_CAN="vcan1"
    elif [[ "$RIGHT_CAN" == "vcan0" ]]; then
        LEFT_CAN="vcan0"
    fi

    if [[ "$RIGHT_CAN" == vcan* && "$LEFT_CAN" == vcan* ]]; then
        log "CAN interfaces not found; falling back to VCAN ($RIGHT_CAN, $LEFT_CAN)."
        log "VCAN mode is for software pipeline testing only (no physical motor communication)."
        return 0
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

    # In no-clean mode we can optionally reuse an existing websocket_teleoperator.
    if [[ "$CLEAN_START" == "false" ]] && echo "$listeners" | grep -q 'websocket_teleo'; then
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

kill_matching_processes() {
    local pattern="$1"
    local sig="${2:-TERM}"
    local pids pid

    mapfile -t pids < <(pgrep -f "$pattern" 2>/dev/null || true)
    for pid in "${pids[@]}"; do
        [[ -z "$pid" ]] && continue
        # Never kill this launcher process, its parent, or pid 1.
        if [[ "$pid" == "$$" || "$pid" == "$PPID" || "$pid" == "1" ]]; then
            continue
        fi
        kill "-$sig" "$pid" 2>/dev/null || true
    done
}

cleanup_previous_teleop_processes() {
    log "Clean start enabled: stopping previous OpenArm teleop/ROS/RViz processes ..."

    # Target known stale processes that can conflict with controller_manager.
    local patterns=(
        "ros2 launch qnbot_teleoperator websocket_teleoperator.launch.py"
        "ros2 launch qnbot_teleoperator exo_retargeting.launch.py"
        "ros2 launch qnbot_teleoperator exoskeleton_bridge.launch.py"
        "ros2 launch openarm_bringup openarm.bimanual.launch.py"
        "websocket_teleoperator"
        "exo_retargeting_node"
        "exoskeleton_bridge_node"
        "ros2_lelab_bridge.py"
        "lelab"
        "controller_manager/spawner"
        "ros2_control_node"
        "robot_state_publisher"
        "joint_state_broadcaster"
        "rviz2"
        "openarm_teleop.sh --"
    )

    local pat
    for pat in "${patterns[@]}"; do
        kill_matching_processes "$pat" TERM
    done
    sleep 1
    for pat in "${patterns[@]}"; do
        kill_matching_processes "$pat" KILL
    done

    # Reset ROS graph cache to avoid stale discovery state after hard cleanup.
    ros2 daemon stop >/dev/null 2>&1 || true
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sim) USE_FAKE_HARDWARE="true" ;;
        --real) USE_FAKE_HARDWARE="false" ;;
        --can) USE_FAKE_HARDWARE="false" ;;
        --waveshare) USE_WAVESHARE="true" ;;
        --right-can) RIGHT_CAN="${2:?--right-can requires an argument}"; shift ;;
        --left-can) LEFT_CAN="${2:?--left-can requires an argument}"; shift ;;
        --can-bitrate) CAN_BITRATE="${2:?--can-bitrate requires an argument}"; shift ;;
        --can-data-bitrate) CAN_DATA_BITRATE="${2:?--can-data-bitrate requires an argument}"; shift ;;
        --gripper-scale) GRIPPER_SCALING="${2:?--gripper-scale requires an argument}"; shift ;;
        --invert-left-j6-j7) LEFT_JOINT_MULTIPLIERS='[1.0, 1.0, 1.0, 1.0, 1.0, -1.0, -1.0]' ;;
        --reverse-left-grip) LEFT_GRIPPER_REVERSE="true" ;;
        --ws-host) WEBSOCKET_HOST="${2:?--ws-host requires an argument}"; shift ;;
        --ws-port) WEBSOCKET_PORT="${2:?--ws-port requires an argument}"; shift ;;
        --delay) STARTUP_DELAY="${2:?--delay requires an argument}"; shift ;;
        --clean-start) CLEAN_START="true" ;;
        --no-clean-start) CLEAN_START="false" ;;
        --lelab) LAUNCH_LELAB="true" ;;
        --lelab-port) LELAB_PORT="${2:?--lelab-port requires an argument}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown option: $1"; usage >&2; exit 1 ;;
    esac
    shift
done

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

# ---------------------------------------------------------------------------
# Fix: colcon's old setuptools path creates .egg-info but NOT .dist-info.
# Python 3.10+ entry-point stubs use importlib.metadata which only reads
# .dist-info.  Without this, every console_script immediately crashes with:
#   PackageNotFoundError: No package metadata was found for qnbot-teleoperator
# ---------------------------------------------------------------------------
ensure_qnbot_dist_info() {
    local site_packages="$WS_DIR/install/qnbot_teleoperator/lib/python3.10/site-packages"
    local dist_info="$site_packages/qnbot_teleoperator-0.0.0.dist-info"
    local egg_info="$site_packages/qnbot_teleoperator-0.0.0-py3.10.egg-info"

    # Already present and valid?
    if [[ -f "$dist_info/METADATA" ]]; then
        return 0
    fi

    if [[ ! -d "$egg_info" ]]; then
        err "Cannot find egg-info at $egg_info — workspace may not be built."
        return 1
    fi

    log "Creating .dist-info so importlib.metadata can find qnbot-teleoperator ..."
    mkdir -p "$dist_info"

    cat > "$dist_info/METADATA" << 'METAEOF'
Metadata-Version: 2.1
Name: qnbot-teleoperator
Version: 0.0.0
Summary: WebSocket teleoperator for OpenArm exoskeleton
METAEOF

    echo "pip"      > "$dist_info/INSTALLER"
    touch              "$dist_info/RECORD"
    [[ -f "$egg_info/entry_points.txt" ]] && cp "$egg_info/entry_points.txt" "$dist_info/entry_points.txt"
    [[ -f "$egg_info/top_level.txt"    ]] && cp "$egg_info/top_level.txt"    "$dist_info/top_level.txt"

    log "dist-info created at $dist_info"
}

for pkg in qnbot_teleoperator openarm_bringup; do
    if ! ros2 pkg prefix "$pkg" &>/dev/null; then
        err "Package '$pkg' not found. Build the workspace first."
        exit 1
    fi
done

ensure_qnbot_dist_info

if [[ "$USE_FAKE_HARDWARE" == "false" ]]; then
    ensure_openarm_hardware_plugin_registry
    maybe_fallback_to_vcan
    if [[ "$USE_WAVESHARE" == "true" ]]; then
        ensure_waveshare_ready "$RIGHT_CAN"
        ensure_waveshare_ready "$LEFT_CAN"
    else
        ensure_can_interface_ready "$RIGHT_CAN"
        ensure_can_interface_ready "$LEFT_CAN"
    fi
fi

if [[ "$CLEAN_START" == "true" ]]; then
    cleanup_previous_teleop_processes
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
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LAN_IP="${LAN_IP:-127.0.0.1}"
log "WebSocket (configure on exoskeleton host PC): ws://${LAN_IP}:${WEBSOCKET_PORT}"
log "  Same machine as ROS: ws://127.0.0.1:${WEBSOCKET_PORT}"
log "  Cursor/VS Code port forwarding is NOT required if the exo PC is on your LAN."
log "  Allow firewall: sudo ufw allow ${WEBSOCKET_PORT}/tcp"
log "  After start, verify: ss -tlnp | grep ${WEBSOCKET_PORT}"
if [[ "$LAUNCH_LELAB" == "true" ]]; then
    log "leLab Web UI: http://${LAN_IP}:${LELAB_PORT}  (training, datasets, teleoperation)"
    log "  Allow firewall: sudo ufw allow ${LELAB_PORT}/tcp"
fi
log ""
log "Safety: stop exoskeleton data forwarding until the suit is worn correctly."
if [[ "$USE_FAKE_HARDWARE" == "true" ]]; then
    log "Running in simulation — use --real only after verifying motion in sim."
fi
log ""

ensure_websocket_port_available

# Step 0 (optional): leLab web UI — training, dataset viewer, teleoperation
LELAB_VENV="$SCRIPT_DIR/lelab_ui/venv/bin/lelab"
if [[ "$LAUNCH_LELAB" == "true" ]]; then
    if [[ ! -x "$LELAB_VENV" ]]; then
        err "leLab venv not found at $LELAB_VENV"
        err "Install it first: cd $SCRIPT_DIR/lelab_ui && python3.12 -m venv venv && venv/bin/pip install -e ."
        exit 1
    fi
    log "Starting ROS 2 UDP Bridge for LeLab on port 19092 ..."
    launch_step ros2_lelab_bridge \
        /usr/bin/python3 "$SCRIPT_DIR/src/qnbot_teleoperator/scripts/ros2_lelab_bridge.py"
        
    log "Starting leLab web UI on port $LELAB_PORT ..."
    PORT="$LELAB_PORT" launch_step lelab "$LELAB_VENV"
fi

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
# Use a longer delay so hardware has time to initialise before the bridge connects
SAVED_DELAY="$STARTUP_DELAY"
STARTUP_DELAY=$(( STARTUP_DELAY > 5 ? STARTUP_DELAY : 6 ))
launch_step openarm_bringup \
    ros2 launch openarm_bringup openarm.bimanual.launch.py \
    arm_type:="$ARM_TYPE" \
    robot_controller:="$ROBOT_CONTROLLER" \
    can_fd:="$CAN_FD" \
    use_fake_hardware:="$USE_FAKE_HARDWARE" \
    right_can_interface:="$RIGHT_CAN" \
    left_can_interface:="$LEFT_CAN"
STARTUP_DELAY="$SAVED_DELAY"

# Step 4: Bridge retargeted commands to controllers
launch_step exoskeleton_bridge \
    ros2 launch qnbot_teleoperator exoskeleton_bridge.launch.py \
    gripper_scaling_factor:="$GRIPPER_SCALING" \
    left_joint_multipliers:="$LEFT_JOINT_MULTIPLIERS" \
    right_joint_multipliers:="$RIGHT_JOINT_MULTIPLIERS" \
    left_gripper_reverse:="$LEFT_GRIPPER_REVERSE" \
    right_gripper_reverse:="$RIGHT_GRIPPER_REVERSE"

log "All components started. Press Ctrl+C to stop."
if [[ "$LAUNCH_LELAB" == "true" ]]; then
    log "leLab Web UI → http://${LAN_IP}:${LELAB_PORT}"
fi
log "Tail logs: tail -f $LOG_DIR/*.log"

# Wait for ALL child processes (not just the first to exit).
# Using 'wait -n' would kill the whole pipeline the moment any single
# component crashes; 'wait' keeps everything else running.
wait
