# OpenArm Bimanual Exoskeleton Teleoperation

![Teleoperation](Teleop.png)

This package provides a complete, low-latency teleoperation stack for controlling the bimanual OpenArm using a wearable exoskeleton. It bridges raw exoskeleton data via WebSocket, performs kinematic retargeting, and securely interfaces with the `ros2_control` hardware interface.

<p align="center">
  <img src="arm.png" alt="OpenArm" width="400"/>
</p>

## Features
- **Bimanual Teleoperation**: Full 7-DOF per arm + gripper control.
- **Safe Startup Interpolation**: Prevents violent hardware jerks by smoothly blending from the robot's current resting pose to the operator's pose over 3 seconds when connecting.
- **Continuous Gripper Holding**: Overcomes physical resistance by applying a continuous 100Hz position holding torque with an integrated deadzone and "over-squeeze" mapping.
- **EMA Smoothing**: Real-time Exponential Moving Average filtering eliminates sensor jitter from the exoskeleton.
- **Simulation & Real Hardware Modes**: Easily test in RViz or execute on the physical CAN-bus connected robot.

---

## 🚀 Quick Start (Run Code)

A unified launcher script `openarm_teleop.sh` is provided in the workspace root to handle all workspace sourcing, background launching of the WebSocket server, retargeting, openarm bringing-up, and exoskeleton bridge.

### Configure CAN Interfaces (Required for Real Hardware)
If running on the real robot, ensure the USB-CAN adapters are connected and configure the CAN interfaces.

**Classic SocketCAN** (most USB adapters — CANable, PCAN native CAN, etc.):
```bash
sudo ip link set can0 type can bitrate 1000000
sudo ip link set up can0
```

**slcand (serial/USB-TTL adapters like DFRobot TEL0150)**:
```bash
# ⚠️  Do NOT use -o (one-shot mode). Without retransmit, the motor enable
#    command may be dropped and motors will stay red.
sudo slcand -c -f -s8 /dev/serial/by-id/usb-DFRobot_Can_To_TTL_TEL0150-if00 can0
sudo ip link set can0 up
```

**CAN-FD** (only for adapters that support FD mode, e.g. PCAN USB Pro FD):
```bash
sudo ip link set can1 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set up can1
```

### Run in Simulation (Fake Hardware)
Use this mode to visualize the retargeting and teleoperation in RViz without physical hardware.
```bash
./openarm_teleop.sh --ws-port 19191
```

### Run on Real Hardware (classic SocketCAN — most setups)
Most USB-CAN adapters (CANable, PCAN, etc.) use plain SocketCAN. Pass `--sc` with the
interface name(s) that need classic mode:

```bash
# Both arms on plain SocketCAN adapters:
./openarm_teleop.sh --real --sc --ws-port 19191

# Mixed: can0 is a plain slcand/SocketCAN adapter, can1 is CAN-FD capable:
./openarm_teleop.sh --real --sc can0 --ws-port 19191

# Both interfaces in the comma-separated list:
./openarm_teleop.sh --real --sc can0,can1 --ws-port 19191
```

### Run on Real Hardware (CAN-FD)
Only use this if your adapter and firmware support CAN-FD and interfaces were configured with `fd on`:
```bash
./openarm_teleop.sh --real --can-fd --ws-port 19191
```

### Connect the Exoskeleton (HMI)
Once the launcher is running:
1. Open the **Qnbot HMI AppImage** on your network.
2. Set the forwarding address to: `ws://localhost:19191`
3. Click **STOP** forwarding.
4. Wear the exoskeleton and assume a comfortable starting position.
5. Click **START** forwarding.
   > *Note: The physical robot will take 3 seconds to smoothly blend into your starting position.*

---

## Architecture Pipeline



1. **WebSocket Teleoperator Node**
   Listens on port `19191` for incoming JSON packets from the Qnbot HMI containing raw joint angles.
2. **Exo Retargeting Node**
   Translates raw exoskeleton sensor data into a physical coordinate space, mapping trigger pulls to gripper aperture sizes, complete with mechanical deadzones.
3. **Exoskeleton Bridge Node**
   Receives the retargeted targets. Seeds commands from `/joint_states`, then applies EMA smoothing and optional per-step slew limits. Publishes `Float64MultiArray` to the forward position controllers.

## Launch Parameters
You can adjust the following parameters inside the `teleop_sim_full.launch.py` or `exoskeleton_bridge.launch.py`:
- `smooth_alpha` (default: 0.15): EMA filter coefficient (lower = smoother but slightly more latent).
- `gripper_scaling_factor` (default: 0.05): Calibration multiplier for the physical gripper.
# OpenArm-Exoskeleton
