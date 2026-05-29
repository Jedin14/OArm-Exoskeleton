#!/usr/bin/env bash
# Reload the patched zcan_usb driver and bring up both CAN interfaces
set -euo pipefail

BUILT_KO="/usr/src/zcan_usb-0.5.1/zcan_usb.ko"
INSTALLED_KO="/lib/modules/$(uname -r)/updates/dkms/zcan_usb.ko"
BITRATE="${1:-1000000}"

echo "[zcan reload] Bringing down CAN interfaces..."
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can1 down 2>/dev/null || true

echo "[zcan reload] Unloading old module..."
sudo rmmod zcan_usb 2>/dev/null || true
sleep 0.5

echo "[zcan reload] Installing new module..."
sudo cp "$BUILT_KO" "$INSTALLED_KO"
sudo depmod -a

echo "[zcan reload] Loading new module..."
sudo insmod "$BUILT_KO"
sleep 2   # allow USB re-enumeration

echo "[zcan reload] Configuring can0 (CH0 → physical CAN1 port)..."
sudo ip link set can0 type can bitrate "$BITRATE"
sudo ip link set can0 up

echo "[zcan reload] Configuring can1 (CH1 → physical CAN2 port)..."
sudo ip link set can1 type can bitrate "$BITRATE"
sudo ip link set can1 up

echo ""
echo "[zcan reload] Done. Interface status:"
ip -details link show can0 | grep -E "can[01]|bitrate|state"
ip -details link show can1 | grep -E "can[01]|bitrate|state"
echo ""
echo "Test CH1 (physical CAN2): candump can1 &  then power on left arm"
echo "Test CH0 (physical CAN1): candump can0 &  then power on right arm"
echo ""
echo "Check kernel log for TX endpoint confirmations:"
echo "  sudo dmesg | grep -E 'zcan|channel|ep_tx|TX ch'"
