import io
import json
import logging
import socket
import threading
import time
from functools import cached_property
import os
import glob
from collections import deque

import numpy as np
from lerobot.robots import RobotConfig, Robot
from lerobot.teleoperators import TeleoperatorConfig, Teleoperator

class RobotDeviceNotConnectedError(Exception): pass
class RobotDeviceAlreadyConnectedError(Exception): pass

logger = logging.getLogger(__name__)


def check_if_not_connected(func):
    def wrapper(self, *args, **kwargs):
        if not self.is_connected:
            raise RobotDeviceNotConnectedError(f"{self} is not connected.")
        return func(self, *args, **kwargs)
    return wrapper


def _get_usb_path_for_device(index_or_path):
    if isinstance(index_or_path, int) or (isinstance(index_or_path, str) and str(index_or_path).isdigit()):
        dev_path = f"/sys/class/video4linux/video{index_or_path}/device"
    else:
        name = os.path.basename(str(index_or_path))
        dev_path = f"/sys/class/video4linux/{name}/device"
        
    try:
        if os.path.exists(dev_path):
            return os.path.realpath(dev_path)
    except Exception:
        pass
    return None

def _find_device_by_usb_path(target_usb_path):
    if not target_usb_path:
        return None
    for sys_path in glob.glob("/sys/class/video4linux/video*/device"):
        try:
            if os.path.realpath(sys_path) == target_usb_path:
                name = os.path.basename(os.path.dirname(sys_path))
                return f"/dev/{name}"
        except Exception:
            continue
    return None


def check_if_already_connected(func):
    def wrapper(self, *args, **kwargs):
        if self.is_connected:
            raise RobotDeviceAlreadyConnectedError(f"{self} is already connected.")
        return func(self, *args, **kwargs)
    return wrapper


from dataclasses import dataclass, field

@dataclass(kw_only=True)
class OArm7DOFRosRobotConfig(RobotConfig):
    """Configuration for 7DOF-OArm running inside ROS 2 via UDP bridge."""
    type: str = "oarm7dof_ros"
    cameras: dict = field(default_factory=dict)
    arm_mode: str = "both"  # "left", "right", or "both"
    ros_camera_names: list = field(default_factory=list)
    # When False, observations are just the raw joint positions (8 per arm:
    # 7 joints + 1 gripper), matching action_features exactly. When True,
    # also includes derived end-effector pose (7 per arm) and normalized
    # gripper width (1 per arm) as extra observation dims.
    include_ee_pose: bool = True
    
    # We define joints depending on the arm mode
    @property
    def joint_names(self):
        names = []
        sides = ("left", "right") if self.arm_mode == "both" else (self.arm_mode,)
        for side in sides:
            for i in range(1, 8):
                names.append(f"openarm_{side}_joint{i}")
            names.append(f"openarm_{side}_finger_joint1")
        return names


class OArm7DOFRosRobot(Robot):
    """
    A Robot backend that listens to ROS 2 joint states and commands via a local UDP bridge.
    It passively reads the ROS 2 teleoperation stream instead of writing to hardware.
    """

    config_class = OArm7DOFRosRobotConfig
    name = "oarm7dof_ros"

    def __init__(self, config: OArm7DOFRosRobotConfig):
        super().__init__(config)
        self.config = config
        
        from lerobot.cameras.utils import make_cameras_from_configs
        self.cameras = make_cameras_from_configs(config.cameras)
        
        # 8 per arm (7 joints + 1 gripper)
        self.num_joints = len(self.config.joint_names) 

        self.udp_ip = "127.0.0.1"
        self.udp_port = 19092
        self.sock = None
        
        self._is_connected = False
        self._stop_event = threading.Event()
        self._listen_thread = None
        
        # Dictionaries to store the latest values
        self._latest_obs = {}
        self._latest_action = {}
        self._latest_obs_timestamp = 0.0
        self._action_timestamps = {}
        self._action_ros_timestamps = {}
        self._data_lock = threading.Lock()
        # get_action_at_sync_time() does a full linear scan of this deque on
        # every recording tick (30/s). The UDP bridge publishes at 100 Hz
        # (ros2_lelab_bridge.py), and sync_within_tolerance() already rejects
        # anything more than 50ms (~5 packets) out of alignment, so a 2s
        # window (200 packets) is generous margin -- maxlen=2000 (20s) meant
        # scanning up to 2000 entries x ~16 joints per call for no benefit,
        # measured at ~3.7ms/call, entirely lost from the 33ms/frame budget.
        self._action_history = deque(maxlen=200)
        # Joint-state history, so a dataset row can use the state sample that
        # matches when its camera frames were actually captured rather than the
        # newest one. 200 entries at the bridge's 100Hz is 2s of history; the
        # target is only ~30ms back, so this is generous.
        self._obs_history = deque(maxlen=200)
        # Latest true capture timestamp per camera (monotonic).
        self._camera_capture_ts: dict[str, float] = {}
        self._last_sync_timestamp = 0.0
        self._last_sync_diagnostics = {}
        # ROS header stamps arrive on the system clock (CLOCK_REALTIME) while
        # every timestamp we synchronize against is CLOCK_MONOTONIC. Both
        # advance at the same rate, so one offset converts between them.
        # CLOCK_MONOTONIC is system-wide on Linux, so this is valid across the
        # camera-bridge process boundary.
        self._realtime_to_monotonic = time.time() - time.monotonic()
        
        # Buffer for latest JPEG frames for web preview
        self._latest_frames = {}
        self._frames_lock = threading.Lock()
        self._camera_frozen = {}
        self._camera_usb_paths = {}

        # Camera auto-recovery watchdog. Tracks the monotonic time each camera
        # last produced a fresh frame so a background thread can transparently
        # reconnect a stalled camera without any user interaction. The manual
        # device-change endpoint is only a last resort for when auto-recovery
        # can't find the device (e.g. physically re-plugged elsewhere).
        self._camera_last_good = {}
        self._camera_last_recover_attempt = {}
        self._camera_stale_recover_s = 1.5   # freeze this long -> try recovery
        self._camera_recover_backoff_s = 2.0  # min gap between attempts
        self._ros_camera_names: list[str] = list(config.ros_camera_names)
        for cam in self._ros_camera_names:
            self._camera_frozen[cam] = True
            import numpy as np
            self._latest_obs[cam] = np.zeros((480, 640, 3), dtype=np.uint8)

        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread = None

        for name in self.config.joint_names:
            self._latest_obs[f"{name}.pos"] = 0.0
            self._latest_action[f"{name}.pos"] = 0.0
            
        for i in range(14):
            self._latest_obs[f"ee_pose_{i}.pos"] = 0.0
        for i in range(2):
            self._latest_obs[f"gripper_state_{i}.pos"] = 0.0
            
        self._latest_buttons = []

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        features = {f"{name}.pos": float for name in self.config.joint_names}

        # ee_pose/gripper_state are optional extra observation dims on top of
        # the raw joint positions above. Skip them when include_ee_pose is
        # False so observations match action_features 1:1 (8 dims per arm).
        if self.config.include_ee_pose:
            if self.config.arm_mode in ("left", "both"):
                for i in range(0, 7):
                    features[f"ee_pose_{i}.pos"] = float
                features["gripper_state_0.pos"] = float

            if self.config.arm_mode in ("right", "both"):
                for i in range(7, 14):
                    features[f"ee_pose_{i}.pos"] = float
                features["gripper_state_1.pos"] = float

        # Add cameras
        for cam_name in getattr(self, "_ros_camera_names", []):
            features[cam_name] = (480, 640, 3)
        for cam_key, cam in self.cameras.items():
            if cam_key not in features:
                features[cam_key] = (cam.config.height, cam.config.width, 3)
        return features

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{name}.pos": float for name in self.config.joint_names}

    @property
    def is_connected(self) -> bool:
        return self._is_connected and all(cam.is_connected for cam in self.cameras.values())

    def _udp_listen_loop(self):
        last_decoded_ts = {}
        while not self._stop_event.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
                payload = json.loads(data.decode('utf-8'))
                received_at = time.monotonic()
                observation_timestamp = float(payload.get("observation_timestamp", received_at))
                observation_ros_timestamp = float(payload.get("observation_ros_timestamp", 0.0))
                action_timestamps = payload.get("action_timestamps", {})
                action_ros_timestamps = payload.get("action_ros_timestamps", {})
                obs_payload = payload.get("observation", {})

                # Decode camera frames BEFORE taking _data_lock.  imdecode is
                # ~1ms per camera and the recorder contends for this same lock
                # every frame (get_action_at_sync_time / get_sync_diagnostics);
                # holding it across two JPEG decodes stalled the capture loop.
                decoded_cams: dict[str, tuple] = {}
                for cam_name, cam_data in payload.get("cameras", {}).items():
                    try:
                        frame_ts = float(cam_data["timestamp"])
                        if last_decoded_ts.get(cam_name) == frame_ts:
                            continue
                        last_decoded_ts[cam_name] = frame_ts

                        shm_path = f"/dev/shm/lelab_cameras/{cam_name}.jpg"
                        if not os.path.exists(shm_path):
                            continue
                        with open(shm_path, "rb") as f:
                            jpeg_bytes = f.read()

                        import cv2 as _cv2
                        arr = _cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), _cv2.IMREAD_COLOR)
                        if arr is None:
                            continue
                        frame_rgb = _cv2.cvtColor(arr, _cv2.COLOR_BGR2RGB)

                        # frame_ts is the camera bridge's ROS stamp: the real
                        # capture instant, on the system clock.  Convert it onto
                        # the monotonic clock everything else is synchronized
                        # against, so we can record true capture->row latency
                        # rather than merely when the packet reached us.
                        capture_monotonic = frame_ts - self._realtime_to_monotonic
                        decoded_cams[cam_name] = (frame_rgb, jpeg_bytes, capture_monotonic)
                        if capture_monotonic > 0:
                            self._camera_capture_ts[cam_name] = capture_monotonic
                    except Exception as _e:
                        logger.debug(f"Camera decode error for {cam_name}: {_e}")

                with self._data_lock:
                    # Parse the bridge's ordered joint_position array.
                    if "joint_position" in obs_payload:
                        jp = obs_payload["joint_position"]
                        if len(jp) == 16:
                            all_names = []
                            for side in ("left", "right"):
                                for i in range(1, 8):
                                    all_names.append(f"openarm_{side}_joint{i}")
                                all_names.append(f"openarm_{side}_finger_joint1")
                            for i, name in enumerate(all_names):
                                self._latest_obs[f"{name}.pos"] = jp[i]

                    if "ee_pose" in obs_payload:
                        for i, val in enumerate(obs_payload["ee_pose"]):
                            self._latest_obs[f"ee_pose_{i}.pos"] = float(val)

                    if "gripper_state" in obs_payload:
                        for i, val in enumerate(obs_payload["gripper_state"]):
                            self._latest_obs[f"gripper_state_{i}.pos"] = float(val)

                    for k, v in obs_payload.items():
                        if f"{k}.pos" in self._latest_obs:
                            self._latest_obs[f"{k}.pos"] = v
                    self._latest_obs_timestamp = observation_timestamp
                    self._last_sync_diagnostics["observation.ros_timestamp"] = observation_ros_timestamp
                    # Snapshot just the scalar joint/state values (not camera
                    # arrays) so get_observation() can pick the sample matching
                    # the camera capture instant.
                    self._obs_history.append(
                        (
                            observation_timestamp,
                            {k: v for k, v in self._latest_obs.items() if k.endswith(".pos")},
                        )
                    )

                    for k, v in payload.get("action", {}).items():
                        if f"{k}.pos" in self._latest_action:
                            key = f"{k}.pos"
                            self._latest_action[key] = v
                            self._action_timestamps[key] = float(action_timestamps.get(k, received_at))
                            self._action_ros_timestamps[key] = float(action_ros_timestamps.get(k, 0.0))
                    if self._latest_action:
                        self._action_history.append(
                            (received_at, self._latest_action.copy(), self._action_timestamps.copy())
                        )

                    # Publish the already-decoded frames (cheap: reference
                    # assignment only, no codec work inside the lock).
                    for cam_name, (frame_rgb, _jpeg, capture_monotonic) in decoded_cams.items():
                        self._latest_obs[cam_name] = frame_rgb
                        # True capture time, not arrival time.  Arrival hides
                        # pipeline latency entirely, which made the tolerance
                        # check and every recorded delta understate the real
                        # misalignment.  The freeze watchdog also reads this and
                        # is unaffected: its threshold is 1s, far beyond the
                        # ~28ms capture/arrival difference.
                        self._last_sync_diagnostics[f"camera.{cam_name}.timestamp"] = capture_monotonic

                    if "buttons" in payload:
                        self._latest_buttons = payload["buttons"]

                if decoded_cams:
                    with self._frames_lock:
                        for cam_name, (_rgb, jpeg_bytes, _ts) in decoded_cams.items():
                            self._latest_frames[cam_name] = jpeg_bytes

            except socket.timeout:
                pass
            except Exception as e:
                logger.debug(f"UDP read error: {e}")

            # Continuously monitor ROS cameras for freeze timeouts independent of get_observation
            now = time.monotonic()
            for cam_name in self._ros_camera_names:
                last_ts = self._last_sync_diagnostics.get(f"camera.{cam_name}.timestamp", 0.0)
                if last_ts == 0.0 or now - last_ts > 1.0:
                    self._camera_frozen[cam_name] = True
                else:
                    self._camera_frozen[cam_name] = False

    @check_if_already_connected
    def connect(self, calibrate: bool = False) -> None:
        """Connect to cameras and start UDP listener."""
        # Create a fresh socket each time so reconnect works after disconnect
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(0.001)
        
        try:
            self.sock.bind((self.udp_ip, self.udp_port))
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning(f"UDP port {self.udp_port} already in use, retrying with SO_REUSEPORT...")
                self.sock.close()
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (AttributeError, OSError):
                    pass  # SO_REUSEPORT not available on all platforms
                self.sock.settimeout(0.001)
                self.sock.bind((self.udp_ip, self.udp_port))
            else:
                raise
        
        self._stop_event.clear()
        self._listen_thread = threading.Thread(target=self._udp_listen_loop, daemon=True)
        self._listen_thread.start()
        
        now = time.monotonic()
        for cam_key, cam in self.cameras.items():
            cam.connect()
            # Record USB path upon successful connection
            if hasattr(cam, 'config') and hasattr(cam.config, 'index_or_path'):
                self._camera_usb_paths[cam_key] = _get_usb_path_for_device(cam.config.index_or_path)
            self._camera_last_good[cam_key] = now
            self._camera_last_recover_attempt[cam_key] = 0.0

        self._is_connected = True

        # Start the camera auto-recovery watchdog.
        self._watchdog_stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._camera_watchdog_loop, daemon=True, name="camera_watchdog"
        )
        self._watchdog_thread.start()

        logger.info(f"{self} connected via ROS 2 UDP Bridge.")

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        # Passively calibrated
        pass
        
    def configure(self) -> None:
        pass
        
    def _reconnect_one_camera(self, cam_key: str, cam) -> bool:
        """Reconnect a single camera. Caller MUST hold self._frames_lock.

        Re-resolves the device by its remembered USB path when frozen, so a
        camera that came back on a different /dev/video* node is recovered
        automatically. Returns True on successful reconnect.
        """
        logger.info(f"Reconnecting camera {cam_key}...")
        try:
            cam.disconnect()
        except Exception as e:
            logger.debug(f"Disconnect error during reconnect for {cam_key}: {e}")

        # Attempt to auto-recover device path if frozen or failed
        if self._camera_frozen.get(cam_key, False):
            old_path = self._camera_usb_paths.get(cam_key)
            if old_path:
                new_dev = _find_device_by_usb_path(old_path)
                if new_dev and hasattr(cam, 'config'):
                    logger.info(f"Auto-recovered {cam_key} device to {new_dev}")
                    cam.config.index_or_path = new_dev

        try:
            cam.connect()
            self._camera_frozen[cam_key] = False
            self._camera_last_good[cam_key] = time.monotonic()
            # Update USB path just in case
            if hasattr(cam, 'config') and hasattr(cam.config, 'index_or_path'):
                self._camera_usb_paths[cam_key] = _get_usb_path_for_device(cam.config.index_or_path)
            return True
        except Exception as e:
            logger.error(f"Failed to reconnect camera {cam_key}: {e}")
            return False

    def reconnect_cameras(self) -> None:
        """Disconnect and reconnect all cameras to recover from hardware freezes."""
        with self._frames_lock:
            for cam_key, cam in self.cameras.items():
                self._reconnect_one_camera(cam_key, cam)

    def _camera_watchdog_loop(self) -> None:
        """Background thread: transparently reconnect any camera that has been
        stalled longer than the stale threshold, so a transient USB glitch
        self-heals without the user touching the UI."""
        while not self._watchdog_stop_event.wait(0.5):
            if not self._is_connected:
                continue
            now = time.monotonic()
            for cam_key, cam in list(self.cameras.items()):
                last_good = self._camera_last_good.get(cam_key, now)
                if (now - last_good) < self._camera_stale_recover_s:
                    continue
                # Camera looks stalled. Rate-limit recovery attempts.
                last_attempt = self._camera_last_recover_attempt.get(cam_key, 0.0)
                if (now - last_attempt) < self._camera_recover_backoff_s:
                    continue
                self._camera_last_recover_attempt[cam_key] = now
                logger.warning(
                    f"Camera {cam_key} stalled for {now - last_good:.1f}s — "
                    f"auto-recovering..."
                )
                with self._frames_lock:
                    if self._reconnect_one_camera(cam_key, cam):
                        logger.info(f"Camera {cam_key} auto-recovered.")
        
    @check_if_not_connected
    def get_observation(self):
        obs = self._latest_obs.copy()
        # All software timestamps used for pairing are on the host's
        # monotonic clock.  Do not use the time at which the recorder happens
        # to inspect a frame: camera backends keep the capture/read timestamp
        # of the frame in ``latest_timestamp``.
        sync_timestamp = time.monotonic()
        
        if self._ros_camera_names:
            # ROS mode: frames already decoded and stored in _latest_obs by UDP thread
            for cam_name in self._ros_camera_names:
                if cam_name in self._latest_obs and isinstance(self._latest_obs[cam_name], np.ndarray):
                    obs[cam_name] = self._latest_obs[cam_name]
        else:
            with self._camera_lock:
                # Append USB camera frames to the observation dict
                for cam_key, cam in self.cameras.items():
                    try:
                        frame_timestamp = self._last_sync_diagnostics.get(
                            f"camera.{cam_key}.timestamp", time.monotonic()
                        )
                        if self._camera_frozen.get(cam_key, False):
                            logger.debug(f"Skipping read for frozen camera {cam_key}")
                            frame = self._latest_obs.get(cam_key, None)
                            if frame is None:
                                raise RuntimeError("No initial frame exists for frozen camera")
                        else:
                            # Newer LeRobot camera backends expose async_read(); older
                            # local builds expose read_latest().  In either case record
                            # the local monotonic acquisition time for synchronization.
                            if hasattr(cam, "read_latest"):
                                frame = cam.read_latest(max_age_ms=2000)
                            else:
                                frame = cam.async_read(timeout_ms=2000)
                        frame_timestamp = time.monotonic()
                        self._camera_frozen[cam_key] = False
                        self._camera_last_good[cam_key] = time.monotonic()
                    except (TimeoutError, RuntimeError) as e:
                        logger.warning(f"Failed to read from {cam_key}, reusing previous frame. Error: {e}")
                        self._camera_frozen[cam_key] = True
                        frame = self._latest_obs.get(cam_key, None)
                        if frame is None:
                            # If we don't even have a first frame, we have to crash
                            raise
                    
                    obs[cam_key] = frame
                    self._latest_obs[cam_key] = frame
                    self._last_sync_diagnostics[f"camera.{cam_key}.timestamp"] = frame_timestamp
                    
                    # Encode frame as JPEG for the web preview endpoint
                    if frame is not None:
                        try:
                            import cv2
                            # LeRobot cameras output RGB, but cv2.imencode expects BGR.
                            if isinstance(frame, np.ndarray):
                                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                                _, jpeg_buf = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
                                with self._frames_lock:
                                    self._latest_frames[cam_key] = jpeg_buf.tobytes()
                        except Exception as e:
                            logger.debug(f"Failed to encode frame for {cam_key}: {e}")
            
        with self._data_lock:
            # Align every modality to the instant the IMAGES were captured,
            # rather than to "now".
            #
            # Cameras are the slowest and coarsest modality: at an arbitrary
            # query moment the newest available frame is already half a frame
            # period old on average, plus pipeline delay (~28ms total, measured).
            # Picking a "nearer" frame is impossible -- the newest frame is by
            # definition the nearest to now -- so that lag cannot be removed.
            # What it *can* be made to do is stop mismatching the other
            # modalities: state and action arrive at 100Hz, so we choose the
            # samples that correspond to when the images were actually taken.
            # Otherwise every row pairs a ~28ms-old image with a brand-new
            # state/action, teaching the policy a 28ms-offset mapping with
            # ~10ms of jitter on top.
            capture_ts = [v for v in self._camera_capture_ts.values() if v > 0.0]
            if capture_ts:
                # Mean, not max: the two cameras free-run at slightly different
                # rates and cannot be genlocked, so they are captured at
                # different phases. The mean minimizes total misalignment
                # across both images.
                sync_timestamp = sum(capture_ts) / len(capture_ts)

                # Use the state sample nearest that instant instead of the newest.
                if self._obs_history:
                    best_ts, best_state = min(
                        self._obs_history, key=lambda item: abs(item[0] - sync_timestamp)
                    )
                    obs.update(best_state)
                    self._last_sync_diagnostics["observation.timestamp"] = best_ts
                else:
                    self._last_sync_diagnostics["observation.timestamp"] = self._latest_obs_timestamp
            else:
                # No camera capture times yet (startup): keep prior behaviour.
                sync_timestamp = max([sync_timestamp, self._latest_obs_timestamp])
                self._last_sync_diagnostics["observation.timestamp"] = self._latest_obs_timestamp

            # get_action_at_sync_time() selects per-joint commands nearest this
            # target, so the action lands on the same instant as the images.
            self._last_sync_timestamp = sync_timestamp
            self._last_sync_diagnostics["sync.timestamp"] = sync_timestamp
        return obs

    def get_latest_frame_jpeg(self, cam_key: str) -> tuple[bytes | None, bool]:
        """Return a tuple (jpeg_bytes, is_frozen) for a given camera key."""
        with self._frames_lock:
            return self._latest_frames.get(cam_key), self._camera_frozen.get(cam_key, False)

    def get_joint_positions(self) -> dict[str, float]:
        """Return a clean dict of joint_name -> position (without .pos suffix)."""
        positions = {}
        for key, val in self._latest_obs.items():
            if key.endswith(".pos"):
                clean_name = key[:-4]  # strip ".pos"
                positions[clean_name] = round(val, 4)
        return positions

    def get_action_positions(self) -> dict[str, float]:
        """Return a clean dict of joint_name -> commanded action (without .pos suffix)."""
        positions = {}
        for key, val in self._latest_action.items():
            if key.endswith(".pos"):
                clean_name = key[:-4]
                positions[clean_name] = round(val, 4)
        return positions

    def get_action_at_sync_time(self) -> dict[str, float]:
        """Return the timestamp-nearest action for the observation row.

        Per-joint timestamps prevent one arm's newly arrived command from being
        paired with another arm's older command. Nearest-sample selection keeps
        action/camera timing error bounded in the 30 Hz recorder.
        """
        with self._data_lock:
            target = self._last_sync_timestamp or time.monotonic()
            selected = {}
            selected_ts = {}
            for _received_at, values, timestamps in self._action_history:
                for key, value in values.items():
                    ts = timestamps.get(key, _received_at)
                    if key not in selected_ts or abs(ts - target) < abs(selected_ts[key] - target):
                        selected[key] = value
                        selected_ts[key] = ts
            if not selected:
                selected = self._latest_action.copy()
            self._last_sync_diagnostics["action.timestamp"] = max(selected_ts.values(), default=0.0)
            self._last_sync_diagnostics["action_age_ms"] = (
                min(abs(target - ts) for ts in selected_ts.values()) * 1000.0
                if selected_ts else None
            )
            if selected_ts:
                self._last_sync_diagnostics["max_action_delta_ms"] = max(
                    abs(target - ts) * 1000.0 for ts in selected_ts.values()
                )
            else:
                self._last_sync_diagnostics["max_action_delta_ms"] = float("inf")
            return selected

    def get_sync_diagnostics(self) -> dict:
        with self._data_lock:
            diagnostics = dict(self._last_sync_diagnostics)
            camera_ts = [
                value for key, value in diagnostics.items()
                if key.startswith("camera.") and key.endswith(".timestamp")
            ]
            diagnostics["camera.timestamp"] = max(camera_ts, default=0.0)
            diagnostics["camera.min_timestamp"] = min(camera_ts, default=0.0)
            diagnostics["camera.max_timestamp"] = max(camera_ts, default=0.0)
            action_ros = [
                value for value in self._action_ros_timestamps.values() if value > 0.0
            ]
            diagnostics["action.ros_timestamp"] = max(action_ros, default=0.0)
            return diagnostics

    def sync_within_tolerance(self, tolerance_s: float = 0.050) -> bool:
        """Return whether the current row can safely enter the dataset."""
        diagnostics = self.get_sync_diagnostics()
        target = float(diagnostics.get("sync.timestamp", 0.0))
        if not target:
            return False
        state_delta = abs(target - float(diagnostics.get("observation.timestamp", target)))
        camera_deltas = [
            abs(target - value) for key, value in diagnostics.items()
            if key.startswith("camera.") and key.endswith(".timestamp")
        ]
        action_delta = float(diagnostics.get("max_action_delta_ms", 0.0)) / 1000.0
        return max([state_delta, action_delta, *camera_deltas]) <= tolerance_s

    @property
    def latest_buttons(self) -> list[int]:
        return self._latest_buttons

    @check_if_not_connected
    def send_action(self, action) -> dict:
        """
        Passive robot: we DO NOT send the action to the hardware.
        Instead, we return the action that the ROS 2 teleop node has ALREADY commanded,
        which we captured via UDP. This ensures the dataset records the true commanded action
        without causing bus contention.
        """
        # We just return the latest action captured from ROS 2
        return self._latest_action.copy()

    @check_if_not_connected
    def disconnect(self):
        self._stop_event.set()
        if self._listen_thread:
            self._listen_thread.join(timeout=1.0)

        # Stop the camera watchdog before tearing down cameras so it can't
        # try to reconnect a camera we're in the middle of disconnecting.
        self._watchdog_stop_event.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=2.0)
            self._watchdog_thread = None

        if self.sock:
            self.sock.close()
            self.sock = None

        for cam in self.cameras.values():
            cam.disconnect()

        self._is_connected = False
        logger.info(f"{self} disconnected.")

@dataclass(kw_only=True)
class PassiveROSTeleopConfig(TeleoperatorConfig):
    type: str = "passive_ros"

class PassiveROSTeleop(Teleoperator):
    """A dummy teleoperator that returns actions passively captured by OArm7DOFRosRobot."""
    config_class = PassiveROSTeleopConfig
    name = "passive_ros"

    def __init__(self, config: PassiveROSTeleopConfig):
        super().__init__(config)
        self.config = config
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_calibrated(self) -> bool:
        return True

    @property
    def action_features(self) -> dict:
        return {}
        
    @property
    def feedback_features(self) -> dict:
        return {}

    def connect(self, calibrate: bool = True):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def calibrate(self):
        pass

    def configure(self):
        pass

    def get_action(self):
        from lelab.record import active_robot
        if active_robot is not None and hasattr(active_robot, 'send_action'):
            if hasattr(active_robot, 'get_action_at_sync_time'):
                return active_robot.get_action_at_sync_time()
            return active_robot.send_action(None)
        return {}

    def send_feedback(self, obs):
        pass

# Register the configs
TeleoperatorConfig.register_subclass("passive_ros")(PassiveROSTeleopConfig)
RobotConfig.register_subclass("oarm7dof_ros")(OArm7DOFRosRobotConfig)
