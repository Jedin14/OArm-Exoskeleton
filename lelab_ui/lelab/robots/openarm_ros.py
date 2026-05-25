import io
import json
import logging
import socket
import threading
import time
from functools import cached_property

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


def check_if_already_connected(func):
    def wrapper(self, *args, **kwargs):
        if self.is_connected:
            raise RobotDeviceAlreadyConnectedError(f"{self} is already connected.")
        return func(self, *args, **kwargs)
    return wrapper


from dataclasses import dataclass, field

@dataclass(kw_only=True)
class OpenArmRosRobotConfig(RobotConfig):
    """Configuration for OpenArm running inside ROS 2 via UDP bridge."""
    type: str = "openarm_ros"
    cameras: dict = field(default_factory=dict)
    
    # We define all 14 joints + 2 grippers
    @property
    def joint_names(self):
        names = []
        for side in ("left", "right"):
            for i in range(1, 8):
                names.append(f"openarm_{side}_joint{i}")
            names.append(f"openarm_{side}_finger_joint1")
        return names


class OpenArmRosRobot(Robot):
    """
    A Robot backend that listens to ROS 2 joint states and commands via a local UDP bridge.
    It passively reads the ROS 2 teleoperation stream instead of writing to hardware.
    """

    config_class = OpenArmRosRobotConfig
    name = "openarm_ros"

    def __init__(self, config: OpenArmRosRobotConfig):
        super().__init__(config)
        self.config = config
        
        from lerobot.cameras.utils import make_cameras_from_configs
        self.cameras = make_cameras_from_configs(config.cameras)
        
        # 14 joints + 2 grippers = 16
        self.num_joints = 16 

        self.udp_ip = "127.0.0.1"
        self.udp_port = 19092
        self.sock = None
        
        self._is_connected = False
        self._stop_event = threading.Event()
        self._listen_thread = None
        
        # Dictionaries to store the latest values
        self._latest_obs = {}
        self._latest_action = {}
        
        # Buffer for latest JPEG frames for web preview
        self._latest_frames = {}
        self._frames_lock = threading.Lock()

        for name in self.config.joint_names:
            self._latest_obs[f"{name}.pos"] = 0.0
            self._latest_action[f"{name}.pos"] = 0.0

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        features = {f"{name}.pos": float for name in self.config.joint_names}
        # Add cameras
        for cam_key, cam in self.cameras.items():
            features[cam_key] = (cam.config.height, cam.config.width, 3)
        return features

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{name}.pos": float for name in self.config.joint_names}

    @property
    def is_connected(self) -> bool:
        return self._is_connected and all(cam.is_connected for cam in self.cameras.values())

    def _udp_listen_loop(self):
        while not self._stop_event.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
                payload = json.loads(data.decode('utf-8'))
                
                # Update observation
                for k, v in payload.get("observation", {}).items():
                    if f"{k}.pos" in self._latest_obs:
                        self._latest_obs[f"{k}.pos"] = v
                        
                # Update action
                for k, v in payload.get("action", {}).items():
                    if f"{k}.pos" in self._latest_action:
                        self._latest_action[f"{k}.pos"] = v
                        
            except socket.timeout:
                pass
            except Exception as e:
                logger.debug(f"UDP read error: {e}")

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
        
        for cam in self.cameras.values():
            cam.connect()
            
        self._is_connected = True
        logger.info(f"{self} connected via ROS 2 UDP Bridge.")

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        # Passively calibrated
        pass
        
    def configure(self) -> None:
        pass
        
    @check_if_not_connected
    def get_observation(self):
        obs = self._latest_obs.copy()
        
        # Capture images from cameras and store latest frames for preview
        for cam_key, cam in self.cameras.items():
            frame = cam.read_latest()
            obs[cam_key] = frame
            
            # Encode frame as JPEG for the web preview endpoint
            if frame is not None:
                try:
                    import cv2
                    # frame is a numpy array (H, W, 3) BGR from OpenCV
                    if isinstance(frame, np.ndarray):
                        _, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        with self._frames_lock:
                            self._latest_frames[cam_key] = jpeg_buf.tobytes()
                except Exception as e:
                    logger.debug(f"Failed to encode frame for {cam_key}: {e}")
            
        return obs

    def get_latest_frame_jpeg(self, cam_key: str) -> bytes | None:
        """Return the latest JPEG-encoded frame for a given camera key, or None."""
        with self._frames_lock:
            return self._latest_frames.get(cam_key)

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
    """A dummy teleoperator that returns actions passively captured by OpenArmRosRobot."""
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
            return active_robot.send_action(None)
        return {}

    def send_feedback(self, obs):
        pass

# Register the configs
TeleoperatorConfig.register_subclass("passive_ros")(PassiveROSTeleopConfig)
RobotConfig.register_subclass("openarm_ros")(OpenArmRosRobotConfig)
