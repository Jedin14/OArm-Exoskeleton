#!/usr/bin/env python3
"""
Direct, timestamped I/O for OpenArm: cameras via OpenCV, motor state via CAN.

WHY THIS EXISTS
---------------
Recording and deployment currently share nothing:

                    recording (training data)              deployment
    camera   V4L2 -> JPEG encode -> ROS CompressedImage    cv2.VideoCapture
             -> UDP -> JPEG decode -> dataset              (deploy_act_policy.py:59)
    state    ros2_control reads CAN -> /joint_states       openarm_can direct
             -> UDP -> dataset                             (deploy_act_policy.py:84)

deploy_act_policy.py even kills the whole ROS stack (ros2_control_node included)
and talks to hardware itself. So a policy trains on JPEG-degraded, multi-hop
delayed frames and then runs on fresh direct ones. That is a genuine train/deploy
distribution shift, and it is the kind that passes an open-loop eval and then
fails on hardware.

This module gives BOTH paths one implementation, so what the policy sees while
recording is byte-for-byte what it sees at deploy time.

CAN IS READ PASSIVELY
---------------------
At recording time ros2_control owns can0/can1 and is actively polling the DM
motors. A second process must NOT poll as well: two masters querying the same
motor IDs collide on the bus and can fault the drives. SocketCAN is a broadcast
interface, so instead we open a read-only socket and decode the feedback frames
that ros2_control's own polling already elicits. Zero added bus traffic, and it
works whether ros2_control is running (recording) or not (deploy, where
openarm_can does the polling).

Feedback frame layout, from openarm_can/src/openarm/damiao_motor/dm_motor_control.cpp:68
    data[0]      : error nibble | motor id
    data[1..2]   : position, 16-bit unsigned
    data[3..4]   : velocity, 12-bit  -> (data[3] << 4) | (data[4] >> 4)
    data[4..5]   : torque,   12-bit  -> ((data[4] & 0xF) << 8) | data[5]
    data[6]      : MOS temperature
    data[7]      : rotor temperature
Decoded with uint_to_double(x, -limit, +limit, bits) (same file, :153).

TIMESTAMPS
----------
Camera frames are stamped with time.monotonic() immediately after read(), and
CAN frames carry the kernel's SO_TIMESTAMP (SOL_SOCKET/SO_TIMESTAMPNS), i.e. the
instant the frame hit the socket rather than when Python got round to it. Both
are monotonic seconds so they can be differenced directly. Use
`StateReader.state_at(t)` to fetch the sample nearest a frame's capture time
instead of pairing whatever happened to be latest.

USAGE
    cams = CameraReader({"main_camera": "/dev/v4l/by-path/...-video-index0"})
    state = StateReader("can0", MOTOR_RECV_IDS, MotorLimits.DM4310)
    cams.start(); state.start()
    frame, t_cap = cams.latest("main_camera")
    q, t_state = state.state_at(t_cap)
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CAN frame decoding
# ---------------------------------------------------------------------------

# struct can_frame { canid_t can_id; __u8 len; __u8 pad, res0, len8_dlc; __u8 data[8]; }
CAN_FRAME_FMT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FMT)
CANFD_FRAME_FMT = "=IBBBx64s"
CANFD_FRAME_SIZE = struct.calcsize(CANFD_FRAME_FMT)

CAN_RAW_FD_FRAMES = 5          # <linux/can/raw.h>
SOL_CAN_RAW = 101
SO_TIMESTAMPNS = 35            # <asm/socket.h>
CAN_EFF_FLAG = 0x80000000
CAN_SFF_MASK = 0x000007FF


@dataclass(frozen=True)
class MotorLimits:
    """Position/velocity/torque full-scale, from dm_motor_constants.hpp:95."""
    p_max: float
    v_max: float
    t_max: float


# Only the types this arm uses; extend from the table in dm_motor_constants.hpp.
DM4310 = MotorLimits(12.5, 30.0, 10.0)
DM4340 = MotorLimits(12.5, 8.0, 28.0)
DM8009 = MotorLimits(12.5, 45.0, 54.0)


def _uint_to_double(x: int, lo: float, hi: float, bits: int) -> float:
    """Port of CanPacketDecoder::uint_to_double (dm_motor_control.cpp:153)."""
    return (x / float((1 << bits) - 1)) * (hi - lo) + lo


@dataclass
class MotorFeedback:
    can_id: int
    position: float          # radians
    velocity: float          # rad/s
    torque: float            # Nm
    t_mos: int
    t_rotor: int
    timestamp: float         # kernel receive time, monotonic seconds


def decode_feedback(can_id: int, data: bytes, limits: MotorLimits,
                    timestamp: float) -> Optional[MotorFeedback]:
    """Decode one DM motor feedback frame. Returns None if it is not one."""
    if len(data) < 8:
        # dm_motor_control.cpp:63 rejects short frames for the same reason.
        return None
    q_uint = (data[1] << 8) | data[2]
    dq_uint = (data[3] << 4) | (data[4] >> 4)
    tau_uint = ((data[4] & 0x0F) << 8) | data[5]
    return MotorFeedback(
        can_id=can_id,
        position=_uint_to_double(q_uint, -limits.p_max, limits.p_max, 16),
        velocity=_uint_to_double(dq_uint, -limits.v_max, limits.v_max, 12),
        torque=_uint_to_double(tau_uint, -limits.t_max, limits.t_max, 12),
        t_mos=data[6],
        t_rotor=data[7],
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Passive CAN state reader
# ---------------------------------------------------------------------------


class StateReader:
    """
    Read-only SocketCAN listener that decodes DM motor feedback.

    Passive by design: it never transmits, so it cannot collide with
    ros2_control (recording) or openarm_can (deploy) polling the same motors.
    """

    def __init__(self, channel: str, recv_ids: Iterable[int],
                 limits: MotorLimits = DM4310, fd: bool = True,
                 history: int = 512):
        self.channel = channel
        self.recv_ids = list(recv_ids)
        self._index = {cid: i for i, cid in enumerate(self.recv_ids)}
        self.limits = limits
        self.fd = fd
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: Dict[int, MotorFeedback] = {}
        self._history: List[Tuple[float, np.ndarray]] = []
        self._history_max = history
        self.frames_seen = 0
        self.frames_decoded = 0

    def start(self) -> "StateReader":
        s = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        if self.fd:
            try:
                s.setsockopt(SOL_CAN_RAW, CAN_RAW_FD_FRAMES, 1)
            except OSError:
                logger.warning("CAN-FD not available on %s, falling back to 2.0", self.channel)
                self.fd = False
        # Kernel receive timestamps: the instant the frame arrived, not when
        # Python was scheduled. Without this, GIL contention shows up as jitter
        # in what is supposed to be the sync reference.
        try:
            s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
            self._hw_ts = True
        except OSError:
            self._hw_ts = False
            logger.warning("SO_TIMESTAMPNS unavailable; using arrival time in userspace")
        s.bind((self.channel,))
        s.settimeout(0.5)
        self._sock = s
        self._thread = threading.Thread(target=self._run, name=f"can-{self.channel}", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        assert self._sock is not None
        bufsize = (CANFD_FRAME_SIZE if self.fd else CAN_FRAME_SIZE) + 64
        ancsize = socket.CMSG_SPACE(16)
        while not self._stop.is_set():
            try:
                if self._hw_ts:
                    raw, anc, _flags, _addr = self._sock.recvmsg(bufsize, ancsize)
                    ts = time.monotonic()
                    for level, ctype, cdata in anc:
                        if level == socket.SOL_SOCKET and len(cdata) >= 16:
                            sec, nsec = struct.unpack("=qq", cdata[:16])
                            # SO_TIMESTAMPNS is CLOCK_REALTIME; rebase onto the
                            # monotonic clock the camera uses so the two are
                            # directly comparable.
                            ts = sec + nsec * 1e-9 - self._realtime_offset()
                            break
                else:
                    raw = self._sock.recv(bufsize)
                    ts = time.monotonic()
            except socket.timeout:
                continue
            except OSError as e:
                if not self._stop.is_set():
                    logger.error("CAN read failed on %s: %s", self.channel, e)
                break

            self.frames_seen += 1
            if len(raw) >= CANFD_FRAME_SIZE and self.fd:
                can_id, length, _flags, _res, payload = struct.unpack(CANFD_FRAME_FMT, raw[:CANFD_FRAME_SIZE])
            elif len(raw) >= CAN_FRAME_SIZE:
                can_id, length, payload = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
            else:
                continue
            can_id &= CAN_SFF_MASK if not (can_id & CAN_EFF_FLAG) else 0x1FFFFFFF
            if can_id not in self._index:
                continue
            fb = decode_feedback(can_id, payload[:max(8, length)], self.limits, ts)
            if fb is None:
                continue
            self.frames_decoded += 1
            with self._lock:
                self._latest[can_id] = fb
                if len(self._latest) == len(self.recv_ids):
                    vec = np.array([self._latest[c].position for c in self.recv_ids], dtype=np.float64)
                    newest = max(f.timestamp for f in self._latest.values())
                    self._history.append((newest, vec))
                    if len(self._history) > self._history_max:
                        del self._history[: len(self._history) - self._history_max]

    _rt_offset: Optional[float] = None

    def _realtime_offset(self) -> float:
        """CLOCK_REALTIME - CLOCK_MONOTONIC, sampled once."""
        if StateReader._rt_offset is None:
            StateReader._rt_offset = time.time() - time.monotonic()
        return StateReader._rt_offset

    def latest(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            if not self._history:
                return None, 0.0
            ts, vec = self._history[-1]
            return vec.copy(), ts

    def state_at(self, t: float) -> Tuple[Optional[np.ndarray], float]:
        """
        Nearest-in-time state sample to `t` (a camera capture instant).

        Pairing "whatever is latest" instead of this is how a fast joint ends up
        labelled with a pose from tens of milliseconds earlier.
        """
        with self._lock:
            if not self._history:
                return None, 0.0
            best = min(self._history, key=lambda kv: abs(kv[0] - t))
            return best[1].copy(), best[0]

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        if self._sock is not None:
            self._sock.close()


# ---------------------------------------------------------------------------
# Direct camera reader
# ---------------------------------------------------------------------------


@dataclass
class _Cam:
    name: str
    device: str
    width: int = 640
    height: int = 480
    fps: int = 30
    cap: object = None
    frame: Optional[np.ndarray] = None
    stamp: float = 0.0
    frames: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class CameraReader:
    """
    Threaded V4L2 capture, one thread per camera, newest-frame-wins.

    Settings deliberately mirror deploy_act_policy.py:59-63 (MJPG, 640x480,
    BUFFERSIZE=1) so recorded frames and deployed frames come off the sensor
    through an identical path. In particular there is no JPEG re-encode here:
    the ROS bridge's imencode/imdecode round trip was adding compression
    artifacts to training data that deployment never sees.
    """

    def __init__(self, devices: Dict[str, str], width: int = 640,
                 height: int = 480, fps: int = 30):
        import cv2  # local import so the CAN half works without OpenCV
        self._cv2 = cv2
        self.cams = {n: _Cam(n, d, width, height, fps) for n, d in devices.items()}
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    @staticmethod
    def _resolve(device: str):
        """
        by-path symlink -> /dev/videoN -> integer index.

        cv2.VideoCapture(<string>, CAP_V4L2) warns "backend is generally
        available but can't be used to capture by name" and fails on a
        /dev/v4l/by-path/... path. The V4L2 backend wants an index, so resolve
        the symlink and extract N.

        by-path is still the right thing to CONFIGURE with: /dev/videoN is
        assigned in enumeration order and moves when cables move (observed here:
        the USB camera went video4 -> video10, while video4 became the
        RealSense depth node). by-path is stable per USB port, so resolve it
        fresh at every start rather than caching an index.
        """
        import os
        import re

        if isinstance(device, int):
            return device
        s = str(device)
        if s.isdigit():
            return int(s)
        real = os.path.realpath(s)
        m = re.fullmatch(r"/dev/video(\d+)", real)
        if m:
            return int(m.group(1))
        # Unknown shape: hand it back and let OpenCV try.
        return s

    def start(self) -> "CameraReader":
        cv2 = self._cv2
        for cam in self.cams.values():
            index = self._resolve(cam.device)
            if index != cam.device:
                logger.info("camera %s: %s -> index %s", cam.name, cam.device, index)
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam.height)
            cap.set(cv2.CAP_PROP_FPS, cam.fps)
            # Depth 1: always hand the policy the newest frame. A deeper queue
            # silently ages observations, which at 30fps is whole control cycles.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                raise RuntimeError(f"camera {cam.name}: cannot open {cam.device}")
            cam.cap = cap
            t = threading.Thread(target=self._run, args=(cam,), name=f"cam-{cam.name}", daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def _run(self, cam: _Cam) -> None:
        while not self._stop.is_set():
            ok, frame = cam.cap.read()
            # Stamp immediately: any work done before this is measurement error
            # against the CAN timestamps we pair with.
            ts = time.monotonic()
            if not ok:
                time.sleep(0.005)
                continue
            with cam.lock:
                cam.frame = frame
                cam.stamp = ts
                cam.frames += 1

    def latest(self, name: str, rgb: bool = True) -> Tuple[Optional[np.ndarray], float]:
        cam = self.cams[name]
        with cam.lock:
            if cam.frame is None:
                return None, 0.0
            frame = cam.frame.copy()
            ts = cam.stamp
        if rgb:
            frame = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        return frame, ts

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.5)
        for cam in self.cams.values():
            if cam.cap is not None:
                cam.cap.release()
