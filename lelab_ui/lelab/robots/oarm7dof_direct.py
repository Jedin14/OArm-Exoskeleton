#!/usr/bin/env python3
"""
7DOF-OArm robot backend that records through the SAME path deployment uses.

WHY
---
The recording and deployment paths currently share nothing:

                     recording (training data)            deployment
    camera    V4L2 -> JPEG encode -> ROS CompressedImage  cv2.VideoCapture
              -> UDP -> JPEG decode -> dataset            (deploy_act_policy.py:59)
    state     ros2_control reads CAN -> /joint_states      openarm_can direct
              -> UDP -> dataset                           (deploy_act_policy.py:84)

deploy_act_policy.py kills the whole ROS stack and talks to hardware itself, so a
policy trains on JPEG-degraded, multi-hop-delayed frames and then runs on fresh
direct ones. This backend removes that gap: observations come straight off the
sensors, exactly as at deploy time.

WHAT STAYS ON ROS, AND WHY
--------------------------
`action` still arrives over the ROS/WebSocket path, unchanged. Two reasons:

  1. It is the correct target. ACT (leader arm positions), LeRobot
     (`robot.send_action()`), pi0/pi0.5 and GR00T N1/N1.5 all define action as
     the COMMAND, not the measured state. Recording measured position as the
     action lets a policy minimise loss by echoing the state, and at deploy you
     then command "stay where you are".
  2. The exoskeleton is teleoperated over the WebSocket bridge, so that path has
     to exist regardless.

So only OBSERVATION moves off ROS. Verified live against hardware: joints 1-7
decode from CAN to within 0.0004 rad of /joint_states (the 16-bit quantisation
step) at 203 Hz per motor.

UNITS
-----
Both `action` and `observation.state` carry the gripper as LINEAR APERTURE IN
METRES, matching what /joint_states reports.

The retargeting node publishes a normalised 0..1 trigger, so the conversion is
done once, in the node that owns the scale factor: exoskeleton_bridge_node
computes `norm * gripper_scaling_factor` (clamped) to drive the hardware and
publishes that same value on /exo/gripper_command_m, which ros2_lelab_bridge
records as the action. The recorded action is therefore literally the aperture
commanded, and nothing here duplicates the scale. Mixing normalised and metric
values in one channel gives a policy two different encodings of "open".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

from .oarm7dof_ros import OArm7DOFRosRobot, OArm7DOFRosRobotConfig

logger = logging.getLogger(__name__)

# Same file the Camera Setup page writes. Direct capture and the ROS bridge are
# configured from one place so "attach camera" means the same thing either way.
ROS_CAMERA_MAPPINGS_PATH = Path.home() / ".config" / "lelab" / "ros_camera_mappings.json"


def load_camera_devices(arm_mode: str = "both") -> dict[str, str]:
    """Camera Setup mappings -> {slot_name: device}, filtered by arm_mode.

    Prefers the by-path symlink when the mapping carries one: /dev/videoN moves
    when cables move, by-path does not (CameraReader._resolve re-resolves it at
    every start).
    """
    if not ROS_CAMERA_MAPPINGS_PATH.is_file():
        return {}
    try:
        entries = json.loads(ROS_CAMERA_MAPPINGS_PATH.read_text())
    except Exception as e:
        logger.error("cannot read %s: %s", ROS_CAMERA_MAPPINGS_PATH, e)
        return {}

    devices: dict[str, str] = {}
    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        if arm_mode == "left" and "right" in name.lower():
            continue
        if arm_mode == "right" and "left" in name.lower():
            continue
        devices[name] = str(entry.get("device_index"))
    return devices


def _direct_io_module():
    """Import oarm7dof_direct_io from the repo root.

    lelab is installed as a package but oarm7dof_direct_io.py lives at the repo
    root next to deploy_act_policy.py, so it is not importable by default.
    Resolved from this file's location (repo/lelab_ui/lelab/robots/…), with
    OARM7DOF_REPO_ROOT as an override for non-standard layouts.
    """
    root = os.environ.get("OARM7DOF_REPO_ROOT") or str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)
    import oarm7dof_direct_io  # noqa: PLC0415

    return oarm7dof_direct_io


@dataclass(kw_only=True)
class OArm7DOFDirectRobotConfig(OArm7DOFRosRobotConfig):
    """
    Direct-I/O variant. Inherits arm_mode / cameras / joint_names.

    include_ee_pose defaults False here: ee_pose is pure FK of the joint angles,
    so it adds nothing a policy cannot derive, and dropping it keeps
    observation.state matching action 1:1 (8 dims).
    """
    type: str = "oarm7dof_direct"
    include_ee_pose: bool = False

    # CAN interfaces per arm and the DM motor feedback (master) ids.
    # 0x011-0x018 verified live on can0: 7 joints + gripper at ~203 Hz each.
    right_can: str = "can0"
    left_can: str = "can1"
    recv_ids: tuple = (0x011, 0x012, 0x013, 0x014, 0x015, 0x016, 0x017, 0x018)

    # Camera device paths for direct capture, e.g.
    #   {"main_camera": "/dev/v4l/by-path/...-video-index0"}
    # Defaults to the ROS camera mappings file when left empty.
    camera_devices: dict = field(default_factory=dict)

    # Capture format. Frames are still RECORDED at the dataset fps; capture_fps
    # only sets how fast the sensor is read.
    #
    # Raising it is the one lever left on camera latency. At 30 fps the newest
    # available frame is on average half a frame period old (~16.7ms) purely
    # because that is when it was exposed — nothing downstream can remove that.
    # Capturing at 60 halves the term to ~8ms, at the cost of USB bandwidth
    # (MJPG keeps it affordable) and one more decode per discarded frame.
    # Verify the camera actually accepts it:
    #   v4l2-ctl -d /dev/videoN --list-formats-ext
    capture_fps: int = 30
    capture_width: int = 640
    capture_height: int = 480

    # Gripper: motor radians -> aperture metres.
    #
    # Magnitude only -- the SIGN of this value is irrelevant because
    # _gripper_motor_to_m() takes the absolute difference. The gripper motor has
    # been observed reporting both polarities for the same physically-open
    # gripper across runs, so neither sign can be assumed; see that method.
    #
    # 1.0472 keeps this path in agreement with ros2_control / /joint_states
    # (GRIPPER_MOTOR_1_RADIANS in v10_simple_hardware.hpp).
    gripper_closed_motor_rad: float = 0.0
    gripper_open_motor_delta_rad: float = 1.0472   # measured: |0.9908| rad <-> 0.0416 m
    # Mechanical limit, used only to bound the action. The normalised->metres
    # scale deliberately does NOT live here: exoskeleton_bridge_node owns it
    # (gripper_scaling_factor) and publishes the result on
    # /exo/gripper_command_m, so there is one definition instead of two that can
    # drift apart.
    gripper_max_m: float = 0.044


class OArm7DOFDirectRobot(OArm7DOFRosRobot):
    """
    Observations straight from hardware; actions still from the ROS bridge.

    Subclasses the ROS backend deliberately: the UDP listener, action history and
    timestamp-nearest pairing are all reused unchanged. Only the observation
    sources are replaced.
    """

    config_class = OArm7DOFDirectRobotConfig
    name = "oarm7dof_direct"

    def __init__(self, config: OArm7DOFDirectRobotConfig):
        super().__init__(config)
        self.direct_config = config
        self._state_readers: dict[str, object] = {}
        self._cam_reader = None
        self._direct_lock = threading.Lock()
        self._direct_diag: dict[str, float] = {}
        self._gripper_closed_cache: dict[str, float] | None = None

        # Resolve devices ONCE, here rather than in connect(): LeRobot reads
        # observation_features to build the dataset schema before the robot is
        # connected, so cameras discovered later would be missing from it.
        self._camera_devices = dict(config.camera_devices) or load_camera_devices(config.arm_mode)
        if self._camera_devices:
            logger.info("direct cameras configured: %s", self._camera_devices)
        else:
            logger.warning(
                "no cameras configured for direct capture; attach one on the Camera "
                "Setup page (writes %s)", ROS_CAMERA_MAPPINGS_PATH
            )

        # Live-preview / freeze state reuses the base class's _latest_frames /
        # _frames_lock so /recording-camera and /reconnect-cameras work unchanged.
        for cam in self._camera_devices:
            self._camera_frozen[cam] = True
            self._camera_usb_paths[cam] = str(self._camera_devices[cam])
        self._preview_jpeg_period_s = 1.0 / 15.0   # preview only; capture stays 30fps
        self._preview_last_encode: dict[str, float] = {}
        self._camera_stale_s = 1.0

    # -- features ----------------------------------------------------------
    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        feats: dict[str, type | tuple] = {f"{n}.pos": float for n in self.config.joint_names}
        for cam in self._direct_camera_names():
            feats[cam] = (self.direct_config.capture_height, self.direct_config.capture_width, 3)
        return feats

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{n}.pos": float for n in self.config.joint_names}

    def _capture_kwargs(self) -> dict:
        cfg = self.direct_config
        return {"width": cfg.capture_width, "height": cfg.capture_height, "fps": cfg.capture_fps}

    def _direct_camera_names(self) -> list[str]:
        return list(self._camera_devices.keys())

    # -- lifecycle ---------------------------------------------------------
    def connect(self, calibrate: bool = False) -> None:
        io = _direct_io_module()
        DM4310, CameraReader, StateReader = io.DM4310, io.CameraReader, io.StateReader

        # The ROS side still provides `action`, so keep its UDP listener.
        super().connect(calibrate=calibrate)

        sides = ("left", "right") if self.config.arm_mode == "both" else (self.config.arm_mode,)
        for side in sides:
            ch = self.direct_config.right_can if side == "right" else self.direct_config.left_can
            # Per-motor limits, NOT one profile for all eight. This arm mixes
            # DM8009 / DM4340 / DM4310 (t_max 54 / 28 / 10 Nm), so decoding
            # everything as DM4310 under-reports J1-J4 torque by up to 5.4x.
            # Position was unaffected (identical p_max), which is exactly why
            # the position validation passed while torque was wrong.
            limits = {
                cid: io.OARM7DOF_MOTOR_LIMITS.get(cid & 0xFF, DM4310)
                for cid in self.direct_config.recv_ids
            }
            reader = StateReader(ch, list(self.direct_config.recv_ids), limits, fd=True)
            reader.start()
            self._state_readers[side] = reader
            logger.info("direct state: passive CAN reader on %s for %s arm", ch, side)

        devices = self._camera_devices
        if devices:
            self._cam_reader = CameraReader(devices, **self._capture_kwargs()).start()
            logger.info("direct cameras: %s", ", ".join(devices))
        else:
            logger.warning("no camera_devices configured; observations will have no images")

    def disconnect(self):
        for r in self._state_readers.values():
            try:
                r.stop()
            except Exception:
                pass
        self._state_readers.clear()
        if self._cam_reader is not None:
            try:
                self._cam_reader.stop()
            except Exception:
                pass
            self._cam_reader = None
        super().disconnect()

    # -- unit conversion ---------------------------------------------------
    def _gripper_motor_to_m(self, motor_rad: float, side: str = "right") -> float:
        """
        Motor radians -> aperture metres. SIGN-AGNOSTIC, deliberately.

        The gripper is not direct-drive: |0.9908| motor rad corresponds to
        0.0416 m of aperture, so a scale maps one to the other. Joints 1-7 need
        no conversion (verified to 0.0004 rad).

        Why the magnitude and not the signed value: the gripper motor's reported
        polarity is NOT stable across runs. Both signs have been observed live
        for the same physically-open gripper -- +0.985 rad while recording
        JedEYE14/Dus_20260812_141952 (2033 frames, aperture 0.0185..0.042
        correlating +0.98 with the commanded value), and -0.98 rad in a later
        session. A signed divisor therefore works in one run and, in the other,
        makes clip(frac, 0, 1) floor EVERY reading to 0.0.

        That failure is quiet and expensive: the observation looks like a valid
        fully-closed gripper, so `_at_home_pose` compares a constant 0.000
        against the 0.040 home target under a 5 mm tolerance and the episode
        waits for a condition that can never be met, while the arm sits at home
        and the graph -- which reads the ROS/UDP path, not CAN -- shows the
        correct 0.0412. Taking the magnitude makes both polarities agree.

        The divisor matches ros2_control's own constant (GRIPPER_MOTOR_1_RADIANS
        = -1.0472 over GRIPPER_JOINT_0_POSITION = 0.044 in
        v10_simple_hardware.hpp) so this path and /joint_states report the same
        aperture. It was 1.0325, which disagreed with ros2_control by ~1.5% and
        also contradicted this docstring's own measurement:
        0.044 * (0.9908 / 1.0472) = 0.0416 m, exactly as measured.
        """
        cfg = self.direct_config
        closed = self._gripper_closed_rad(side)
        frac = abs(float(motor_rad) - closed) / abs(cfg.gripper_open_motor_delta_rad)
        return float(np.clip(frac, 0.0, 1.0)) * cfg.gripper_max_m

    def _gripper_closed_rad(self, side: str) -> float:
        """Closed-gripper motor angle for one arm, from gripper_home.yaml.

        deploy_act_policy.py reads that file (load_gripper_homes) and this path
        did not -- it assumed 0.0 rad. The file says 0.0967 (left) / 0.2157
        (right), so observation.state's gripper was offset from the value deploy
        computes for the SAME pose by ~4mm on the left and ~9mm on the right.
        A policy would train on one encoding of "closed" and run on another,
        which is precisely the train/deploy gap this backend exists to remove.

        Cached: the file is read once per session, not per frame.
        """
        if self._gripper_closed_cache is None:
            try:
                from lelab.motors import gripper_closed_offsets

                self._gripper_closed_cache = gripper_closed_offsets()
            except Exception as e:
                logger.warning(
                    "cannot load gripper_home.yaml (%s); falling back to the "
                    "configured %.4f rad for both arms",
                    e, self.direct_config.gripper_closed_motor_rad,
                )
                self._gripper_closed_cache = {}
            logger.info("gripper closed offsets: %s", self._gripper_closed_cache)
        return float(
            self._gripper_closed_cache.get(side, self.direct_config.gripper_closed_motor_rad)
        )

    def _clamp_gripper_action_m(self, value: float) -> float:
        """Clamp a gripper action already expressed in metres.

        No unit conversion here any more: exoskeleton_bridge_node publishes the
        commanded aperture in metres on /exo/gripper_command_m and
        ros2_lelab_bridge puts that in the action, so the scale factor lives in
        exactly one place — the node that owns it. Converting again here would
        scale metres a second time.
        """
        return float(np.clip(float(value), 0.0, self.direct_config.gripper_max_m))

    # -- observation -------------------------------------------------------
    def get_observation(self):
        """
        Build the observation from hardware directly.

        Camera frames are paired with the CAN sample NEAREST their capture
        instant rather than with whatever state happens to be latest. Measured
        on a virtual bus, "latest wins" was 90.9 ms off where nearest-sample was
        0.0 ms — at 30 fps that is nearly three frames of mislabelling.
        """
        obs: dict = {}
        names = self.config.joint_names

        # Cameras first: their capture time is the sync reference.
        #
        # Bind the reader to a local ONCE. reconnect_cameras() runs on the HTTP
        # thread and sets self._cam_reader = None before building a replacement,
        # so re-reading the attribute per camera (after an `is not None` guard
        # that already passed) raced with that and raised AttributeError on
        # None.latest() -- killing the record loop mid-episode, triggered by the
        # very reconnect that was supposed to rescue the recording. A local
        # reference keeps using the old reader for this one observation, which is
        # correct: it is either still delivering frames or its cameras report
        # None and get zero-filled below.
        reader = self._cam_reader
        frames: dict[str, tuple] = {}
        if reader is not None:
            for cam in self._direct_camera_names():
                try:
                    frame, ts = reader.latest(cam, rgb=True)
                except (KeyError, AttributeError):
                    # Camera dropped from the reader's map by a concurrent
                    # reconnect that re-read the mappings file; zero-filled below.
                    continue
                if frame is not None:
                    frames[cam] = (frame, ts)

        # Mean, not max, of the capture instants: the cameras free-run at
        # slightly different phases and cannot be genlocked, so the mean
        # minimizes total misalignment across all images (same reasoning as the
        # ROS backend). With one camera mean == that camera's stamp.
        capture_ts = [ts for _, ts in frames.values() if ts > 0.0]
        ref_t = sum(capture_ts) / len(capture_ts) if capture_ts else time.monotonic()

        per_side: dict[str, np.ndarray] = {}
        state_ts_used: list[float] = []
        for side, reader in self._state_readers.items():
            vec, ts = reader.state_at(ref_t)
            if vec is None:
                vec, ts = reader.latest()
            if vec is not None:
                per_side[side] = vec
                state_ts_used.append(ts)
                self._direct_diag[f"can.{side}.age_ms"] = (ref_t - ts) * 1000.0
                self._direct_diag[f"observation.{side}.timestamp"] = ts

        # joint_names is ordered [<side> joint1..7, <side> finger] per arm.
        for i, name in enumerate(names):
            side = "left" if "_left_" in name else "right"
            vec = per_side.get(side)
            if vec is None:
                obs[f"{name}.pos"] = 0.0
                continue
            idx = i % 8
            raw = float(vec[idx]) if idx < len(vec) else 0.0
            obs[f"{name}.pos"] = self._gripper_motor_to_m(raw, side) if "finger" in name else raw

        now = time.monotonic()
        for cam in self._direct_camera_names():
            got = frames.get(cam)
            if got is None:
                # A dataset row must carry every declared feature, so a camera
                # that has not produced a frame yet contributes a black image
                # rather than a KeyError that aborts the episode.
                obs[cam] = np.zeros((480, 640, 3), dtype=np.uint8)
                self._camera_frozen[cam] = True
                continue
            frame, ts = got
            obs[cam] = frame
            self._direct_diag[f"camera.{cam}.timestamp"] = ts
            self._camera_frozen[cam] = (now - ts) > self._camera_stale_s
            self._publish_preview(cam, frame, now)

        # Publish the timing provenance the recorder validates against. Both
        # keys are mandatory, not informational: sync_within_tolerance() returns
        # False when `sync.timestamp` is missing, and the recorder drops every
        # frame it rejects — so omitting them yields an empty dataset. They are
        # written under _data_lock because get_sync_diagnostics() copies this
        # dict from the caller's thread.
        with self._data_lock:
            if state_ts_used:
                # Conservative: report the state sample FURTHEST from the sync
                # instant, so the tolerance check grades the worst arm rather
                # than an average that could hide one arm being stale.
                self._direct_diag["observation.timestamp"] = max(
                    state_ts_used, key=lambda ts: abs(ts - ref_t)
                )
            else:
                self._direct_diag["observation.timestamp"] = ref_t
            self._direct_diag["sync.timestamp"] = ref_t
            self._latest_obs_timestamp = self._direct_diag["observation.timestamp"]
            self._last_sync_timestamp = ref_t
            self._last_sync_diagnostics.update(self._direct_diag)
        return obs

    # -- live preview ------------------------------------------------------
    def _publish_preview(self, cam: str, rgb_frame: np.ndarray, now: float) -> None:
        """Cache a JPEG of the frame the recorder just used, for the dashboard.

        Encoding happens on the record-loop thread, so it is throttled to 15 Hz
        and quality 70: at 30fps x 2 cameras an unthrottled full-quality encode
        is a few ms per frame taken straight out of the 33ms budget, for a
        preview no one can see the difference in.
        """
        if (now - self._preview_last_encode.get(cam, 0.0)) < self._preview_jpeg_period_s:
            return
        self._preview_last_encode[cam] = now
        try:
            import cv2  # noqa: PLC0415 -- keeps the CAN-only path OpenCV-free

            ok, buf = cv2.imencode(
                ".jpg",
                cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR),
                [int(cv2.IMWRITE_JPEG_QUALITY), 70],
            )
        except Exception as e:
            logger.debug("preview encode failed for %s: %s", cam, e)
            return
        if ok:
            with self._frames_lock:
                self._latest_frames[cam] = buf.tobytes()

    def refresh_preview(self) -> None:
        """Update the preview JPEGs and freeze flags WITHOUT recording a row.

        Called by the recorder while it is paused. Everything that keeps the
        dashboard alive normally happens inside get_observation(), which the
        pause loop never calls, so the preview froze the moment recording paused
        and the operator had no way to see whether a camera had come back.

        It also breaks a deadlock. `_camera_frozen` was likewise only ever
        written by get_observation(), and the pause loop auto-resumes on
        `_freeze_paused and not _cameras_frozen()` — so a freeze-induced pause
        could never clear itself, because the flag it waits on was only updated
        by the loop it had stopped. Refreshing here lets a camera that recovers
        on its own resume the recording.
        """
        reader = self._cam_reader
        if reader is None:
            return
        now = time.monotonic()
        for cam in self._direct_camera_names():
            try:
                frame, ts = reader.latest(cam, rgb=True)
            except (KeyError, AttributeError):
                continue
            if frame is None:
                self._camera_frozen[cam] = True
                continue
            self._camera_frozen[cam] = (now - ts) > self._camera_stale_s
            self._publish_preview(cam, frame, now)

    # -- diagnostics -------------------------------------------------------
    def get_sync_diagnostics(self) -> dict:
        """Base diagnostics, minus the zero camera stamps when there are none.

        The base implementation always injects camera.timestamp/min/max, using
        0.0 as the default. sync_within_tolerance() then measures |sync_ts - 0|,
        which is seconds-since-boot, so with no cameras attached EVERY frame is
        rejected and the recording lands zero rows. A camera-less dataset
        (state + action only) is a legitimate thing to record, so drop the keys
        instead of reporting a stamp that does not exist.
        """
        diagnostics = super().get_sync_diagnostics()
        if not any(
            value > 0.0
            for key, value in diagnostics.items()
            if key.startswith("camera.") and key.endswith("timestamp")
        ):
            for key in ("camera.timestamp", "camera.min_timestamp", "camera.max_timestamp"):
                diagnostics.pop(key, None)
        return diagnostics

    def get_latest_frame_jpeg(self, cam_key: str) -> tuple[bytes | None, bool]:
        with self._frames_lock:
            return self._latest_frames.get(cam_key), self._camera_frozen.get(cam_key, False)

    def reconnect_cameras(self) -> dict[str, bool]:
        """Restart direct capture and report, per camera, whether frames resumed.

        Safe to call DURING a recording session, which is the point: a camera
        that drops mid-dataset used to be unrecoverable without losing the run.

        Three things this must do that the previous version did not:

        1. RE-READ THE MAPPINGS FILE. It restarted the reader from the
           `_camera_devices` captured in __init__, so re-attaching a camera on
           the Camera Setup page (which rewrites that file) had no effect on the
           live robot -- the operator re-attached, saw nothing change, and had no
           way to tell why. by-path resolution alone only covers a device
           returning to the SAME USB port.

        2. CLEAR `_camera_frozen` FOR RECOVERED CAMERAS. This is what actually
           un-sticks a frozen recording. The record loop auto-pauses on a frozen
           camera and resumes only when `_freeze_paused and not _cameras_frozen()`
           -- but for this backend the frozen flag is written ONLY inside
           get_observation(), which is not called while paused, and the inherited
           watchdog iterates `self.cameras`, which is empty here. So the flag
           could never clear on its own: the freeze was a permanent deadlock and
           reconnecting did not help because nothing reset it.

        3. VERIFY. It returned None and the endpoint reported success
           unconditionally, so a reconnect that recovered nothing still said
           "Cameras reconnected" -- the single most misleading thing it could do
           when the operator is trying to rescue a dataset.

        Slots are matched against the dataset's fixed schema on purpose: a
        recording's features are frozen when the dataset is created, so a slot
        that is not already part of it cannot be added mid-run. Such slots are
        logged and ignored rather than silently changing the schema.
        """
        with self._direct_lock:
            desired = load_camera_devices(self.config.arm_mode)
            for slot, dev in desired.items():
                if slot not in self._camera_devices:
                    logger.warning(
                        "camera slot %r is newly mapped but is not part of this "
                        "dataset's schema; ignoring it until the next session",
                        slot,
                    )
                    continue
                if str(dev) != str(self._camera_devices[slot]):
                    logger.info(
                        "camera %s: device changed %s -> %s",
                        slot, self._camera_devices[slot], dev,
                    )
                self._camera_devices[slot] = str(dev)
            for slot in set(self._camera_devices) - set(desired):
                logger.warning(
                    "camera slot %r is no longer mapped; keeping its last known "
                    "device because the dataset schema still requires it", slot,
                )

            # Stop the old reader but leave self._cam_reader pointing at it until
            # a replacement exists, then swap in one assignment. Nulling it first
            # left a window where a concurrent get_observation() saw None and
            # zero-filled every image; the V4L2 devices are already released by
            # stop(), so the old object simply returns None frames in that window
            # instead of vanishing.
            old_reader = self._cam_reader
            if old_reader is not None:
                try:
                    old_reader.stop()
                except Exception as e:
                    logger.warning("camera reader stop failed: %s", e)
            if not self._camera_devices:
                self._cam_reader = None
                return {}
            try:
                self._cam_reader = _direct_io_module().CameraReader(
                    self._camera_devices, **self._capture_kwargs()
                ).start()
                logger.info("direct cameras reconnected: %s", self._camera_devices)
            except Exception as e:
                logger.error("camera reconnect failed: %s", e)
                self._cam_reader = None  # old one is stopped; nothing usable left
                return dict.fromkeys(self._camera_devices, False)
            for cam, dev in self._camera_devices.items():
                self._camera_usb_paths[cam] = str(dev)
            reader, cams = self._cam_reader, list(self._camera_devices)

        # Verify outside the lock: this waits on hardware, and get_observation
        # must not be blocked meanwhile.
        results: dict[str, bool] = {}
        pending = set(cams)
        deadline = time.monotonic() + 3.0
        while pending and time.monotonic() < deadline:
            for cam in list(pending):
                frame, ts = reader.latest(cam, rgb=True)
                if frame is not None and (time.monotonic() - ts) <= self._camera_stale_s:
                    results[cam] = True
                    pending.discard(cam)
            if pending:
                time.sleep(0.1)
        for cam in pending:
            results[cam] = False
            logger.error("camera %s produced no fresh frame after reconnect", cam)

        # Only now is it safe to say the freeze is over. get_observation() will
        # keep this honest from here on using real frame age.
        with self._frames_lock:
            for cam, ok in results.items():
                self._camera_frozen[cam] = not ok
        return results

    # -- action ------------------------------------------------------------
    def get_action_at_sync_time(self) -> dict[str, float]:
        """
        Action from the ROS bridge. The gripper already arrives in metres
        (/exo/gripper_command_m), so this only bounds it to the mechanical range.
        """
        action = super().get_action_at_sync_time()
        return {
            k: (self._clamp_gripper_action_m(v) if "finger" in k else v)
            for k, v in action.items()
        }

    def get_action_positions(self) -> dict[str, float]:
        pos = super().get_action_positions()
        return {
            k: (self._clamp_gripper_action_m(v) if "finger" in k else v)
            for k, v in pos.items()
        }
