# Camera Device Mapping

## Overview

The cameras and depth camera are identified using Linux's persistent `/dev/v4l/by-path` entries.

The Xitech stereo cameras report identical serial numbers, making serial-number-based identification unreliable. Instead, camera assignment is based on the physical USB connection path.

## Camera Assignments

| Camera            | Persistent Path                                                  |
| ----------------- | ---------------------------------------------------------------- |
| Left Camera       | `/dev/v4l/by-path/pci-0000:80:14.0-usb-0:9.1:1.0-video-index0`   |
| Right Camera      | `/dev/v4l/by-path/pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0` |
| Main Depth Camera | `/dev/v4l/by-path/pci-0000:80:14.0-usb-0:3.1:1.3-video-index0`   |

---

## Create Persistent Camera Names


Make the script executable:

```bash
chmod +x setup_cameras.sh
```

Run:

```bash
setup_cameras.sh
```

---

## Verify Links

```bash
ls -l /dev/left_camera
ls -l /dev/right_camera
ls -l /dev/main_camera
```

Expected:

```text
/dev/left_camera  -> /dev/v4l/by-path/pci-0000:80:14.0-usb-0:9.1:1.0-video-index0
/dev/right_camera -> /dev/v4l/by-path/pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0
/dev/main_camera  -> /dev/v4l/by-path/pci-0000:80:14.0-usb-0:3.1:1.3-video-index0
```

---

## ROS 2 Configuration

```yaml
left_camera:
  video_device: "/dev/left_camera"

right_camera:
  video_device: "/dev/right_camera"

main_camera:
  video_device: "/dev/main_camera"
```

---