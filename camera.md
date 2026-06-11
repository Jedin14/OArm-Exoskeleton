# Persistent Left/Right Camera Mapping with udev

This configuration creates stable device names for stereo cameras so that the left and right cameras remain correctly identified across reboots and USB re-enumeration.

## Camera Mapping

| Camera       | Persistent USB Path                                              |
| ------------ | ---------------------------------------------------------------- |
| Left Camera  | `/dev/v4l/by-path/pci-0000:80:14.0-usb-0:9.1:1.0-video-index0`   |
| Right Camera | `/dev/v4l/by-path/pci-0000:80:14.0-usb-0:2.1.1:1.0-video-index0` |

---

## Create udev Rules

Create a new udev rules file:

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
```

---

## Reload udev Rules

Apply the new rules without rebooting:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## Verify Symbolic Links

Check that the symbolic links have been created successfully:

```bash
ls -l /dev/left_camera
ls -l /dev/right_camera
```

Expected output:

```text
/dev/left_camera  -> videoX
/dev/right_camera -> videoY
```

The underlying `videoX` and `videoY` numbers may change after reboot, but the symbolic links will always point to the correct physical cameras.

---

## ROS 2 Usage

Use the persistent device names in launch files or parameter files:

```yaml
left_camera:
  video_device: "/dev/left_camera"

right_camera:
  video_device: "/dev/right_camera"
```

This ensures that the stereo pipeline always receives images from the correct physical camera regardless of device enumeration order.
