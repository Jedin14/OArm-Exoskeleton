#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo ./capture_official.sh"
  exit 1
fi

echo "[*] Ensuring usbmon is loaded..."
modprobe usbmon

# Find the bus number of the Waveshare device
BUS_NUM=$(lsusb -d 04d8:0053 | awk '{print $2}' | sed 's/^0*//')
if [ -z "$BUS_NUM" ]; then
  echo "Waveshare device not found! Is it plugged in?"
  exit 1
fi
echo "[*] Waveshare device found on Bus $BUS_NUM"

echo "[*] Unloading kernel driver to free the device..."
rmmod zcan_usb 2>/dev/null || true
ip link set can0 down 2>/dev/null || true
ip link set can1 down 2>/dev/null || true
sleep 1

# Start tcpdump in the background
echo "[*] Starting packet capture on usbmon${BUS_NUM}..."
if ! command -v tcpdump >/dev/null; then
  apt-get update && apt-get install -y tcpdump
fi
tcpdump -i "usbmon${BUS_NUM}" -w /tmp/waveshare_can1.pcap >/dev/null 2>&1 &
TCPDUMP_PID=$!
sleep 1

echo "[*] Running the official Waveshare Python SDK to transmit on CAN2..."
cd /tmp/USB-CAN-FD-B-Linux/VMware/x86-python3
# The script is modified to ONLY transmit on CAN1 (dev_ch2)
python3 python3.8.0.py || echo "Python script encountered an error, but continuing..."

echo "[*] Stopping packet capture..."
kill $TCPDUMP_PID || true
sleep 1

echo "[*] Extracting CAN1 TX payloads using tshark..."
if ! command -v tshark >/dev/null; then
  echo "Installing tshark..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y tshark >/dev/null 2>&1 || true
fi

# Print all outgoing USB bulk transfers (URB_BULK OUT) with payload
echo "=========================================================="
echo "    CAPTURED USB PAYLOADS (Sent by Official SDK)"
echo "=========================================================="
tshark -r /tmp/waveshare_can1.pcap -Y "usb.transfer_type == 0x03 && usb.endpoint_address.direction == 0" -T fields -e usb.capdata | grep -v "^$" | head -n 10
echo "=========================================================="

echo "[*] Reloading the kernel driver..."
modprobe zcan_usb || insmod /usr/src/zcan_usb-0.5.1/zcan_usb.ko
echo "[*] Done! Please copy the output above to the AI."
