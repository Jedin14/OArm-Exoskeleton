#!/bin/bash

set -e

sudo ln -sfn \
/dev/v4l/by-path/pci-0000:80:14.0-usb-0:9.1:1.0-video-index0 \
/dev/left_camera

sudo ln -sfn \
/dev/v4l/by-path/pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0 \
/dev/right_camera

sudo ln -sfn \
/dev/v4l/by-path/pci-0000:80:14.0-usb-0:3.1:1.3-video-index0 \
/dev/main_camera

echo ""
echo "Camera links created:"
ls -l /dev/left_camera
ls -l /dev/right_camera
ls -l /dev/main_camera
