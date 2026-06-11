# Camera Device Mapping

This document describes the persistent camera device naming configuration using udev rules. The goal is to ensure that camera device names remain consistent across reboots and USB re-enumeration.

## Camera Assignments

| Camera            | Persistent Device   | USB Path                                        |
| ----------------- | ------------------- | ----------------------------------------------- |
| Left Camera       | `/dev/left_camera`  | `pci-0000:80:14.0-usb-0:9.1:1.0-video-index0`   |
| Right Camera      | `/dev/right_camera` | `pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0` |
| Main Depth Camera | `/dev/main_camera`  | `pci-0000:80:14.0-usb-0:3.1:1.3-video-index0`   |

---

## Create udev Rules

Create the udev rules file:

```bash
sudo nano /etc/udev/rules.d/99-stereo-cameras.rules
```

Add the following contents:

```udev
# Left Camera
SUBSYSTEM=="video4linux", KERNEL=="video*", \
ENV{ID_PATH}=="pci-0000:80:14.0-usb-0:9.1:1.0", \
ATTR{index}=="0", \
SYMLINK+="left_camera"

# Right Camera
SUBSYSTEM=="video4linux", KERNEL=="video*", \
ENV{ID_PATH}=="pci-0000:80:14.0-usb-0:2.1.1:1.0", \
ATTR{index}=="0", \
SYMLINK+="right_camera"

# Main Depth Camera
SUBSYSTEM=="video4linux", KERNEL=="video*", \
ENV{ID_PATH}=="pci-0000:80:14.0-usb-0:3.1:1.3", \
ATTR{index}=="0", \
SYMLINK+="main_camera"
```

---

## Apply Rules

Reload udev and trigger the rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## Verify Device Links

Verify that the symbolic links have been created correctly:

```bash
ls -l /dev/left_camera
ls -l /dev/right_camera
ls -l /dev/main_camera
```

Expected output:

```text
/dev/left_camera  -> videoX
/dev/right_camera -> videoY
/dev/main_camera  -> videoZ
```

The underlying `videoX`, `videoY`, and `videoZ` numbers may change between boots, but the symbolic links will always point to the correct physical devices.

---

## ROS 2 Configuration

Example ROS 2 camera configuration:

```yaml
left_camera:
  video_device: "/dev/left_camera"

right_camera:
  video_device: "/dev/right_camera"

main_camera:
  video_device: "/dev/main_camera"
```

This allows ROS 2 nodes to access cameras using stable device names independent of Linux video device numbering.
