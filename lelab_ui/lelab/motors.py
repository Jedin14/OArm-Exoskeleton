"""Live motor torque readout, independent of any recording session.

WHY A SEPARATE READER
---------------------
The force page has to work on the landing page and mid-recording alike, so it
cannot depend on `record.active_robot` existing. StateReader is READ-ONLY -- it
never transmits -- so an extra listener on the same bus is safe: a SocketCAN raw
socket delivers every frame to every bound socket, and this one cannot collide
with ros2_control (recording) or openarm_can (deploy) polling the same motors.

UNITS
-----
Torque in Nm, straight from the motor's own 12-bit feedback field. NOT force in
Newtons: converting to a fingertip force needs a lever-arm/transmission constant
that does not exist anywhere in this repo, and inventing one would put a
confident wrong number on screen. `t_max` per motor is reported alongside so the
UI can show "% of rated" without hard-coding anything.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Joint order matches the recorded dataset's joint_names for one arm.
JOINT_LABELS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7", "finger")

# A reading older than this is reported stale rather than shown as current: the
# motors report at ~203 Hz, so anything approaching a second means the bus went
# quiet (arm powered down, CAN unplugged, ros2_control stopped).
STALE_AFTER_S = 0.5


def _direct_io():
    """Import openarm_direct_io from the repo root (same shim as the robot backend)."""
    root = os.environ.get("OPENARM_REPO_ROOT") or str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    import openarm_direct_io  # noqa: PLC0415

    return openarm_direct_io


class TorqueMonitor:
    """Passive per-arm CAN readers, started on first use and reused after that."""

    def __init__(self, right_can: str = "can0", left_can: str = "can1"):
        self._channels = {"right": right_can, "left": left_can}
        self._readers: dict[str, object] = {}
        self._lock = threading.Lock()
        self._failed: dict[str, str] = {}

    def ensure_started(self) -> None:
        """Open any reader not yet running. Failures are remembered, not raised:
        one arm powered off must not stop the other from being displayed."""
        io = _direct_io()
        with self._lock:
            for side, channel in self._channels.items():
                if side in self._readers:
                    continue
                try:
                    reader = io.StateReader(
                        channel,
                        list(io.OPENARM_RECV_IDS),
                        io.OPENARM_MOTOR_LIMITS,
                        fd=True,
                    )
                    reader.start()
                    self._readers[side] = reader
                    self._failed.pop(side, None)
                    logger.info("torque monitor: passive reader on %s (%s arm)", channel, side)
                except Exception as e:
                    # Typically ENODEV (interface absent) or EPERM.
                    self._failed[side] = str(e)
                    logger.warning("torque monitor: cannot listen on %s: %s", channel, e)

    def read(self) -> dict:
        """Snapshot of every motor's torque, per arm."""
        self.ensure_started()
        io = _direct_io()
        now = time.monotonic()
        arms: dict[str, dict] = {}

        with self._lock:
            readers = dict(self._readers)
            failed = dict(self._failed)

        for side, reader in readers.items():
            feedback = reader.latest_feedback()
            t_max = reader.torque_limits()
            motors = []
            for index, can_id in enumerate(io.OPENARM_RECV_IDS):
                fb = feedback.get(can_id)
                label = JOINT_LABELS[index] if index < len(JOINT_LABELS) else f"id_{can_id:#x}"
                if fb is None:
                    motors.append({
                        "joint": label,
                        "can_id": can_id,
                        "torque_nm": None,
                        "t_max_nm": t_max.get(can_id),
                        "stale": True,
                        "age_ms": None,
                    })
                    continue
                age = now - fb.timestamp
                if label == "finger" and age <= STALE_AFTER_S:
                    # Feeds the cap-enforcement check below.
                    note_torque_sample(side, fb.torque)
                motors.append({
                    "joint": label,
                    "can_id": can_id,
                    "torque_nm": round(fb.torque, 3),
                    "t_max_nm": t_max.get(can_id),
                    "position_rad": round(fb.position, 4),
                    "velocity_rad_s": round(fb.velocity, 3),
                    "t_rotor_c": fb.t_rotor,
                    "stale": age > STALE_AFTER_S,
                    "age_ms": round(age * 1000.0, 1),
                })
            arms[side] = {
                "channel": self._channels[side],
                "motors": motors,
                "frames_decoded": getattr(reader, "frames_decoded", 0),
            }

        for side, error in failed.items():
            arms.setdefault(side, {"channel": self._channels[side], "error": error, "motors": []})

        return {"arms": arms, "units": "Nm", "stale_after_s": STALE_AFTER_S}

    def stop(self) -> None:
        with self._lock:
            for reader in self._readers.values():
                try:
                    reader.stop()
                except Exception:
                    pass
            self._readers.clear()


_monitor: TorqueMonitor | None = None
_monitor_lock = threading.Lock()


def get_monitor() -> TorqueMonitor:
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = TorqueMonitor()
        return _monitor


# ---------------------------------------------------------------------------
# Gripper aperture limit ("force limit")
# ---------------------------------------------------------------------------
#
# With the gripper commanded as a POSITION (it is the 8th entry of the forward
# position controller), closing force is position-error x the hardware's fixed
# GRIPPER_DEFAULT_KP. There is no runtime effort cap to turn down, so bounding
# force means refusing to command it any further closed than the aperture it had
# when the operator pressed Limit. exoskeleton_bridge_node applies the floor to
# the aperture it derives from the trigger, so the value published on
# /exo/gripper_command_m -- and therefore the recorded `action` -- is the
# clamped one, matching what the hardware was actually told to do.

_gripper_limits: dict[str, float] = {}          # active aperture floor, metres
_gripper_torque_limits: dict[str, float] = {}   # requested torque cap, Nm
_gripper_limits_lock = threading.Lock()

# Persisted so a cap survives a restart -- a force limit you have to re-enter
# every session is a limit you will eventually forget to set.
MOTOR_CONFIG_PATH = Path.home() / ".config" / "lelab" / "motor_config.json"
DEFAULT_GRIPPER_TORQUE_CAP_NM = 5.0


def load_gripper_torque_caps() -> dict[str, float]:
    """Persisted per-arm torque caps, defaulting to 5 Nm for both."""
    caps = {
        "left": DEFAULT_GRIPPER_TORQUE_CAP_NM,
        "right": DEFAULT_GRIPPER_TORQUE_CAP_NM,
    }
    try:
        with MOTOR_CONFIG_PATH.open() as fh:
            stored = (json.load(fh) or {}).get("gripper_torque_cap_nm", {})
        for side in ("left", "right"):
            if side in stored and stored[side]:
                caps[side] = float(stored[side])
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("motor_config unreadable (%s); using %.1f Nm defaults",
                       e, DEFAULT_GRIPPER_TORQUE_CAP_NM)
    return caps


def save_gripper_torque_cap(side: str, torque_nm: float) -> dict[str, float]:
    """Persist one arm's cap, atomically, and return all caps."""
    caps = load_gripper_torque_caps()
    caps[side] = float(torque_nm)
    try:
        MOTOR_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MOTOR_CONFIG_PATH.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump({"gripper_torque_cap_nm": caps}, fh, indent=1)
        os.replace(tmp, MOTOR_CONFIG_PATH)   # never leave a half-written config
        logger.info("gripper torque caps persisted: %s", caps)
    except Exception as e:
        logger.error("could not persist gripper torque cap: %s", e)
    return caps


# Peak |torque| seen since a cap was last set, per side. Lets the UI say "the cap
# is not actually being enforced" instead of leaving you to wonder why the number
# went past it -- the usual cause being a bridge still running pre-cap code.
_torque_peaks: dict[str, float] = {}


def note_torque_sample(side: str, torque_nm: float) -> None:
    with _gripper_limits_lock:
        _torque_peaks[side] = max(_torque_peaks.get(side, 0.0), abs(float(torque_nm)))


def reset_torque_peak(side: str) -> None:
    with _gripper_limits_lock:
        _torque_peaks.pop(side, None)


BRIDGE_CAP_STATE_PATH = Path("/tmp/lelab_gripper_cap_state.json")


def bridge_cap_state() -> dict:
    """The bridge's own reported cap/hold/torque, or {'seen': False} if silent.

    Read from a FILE, not a ROS topic. lelab runs in a python3.12 venv with no
    rclpy (which is why _send_ui_command falls back to a subprocess publisher), so
    a subscriber here can never receive anything -- the first attempt at this used
    one and reported `seen: false` forever while the bridge was publishing
    correctly. openarm_camera_bridge_node.py already uses the same file approach
    for /tmp/lelab_camera_status.json.

    Silent means an old bridge or no bridge, and in both cases nothing is
    enforcing the cap.
    """
    try:
        with BRIDGE_CAP_STATE_PATH.open() as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return {"seen": False, "reason": "no state file — bridge is old or not running"}
    except Exception as e:
        return {"seen": False, "reason": f"state file unreadable: {e}"}

    age = time.time() - float(payload.get("updated_at", 0.0))
    payload.update({
        "seen": True,
        "age_s": round(age, 2),
        # The bridge writes this at 5Hz, so a few seconds of silence means it died
        # or is wedged.
        "stale": age > 3.0,
    })
    return payload


def cap_enforcement_report() -> dict:
    """Per side: the cap, the peak torque seen, and whether the cap is holding.

    `enforced: false` means measured torque went meaningfully past the cap, which
    is only possible if something upstream is not applying it.
    """
    with _gripper_limits_lock:
        caps = dict(_gripper_torque_limits)
        peaks = dict(_torque_peaks)
    report = {}
    for side, cap in caps.items():
        peak = peaks.get(side)
        report[side] = {
            "cap_nm": cap,
            "peak_nm": round(peak, 2) if peak is not None else None,
            # 15% over the cap is beyond what one rate-limited setpoint step can
            # explain, so it indicates the clamp is not running rather than
            # ordinary overshoot.
            "enforced": None if peak is None else peak <= cap * 1.15,
        }
    return report

GRIPPER_HOME_PATH = Path(__file__).resolve().parents[2] / "gripper_home.yaml"
# Motor radians spanning fully-closed -> fully-open, magnitude only. Same value
# deploy_act_policy.py uses (it carries the sign; direction is handled with abs
# here because the sign differs per build and only the span matters).
GRIPPER_OPEN_MOTOR_SPAN_RAD = 1.0472
GRIPPER_OPEN_M = 0.044


def gripper_closed_offsets() -> dict[str, float]:
    """Public alias — the recording backend needs these too, and a second copy of
    this loader is how the recorded aperture and the deployed one drift apart."""
    return _gripper_closed_offsets()


def _gripper_closed_offsets() -> dict[str, float]:
    """Per-arm closed-gripper motor angle, from the file deploy already reads.

    Assuming 0.0 rad is wrong on this hardware (the file says 0.0967 / 0.2157),
    and that offset error alone is a third of the gripper's travel.
    """
    defaults = {"left": 0.0, "right": 0.0}
    try:
        import yaml  # noqa: PLC0415

        with GRIPPER_HOME_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        homes = data.get("gripper_home", {})
        return {
            "left": float(homes.get("left_arm_can1", defaults["left"])),
            "right": float(homes.get("right_arm_can0", defaults["right"])),
        }
    except Exception as e:
        logger.warning("cannot read %s (%s); assuming 0 rad closed", GRIPPER_HOME_PATH, e)
        return defaults


def aperture_m_from_motor(side: str, motor_rad: float) -> float:
    """Gripper motor angle -> aperture in metres.

    abs() on both terms deliberately: opening drives the motor NEGATIVE on this
    build, so a signed divide clamps every open reading to 0 — which is exactly
    why the limit button first latched at "0.0 mm".
    """
    closed = _gripper_closed_offsets().get(side, 0.0)
    frac = abs(float(motor_rad) - closed) / abs(GRIPPER_OPEN_MOTOR_SPAN_RAD)
    return float(min(max(frac, 0.0), 1.0) * GRIPPER_OPEN_M)


def set_gripper_limit(side: str, aperture_m: float) -> dict:
    with _gripper_limits_lock:
        _gripper_limits[side] = float(aperture_m)
        return dict(_gripper_limits)


def clear_gripper_limit(side: str) -> dict:
    with _gripper_limits_lock:
        _gripper_limits.pop(side, None)
        _gripper_torque_limits.pop(side, None)
        return dict(_gripper_limits)


def get_gripper_limits() -> dict:
    with _gripper_limits_lock:
        return dict(_gripper_limits)


def set_gripper_torque_limit(side: str, torque_nm: float, enforce_locally: bool = False) -> dict:
    """Record a closing-torque cap for one gripper.

    `enforce_locally` defaults to False because ENFORCEMENT BELONGS IN THE BRIDGE:
    it reads gripper torque off CAN itself and clamps inside its 100 Hz control
    loop, so the cap acts on the tick the force is measured. Doing it from here
    means CAN -> lelab -> ROS -> bridge, which measured 20-40ms of lag and let the
    gripper reach 6.84 Nm against a 4.49 Nm cap before the clamp arrived.

    The local watchdog is kept only as a fallback for a bridge too old to know
    `set_gripper_torque_cap`; it is strictly worse and off by default.
    """
    with _gripper_limits_lock:
        _gripper_torque_limits[side] = float(torque_nm)
    if enforce_locally:
        _ensure_watchdog()
    return get_gripper_torque_limits()


def get_gripper_torque_limits() -> dict:
    with _gripper_limits_lock:
        return dict(_gripper_torque_limits)


_watchdog: threading.Thread | None = None
_watchdog_stop = threading.Event()
_push_limit_hook = None   # set by server.py: callable(side, aperture_m | None)


def set_push_limit_hook(fn) -> None:
    """Register how a new aperture floor reaches the bridge (a ROS publish)."""
    global _push_limit_hook
    _push_limit_hook = fn


def _ensure_watchdog() -> None:
    global _watchdog
    if _watchdog is not None and _watchdog.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog = threading.Thread(target=_watchdog_loop, name="gripper-torque-limit", daemon=True)
    _watchdog.start()
    logger.info("gripper torque-limit watchdog started")


def _watchdog_loop() -> None:
    """Engage an aperture hold the moment measured torque reaches the cap.

    ENGAGE ONLY. The hold is released by the bridge, not here, because release
    depends on the operator's live request -- which only the bridge sees. It
    drops the hold as soon as the trigger opens past it, so the gripper is back
    to full range and full speed for the next close.

    Splitting it that way is deliberate. An earlier version tracked the aperture
    continuously from here (floor = min(floor, aperture)), which meant the hold
    outlived the squeeze that caused it and the gripper could never close as far
    again -- indistinguishable from a broken gripper. The hold now exists only
    while the operator is actively asking for more force than the cap allows.
    """
    PERIOD_S = 0.02           # 50 Hz; motors report at ~203 Hz
    monitor = get_monitor()

    while not _watchdog_stop.is_set():
        try:
            limits = get_gripper_torque_limits()
            if not limits:
                # Nothing to enforce; idle cheaply rather than spinning.
                _watchdog_stop.wait(0.2)
                continue

            data = monitor.read()
            for side, cap in limits.items():
                arm = data.get("arms", {}).get(side) or {}
                gripper = next(
                    (m for m in arm.get("motors", []) if m.get("joint") == "finger"), None
                )
                if not gripper or gripper.get("stale") or gripper.get("torque_nm") is None:
                    continue
                if gripper.get("position_rad") is None:
                    continue

                torque = abs(float(gripper["torque_nm"]))
                if torque < cap:
                    # Below the cap: do not interfere at all. Normal motion,
                    # normal speed, and any previous hold is the bridge's to drop.
                    continue

                aperture = aperture_m_from_motor(side, gripper["position_rad"])
                with _gripper_limits_lock:
                    already = _gripper_limits.get(side)
                    # Only tighten. Re-pushing the same hold every 20ms would
                    # flood the bridge for no benefit.
                    if already is not None and aperture >= already - 0.0003:
                        continue
                    _gripper_limits[side] = aperture

                logger.info(
                    "%s gripper hit %.2f Nm (cap %.2f); holding at %.4f m",
                    side, torque, cap, aperture,
                )
                if _push_limit_hook is not None:
                    try:
                        _push_limit_hook(side, aperture)
                    except Exception as e:
                        logger.debug("could not push gripper hold: %s", e)
        except Exception as e:
            logger.debug("gripper torque watchdog iteration failed: %s", e)

        _watchdog_stop.wait(PERIOD_S)


def stop_watchdog() -> None:
    _watchdog_stop.set()
