#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo ./test_isolate.sh"
  exit 1
fi

echo "[*] Setting hardware routing variables..."
echo 2 > /sys/module/zcan_usb/parameters/ch1_test_ep
echo 20 > /sys/module/zcan_usb/parameters/ch1_test_byte
echo 2 > /sys/module/zcan_usb/parameters/ch1_test_val

echo "[*] Testing Byte[20] = 2 on EP2 OUT (attempting to isolate CAN2)"
for i in {1..10}; do
  cansend can1 001#FFFFFFFFFFFFFFFC
  sleep 0.05
done
echo "Done!"
