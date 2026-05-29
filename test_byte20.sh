#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo ./test_byte20.sh"
  exit 1
fi

rmmod zcan_usb || true
modprobe zcan_usb || insmod /usr/src/zcan_usb-0.5.1/zcan_usb.ko
sleep 1

ip link set can1 type can bitrate 1000000 && ip link set can1 up
sleep 1

echo "[*] Setting hardware routing variables..."
echo 2 > /sys/module/zcan_usb/parameters/ch1_test_ep
echo 20 > /sys/module/zcan_usb/parameters/ch1_test_byte
echo 1 > /sys/module/zcan_usb/parameters/ch1_test_val

echo "[*] Testing Byte[20] = 1 on EP2 OUT"
for i in {1..10}; do
  cansend can1 001#FFFFFFFFFFFFFFFC
  sleep 0.05
done
echo "Done!"
