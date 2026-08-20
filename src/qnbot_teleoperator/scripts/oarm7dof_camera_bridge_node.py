#!/usr/bin/env python3
"""
oarm7dof_camera_bridge_node.py

Publishes each USB camera listed in ~/.config/lelab/ros_camera_mappings.json
as a sensor_msgs/CompressedImage ROS 2 topic, stamped with the ROS clock so
it shares the same time reference as /joint_states.

Topics published:
  /camera/{name}/image_raw/compressed   (sensor_msgs/CompressedImage)

Each camera runs in its own capture thread. The node also writes a per-camera
FPS heartbeat to /tmp/lelab_camera_status.json every second so the backend
can report live status to the UI.

Start/stop is managed by the LeLab backend via subprocess.
"""

import base64
import json
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import CompressedImage

MAPPINGS_PATH = Path.home() / ".config" / "lelab" / "ros_camera_mappings.json"
STATUS_PATH = Path("/tmp/lelab_camera_status.json")


def load_mappings():
    if not MAPPINGS_PATH.is_file():
        return []
    try:
        return json.loads(MAPPINGS_PATH.read_text())
    except Exception:
        return []


class CameraPublisherNode(Node):
    def __init__(self):
        super().__init__("oarm7dof_camera_bridge")
        self._threads = []
        self._stop_event = threading.Event()
        self._fps_counters = {}  # name -> deque of timestamps
        self._fps_lock = threading.Lock()

        mappings = load_mappings()
        if not mappings:
            self.get_logger().warning(
                f"No camera mappings found at {MAPPINGS_PATH}. "
                "Run Camera Setup in the UI to attach cameras."
            )

        for cam in mappings:
            name = cam["name"]
            index_raw = cam["device_index"]
            if isinstance(index_raw, str) and not index_raw.isdigit():
                index = index_raw
            else:
                index = int(index_raw)
            width = int(cam.get("width", 640))
            height = int(cam.get("height", 480))
            fps = int(cam.get("fps", 30))

            device_str = index if isinstance(index, str) else f"/dev/video{index}"
            topic = f"/camera/{name}/image_raw/compressed"
            pub = self.create_publisher(CompressedImage, topic, 10)
            self.get_logger().info(
                f"  Camera '{name}' -> {device_str} -> {topic}"
            )

            with self._fps_lock:
                self._fps_counters[name] = []

            t = threading.Thread(
                target=self._capture_loop,
                args=(name, index, width, height, fps, pub),
                daemon=True,
                name=f"cam_{name}",
            )
            self._threads.append(t)

        # FPS status writer — every second
        self._status_timer = self.create_timer(1.0, self._write_status)

        for t in self._threads:
            t.start()

        self.get_logger().info(
            f"7DOF-OArm Camera Bridge started with {len(self._threads)} camera(s)."
        )

    def _capture_loop(self, name, index, width, height, target_fps, pub):
        """Tight capture loop for one camera. Runs in its own thread."""
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        # We removed BUFFERSIZE=1 because it cripples V4L2 performance on the IMX335

        if not cap.isOpened():
            self.get_logger().error(
                f"Camera '{name}' (index {index}) could not be opened!"
            )
            return

        # Force disable dynamic framerate (often enabled by default on IMX cameras in low light)
        # to ensure the camera hardware strictly adheres to the target FPS.
        import subprocess
        device_str = index if isinstance(index, str) else f"/dev/video{index}"
        subprocess.run(
            ["v4l2-ctl", "-d", device_str, "-c", "exposure_dynamic_framerate=0"],
            stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        )

        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 90]

        while not self._stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.001)
                continue

            stamp = self.get_clock().now().to_msg()

            ok, buf = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            msg = CompressedImage()
            msg.header.stamp = stamp
            msg.header.frame_id = name
            msg.format = "jpeg"
            msg.data = buf.tobytes()
            pub.publish(msg)

            # FPS tracking (keep a 2-second sliding window)
            now = time.monotonic()
            with self._fps_lock:
                self._fps_counters[name].append(now)
                cutoff = now - 2.0
                self._fps_counters[name] = [
                    t for t in self._fps_counters[name] if t > cutoff
                ]

        cap.release()
        self.get_logger().info(f"Camera '{name}' capture thread stopped.")

    def _write_status(self):
        """Write per-camera FPS to /tmp/lelab_camera_status.json."""
        with self._fps_lock:
            status = {}
            for name, ts_list in self._fps_counters.items():
                fps = len(ts_list) / 2.0  # frames in last 2 seconds / 2
                status[name] = {"fps": round(fps, 1), "ok": fps >= 28.0}
        try:
            STATUS_PATH.write_text(json.dumps(status))
        except Exception:
            pass

    def destroy_node(self):
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=2.0)
        # Clear status on exit
        try:
            STATUS_PATH.write_text("{}")
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraPublisherNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
