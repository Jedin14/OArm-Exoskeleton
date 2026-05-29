#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo ./test_can2.sh"
  exit 1
fi

echo "[*] Reloading ZCAN driver with dynamic module parameters..."
rmmod zcan_usb || true
cp /usr/src/zcan_usb-0.5.1/zcan_usb.ko /lib/modules/$(uname -r)/updates/dkms/zcan_usb.ko
depmod -a
insmod /usr/src/zcan_usb-0.5.1/zcan_usb.ko
sleep 1

ip link set can0 type can bitrate 1000000 && ip link set can0 up
ip link set can1 type can bitrate 1000000 && ip link set can1 up
sleep 1

echo ""
echo "=========================================================="
echo "    STARTING DYNAMIC HARDWARE BRUTE-FORCE TEST"
echo "=========================================================="
echo "Watch the CAN2 LED on the Waveshare board!"
echo ""

# Test endpoint 1 and 2
for EP in 1 2; do
  echo "$EP" > /sys/module/zcan_usb/parameters/ch1_test_ep
  
  # Test bytes 2 through 7 (the reserved bytes in the CAN payload)
  for BYTE_IDX in 2 3 4 5 6 7 21; do
    echo "$BYTE_IDX" > /sys/module/zcan_usb/parameters/ch1_test_byte
    echo "1" > /sys/module/zcan_usb/parameters/ch1_test_val
    
    echo "----------------------------------------------------------"
    echo "Testing: EP=$EP OUT | Byte[$BYTE_IDX] = 1"
    
    # Send a burst of 10 frames to make the LED blink noticeably
    for i in {1..10}; do
      cansend can1 001#FFFFFFFFFFFFFFFC
      sleep 0.05
    done
    
    read -p "Did CAN2 blink? (y/n, or type '1' if CAN1 blinked): " resp
    if [[ "$resp" == "y" || "$resp" == "Y" || "$resp" == "yes" ]]; then
      echo ""
      echo "✅ SUCCESS! CAN2 requires EP=$EP OUT and Byte[$BYTE_IDX]=1"
      echo "We finally found the hardware mapping!"
      exit 0
    elif [[ "$resp" == "1" ]]; then
      echo "  (CAN1 blinked, so firmware still routed it to port 1)"
    else
      echo "  (No/ignored)"
    fi
  done
done

echo ""
echo "❌ Test complete. No combination triggered CAN2."
