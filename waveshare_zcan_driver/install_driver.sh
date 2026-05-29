#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run with sudo: sudo ./install_driver.sh"
  exit 1
fi

DRIVER_VERSION="0.5.3"
DKMS_DIR="/usr/src/zcan_usb-${DRIVER_VERSION}"

echo "[*] Copying driver files to DKMS source directory..."
mkdir -p "${DKMS_DIR}"
cp zcan_usb.c Makefile dkms.conf "${DKMS_DIR}/"

echo "[*] Building and installing zcan_usb via DKMS..."
# Remove any existing DKMS installation of this version
dkms remove -m zcan_usb -v "${DRIVER_VERSION}" --all 2>/dev/null || true

# Add, build, and install
dkms add -m zcan_usb -v "${DRIVER_VERSION}"
dkms build -m zcan_usb -v "${DRIVER_VERSION}"
dkms install -m zcan_usb -v "${DRIVER_VERSION}"

echo "[*] Loading module..."
rmmod zcan_usb 2>/dev/null || true
modprobe zcan_usb

echo "[*] Done! The dual-channel zcan_usb driver is successfully installed."
