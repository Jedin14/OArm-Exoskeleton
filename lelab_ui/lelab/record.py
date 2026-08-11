# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lerobot.configs.dataset import DatasetRecordConfig
from lerobot.datasets import LeRobotDataset
from lerobot.robots.so_follower import SO101FollowerConfig

# Import the main record functionality to reuse it
from lerobot.scripts.lerobot_record import RecordConfig
from lerobot.teleoperators.so_leader import SO101LeaderConfig

from .utils.config import setup_calibration_files, with_lelab_tag

logger = logging.getLogger(__name__)

# Do not allow accidental stop/end commands to create tiny episodes.  This
# also prevents their homing transition from being written as a malformed
# video segment.
MIN_EPISODE_SECONDS = 5.0

# Global variables for recording state
recording_active = False
recording_thread: threading.Thread | None = None
recording_events = None  # Events dict for controlling recording session
recording_config = None  # Store recording configuration
recording_start_time = None  # Track when recording started
session_end_elapsed_seconds = None  # Final session duration after the run ends
current_episode = 1  # Track current episode number
saved_episodes = 0  # Track how many episodes have been saved
current_phase = "preparing"  # Track current phase: "preparing", "recording", "saving", "resetting", "completed"


def _events_state_for_response(events: dict) -> dict:
    """Copy of the events dict safe to log or serialize into an HTTP response.

    Excludes ``_sync_rows``/``_sync_pending_rows``: those accumulate one entry
    per recorded frame for the whole session (tens of thousands by the end)
    and no caller reads them from here — the frontend only reads keys like
    ``_is_homing``/``current_robot_state``/``target_home_state``. Including
    them turned this into an O(total frames so far) dict copy + JSON/repr
    dump, repeated every ~1s status poll and every phase-transition log line,
    which measurably slowed down the live capture loop as a session went on.
    """
    return {k: v for k, v in events.items() if k not in ("_sync_rows", "_sync_pending_rows")}
phase_start_time = None  # Track when current phase started
last_recording_info: dict[str, Any] | None = (
    None  # Snapshot of the most recently completed dataset (for /dataset-info)
)
# Persist the last used home position ID so it is automatically re-used when
# continuing/resuming a recording session without the user having to re-select.
_last_home_position_id: str | None = None
_last_home_robot_name: str | None = None

# Reference to the active robot during recording, so the server can query
# joint positions and camera frames for the live dashboard.
active_robot = None
# Guards the start path so two concurrent POST /start-recording calls cannot
# both pass the active-flag check.
_state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Module-level singleton ROS publisher for /exo/ui_command
# ---------------------------------------------------------------------------
# Created once on first use, reused for all homing/unlock commands.
# This avoids the 2-3 second subprocess startup cost every time.
_ros_ui_cmd_node = None
_ros_ui_cmd_pub = None
_ros_ui_cmd_lock = threading.Lock()

def _send_ui_command(cmd_dict: dict) -> bool:
    """Publish a UI command to /exo/ui_command instantly via in-process ROS publisher.
    Falls back to subprocess if ROS is not available in this process."""
    global _ros_ui_cmd_node, _ros_ui_cmd_pub
    import json
    cmd_str = json.dumps(cmd_dict)
    try:
        import rclpy
        from std_msgs.msg import String
        with _ros_ui_cmd_lock:
            # Initialize rclpy if not already done in this process
            if not rclpy.ok():
                try:
                    rclpy.init()
                except Exception:
                    pass  # Already initialized or not available
            if not rclpy.ok():
                raise RuntimeError("rclpy not available")
            if _ros_ui_cmd_pub is None:
                from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
                _ui_cmd_qos = QoSProfile(
                    depth=10,
                    reliability=ReliabilityPolicy.RELIABLE,
                    durability=DurabilityPolicy.VOLATILE,
                )
                _ros_ui_cmd_node = rclpy.create_node('lelab_ui_cmd_singleton')
                _ros_ui_cmd_pub = _ros_ui_cmd_node.create_publisher(String, '/exo/ui_command', _ui_cmd_qos)
                time.sleep(0.3)  # one-time discovery wait
            msg = String()
            msg.data = cmd_str
            for _ in range(3):  # publish 3x for reliability
                _ros_ui_cmd_pub.publish(msg)
        logger.info(f"_send_ui_command (ROS): {cmd_str}")
        return True
    except Exception as e:
        logger.warning(f"_send_ui_command ROS failed ({e}), falling back to subprocess")
        # Subprocess fallback — always works but has 2-3s startup cost
        try:
            import subprocess, os
            script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
            subprocess.Popen(["/usr/bin/python3", script_path, cmd_str])
            return True
        except Exception as e2:
            logger.error(f"_send_ui_command subprocess fallback also failed: {e2}")
            return False


class RecordingRequest(BaseModel):
    leader_port: str
    follower_port: str
    leader_config: str
    follower_config: str
    dataset_repo_id: str
    single_task: str
    num_episodes: int = 5
    episode_time_s: int = 30
    reset_time_s: int = 10
    fps: int = 30
    video: bool = True
    push_to_hub: bool = False
    tags: list[str] = []
    private: bool = False
    resume: bool = False
    # Keep video encoding off the real-time capture path. Streaming H.264 can
    # backpressure add_frame() and starve the camera/action loop, producing
    # nominally-30-FPS videos with discontinuous source timestamps.
    streaming_encoding: bool = False
    vcodec: str = "auto"
    cameras: dict = {}
    test_mode: bool = False  # Skip robot connection for testing
    dataset_version: str = "v3.0"  # Target version to save dataset in (v2.1 or v3.0)
    arm_mode: str = "both"  # "left", "right", or "both"
    home_position_id: str | None = None  # ID of the chosen arm position for homing
    robot_name: str | None = None  # Name of the robot to lookup position
    include_ee_pose: bool = True  # Add derived ee_pose/gripper_state observation dims (openarm_ros only)


class UploadRequest(BaseModel):
    dataset_repo_id: str
    tags: list[str] = []
    private: bool = False


class DatasetInfoRequest(BaseModel):
    dataset_repo_id: str


class SetEpisodeTaskRequest(BaseModel):
    task: str


def _record_sync_timestamp(robot, events: dict, dataset) -> bool:
    """Record source timing in a sidecar without adding policy inputs."""
    if not hasattr(robot, "get_sync_diagnostics"):
        return False
    diagnostics = robot.get_sync_diagnostics()
    state_ts = float(diagnostics.get("observation.timestamp", 0.0))
    action_ts = float(diagnostics.get("action.timestamp", 0.0))
    camera_ts = float(diagnostics.get("camera.timestamp", 0.0))
    sync_ts = float(diagnostics.get("sync.timestamp", 0.0))
    if not state_ts and not action_ts and not camera_ts:
        return False

    episode_index = getattr(dataset, "num_episodes", None)
    if episode_index is None:
        episode_index = getattr(getattr(dataset, "meta", None), "total_episodes", 0)
    frame_index = int(events.get("_sync_frame_index", 0))
    row = {
        "episode_index": int(episode_index),
        "frame_index": frame_index,
        "state_timestamp": state_ts,
        "action_timestamp": action_ts,
        "camera_timestamp": camera_ts,
        "state_ros_timestamp": float(diagnostics.get("observation.ros_timestamp", 0.0)),
        "action_ros_timestamp": float(diagnostics.get("action.ros_timestamp", 0.0)),
        "sync_timestamp": sync_ts,
        "state_action_delta": abs(state_ts - action_ts),
        "state_camera_delta": abs(state_ts - camera_ts),
        "action_camera_delta": abs(action_ts - camera_ts),
        "max_delta": max(
            abs(state_ts - action_ts),
            abs(state_ts - camera_ts),
            abs(action_ts - camera_ts),
            float(diagnostics.get("max_action_delta_ms", 0.0)) / 1000.0,
        ),
        "max_action_delta_ms": float(diagnostics.get("max_action_delta_ms", 0.0)),
    }
    # camera.*.timestamp is the frame's true capture time. Latency is measured
    # against wall-clock *now*, not sync_ts: sync_ts is itself derived from these
    # capture times, so using it would report ~0 and hide the real staleness.
    _now = time.monotonic()
    for key, value in diagnostics.items():
        if key.startswith("camera.") and key.endswith(".timestamp") and key != "camera.timestamp":
            camera_name = key[len("camera.") : -len(".timestamp")]
            row[f"camera_{camera_name}_timestamp"] = float(value)
            row[f"camera_{camera_name}_latency_ms"] = (_now - float(value)) * 1000.0
    # Keep rows private to the current recording attempt. The caller commits
    # them only after dataset.save_episode() succeeds, so re-recorded attempts
    # never leak into the sidecar.
    events.setdefault("_sync_pending_rows", []).append(row)
    events["_sync_frame_index"] = frame_index + 1
    return True


def _write_sync_sidecar(dataset, rows: list) -> None:
    """Append sync-diagnostic rows to the dataset's sidecar parquet file.

    Called once per episode (right after that episode's dataset.save_episode()
    succeeds) rather than accumulated for the whole session and written once
    at the end: keeping tens of thousands of per-frame dicts alive in memory
    for the session's whole duration made Python's cyclic GC progressively
    slower to scan that ever-growing object graph, which showed up as a
    session-long, worsening capture-loop slowdown (choppier video/state the
    longer a session ran). Writing+dropping each episode's rows immediately
    keeps peak live-object count bounded to a single episode's frames.
    """
    if not rows or not getattr(dataset, "root", None):
        return
    import pandas as pd
    from pathlib import Path

    path = Path(dataset.root) / "meta" / "sync_timestamps.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    output = pd.DataFrame(rows)
    if path.exists():
        output = pd.concat([pd.read_parquet(path), output], ignore_index=True)
    output.to_parquet(path, index=False)
    logger.info("Wrote synchronization sidecar: %s (%d rows)", path, len(output))


def _discard_episode_attempt(dataset) -> None:
    """Discard an in-progress episode, including any video encoder state."""
    writer = getattr(dataset, "writer", None)
    encoder = getattr(writer, "_streaming_encoder", None)
    if encoder is not None:
        # Be explicit here: older LeRobot writer versions only cancelled the
        # encoder indirectly, which could leave discarded frames in a video.
        try:
            encoder.cancel_episode()
        except Exception:
            logger.debug("Streaming encoder was already inactive", exc_info=True)

    try:
        dataset.clear_episode_buffer(delete_images=True)
    except TypeError:
        # LeRobotDatasetV2 exposes the older no-argument signature.
        dataset.clear_episode_buffer()


def _log_sync_rerun(robot, events: dict) -> None:
    """Log frame-alignment diagnostics as Rerun scalar plots."""
    if not hasattr(robot, "get_sync_diagnostics"):
        return
    try:
        import rerun as rr

        diagnostics = robot.get_sync_diagnostics()
        frame_index = int(events.get("_sync_frame_index", 1)) - 1
        rr.set_time_sequence("frame", max(frame_index, 0))

        state_ts = float(diagnostics.get("observation.timestamp", 0.0))
        action_ts = float(diagnostics.get("action.timestamp", 0.0))
        camera_ts = float(diagnostics.get("camera.timestamp", 0.0))
        rr.log("sync/delta/state_action_ms", rr.Scalar(abs(state_ts - action_ts) * 1000.0))
        rr.log("sync/delta/state_camera_ms", rr.Scalar(abs(state_ts - camera_ts) * 1000.0))
        rr.log("sync/delta/action_camera_ms", rr.Scalar(abs(action_ts - camera_ts) * 1000.0))
        rr.log(
            "sync/delta/max_action_ms",
            rr.Scalar(float(diagnostics.get("max_action_delta_ms", 0.0))),
        )
        rr.log("sync/threshold_ms", rr.Scalar(20.0), static=True)
    except Exception:
        # Rerun is an optional visualization path and must never affect capture.
        return


def _extract_camera_names_from_features(features: list[str]) -> list[str]:
    """Extract logical camera names from LeRobot feature keys.

    Example feature key:
      observation.images.main_camera
    -> camera name:
      main_camera
    """
    names: list[str] = []
    prefix = "observation.images."
    for feat in features:
        if not feat.startswith(prefix):
            continue
        cam_name = feat[len(prefix) :]
        if cam_name and cam_name not in names:
            names.append(cam_name)
    return names


def _infer_arm_mode_from_features(features: Any) -> str:
    """Infer which arms a dataset was recorded with from its feature schema.

    Looks at the per-joint `names` of the `action` feature (falling back to
    `observation.state`) and checks for `openarm_left_*` / `openarm_right_*`
    tokens. Returns "both", "left", or "right"; defaults to "both" when the
    schema can't be interpreted.
    """
    names: list[str] = []
    if isinstance(features, dict):
        for key in ("action", "observation.state"):
            feat = features.get(key)
            if isinstance(feat, dict) and isinstance(feat.get("names"), list):
                names = [str(n) for n in feat["names"]]
                break

    joined = " ".join(names).lower()
    has_left = "openarm_left" in joined
    has_right = "openarm_right" in joined
    if has_left and not has_right:
        return "left"
    if has_right and not has_left:
        return "right"
    return "both"


def _infer_include_ee_pose_from_features(features: Any) -> bool:
    """Infer whether a dataset's observation.state includes derived ee_pose/
    gripper_state dims (as opposed to just raw joint positions).

    Looks at `observation.state`'s `names` list for `ee_pose_*` tokens.
    Defaults to True (the long-standing behavior) when the schema can't be
    interpreted, so older datasets recorded before this toggle existed keep
    resuming with the same feature set they were created with.
    """
    if isinstance(features, dict):
        feat = features.get("observation.state")
        if isinstance(feat, dict) and isinstance(feat.get("names"), list):
            names = [str(n) for n in feat["names"]]
            return any(n.startswith("ee_pose_") for n in names)
    return True


def _resolve_local_dataset_dir(repo_id: str) -> Path | None:
    """Resolve a local LeRobot dataset directory for a repo_id.

    Supports:
    - <HF_LEROBOT_HOME>/<repo_id>
    - one-level nested layouts where a folder contains another same-named folder
      produced by some zip extraction flows.
    """
    from lerobot.utils.constants import HF_LEROBOT_HOME

    root = Path(HF_LEROBOT_HOME).expanduser()
    direct = root / repo_id
    info = direct / "meta" / "info.json"
    if info.is_file():
        return direct

    nested = direct / repo_id.split("/")[-1]
    info_nested = nested / "meta" / "info.json"
    if info_nested.is_file():
        return nested

    # Fallback scan (same depth policy as dataset listing).
    parts = repo_id.split("/")
    candidates: list[Path] = []
    if len(parts) == 2:
        candidates.append(root / parts[0] / parts[1])
    else:
        candidates.append(root / parts[0])

    for c in candidates:
        if (c / "meta" / "info.json").is_file():
            return c
        c2 = c / c.name
        if (c2 / "meta" / "info.json").is_file():
            return c2
    return None


def _load_local_dataset_info(repo_id: str) -> dict[str, Any] | None:
    """Load dataset summary from local meta files without Hub access."""
    ds_dir = _resolve_local_dataset_dir(repo_id)
    if ds_dir is None:
        return None

    info_path = ds_dir / "meta" / "info.json"
    tasks_path = ds_dir / "meta" / "tasks.jsonl"
    tasks_parquet_path = ds_dir / "meta" / "tasks.parquet"
    episodes_path = ds_dir / "meta" / "episodes.jsonl"
    episodes_stats_path = ds_dir / "meta" / "episodes_stats.jsonl"

    def _append_task(tasks: list[str], raw_task: Any) -> None:
        t = str(raw_task or "").strip()
        if t and t not in tasks:
            tasks.append(t)

    def _tasks_from_jsonl(path: Path) -> list[str]:
        found: list[str] = []
        if not path.is_file():
            return found
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            # Common legacy/current keys observed across LeRobot metadata variants.
            _append_task(found, obj.get("task"))
            _append_task(found, obj.get("single_task"))
            _append_task(found, obj.get("instruction"))
            # Some variants store nested task metadata.
            task_meta = obj.get("task_metadata")
            if isinstance(task_meta, dict):
                _append_task(found, task_meta.get("task"))
                _append_task(found, task_meta.get("instruction"))
        return found

    def _tasks_from_parquet(path: Path) -> list[str]:
        found: list[str] = []
        if not path.is_file():
            return found
        # Prefer pandas when available (reads legacy/current parquet task tables).
        try:
            import pandas as pd

            df = pd.read_parquet(path)
            if "task" in df.columns:
                for t in df["task"].tolist():
                    _append_task(found, t)
            if not found and getattr(df.index, "name", None) == "task":
                for t in df.index.tolist():
                    _append_task(found, t)
            return found
        except Exception:
            pass
        # Last-resort fallback when parquet readers are unavailable:
        # pull plausible task strings from raw bytes for visibility.
        try:
            raw_bytes = path.read_bytes()
            for m in re.findall(rb"[ -~]{16,}", raw_bytes):
                text = m.decode("utf-8", errors="ignore").strip()
                if "unknown task" in text.lower():
                    continue
                # Filter obvious schema/noise lines.
                if text.lower() in {"pandas", "schema", "task", "task_index"}:
                    continue
                _append_task(found, text)
        except Exception:
            pass
        return found
    try:
        raw = json.loads(info_path.read_text(encoding="utf-8"))
        feature_keys = list((raw.get("features") or {}).keys())
        single_task = "Unknown task"
        tasks: list[str] = []
        # Primary source: tasks.jsonl (newer datasets)
        for t in _tasks_from_jsonl(tasks_path):
            _append_task(tasks, t)
        # Common v3 source: tasks.parquet
        for t in _tasks_from_parquet(tasks_parquet_path):
            _append_task(tasks, t)
        # Fallbacks: older dataset metadata layouts
        for t in _tasks_from_jsonl(episodes_path):
            _append_task(tasks, t)
        for t in _tasks_from_jsonl(episodes_stats_path):
            _append_task(tasks, t)

        # Final fallback from info.json if present.
        info_single_task = raw.get("single_task")
        _append_task(tasks, info_single_task)

        if tasks:
            single_task = tasks[0]

        return {
            "success": True,
            "dataset_repo_id": repo_id,
            "num_episodes": int(raw.get("total_episodes", 0)),
            "single_task": single_task,
            "tasks": tasks,
            "fps": raw.get("fps"),
            "features": feature_keys,
            "camera_names": _extract_camera_names_from_features(feature_keys),
            "total_frames": int(raw.get("total_frames", 0)),
            "robot_type": raw.get("robot_type", "Unknown robot"),
            "codebase_version": raw.get("codebase_version", "v2.1"),
            "arm_mode": _infer_arm_mode_from_features(raw.get("features")),
            "include_ee_pose": _infer_include_ee_pose_from_features(raw.get("features")),
        }
    except Exception as e:
        logger.warning(f"Failed reading local dataset metadata for {repo_id}: {e}")
        return None


def _extract_tasks_from_dataset_meta(meta: Any) -> list[str]:
    """Best-effort extraction of task labels from LeRobotDataset.meta."""
    tasks: list[str] = []
    if meta is None:
        return tasks
    # Newer layouts often expose `meta.tasks` as a pandas object.
    meta_tasks = getattr(meta, "tasks", None)
    if meta_tasks is not None:
        try:
            # DataFrame with "task" column.
            if hasattr(meta_tasks, "columns") and "task" in list(meta_tasks.columns):
                for t in meta_tasks["task"].tolist():
                    tt = str(t or "").strip()
                    if tt and tt not in tasks:
                        tasks.append(tt)
            # Index may itself contain task labels.
            if hasattr(meta_tasks, "index"):
                for t in meta_tasks.index.tolist():
                    tt = str(t or "").strip()
                    if tt and tt not in tasks and tt.lower() != "unknown task":
                        tasks.append(tt)
        except Exception:
            pass

    single = getattr(meta, "single_task", None)
    if isinstance(single, str):
        s = single.strip()
        if s and s not in tasks and s.lower() != "unknown task":
            tasks.append(s)
    return tasks


def create_record_config(request: RecordingRequest) -> RecordConfig:
    """Create a RecordConfig from the recording request"""
    import platform

    from lerobot.cameras.configs import Cv2Backends
    from lerobot.cameras.opencv import OpenCVCameraConfig

    # Pin the backend so the index→camera mapping matches what the
    # /available-cameras thumbnails were captured with. cv2.CAP_ANY can
    # pick different backends across calls on macOS, which silently
    # reorders the cameras between the modal's preview and the recording.
    if platform.system() == "Darwin":
        opencv_backend = Cv2Backends.AVFOUNDATION
    elif platform.system() == "Linux":
        opencv_backend = Cv2Backends.V4L2
    else:
        opencv_backend = Cv2Backends.ANY

    # 🔧 CAMERA CONFIG CONVERSION: Convert frontend camera dict to proper CameraConfig objects
    camera_configs = {}
    for camera_name, camera_data in request.cameras.items():
        if request.arm_mode == "left" and "right" in camera_name.lower():
            continue
        if request.arm_mode == "right" and "left" in camera_name.lower():
            continue

        if camera_data.get("type") == "opencv":
            # Request MJPG (compressed) by default. USB webcams default to raw
            # YUYV (~18 MB/s @ 640x480x30), which saturates a shared USB-2 bus
            # when several cameras stream at once and causes the UVC stalls that
            # show up as "FROZEN" feeds. MJPG cuts that ~10x. lerobot validates
            # FOURCC softly (warns + falls back), so a camera that rejects MJPG
            # keeps working with its default format. Overridable per-camera.
            fourcc = camera_data.get("fourcc", "MJPG")
            camera_configs[camera_name] = OpenCVCameraConfig(
                index_or_path=camera_data.get("camera_index", 0),
                backend=opencv_backend,
                fps=camera_data.get("fps"),
                width=camera_data.get("width"),
                height=camera_data.get("height"),
                fourcc=fourcc,
            )
            logger.info(
                f"✅ CAMERA CONFIG: Converted {camera_name} -> OpenCVCameraConfig(index={camera_data.get('camera_index')}, backend={opencv_backend.name}, {camera_data.get('width')}x{camera_data.get('height')}@{camera_data.get('fps')}fps, fourcc={fourcc})"
            )
        else:
            logger.warning(
                f"⚠️ CAMERA CONFIG: Unsupported camera type '{camera_data.get('type')}' for {camera_name}"
            )

    # Create robot config
    if "openarm_ros" in request.follower_port or "ROS2 (humble)" in request.follower_port:
        from lelab.robots.openarm_ros import OpenArmRosRobotConfig, PassiveROSTeleopConfig
        # Load ROS camera names from mappings file (ignoring UI camera selection for ROS mode)
        import json as _json
        from pathlib import Path as _Path
        _mappings_path = _Path.home() / ".config" / "lelab" / "ros_camera_mappings.json"
        _ros_camera_names = []
        if _mappings_path.is_file():
            try:
                _data = _json.loads(_mappings_path.read_text())
                _ros_camera_names = [m["name"] for m in _data]
            except Exception as e:
                logger.error(f"Failed to read ros_camera_mappings: {e}")
        robot_config = OpenArmRosRobotConfig(
            cameras={},  # No hardware camera objects for ROS mode
            arm_mode=request.arm_mode,
            ros_camera_names=_ros_camera_names,
            include_ee_pose=request.include_ee_pose,
        )
        # We MUST provide a teleop config for lerobot 1.5.0 recording loop
        teleop_config = PassiveROSTeleopConfig()
    else:
        # Setup calibration files (only for hardware robots)
        leader_config_name, follower_config_name = setup_calibration_files(
            request.leader_config, request.follower_config
        )

        robot_config = SO101FollowerConfig(
            port=request.follower_port,
            id=follower_config_name,
            cameras=camera_configs,
        )

        # Create teleop config
        teleop_config = SO101LeaderConfig(
            port=request.leader_port,
            id=leader_config_name,
        )

    # Create dataset config
    dataset_config = DatasetRecordConfig(
        repo_id=request.dataset_repo_id,
        single_task=request.single_task,
        num_episodes=request.num_episodes,
        episode_time_s=request.episode_time_s,
        reset_time_s=request.reset_time_s,
        fps=request.fps,
        video=request.video,
        push_to_hub=request.push_to_hub,
        # Upstream typing: tags is `list[str] | None`. None when push is off
        # keeps the lerobot default.
        tags=with_lelab_tag(request.tags) if request.push_to_hub else None,
        private=request.private,
        streaming_encoding=request.streaming_encoding,
    )
    # LeRobot versions differ on whether vcodec is declared on the config
    # dataclass. Set it after construction. OpenArm capture must never depend
    # on a real-time encoder: encoding while frames are still being captured
    # can backpressure add_frame() and starve the 30 FPS capture loop. That is
    # why streaming_encoding is always forced off below, regardless of vcodec.
    #
    # With streaming disabled, encoding only ever runs inside save_episode()
    # (the "saving" phase, after the episode has already fully landed on
    # disk), fully decoupled from the real-time capture path. There is no
    # longer a reason to force slow software libx264 (vcodec="h264") there:
    # letting it resolve to "auto" picks a hardware encoder (e.g. NVENC) when
    # one is available and falls back to a software codec otherwise, cutting
    # save time without touching the parallel_encoding/image_writer_processes
    # settings that were disabled to work around the encoder-process-pool
    # freeze bug (multiprocessing forking this process's live ROS sockets and
    # threads is what caused that, not the codec choice).
    if getattr(robot_config, "type", "") == "openarm_ros":
        # Video encoding strategy.  With streaming off, LeRobot writes one PNG
        # per frame per camera and re-encodes them at save time.  Measured on
        # this hardware with real 640x480 camera frames: PNG encode is ~20ms
        # per image (plus ~900KB written to disk), so two 30fps cameras cost
        # ~1.2 cores and ~54MB/s continuously, then pay another ~6ms/frame PNG
        # decode during save_episode().  Streaming straight into a hardware
        # H.264 encoder measured ~1.6ms/frame instead, uses one thread per
        # camera rather than eight PNG writer threads, writes no intermediate
        # files, and makes save_episode() near-instant.
        #
        # Only enable it when a hardware encoder genuinely exists.  That is the
        # real constraint behind this having been forced off before: a software
        # fallback (libsvtav1/libx264) is slow enough that feed_frame()'s bounded
        # queue fills and backpressures the 30fps capture loop.  Hardware
        # encoding has ~20x headroom against the 33ms frame budget, software
        # does not.
        # Codec choice is deliberately software libx264, NOT the GPU encoder.
        # An nvenc encoder *session* is created lazily inside its first encode
        # call and takes ~163ms while holding the GIL, and one session is
        # created per camera per episode -- which stalled the record loop by
        # ~326ms once per episode and is exactly the frame0->frame1 gap seen in
        # recorded datasets.  libx264 initializes in ~2ms.  Its higher
        # per-frame cost (1.5ms vs 0.5ms) does not matter: that work runs on a
        # background encoder thread which releases the GIL, and two 30fps
        # cameras need only ~9% of one core.
        try:
            import av

            av.codec.Codec("libx264", "w")
            dataset_config.streaming_encoding = True
            dataset_config.vcodec = "h264"  # PyAV maps this to libx264
            logger.info(
                "OpenArm recording: streaming video encoding enabled (libx264, "
                "no per-episode encoder-session stall)"
            )
        except Exception as e:
            dataset_config.streaming_encoding = False
            dataset_config.vcodec = "h264"
            logger.warning(
                "OpenArm recording: libx264 unavailable (%s); falling back to the "
                "PNG-then-encode path. Expect higher CPU load during capture.",
                e,
            )
    else:
        dataset_config.vcodec = request.vcodec

    # Create the main record config
    record_config = RecordConfig(
        robot=robot_config,
        teleop=teleop_config,
        dataset=dataset_config,
        resume=request.resume,
        display_data=False,  # Don't display data in API mode
        play_sounds=False,  # Don't play sounds in API mode
    )

    return record_config


def handle_start_recording(request: RecordingRequest) -> dict[str, Any]:
    """Handle start recording request by using the existing record() function"""
    global \
        recording_active, \
        recording_thread, \
        recording_events, \
        recording_config, \
        recording_start_time, \
        session_end_elapsed_seconds, \
        current_episode, \
        saved_episodes, \
        current_phase, \
        phase_start_time, \
        last_recording_info, \
        active_robot, \
        _last_home_position_id, \
        _last_home_robot_name

    from . import rollout as _rollout, teleoperate as _teleoperate

    # Claim the active flag under the lock so two concurrent starts can't both
    # pass the precondition check.
    with _state_lock:
        if recording_active:
            return {"success": False, "message": "Recording is already active"}
        if _teleoperate.teleoperation_active:
            return {"success": False, "message": "Teleoperation is currently active. Stop it first."}
        if _rollout.inference_active:
            return {"success": False, "message": "Inference is currently active. Stop it first."}
        recording_active = True
        recording_thread = None
        recording_events = None
        recording_config = None
        recording_start_time = None
        session_end_elapsed_seconds = None
        current_episode = 1
        saved_episodes = 0
        current_phase = "preparing"
        phase_start_time = None
        last_recording_info = None

    try:
        # Sanitize the dataset name so push_to_hub never rejects a finished
        # recording over an invalid character. HF repo names allow only
        # [A-Za-z0-9._-]; everything else becomes "_".
        if request.dataset_repo_id:
            if "/" in request.dataset_repo_id:
                namespace, name = request.dataset_repo_id.split("/", 1)
                name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
                request.dataset_repo_id = f"{namespace}/{name}"
            else:
                request.dataset_repo_id = re.sub(r"[^A-Za-z0-9._-]", "_", request.dataset_repo_id)
        # Stamp the repo_id with a timestamp (matches lerobot-record CLI behavior),
        # so each session lands in a unique directory and the frontend gets the
        # final id back in the response and status payload.
        if not request.resume and request.dataset_repo_id:
            request.dataset_repo_id = f"{request.dataset_repo_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting recording for dataset: {request.dataset_repo_id}")
        logger.info(f"Task: {request.single_task}")

        from lelab.server import global_persistent_locks

        # A persistent lock is a per-session choice ("keep this arm locked even
        # while recording"), but global_persistent_locks is a module global that
        # nothing ever clears, so a lock set in an earlier session silently
        # carried into the next one and suppressed that arm's auto-unlock. Start
        # each session from a clean slate; the Lock buttons still work mid-run.
        for _arm in ("left", "right"):
            if global_persistent_locks.get(_arm):
                logger.info("Clearing stale persistent %s-arm lock from a previous session", _arm)
            global_persistent_locks[_arm] = False

        recording_config = request
        recording_events = {
            "exit_early": False,  # Right arrow key -> "Skip to next episode" button
            "stop_recording": False,  # ESC key -> "Stop recording" button
            "rerecord_episode": False,  # Left arrow key -> "Re-record episode" button
            "current_task": request.single_task,
            "persistent_left_lock": global_persistent_locks.get("left", False),
            "persistent_right_lock": global_persistent_locks.get("right", False),
            "target_home_state": None,
            "current_robot_state": None,
            "_arm_mode": getattr(request, "arm_mode", "both"),  # "left", "right", or "both"
        }

        if request.home_position_id and request.robot_name:
            from lelab.utils.config import get_arm_positions
            positions = get_arm_positions(request.robot_name)
            for p in positions:
                if p["id"] == request.home_position_id:
                    recording_events["target_home_state"] = p["joint_values"]
                    logger.info(f"Loaded custom home position: {p['name']}")
                    # Persist so future resume sessions reuse this automatically
                    _last_home_position_id = request.home_position_id
                    _last_home_robot_name = request.robot_name
                    break
        elif _last_home_position_id and _last_home_robot_name:
            # No home position selected this session — reuse the last one
            from lelab.utils.config import get_arm_positions
            positions = get_arm_positions(_last_home_robot_name)
            for p in positions:
                if p["id"] == _last_home_position_id:
                    recording_events["target_home_state"] = p["joint_values"]
                    logger.info(f"Reusing previously selected home position: {p['name']}")
                    break

        record_config = create_record_config(request)

        def recording_worker():
            global \
                recording_active, \
                recording_start_time, \
                session_end_elapsed_seconds, \
                current_phase, \
                phase_start_time, \
                current_episode, \
                saved_episodes, \
                last_recording_info, \
                active_robot
            recording_start_time = time.time()
            current_episode = 1
            
            # Start background thread to poll gamepad buttons
            def gamepad_poller():
                last_buttons = []
                while recording_active:
                    if active_robot and hasattr(active_robot, 'latest_buttons'):
                        btns = active_robot.latest_buttons
                        # /exo/gamepad_keys layout:
                        #   left:  4=joystick, 5=A, 6=B, 7=C
                        #   right: 10=joystick, 11=A, 12=B, 13=C
                        # Physical mapping for this exoskeleton:
                        #   A/red  -> finish current episode / advance
                        #   B/blue -> pause or resume recording
                        #   C/white -> re-record current episode
                        if len(btns) >= 14 and len(last_buttons) >= 14:
                            rising = [
                                index for index in (5, 6, 7, 11, 12, 13)
                                if btns[index] == 1 and last_buttons[index] == 0
                            ]
                            # Trigger each logical color at most once when
                            # both handles are pressed together.
                            if any(index in rising for index in (5, 11)):
                                handle_exit_early()
                            elif any(index in rising for index in (6, 12)):
                                handle_toggle_pause()
                            elif any(index in rising for index in (7, 13)):
                                handle_rerecord_episode()
                        last_buttons = btns
                    time.sleep(0.05)
            
            threading.Thread(target=gamepad_poller, daemon=True).start()

            saved_episodes = 0

            try:
                logger.info(
                    "Recording session started: dataset=%s task=%r episodes=%d",
                    request.dataset_repo_id,
                    request.single_task,
                    request.num_episodes,
                )

                # Give the frontend's camera streams time to release the
                # underlying devices before lerobot tries to open them.
                if request.cameras:
                    logger.info(
                        "Waiting for camera resources to be released (cameras: %s)",
                        list(request.cameras.keys()),
                    )
                    time.sleep(3.0)

                dataset = record_with_web_events(record_config, recording_events, request.dataset_version)
                if recording_events.get("discard_recording"):
                    logger.info("Discarding dataset recording as requested...")
                    if hasattr(dataset, "finalize"):
                        logger.info("Finalizing dataset buffers before discard...")
                        dataset.finalize()
                    import shutil
                    if dataset.root and dataset.root.exists():
                        if not getattr(request, "resume", False):
                            shutil.rmtree(dataset.root, ignore_errors=True)
                            logger.info(f"Deleted dataset directory {dataset.root}")
                        else:
                            logger.info(f"Session discarded. Root directory {dataset.root} was NOT deleted because this was a resumed dataset.")
                    current_phase = "discarded"
                    last_recording_info = {"success": False, "error": "Recording was discarded by user."}
                else:
                    logger.info(f"Recording completed successfully. Dataset has {dataset.num_episodes} episodes")
                    if hasattr(dataset, "finalize"):
                        logger.info("Finalizing dataset buffers...")
                        dataset.finalize()
                    features = list(dataset.features.keys())
                    last_recording_info = {
                        "success": True,
                        "dataset_repo_id": request.dataset_repo_id,
                        "num_episodes": dataset.num_episodes,
                        "single_task": request.single_task,
                        "tasks": [request.single_task],
                        "fps": dataset.fps,
                        "features": features,
                        "camera_names": _extract_camera_names_from_features(features),
                        "total_frames": dataset.num_frames,
                        "robot_type": getattr(dataset.meta, "robot_type", "Unknown robot"),
                        "codebase_version": request.dataset_version,
                        "arm_mode": request.arm_mode,
                    }
            except Exception as e:
                import traceback
                with open("/tmp/crash.log", "w") as f:
                    traceback.print_exc(file=f)
                logger.exception("Recording session failed")
                current_phase = "error"
                if recording_start_time:
                    session_end_elapsed_seconds = int(time.time() - recording_start_time)
                last_recording_info = {"success": False, "error": str(e)}
            finally:
                if current_phase not in ["error", "discarded"]:
                    current_phase = "completed"
                if recording_start_time:
                    session_end_elapsed_seconds = int(time.time() - recording_start_time)
                recording_active = False
                recording_start_time = None
                phase_start_time = None
                current_episode = 1
                saved_episodes = 0
                active_robot = None
                logger.info("Recording session ended")

        recording_thread = threading.Thread(target=recording_worker, name="recording-worker", daemon=True)
        recording_thread.start()

        return {
            "success": True,
            "message": "Recording started successfully",
            "dataset_id": request.dataset_repo_id,
            "num_episodes": request.num_episodes,
        }

    except Exception as e:
        recording_active = False
        logger.error(f"Failed to start recording: {e}")
        return {"success": False, "message": f"Failed to start recording: {str(e)}"}


def handle_stop_recording() -> dict[str, Any]:
    """Handle stop recording request - replaces ESC key"""
    global current_phase, phase_start_time

    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}

    if current_phase == "recording" and phase_start_time is not None:
        elapsed = time.time() - phase_start_time
        if elapsed < MIN_EPISODE_SECONDS:
            logger.info(
                "Ignoring stop request %.2fs into episode; minimum is %.1fs",
                elapsed,
                MIN_EPISODE_SECONDS,
            )
            return {
                "success": False,
                "message": f"Stop ignored: record at least {MIN_EPISODE_SECONDS:.0f} seconds of the episode first",
                "minimum_episode_seconds": MIN_EPISODE_SECONDS,
                "episode_elapsed_seconds": max(0.0, elapsed),
            }

    recording_events["stop_recording"] = True
    recording_events["exit_early"] = True
    # A stop request ends the current episode after homing; it must not be
    # mistaken for a natural timeout, which would discard the episode and
    # enter the re-record/reset path before the arms finish moving home.
    recording_events["_exit_early_triggered"] = True
    current_phase = "stopping"
    phase_start_time = None
    logger.info("Stop recording triggered from web interface")
    return {
        "success": True,
        "message": "Recording stop requested successfully",
        "session_ending": True,
    }


def handle_discard_recording() -> dict[str, Any]:
    """Handle discard recording request - stops and deletes dataset"""
    global current_phase, phase_start_time

    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}

    recording_events["stop_recording"] = True
    recording_events["exit_early"] = True
    recording_events["discard_recording"] = True
    current_phase = "stopping"
    phase_start_time = None
    logger.info("Discard recording triggered from web interface")
    return {
        "success": True,
        "message": "Recording discard requested successfully",
        "session_ending": True,
    }


def handle_exit_early() -> dict[str, Any]:
    """Handle exit early request - replaces right arrow key"""
    import time
    now = time.time()
    last_called = getattr(handle_exit_early, "_last_called", 0)
    if now - last_called < 1.0:
        logger.info("Ignoring exit_early request (debounced)")
        return {"success": False, "message": "Ignoring repeated press (debounce)"}
    handle_exit_early._last_called = now

    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}

    if current_phase == "recording" and phase_start_time is not None:
        elapsed = time.time() - phase_start_time
        if elapsed < MIN_EPISODE_SECONDS:
            logger.info(
                "Ignoring end-episode request %.2fs into episode; minimum is %.1fs",
                elapsed,
                MIN_EPISODE_SECONDS,
            )
            return {
                "success": False,
                "message": f"End Episode ignored: record at least {MIN_EPISODE_SECONDS:.0f} seconds first",
                "minimum_episode_seconds": MIN_EPISODE_SECONDS,
                "episode_elapsed_seconds": max(0.0, elapsed),
            }

    recording_events["exit_early"] = True
    # Tracking flag that record_loop won't reset, so the worker can tell
    # "user pressed skip" from "control_time_s elapsed naturally".
    recording_events["_exit_early_triggered"] = True
    logger.info("Exit early triggered (current phase: %s)", current_phase)
    phase_name = "recording phase" if current_phase == "recording" else "reset phase"
    return {
        "success": True,
        "message": f"Exit early triggered successfully for {phase_name}",
        "current_phase": current_phase,
        "events_state": _events_state_for_response(recording_events),
    }


def handle_rerecord_episode() -> dict[str, Any]:
    """Handle rerecord episode request - replaces left arrow key"""
    import time
    now = time.time()
    last_called = getattr(handle_rerecord_episode, "_last_called", 0)
    if now - last_called < 1.0:
        logger.info("Ignoring rerecord request (debounced)")
        return {"success": False, "message": "Ignoring repeated press (debounce)"}
    handle_rerecord_episode._last_called = now

    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}
    recording_events["rerecord_episode"] = True
    recording_events["exit_early"] = True
    logger.info("Re-record episode triggered")
    return {
        "success": True,
        "message": "Re-record episode requested successfully",
        "events_state": _events_state_for_response(recording_events),
    }


def handle_recording_status() -> dict[str, Any]:
    """Handle recording status request"""
    # If recording is not active and phase is completed, error, or discarded, indicate session has ended
    session_ended = not recording_active and current_phase in ["completed", "error", "discarded"]

    # Log when session has ended to help debug frontend polling
    if session_ended:
        if current_phase == "error":
            logger.info(
                "📡 RECORDING STATUS REQUEST: Session failed with error - frontend should stop polling"
            )
            print("📡 STATUS CHANGE: Frontend is still polling after session error - should stop now")
        elif current_phase == "discarded":
            logger.info("📡 RECORDING STATUS REQUEST: Session was discarded - frontend should stop polling")
        else:
            logger.info("📡 RECORDING STATUS REQUEST: Session has ended - frontend should stop polling")
            print("📡 STATUS CHANGE: Frontend is still polling after session end - should stop now")

    is_paused = recording_events.get("pause_recording", False) if recording_events else False
    status = {
        "recording_active": recording_active,
        "current_phase": current_phase,  # "preparing", "recording", "saving", "resetting", "completed"
        "session_ended": session_ended,  # New field to indicate session completion
        "is_paused": is_paused,
        "available_controls": {
            "stop_recording": recording_active,  # ESC key replacement
            "exit_early": recording_active,  # Right arrow key replacement
            "rerecord_episode": recording_active
            and current_phase == "recording",  # Only during recording phase
            "toggle_pause": recording_active and current_phase in ("recording", "resetting"),
        },
        "message": "Recording session failed with error - check logs"
        if current_phase == "error"
        else (
            "Recording session discarded"
            if current_phase == "discarded"
            else (
                "Recording session has ended - stop polling"
                if session_ended
                else "Recording status retrieved successfully"
            )
        ),
        "error": (
            last_recording_info.get("error")
            if current_phase == "error" and last_recording_info
            else None
        ),
    }

    if recording_active and recording_events:
        status["events_state"] = _events_state_for_response(recording_events)

    # Always echo the stamped dataset id whenever a config exists, so the frontend
    # can read the actual on-disk repo_id (post stamp) for upload navigation.
    if recording_config:
        status["dataset_repo_id"] = recording_config.dataset_repo_id

    # Add episode information if recording is active
    if recording_active and recording_config:
        status["current_episode"] = current_episode
        status["total_episodes"] = recording_config.num_episodes
        status["saved_episodes"] = saved_episodes  # Track completed episodes
        status["current_task"] = recording_events.get("current_task", recording_config.single_task) if recording_events else recording_config.single_task

        # Add session start time if available
        if recording_start_time:
            status["session_start_time"] = recording_start_time
            status["session_elapsed_seconds"] = int(time.time() - recording_start_time)

        # Add phase timing information
        if phase_start_time:
            status["phase_start_time"] = phase_start_time
            status["phase_elapsed_seconds"] = int(time.time() - phase_start_time)

            # Add phase time limits
            if current_phase == "recording":
                status["phase_time_limit_s"] = max(
                    MIN_EPISODE_SECONDS, recording_config.episode_time_s
                )
            elif current_phase == "resetting":
                status["phase_time_limit_s"] = recording_config.reset_time_s
    elif session_end_elapsed_seconds is not None:
        status["session_elapsed_seconds"] = session_end_elapsed_seconds

    # Add joint positions if active_robot supports it
    if recording_active and active_robot and hasattr(active_robot, "get_joint_positions"):
        try:
            status["joint_positions"] = active_robot.get_joint_positions()
        except Exception as e:
            logger.debug(f"Failed to get joint positions for status: {e}")

    # Name the frozen camera(s) so the UI can tell the operator which feed to
    # fix rather than just that "a" camera stalled.
    if recording_active and active_robot is not None:
        frozen_map = getattr(active_robot, "_camera_frozen", None)
        if isinstance(frozen_map, dict):
            status["frozen_cameras"] = sorted(
                name for name, is_frozen in frozen_map.items() if is_frozen
            )

    return status


def handle_get_dataset_info(request: DatasetInfoRequest) -> dict[str, Any]:
    """Return dataset metadata — from the most recent session if it matches,
    otherwise by loading the local LeRobot cache copy."""
    if last_recording_info and last_recording_info.get("dataset_repo_id") == request.dataset_repo_id:
        return last_recording_info

    # Prefer direct local metadata load to avoid accidental Hub 404s in
    # mixed local-only / private-repo setups.
    local_info = _load_local_dataset_info(request.dataset_repo_id)

    try:
        from lerobot.datasets import LeRobotDataset
        from lerobot.utils.constants import HF_LEROBOT_HOME
        from pathlib import Path as _Path

        _local_root = _Path(HF_LEROBOT_HOME).expanduser() / request.dataset_repo_id
        dataset = LeRobotDataset(request.dataset_repo_id, root=_local_root)
        features = list(dataset.features.keys())
        dataset_tasks = _extract_tasks_from_dataset_meta(getattr(dataset, "meta", None))
        dataset_single_task = dataset_tasks[0] if dataset_tasks else "Unknown task"

        if local_info is not None:
            local_tasks = local_info.get("tasks") or []
            local_single = str(local_info.get("single_task") or "").strip()
            # If local parsing was weak (e.g. no jsonl but parquet exists),
            # enrich from LeRobotDataset meta-derived tasks.
            if not local_tasks or local_single.lower() == "unknown task":
                merged_tasks: list[str] = []
                for t in [*local_tasks, *dataset_tasks]:
                    tt = str(t or "").strip()
                    if tt and tt not in merged_tasks:
                        merged_tasks.append(tt)
                local_info["tasks"] = merged_tasks
                local_info["single_task"] = (
                    merged_tasks[0] if merged_tasks else dataset_single_task
                )
            return local_info

        return {
            "success": True,
            "dataset_repo_id": request.dataset_repo_id,
            "num_episodes": dataset.num_episodes,
            "single_task": dataset_single_task,
            "tasks": dataset_tasks,
            "fps": dataset.fps,
            "features": features,
            "camera_names": _extract_camera_names_from_features(features),
            "total_frames": dataset.num_frames,
            "robot_type": getattr(dataset.meta, "robot_type", "Unknown robot"),
            "codebase_version": getattr(dataset.meta, "codebase_version", "v3.0"),
            "arm_mode": _infer_arm_mode_from_features(dataset.features),
            "include_ee_pose": _infer_include_ee_pose_from_features(dataset.features),
        }
    except Exception as e:
        if local_info is not None:
            return local_info
        logger.warning(f"Could not load local dataset {request.dataset_repo_id}: {e}")
        return {
            "success": False,
            "message": f"Dataset {request.dataset_repo_id} not found locally",
        }


def handle_delete_dataset(request: DatasetInfoRequest) -> dict[str, Any]:
    """Remove a recorded dataset's directory from local disk."""
    global last_recording_info
    from pathlib import Path

    from lerobot.utils.constants import HF_LEROBOT_HOME

    repo_id = request.dataset_repo_id
    root = Path(HF_LEROBOT_HOME).resolve()
    target = (root / repo_id).resolve()

    # Reject path traversal: target must stay strictly inside HF_LEROBOT_HOME.
    if target == root or root not in target.parents:
        return {"success": False, "message": "Invalid dataset path"}

    if not target.exists():
        return {"success": False, "message": f"Dataset not found on disk: {repo_id}"}

    try:
        shutil.rmtree(target)
    except Exception as e:
        logger.error(f"Failed to delete dataset {repo_id}: {e}")
        return {"success": False, "message": f"Failed to delete dataset: {e}"}

    if last_recording_info and last_recording_info.get("dataset_repo_id") == repo_id:
        last_recording_info = None

    logger.info(f"Deleted dataset directory {target}")
    return {"success": True, "message": f"Deleted {repo_id}"}


def handle_upload_dataset(request: UploadRequest) -> dict[str, Any]:
    """Handle dataset upload to HuggingFace Hub"""
    try:
        # Import LeRobotDataset to load and upload the dataset
        from lerobot.datasets import LeRobotDataset
        from lerobot.utils.constants import HF_LEROBOT_HOME
        from pathlib import Path as _Path

        logger.info(f"Loading dataset {request.dataset_repo_id} for upload")

        # Load the dataset from local storage
        _local_root = _Path(HF_LEROBOT_HOME).expanduser() / request.dataset_repo_id
        dataset = LeRobotDataset(request.dataset_repo_id, root=_local_root)

        logger.info(f"Dataset loaded with {dataset.num_episodes} episodes")
        tags = with_lelab_tag(request.tags)
        logger.info(f"Uploading to HuggingFace Hub with tags: {tags}, private: {request.private}")

        # Upload dataset to HuggingFace Hub
        dataset.push_to_hub(tags=tags, private=request.private)

        logger.info(f"Dataset {request.dataset_repo_id} uploaded successfully to HuggingFace Hub")

        return {
            "success": True,
            "message": f"Dataset {request.dataset_repo_id} uploaded successfully to HuggingFace Hub",
            "dataset_url": f"https://huggingface.co/datasets/{request.dataset_repo_id}",
            "num_episodes": dataset.num_episodes,
        }

    except Exception as e:
        logger.error(f"Error uploading dataset {request.dataset_repo_id}: {e}")
        import traceback

        logger.error(f"Full traceback: {traceback.format_exc()}")

        err_text = str(e).lower()
        looks_like_auth = any(
            m in err_text
            for m in ("401", "you must be authenticated", "authentication required", "huggingfacehub_token")
        )
        if looks_like_auth:
            return {
                "success": False,
                "message": "You're not logged into the Hugging Face Hub. Run `hf auth login` in your terminal, then retry.",
                "docs_url": "https://huggingface.co/docs/huggingface_hub/en/quick-start#authentication",
            }
        return {"success": False, "message": f"Failed to upload dataset: {str(e)}"}



from lerobot.datasets import safe_stop_image_writer
from lerobot.processor import RobotAction, RobotObservation, RobotProcessorPipeline
from lerobot.robots import Robot
from lerobot.teleoperators import Teleoperator
from lerobot.teleoperators.keyboard import KeyboardTeleop
from lerobot.utils.constants import ACTION, OBS_STR
from lerobot.utils.feature_utils import build_dataset_frame
from lerobot.utils.robot_utils import precise_sleep
from lerobot.utils.visualization_utils import log_rerun_data

@safe_stop_image_writer
def custom_custom_record_loop(
    robot: Robot,
    events: dict,
    fps: int,
    teleop_action_processor: RobotProcessorPipeline,
    robot_action_processor: RobotProcessorPipeline,
    robot_observation_processor: RobotProcessorPipeline,
    dataset = None,
    teleop = None,
    control_time_s: int | None = None,
    single_task: str | None = None,
    display_data: bool = False,
    display_compressed_images: bool = False,
):
    import time
    if dataset is not None and dataset.fps != fps:
        raise ValueError(f"The dataset fps should be equal to requested fps ({dataset.fps} != {fps}).")

    teleop_arm = teleop_keyboard = None
    if isinstance(teleop, list):
        teleop_keyboard = next((t for t in teleop if isinstance(t, KeyboardTeleop)), None)
        teleop_arm = next((t for t in teleop if not isinstance(t, KeyboardTeleop)), None)

    control_interval = 1 / fps
    no_action_count = 0
    timestamp = 0
    start_episode_t = time.perf_counter()
    
    total_paused_time = 0.0
    was_paused = False
    pause_start_t = 0.0

    while timestamp < control_time_s:
        start_loop_t = time.perf_counter()

        if events.get("exit_early"):
            events["exit_early"] = False
            break
            
        is_paused = events.get("pause_recording", False)
        if is_paused and not was_paused:
            was_paused = True
            pause_start_t = time.perf_counter()
        elif not is_paused and was_paused:
            was_paused = False
            total_paused_time += time.perf_counter() - pause_start_t

        # Get robot observation
        obs = robot.get_observation()
        obs_processed = robot_observation_processor(obs)

        if dataset is not None:
            observation_frame = build_dataset_frame(dataset.features, obs_processed, prefix=OBS_STR)

        # Get action from teleop
        if isinstance(teleop, Teleoperator):
            act = teleop.get_action()
            if robot.name == "unitree_g1":
                teleop.send_feedback(obs)

            act_processed_teleop = teleop_action_processor((act, obs))
            action_values = act_processed_teleop
            robot_action_to_send = robot_action_processor((act_processed_teleop, obs))

        elif isinstance(teleop, list):
            arm_action = teleop_arm.get_action()
            arm_action = {f"arm_{k}": v for k, v in arm_action.items()}
            keyboard_action = teleop_keyboard.get_action()
            base_action = robot._from_keyboard_to_base_action(keyboard_action)
            act = {**arm_action, **base_action} if len(base_action) > 0 else arm_action
            act_processed_teleop = teleop_action_processor((act, obs))
            action_values = act_processed_teleop
            robot_action_to_send = robot_action_processor((act_processed_teleop, obs))
        else:
            no_action_count += 1
            if no_action_count == 1 or no_action_count % 10 == 0:
                logging.warning("No teleoperator provided, skipping action generation.")
            continue

        # Send action to robot
        _sent_action = robot.send_action(robot_action_to_send)

        # Write to dataset
        if dataset is not None and not is_paused:
            action_frame = build_dataset_frame(dataset.features, action_values, prefix=ACTION)
            frame = {**observation_frame, **action_frame, "task": single_task}
            dataset.add_frame(frame)

        if display_data:
            _log_sync_rerun(robot, events)
            log_rerun_data(
                observation=obs_processed, action=action_values, compress_images=display_compressed_images
            )

        dt_s = time.perf_counter() - start_loop_t
        if dt_s > 0.050:
            logging.warning(f'Slow loop: {dt_s*1000:.1f}ms')
        sleep_time_s = control_interval - dt_s
        if sleep_time_s < 0:
            pass # ignore slow loop warnings to prevent spam
        precise_sleep(max(sleep_time_s, 0.0))

        current_pause_duration = (time.perf_counter() - pause_start_t) if is_paused else 0.0
        timestamp = time.perf_counter() - start_episode_t - total_paused_time - current_pause_duration

def handle_toggle_pause() -> dict:
    """Toggle pause state of recording"""
    global recording_events, current_phase
    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}
    if current_phase not in ("recording", "resetting"):
        return {"success": False, "message": "Can only pause during recording or resetting phase"}
    current_state = recording_events.get("pause_recording", False)
    recording_events["pause_recording"] = not current_state
    logger.info(f"Recording pause state toggled to: {not current_state}")
    return {"success": True, "is_paused": not current_state}



from lerobot.datasets import safe_stop_image_writer
from lerobot.utils.feature_utils import build_dataset_frame
from lerobot.datasets import LeRobotDataset
from lerobot.processor import RobotProcessorPipeline
from lerobot.robots import Robot
from lerobot.teleoperators import Teleoperator
from lerobot.teleoperators.keyboard import KeyboardTeleop
from lerobot.utils.visualization_utils import log_rerun_data
from lerobot.utils.robot_utils import precise_sleep
import time
import logging
from lerobot.utils.constants import ACTION, OBS_STR

def custom_record_loop(
    robot: Robot,
    events: dict,
    fps: int,
    teleop_action_processor: RobotProcessorPipeline[
        tuple[RobotAction, RobotObservation], RobotAction
    ],  # runs after teleop
    robot_action_processor: RobotProcessorPipeline[
        tuple[RobotAction, RobotObservation], RobotAction
    ],  # runs before robot
    robot_observation_processor: RobotProcessorPipeline[
        RobotObservation, RobotObservation
    ],  # runs after robot
    dataset: LeRobotDataset | None = None,
    teleop: Teleoperator | list[Teleoperator] | None = None,
    control_time_s: int | None = None,
    single_task: str | None = None,
    display_data: bool = False,
    display_compressed_images: bool = False,
):
    global current_phase, phase_start_time

    if dataset is not None and dataset.fps != fps:
        raise ValueError(f"The dataset fps should be equal to requested fps ({dataset.fps} != {fps}).")

    teleop_arm = teleop_keyboard = None
    if isinstance(teleop, list):
        teleop_keyboard = next((t for t in teleop if isinstance(t, KeyboardTeleop)), None)
        teleop_arm = next(
            (
                t
                for t in teleop
                if isinstance(
                    t,
                    (
                        so_leader.SO100Leader
                        | so_leader.SO101Leader
                        | koch_leader.KochLeader
                        | omx_leader.OmxLeader
                    ),
                )
            ),
            None,
        )

        if not (teleop_arm and teleop_keyboard and len(teleop) == 2 and robot.name == "lekiwi_client"):
            raise ValueError(
                "For multi-teleop, the list must contain exactly one KeyboardTeleop and one arm teleoperator. Currently only supported for LeKiwi robot."
            )

    control_interval = 1 / fps

    no_action_count = 0
    timestamp = 0
    start_episode_t = time.perf_counter()
    def _cameras_frozen() -> bool:
        return bool(getattr(robot, "_camera_frozen", None)) and any(robot._camera_frozen.values())

    events["_is_homing"] = False
    events["_homing_resend_gave_up"] = False
    events["_homing_operator_override"] = False
    # A pause (manual button-B toggle, or an auto camera-freeze pause) must not
    # leak into the next phase.  Without this, a pause left active when this
    # phase's control_time_s naturally elapsed (or exit_early broke out of it)
    # would immediately re-enter the pause-wait spin loop below at the start
    # of the *next* phase, freezing phase_elapsed_seconds at ~0 indefinitely
    # since that spin loop advances phase_start_time in lockstep with
    # wall-clock time while paused.
    events["pause_recording"] = False
    events["_freeze_paused"] = False

    # Auto-unlock arms based on arm_mode when each recording episode starts.
    # Uses module-level singleton publisher (_send_ui_command) - instant, no subprocess.
    if dataset is not None:
        arm_mode = events.get("_arm_mode", "both")
        unlock_left  = (arm_mode in ("both", "left"))  and not events.get("persistent_left_lock", False)
        unlock_right = (arm_mode in ("both", "right")) and not events.get("persistent_right_lock", False)
        if unlock_left:
            _send_ui_command({"action": "toggle_left_home",  "value": False})
        if unlock_right:
            _send_ui_command({"action": "toggle_right_home", "value": False})

    # Per-stage timing accumulator, so a loop overrun says *which* stage ate the
    # budget instead of just "slower than target FPS". perf_counter() costs ~50ns,
    # so these probes are noise against a 33ms/frame budget. Averages are logged
    # as one compact line every _PROF_EVERY frames and then reset.
    _prof: dict[str, float] = {}
    _prof_n = 0
    _PROF_EVERY = 300  # ~10s at 30 fps

    while timestamp < control_time_s:
        global recording_active
        if not recording_active:
            break
            
        # Wait if recording is paused
        while events.get("pause_recording", False) and not events.get("exit_early", False):
            precise_sleep(0.1)
            # Update start_episode_t so timestamp doesn't advance while paused
            start_episode_t += 0.1
            global phase_start_time
            if phase_start_time is not None:
                phase_start_time += 0.1

            # Auto-resume once the camera freeze that triggered this pause has
            # cleared (the auto-recovery watchdog reconnects stalled cameras in
            # the background). We only auto-resume freeze-induced pauses — a
            # pause the user requested manually is left untouched.
            if events.get("_freeze_paused", False) and not _cameras_frozen():
                logger.info("Camera feed recovered — auto-resuming recording.")
                print("\n✅ STATUS CHANGE: Camera recovered. Auto-resuming recording.")
                events["pause_recording"] = False
                events["_freeze_paused"] = False

        start_loop_t = time.perf_counter()

        if events.get("exit_early") and events.get("_is_homing", False):
            if events.get("rerecord_episode"):
                # Re-record means the entire current attempt is invalid,
                # including any homing frames already sent to the encoder.
                # Return immediately so the caller can cancel the encoder and
                # clear the episode buffer before starting the replacement.
                events["exit_early"] = False
                events["_discard_current_attempt"] = True
                logger.info("Re-record requested during homing; discarding current attempt.")
                break

            # Ignore additional end/stop requests while homing.  The episode
            # must remain active until the home target is actually reached.
            events["exit_early"] = False
            logger.info("Ignoring end-episode request while homing is in progress.")

        if events.get("exit_early") and not events.get("_is_homing", False) and dataset is not None:
            # Guard against a command racing with the HTTP handler.  An early
            # command must not enter homing, because those frames would belong
            # to neither a valid episode nor a valid episode ending.
            if time.perf_counter() - start_episode_t < MIN_EPISODE_SECONDS:
                events["exit_early"] = False
                logger.info("Ignoring end/stop command before the %.1fs episode minimum.", MIN_EPISODE_SECONDS)

        if (events.get("exit_early") or (control_time_s - timestamp <= 0.2)) and not events.get("_is_homing", False):
            if dataset is not None:
                # A re-record request discards this attempt.  Stop the
                # dataset-backed loop before homing so discarded camera frames
                # cannot be sent to the video encoder.  The reset phase below
                # homes the robot with dataset=None.
                if events.get("rerecord_episode"):
                    events["exit_early"] = False
                    events["_discard_current_attempt"] = True
                    logger.info("Re-record requested; discarding current attempt before homing.")
                    break

                events["_is_homing"] = True
                events["_homing_start_time"] = time.perf_counter()  # wall-clock, immune to pause adjustments
                events["_last_home_cmd_time"] = 0.0  # force immediate first send
                events["_home_confirm_start"] = None
                
                if events.get("exit_early"):
                    events["exit_early"] = False
                    logger.info("Early exit triggered! Sending arms home and recording until target reached...")
                    print("\n🛑 End episode pressed. Sending arms home. Continuing recording...")
                    print("   (Press Space again to forcefully exit immediately)")
                else:
                    logger.info("Natural episode end approaching. Sending arms home and recording until target reached...")
                
                # Send home command instantly via module-level singleton publisher
                try:
                    target_state = events.get("target_home_state")
                    if target_state is not None:
                        if len(target_state) == 16:
                            set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15], "lock_all": True}
                        elif len(target_state) == 8:
                            set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7], "lock_all": True}
                        else:
                            set_home = {"action": "home_all"}
                    else:
                        set_home = {"action": "home_all"}
                    _send_ui_command(set_home)
                    events["_last_home_cmd_time"] = time.perf_counter()
                except Exception as e:
                    logger.error(f"Failed to publish homing command: {e}")
                    
                # Extend loop indefinitely to wait for arms to reach home
                control_time_s = float('inf')
            else:
                # If we're in the resetting phase, just break when time is up or exit_early is pressed
                events["exit_early"] = False
                break

        # Get robot observation
        _t_probe = time.perf_counter()
        obs = robot.get_observation()
        _prof["get_obs"] = _prof.get("get_obs", 0.0) + (time.perf_counter() - _t_probe)

        # Always update current_robot_state so UI can display it continuously
        current_state = obs.get("observation.state")
        if current_state is None and hasattr(robot.config, "joint_names"):
            current_state = [obs.get(f"{name}.pos", 0.0) for name in robot.config.joint_names]
            
        if current_state is not None:
            import torch
            if isinstance(current_state, torch.Tensor):
                current_state = current_state.tolist()
            elif hasattr(current_state, "tolist"):
                current_state = current_state.tolist()
            events["current_robot_state"] = current_state
        
        # Check if homing is complete
        if events.get("_is_homing", False):
            target_state = events.get("target_home_state")
            # Use wall-clock time so homing timeout works even if episode timestamp
            # is frozen by a camera-freeze auto-pause.
            elapsed_homing = time.perf_counter() - events.get("_homing_start_time", time.perf_counter())

            # The operator manually released an arm during homing (Lock L/R in
            # the UI). Treat that as "homing is over": the arm is deliberately
            # following the exoskeleton again, so it will never reach the home
            # target and waiting for it would hang the episode forever.
            if events.get("_homing_operator_override"):
                logger.info("Operator released an arm during homing; ending the homing phase.")
                print("\n✋ Manual release during homing. Ending episode.")
                events["_is_homing"] = False
                events["_home_reached"] = True
                current_phase = "saving"
                phase_start_time = time.time()
                break

            # Re-send home command every ~1s to guard against ROS message drops.
            #
            # BOUNDED: every re-send carries lock_all=True, so an unbounded loop
            # keeps re-locking the arm for as long as `is_home` stays false —
            # which is exactly the "recording started but the arm is still
            # locked" symptom. After _HOMING_RESEND_LIMIT_S we stop re-asserting
            # the lock and let the operator take over, rather than pinning the
            # arm indefinitely because one joint sits a hair outside tolerance.
            _HOMING_RESEND_LIMIT_S = 10.0
            last_home_cmd_time = events.get("_last_home_cmd_time", 0.0)
            if elapsed_homing > _HOMING_RESEND_LIMIT_S:
                if not events.get("_homing_resend_gave_up"):
                    events["_homing_resend_gave_up"] = True
                    logger.warning(
                        "Homing did not confirm within %.0fs; stopping the lock_all re-send so "
                        "the arm is not held locked. Releasing arms for teleoperation.",
                        _HOMING_RESEND_LIMIT_S,
                    )
                    # Actively release, otherwise the last lock_all we sent stands.
                    arm_mode_r = events.get("_arm_mode", "both")
                    if arm_mode_r in ("both", "left") and not events.get("persistent_left_lock", False):
                        _send_ui_command({"action": "toggle_left_home", "value": False})
                    if arm_mode_r in ("both", "right") and not events.get("persistent_right_lock", False):
                        _send_ui_command({"action": "toggle_right_home", "value": False})
            elif time.perf_counter() - last_home_cmd_time > 1.0:
                try:
                    if target_state is not None:
                        if len(target_state) == 16:
                            resend_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15], "lock_all": True}
                        elif len(target_state) == 8:
                            resend_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7], "lock_all": True}
                        else:
                            resend_home = {"action": "home_all"}
                    else:
                        resend_home = {"action": "home_all"}
                    _send_ui_command(resend_home)
                    events["_last_home_cmd_time"] = time.perf_counter()
                except Exception as e:
                    logger.error(f"Failed to re-send homing command: {e}")

            # Do not end the episode on a wall-clock timeout.  Homing can take
            # longer under load, and stopping here truncates the homing frames.
            if elapsed_homing > 8.0 and int(elapsed_homing) % 5 == 0:
                logger.warning("Homing is still in progress after %.1fs; waiting for home target.", elapsed_homing)

            if current_state is not None and target_state is not None:
                # Build arm_mode-aware comparison pairs (current_idx, target_idx)
                arm_mode = events.get("_arm_mode", "both")
                n_cur = len(current_state)
                n_tgt = len(target_state)

                if arm_mode == "right":
                    # Right arm only mode: current_state[0:8] → target_state[8:16]
                    pairs = [(i, 8 + i) for i in range(min(8, n_cur, n_tgt - 8))]
                elif arm_mode == "left":
                    # Left arm only mode: current_state[0:8] → target_state[0:8]
                    pairs = [(i, i) for i in range(min(8, n_cur, n_tgt))]
                else:
                    # Both arms mode: check both, including grippers at indices 7 and 15
                    num_per_arm = 8 if n_cur > 14 else 7
                    left_pairs = [(i, i) for i in range(min(num_per_arm, n_cur, n_tgt))]
                    right_start = num_per_arm
                    right_pairs = [(right_start + i, 8 + i) for i in range(min(num_per_arm, n_cur - right_start, n_tgt - 8))]
                    pairs = left_pairs + [p for p in right_pairs if p[0] < n_cur and p[1] < n_tgt]

                is_home = bool(pairs)
                if is_home:
                    for ci, ti in pairs:
                        # 5mm on the gripper. Kept tight deliberately: the gripper's
                        # full travel is only ~44mm, so a looser tolerance would let
                        # homing "confirm" with the gripper a third of its range open
                        # and bake an imprecise home pose into the recording. The
                        # stuck-lock failure was never this check — it was the
                        # unbounded lock_all re-send (bounded below) and the homing
                        # latch in the bridge, both fixed at source.
                        tol = 0.005 if ci in (7, 15) else 0.15
                        if abs(current_state[ci] - target_state[ti]) > tol:
                            is_home = False
                            break

                if is_home:
                    # Require the target to be observed for a short interval
                    # so one transient state sample cannot stop recording.
                    if events.get("_home_confirm_start") is None:
                        events["_home_confirm_start"] = time.perf_counter()
                    elif time.perf_counter() - events["_home_confirm_start"] >= 0.3:
                        logger.info("Arms reached home! Ending episode.")
                        print("\n✅ Arms reached home. Episode complete.")
                        # The physical homing phase is complete now.  Clear
                        # this before dataset finalization so the UI can move
                        # to a non-countdown "saving" state instead of showing
                        # the reset get-ready countdown while video encoding
                        # (dataset.save_episode(), which can take many seconds)
                        # is still running.  Labeling this "resetting" here
                        # was wrong: it made phase_elapsed_seconds run for the
                        # whole encoding duration against reset_time_s, so the
                        # UI showed the get-ready timer overshoot past its
                        # limit and then visibly jump back to 0 once the real
                        # reset phase started afterward.
                        events["_is_homing"] = False
                        events["_home_reached"] = True
                        current_phase = "saving"
                        phase_start_time = time.time()
                        break
                else:
                    events["_home_confirm_start"] = None

            elif current_state is None:
                # Keep recording while the robot state is temporarily
                # unavailable; stopping here would cut off homing.
                logger.debug("Robot state unavailable during homing; waiting for home confirmation.")


        # Auto-pause if any camera is frozen, and DO NOT record this tick — the

        # frozen camera reuses its last frame, which would pair a stale image
        # with a fresh action and corrupt the dataset. Skipping the write here
        # (in addition to the pause) closes the single-tick window before the
        # pause takes effect on the next iteration.
        if _cameras_frozen():
            if not events.get("pause_recording", False):
                logger.warning("Camera frozen! Auto-pausing recording to prevent corrupted data.")
                print("\n⚠️ STATUS CHANGE: Auto-paused due to camera freeze. Auto-recovery in progress...")
                events["pause_recording"] = True
                events["_freeze_paused"] = True
            # Skip the rest of this iteration so no stale frame is written.
            time.sleep(control_interval)
            continue

        # Applies a pipeline to the raw robot observation, default is IdentityProcessor
        _t_probe = time.perf_counter()
        obs_processed = robot_observation_processor(obs)

        if dataset is not None:
            observation_frame = build_dataset_frame(dataset.features, obs_processed, prefix=OBS_STR)
        _prof["build_obs_frame"] = _prof.get("build_obs_frame", 0.0) + (time.perf_counter() - _t_probe)

        # Get action from teleop
        _t_probe = time.perf_counter()
        if isinstance(teleop, Teleoperator):
            act = teleop.get_action()
            if robot.name == "unitree_g1":
                teleop.send_feedback(obs)

            # Applies a pipeline to the raw teleop action, default is IdentityProcessor
            act_processed_teleop = teleop_action_processor((act, obs))
            action_values = act_processed_teleop
            robot_action_to_send = robot_action_processor((act_processed_teleop, obs))

        elif isinstance(teleop, list):
            arm_action = teleop_arm.get_action()
            arm_action = {f"arm_{k}": v for k, v in arm_action.items()}
            keyboard_action = teleop_keyboard.get_action()
            base_action = robot._from_keyboard_to_base_action(keyboard_action)
            act = {**arm_action, **base_action} if len(base_action) > 0 else arm_action
            act_processed_teleop = teleop_action_processor((act, obs))
            action_values = act_processed_teleop
            robot_action_to_send = robot_action_processor((act_processed_teleop, obs))
        else:
            no_action_count += 1
            if no_action_count == 1 or no_action_count % 10 == 0:
                logging.warning(
                    "No teleoperator provided, skipping action generation. "
                    "This is likely to happen when resetting the environment without a teleop device. "
                    "The robot won't be at its rest position at the start of the next episode."
                )
            continue
        _prof["get_action"] = _prof.get("get_action", 0.0) + (time.perf_counter() - _t_probe)

        if events.get("_is_homing", False) and current_state is not None:
            import torch
            import numpy as np
            if isinstance(action_values, torch.Tensor):
                action_values = torch.tensor(current_state, dtype=action_values.dtype, device=action_values.device)
            elif isinstance(action_values, np.ndarray):
                action_values = np.array(current_state, dtype=np.float32)
            elif isinstance(action_values, dict):
                # Preserve dict format for robots like openarm_ros
                new_action = dict(action_values)
                if hasattr(robot.config, "joint_names"):
                    # Find if keys are suffixed with .pos
                    is_pos_suffixed = any(k.endswith(".pos") for k in new_action.keys())
                    suffix = ".pos" if is_pos_suffixed else ""
                    for i, name in enumerate(robot.config.joint_names):
                        if i < len(current_state):
                            new_action[f"{name}{suffix}"] = current_state[i]
                action_values = new_action

        # Send action to robot
        # Action can eventually be clipped using `max_relative_target`,
        # so action actually sent is saved in the dataset. action = postprocessor.process(action)
        # TODO(steven, pepijn, adil): we should use a pipeline step to clip the action, so the sent action is the action that we input to the robot.
        _t_probe = time.perf_counter()
        if not events.get("_is_homing", False):
            _sent_action = robot.send_action(robot_action_to_send)
        else:
            # During homing, we let the backend ROS bridge control the arm.
            # We skip sending teleop actions to prevent fighting the homing trajectory.
            _sent_action = robot_action_to_send
        _prof["send_action"] = _prof.get("send_action", 0.0) + (time.perf_counter() - _t_probe)

        # Write to dataset
        _t_probe = time.perf_counter()
        if dataset is not None:
            sync_row_recorded = True
            # Bypass sync check if homing, since teleop is disconnected from robot movement
            if not events.get("_is_homing", False) and hasattr(robot, "sync_within_tolerance") and not robot.sync_within_tolerance(0.050):
                sync_row_recorded = False
                events["_sync_rejected_count"] = events.get("_sync_rejected_count", 0) + 1
                if events["_sync_rejected_count"] <= 5 or events["_sync_rejected_count"] % 50 == 0:
                    logger.warning(
                        "Skipping unsynchronized frame %d (total rejected=%d)",
                        events.get("_sync_frame_index", 0), events["_sync_rejected_count"],
                    )
            else:
                sync_row_recorded = _record_sync_timestamp(robot, events, dataset)
            # A frame without timing provenance cannot be validated or safely
            # used for training. Keep data and sidecar row counts identical.
            if not sync_row_recorded:
                events["_sync_rejected_count"] = events.get("_sync_rejected_count", 0) + 1
                # Preserve the control cadence even when a sample is rejected;
                # otherwise the next camera read happens immediately and can
                # make the physical motion/video look bursty.
                rejected_dt = time.perf_counter() - start_loop_t
                precise_sleep(max(control_interval - rejected_dt, 0.0))
                continue
            if events.get("_captured_home_state") is None:
                # Capture the raw state as the true home state!
                try:
                    state = obs.get("observation.state")
                    if state is None and hasattr(robot.config, "joint_names"):
                        state = [obs.get(f"{name}.pos", 0.0) for name in robot.config.joint_names]
                    if state is not None:
                        import torch
                        if isinstance(state, torch.Tensor):
                            state = state.tolist()
                        elif hasattr(state, "tolist"):
                            state = state.tolist()
                        events["_captured_home_state"] = state
                        logger.info(f"📍 Captured TRUE live robot home state: {state}")
                        print(f"\n📍 Captured TRUE live robot home state: {state}\n")
                except Exception as e:
                    logger.warning(f"Failed to capture live robot state: {e}")
                    events["_captured_home_state"] = []
            _prof["sync_check"] = _prof.get("sync_check", 0.0) + (time.perf_counter() - _t_probe)

            _t_probe = time.perf_counter()
            action_frame = build_dataset_frame(dataset.features, action_values, prefix=ACTION)
            frame = {**observation_frame, **action_frame, "task": single_task}
            dataset.add_frame(frame)
            _prof["add_frame"] = _prof.get("add_frame", 0.0) + (time.perf_counter() - _t_probe)

        if display_data:
            log_rerun_data(
                observation=obs_processed, action=action_values, compress_images=display_compressed_images
            )

        dt_s = time.perf_counter() - start_loop_t
        _prof["tick_total"] = _prof.get("tick_total", 0.0) + dt_s
        _prof_n += 1

        sleep_time_s: float = control_interval - dt_s

        # Still surface individual pathological stalls (4x budget), which the
        # aggregate average would smear away -- these are the ones that show up
        # as a visible jump in the recorded video rather than uniform slowness.
        if dt_s > control_interval * 4:
            logger.warning("Loop stall: single frame took %.0fms", dt_s * 1000.0)

        # One aggregated line per _PROF_EVERY frames instead of a per-frame
        # warning. The old code logged ~1 line per frame once the loop ran even
        # slightly long (26k lines in a 50-episode session), which is both noise
        # and per-frame formatting/IO cost inside the very budget it complains
        # about. The breakdown names the stage responsible.
        if _prof_n >= _PROF_EVERY:
            _avg = {k: (v / _prof_n) * 1000.0 for k, v in _prof.items()}
            _stages = " ".join(
                f"{k}={_avg[k]:.1f}" for k in sorted(_avg, key=_avg.get, reverse=True) if k != "tick_total"
            )
            # Sampled once per report (not per frame) so it costs nothing:
            # how stale the camera frames actually are relative to this row.
            _cam_lat = ""
            try:
                if hasattr(robot, "get_sync_diagnostics"):
                    _d = robot.get_sync_diagnostics()
                    _now = time.monotonic()
                    _lats = {
                        k[len("camera.") : -len(".timestamp")]: (_now - float(v)) * 1000.0
                        for k, v in _d.items()
                        if k.startswith("camera.")
                        and k.endswith(".timestamp")
                        and k != "camera.timestamp"
                    }
                    if _lats:
                        _cam_lat = " | cam_latency_ms " + " ".join(
                            f"{n}={v:.0f}" for n, v in sorted(_lats.items())
                        )
            except Exception:
                pass
            logger.info(
                "loop profile over %d frames: %.1f Hz (tick=%.1fms of %.1fms budget) | %s%s",
                _prof_n,
                1.0 / _avg["tick_total"] * 1000.0 if _avg.get("tick_total") else 0.0,
                _avg.get("tick_total", 0.0),
                control_interval * 1000.0,
                _stages,
                _cam_lat,
            )
            _prof = {}
            _prof_n = 0

        precise_sleep(max(sleep_time_s, 0.0))

        timestamp = time.perf_counter() - start_episode_t
def record_with_web_events(cfg: RecordConfig, web_events: dict, dataset_version: str = "v3.0") -> LeRobotDataset:
    """
    Implement recording with phase tracking - exactly mirrors original record() function behavior
    """
    import time

    from lerobot.common.control_utils import (
        sanity_check_dataset_name,
        sanity_check_dataset_robot_compatibility,
    )
    from lerobot.datasets import LeRobotDataset, VideoEncodingManager
    from lerobot.processor import make_default_processors
    from lerobot.robots import make_robot_from_config
    # Using custom_record_loop instead of imported record_loop
    from lerobot.teleoperators import make_teleoperator_from_config
    from lerobot.utils.feature_utils import hw_to_dataset_features
    from lerobot.utils.utils import log_say

    global current_phase, phase_start_time, current_episode, saved_episodes, active_robot

    web_events.setdefault("_sync_pending_rows", [])

    if getattr(cfg.robot, "type", "") == "openarm_ros":
        from lelab.robots.openarm_ros import OpenArmRosRobot
        robot = OpenArmRosRobot(cfg.robot)
    else:
        robot = make_robot_from_config(cfg.robot)
    
    active_robot = robot
    if getattr(cfg.teleop, "type", "") == "passive_ros":
        from lelab.robots.openarm_ros import PassiveROSTeleop
        teleop = PassiveROSTeleop(cfg.teleop)
    else:
        teleop = make_teleoperator_from_config(cfg.teleop) if cfg.teleop is not None else None

    teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()

    action_features = hw_to_dataset_features(robot.action_features, "action", cfg.dataset.video)
    obs_features = hw_to_dataset_features(robot.observation_features, "observation", cfg.dataset.video)
    dataset_features = {**action_features, **obs_features}

    # Number of image/video streams actually written per frame.  This must come
    # from the dataset schema, NOT len(robot.cameras): openarm_ros serves its
    # cameras over ROS (self._ros_camera_names) and leaves robot.cameras empty,
    # so sizing the image-writer pool off robot.cameras gave 4*0 == 0 threads.
    # LeRobot only creates an AsyncImageWriter when threads or processes are
    # non-zero, so zero threads silently made _save_image() write PNGs
    # *synchronously on the record loop thread* -- ~20ms per camera per frame,
    # i.e. ~40ms of the 33ms budget for two cameras, which is exactly the
    # ~22fps this was recording at instead of 30.
    num_video_streams = sum(
        1 for ft in dataset_features.values() if ft.get("dtype") in ("video", "image")
    )

    if cfg.resume:
        if not cfg.dataset.root:
            import os
            from pathlib import Path
            lerobot_home = os.environ.get("HF_LEROBOT_HOME", os.environ.get("LEROBOT_HOME", "~/.cache/huggingface/lerobot"))
            cfg.dataset.root = Path(lerobot_home).expanduser() / cfg.dataset.repo_id
            logger.info(f"🔧 RESUME: Set explicit local root directory path: {cfg.dataset.root}")

        num_cameras = num_video_streams

        # When resuming, determine version from dataset metadata rather than user selection
        actual_version = dataset_version
        try:
            info = _load_local_dataset_info(cfg.dataset.repo_id)
            if info and "codebase_version" in info:
                actual_version = info["codebase_version"]
                logger.info(f"🔧 RESUME: Detected dataset codebase_version '{actual_version}'")
        except Exception as e:
            logger.warning(f"🔧 RESUME: Failed to parse existing dataset version: {e}")
            
        if actual_version == "v2.1":
            from .utils.v2_dataset import LeRobotDatasetV2
            dataset = LeRobotDatasetV2.resume(
                cfg.dataset.repo_id,
                fps=cfg.dataset.fps,
                features=dataset_features,
                root=cfg.dataset.root,
                robot_type=robot.name,
                use_videos=cfg.dataset.video,
            )
        else:
            try:
                dataset = LeRobotDataset.resume(
                    cfg.dataset.repo_id,
                    root=cfg.dataset.root,
                    batch_encoding_size=cfg.dataset.video_encoding_batch_size,
                    vcodec=cfg.dataset.vcodec,
                    streaming_encoding=cfg.dataset.streaming_encoding,
                    encoder_queue_maxsize=cfg.dataset.encoder_queue_maxsize,
                    encoder_threads=cfg.dataset.encoder_threads,
                    image_writer_processes=0,
                    # Streaming encoding feeds frames straight to the video
                    # encoder, so no PNGs are written and these threads would
                    # sit idle -- each one is another thread competing for the
                    # GIL with the capture loop.
                    image_writer_threads=0
                    if cfg.dataset.streaming_encoding
                    else (
                        cfg.dataset.num_image_writer_threads_per_camera * num_cameras
                        if num_cameras > 0
                        else 0
                    ),
                )
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "Repository Not Found" in err_str:
                    raise RuntimeError(
                        f"Dataset {cfg.dataset.repo_id} is corrupted (likely due to an interrupted previous recording) "
                        "and cannot be resumed. Please delete this dataset and create a new one."
                    ) from e
                raise
        sanity_check_dataset_robot_compatibility(dataset, robot, cfg.dataset.fps, dataset_features)
    else:
        sanity_check_dataset_name(cfg.dataset.repo_id, None)
        if dataset_version == "v2.1":
            from .utils.v2_dataset import LeRobotDatasetV2
            dataset = LeRobotDatasetV2.create(
                cfg.dataset.repo_id,
                cfg.dataset.fps,
                features=dataset_features,
                root=cfg.dataset.root,
                robot_type=robot.name,
                use_videos=cfg.dataset.video,
            )
        else:
            dataset = LeRobotDataset.create(
                cfg.dataset.repo_id,
                cfg.dataset.fps,
                root=cfg.dataset.root,
                robot_type=robot.name,
                features=dataset_features,
                use_videos=cfg.dataset.video,
                image_writer_processes=0,
                image_writer_threads=0
                if cfg.dataset.streaming_encoding
                else cfg.dataset.num_image_writer_threads_per_camera * num_video_streams,
                batch_encoding_size=cfg.dataset.video_encoding_batch_size,
                vcodec=cfg.dataset.vcodec,
                streaming_encoding=cfg.dataset.streaming_encoding,
                encoder_queue_maxsize=cfg.dataset.encoder_queue_maxsize,
                encoder_threads=cfg.dataset.encoder_threads,
            )

    # 🔧 ROBOT CONNECTION: Connect with enhanced error handling for camera conflicts
    try:
        logger.info("🔧 ROBOT CONNECTION: Attempting to connect robot...")
        robot.connect()
        logger.info("✅ ROBOT CONNECTION: Robot connected successfully")
    except Exception as e:
        logger.error(f"❌ ROBOT CONNECTION: Failed to connect robot: {e}")
        # If robot connection fails due to camera conflict, provide clear error
        if "camera" in str(e).lower() or "device" in str(e).lower() or "busy" in str(e).lower():
            logger.error("💡 ROBOT CONNECTION: Camera connection failure - likely camera resource conflict")
            logger.error(
                "💡 ROBOT CONNECTION: Make sure frontend camera streams are released before recording"
            )
        try:
            robot.disconnect()
        except Exception:
            pass
        raise

    if teleop is not None:
        try:
            logger.info("🔧 TELEOP CONNECTION: Attempting to connect teleoperator...")
            teleop.connect()
            logger.info("✅ TELEOP CONNECTION: Teleoperator connected successfully")
        except Exception as e:
            logger.error(f"❌ TELEOP CONNECTION: Failed to connect teleoperator: {e}")
            try:
                robot.disconnect()
                teleop.disconnect()
            except Exception:
                pass
            raise

    # Ensure calibration is properly loaded and applied to the devices
    logger.info("Applying calibration to devices")

    # Write calibration to motors' memory (similar to teleoperation code)
    if hasattr(robot, "bus") and robot.calibration is not None:
        try:
            logger.info("Writing robot calibration to motors...")
            robot.bus.write_calibration(robot.calibration)
            logger.info("Robot calibration applied successfully")
        except Exception as e:
            logger.error(f"Error writing robot calibration: {e}")
    else:
        logger.warning("Robot bus or calibration not available - calibration may not be applied")

    if teleop is not None and hasattr(teleop, "bus") and teleop.calibration is not None:
        try:
            logger.info("Writing teleop calibration to motors...")
            teleop.bus.write_calibration(teleop.calibration)
            logger.info("Teleop calibration applied successfully")
        except Exception as e:
            logger.error(f"Error writing teleop calibration: {e}")
    else:
        logger.warning("Teleop bus or calibration not available - calibration may not be applied")

    # Save calibration to dataset folder when creating a new dataset
    # Save calibration to dataset folder when creating a new dataset
    if not cfg.resume:
        try:
            import yaml
            from pathlib import Path
            calib_path = Path(dataset.root) / "calibration.yaml"
            workspace_yaml = Path(__file__).resolve().parent.parent.parent / "gripper_home.yaml"
            
            calib_data = {}
            if workspace_yaml.exists():
                with open(workspace_yaml, "r") as f:
                    calib_data = yaml.safe_load(f) or {}
            
            if not calib_path.exists():
                with open(calib_path, "w") as f:
                    yaml.dump(calib_data, f)
                logger.info(f"Saved initial calibration.yaml to dataset (without live home state yet): {calib_path}")
        except Exception as e:
            logger.warning(f"Failed to save calibration.yaml to dataset: {e}")
            
    # Read target_home_state so we can use it for dynamic homing
    target_home_state = None
    try:
        import yaml
        calib_path = dataset.root / "calibration.yaml"
        if calib_path.exists():
            with open(calib_path, "r") as f:
                calib_data = yaml.safe_load(f)
                if calib_data and "live_robot_home_state" in calib_data:
                    target_home_state = calib_data["live_robot_home_state"]
    except Exception as e:
        logger.warning(f"Failed to load target home state from calibration.yaml: {e}")
    if web_events.get("target_home_state") is None:
        web_events["target_home_state"] = target_home_state

    # Start with episode 1 - but track it properly
    current_episode = 1
    saved_episodes = 0  # Track how many episodes we've actually saved

    try:
        with VideoEncodingManager(dataset):
            global recording_active

            # Move to home before the first real episode without attaching the
            # dataset.  The old implementation injected an artificial
            # ``exit_early`` into the first dataset-backed loop to create a
            # get-ready phase; those frames could remain in the video/data
            # buffer and appear as an unexplained prefix of episode 0.
            if recording_active and not web_events.get("_initial_get_ready_done"):
                current_phase = "resetting"
                phase_start_time = time.time()
                web_events["_initial_get_ready_done"] = True
                logger.info("Starting dataset-free initial homing before episode 1")
                print("🔄 STATUS CHANGE: Homing before episode 1 (not recording)")

                try:
                    target_state = web_events.get("target_home_state")
                    if target_state is not None and len(target_state) == 16:
                        set_home = {
                            "action": "set_home_target",
                            "left_arm": target_state[0:7],
                            "left_gripper": target_state[7],
                            "right_arm": target_state[8:15],
                            "right_gripper": target_state[15],
                            "lock_all": True,
                        }
                    elif target_state is not None and len(target_state) == 8:
                        set_home = {
                            "action": "set_home_target",
                            "left_arm": target_state[0:7],
                            "left_gripper": target_state[7],
                            "right_arm": target_state[0:7],
                            "right_gripper": target_state[7],
                            "lock_all": True,
                        }
                    else:
                        set_home = {"action": "home_all"}
                    _send_ui_command(set_home)
                except Exception as e:
                    logger.error(f"Failed to publish initial homing command: {e}")

                web_events["exit_early"] = False
                custom_record_loop(
                    robot=robot,
                    events=web_events,
                    fps=cfg.dataset.fps,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=teleop,
                    dataset=None,
                    control_time_s=cfg.dataset.reset_time_s,
                    single_task=cfg.dataset.single_task,
                    display_data=cfg.display_data,
                )

                current_phase = "preparing"

            while saved_episodes < cfg.dataset.num_episodes and recording_active:
                # RECORDING PHASE - with dataset (matches original record.py exactly)
                current_phase = "recording"
                phase_start_time = time.time()
                web_events["_sync_frame_index"] = 0
                web_events["_sync_pending_rows"] = []
                logger.info(f"Starting recording phase for episode {current_episode}")
                _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                logger.info(f"Events state at start of recording phase: {_loggable}")
                print(
                    f"🎬 STATUS CHANGE: Starting recording phase for episode {current_episode}/{cfg.dataset.num_episodes}"
                )

                log_say(f"Recording episode {current_episode}", cfg.play_sounds)

                # Add a tracking flag that won't be reset by record_loop
                web_events["_exit_early_triggered"] = False
                _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                logger.info(f"Recording phase - calling record_loop with events: {_loggable}")

                custom_record_loop(
                    robot=robot,
                    events=web_events,
                    fps=cfg.dataset.fps,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=teleop,
                    dataset=dataset,
                    control_time_s=max(MIN_EPISODE_SECONDS, cfg.dataset.episode_time_s),
                    single_task=web_events.get("current_task", cfg.dataset.single_task),
                    display_data=cfg.display_data,
                )

                _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                logger.info(f"Recording phase completed - events state: {_loggable}")

                # Check if we captured a new home state during this episode
                if web_events.get("_captured_home_state") is not None and not web_events.get("_calibration_saved"):
                    try:
                        import yaml
                        from pathlib import Path
                        calib_path = Path(dataset.root) / "calibration.yaml"
                        calib_data = {}
                        workspace_yaml = Path(__file__).resolve().parent.parent.parent / "gripper_home.yaml"
                        if workspace_yaml.exists():
                            with open(workspace_yaml, "r") as f:
                                calib_data = yaml.safe_load(f) or {}
                        calib_data["live_robot_home_state"] = web_events["_captured_home_state"]
                        with open(calib_path, "w") as f:
                            yaml.dump(calib_data, f)
                        logger.info(f"Saved true calibration.yaml to dataset: {calib_path}")
                        web_events["_calibration_saved"] = True
                        # Only overwrite target_home_state if the user didn't explicitly select a home position
                        if not web_events.get("target_home_state"):
                            web_events["target_home_state"] = web_events["_captured_home_state"]
                    except Exception as e:
                        logger.warning(f"Failed to save true calibration.yaml: {e}")

                # Check if exit_early was triggered (use our tracking flag)
                recording_interrupted_by_exit_early = web_events.get("_exit_early_triggered", False)
                if recording_interrupted_by_exit_early:
                    logger.info("🟡 RECORDING PHASE INTERRUPTED BY EXIT_EARLY - proceeding to save episode")
                    print(
                        f"🟡 STATUS CHANGE: Recording phase interrupted by user - episode {current_episode} data collected"
                    )
                    # Reset our tracking flag
                    web_events["_exit_early_triggered"] = False
                else:
                    # Recording completed due to timeout - trigger re-record behavior
                    logger.info("⏰ RECORDING PHASE COMPLETED DUE TO TIMEOUT - triggering re-record")
                    print(
                        f"⏰ STATUS CHANGE: Recording timeout reached for episode {current_episode} - re-recording"
                    )
                    web_events["rerecord_episode"] = True

                # Handle rerecord logic first (before saving)
                if web_events["rerecord_episode"]:
                    log_say("Re-record episode", cfg.play_sounds)
                    print(
                        f"🔄 STATUS CHANGE: Re-recording episode {current_episode} (episode number stays the same)"
                    )
                    web_events["rerecord_episode"] = False
                    web_events["exit_early"] = False
                    _discard_episode_attempt(dataset)

                    # Go through reset phase before re-recording (don't increment episode counters)
                    # RESET PHASE - without dataset (matches original record.py exactly)
                    current_phase = "resetting"
                    phase_start_time = time.time()
                    logger.info(f"Starting reset phase for re-record of episode {current_episode}")
                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Events state at start of reset phase: {_loggable}")
                    print(f"🔄 STATUS CHANGE: Starting reset phase for episode {current_episode}")

                    log_say("Reset the environment", cfg.play_sounds)

                    # Trigger homing during reset phase instantly via _send_ui_command
                    try:
                        target_state = web_events.get("target_home_state")
                        if target_state is not None:
                            if len(target_state) == 16:
                                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15], "lock_all": True}
                            elif len(target_state) == 8:
                                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7], "lock_all": True}
                            else:
                                set_home = {"action": "home_all"}
                        else:
                            set_home = {"action": "home_all"}
                        
                        _send_ui_command(set_home)
                    except Exception as e:
                        logger.error(f"Failed to publish homing command during reset: {e}")

                    # Reset exit_early flag at the start of each phase
                    web_events["exit_early"] = False
                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Reset phase - calling record_loop with events: {_loggable}")

                    custom_record_loop(
                        robot=robot,
                        events=web_events,
                        fps=cfg.dataset.fps,
                        teleop_action_processor=teleop_action_processor,
                        robot_action_processor=robot_action_processor,
                        robot_observation_processor=robot_observation_processor,
                        teleop=teleop,
                        # NOTE: NO dataset parameter here - matches LeRobot CLI exactly
                        # This means NO recording happens during reset phase
                        control_time_s=cfg.dataset.reset_time_s,
                        single_task=cfg.dataset.single_task,
                        display_data=cfg.display_data,
                    )

                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Reset phase completed - events state: {_loggable}")

                    # Check if reset was interrupted by exit_early
                    if web_events["exit_early"]:
                        logger.info("🟡 RESET PHASE INTERRUPTED BY EXIT_EARLY during re-record")
                        print("🟡 STATUS CHANGE: Reset phase interrupted by user during re-record")
                        web_events["exit_early"] = False

                    # Check if stop recording was requested during re-record reset phase
                    if web_events["stop_recording"]:
                        logger.info("🛑 STOP RECORDING requested during re-record reset phase - ending session")
                        print(
                            "🛑 STATUS CHANGE: Stop recording requested during re-record reset - ending session"
                        )
                        break

                    # Don't increment current_episode or saved_episodes - we're re-recording the same episode
                    continue

                # Save episode immediately after recording phase (matches expected flow)
                logger.info(f"💾 Saving episode {current_episode}...")
                print(f"💾 STATUS CHANGE: Saving episode {current_episode}")
            
                dataset.save_episode(parallel_encoding=False)
                _write_sync_sidecar(dataset, web_events.pop("_sync_pending_rows", []))
            
                episode_task = web_events.get("current_task", cfg.dataset.single_task)
                logger.info(f"✅ Episode {current_episode} saved successfully with task: {episode_task}")
                print(f"✅ STATUS CHANGE: Episode {current_episode} saved successfully")

                # Increment episode counters after successful save
                saved_episodes += 1
                current_episode += 1

                # Check if we should stop recording
                if web_events["stop_recording"]:
                    print("🛑 STATUS CHANGE: Recording manually stopped by user")
                    break

                # Check if we've completed all episodes
                if saved_episodes >= cfg.dataset.num_episodes:
                    break

                # Execute reset phase to prepare for next episode
                # Skip reset for the last episode that was just saved
                if saved_episodes < cfg.dataset.num_episodes:
                    # RESET PHASE - without dataset (matches original record.py exactly)
                    current_phase = "resetting"
                    phase_start_time = time.time()
                    logger.info(f"Starting reset phase for next episode {current_episode}")
                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Events state at start of reset phase: {_loggable}")
                    print(f"🔄 STATUS CHANGE: Starting reset phase for episode {current_episode}")

                    log_say("Reset the environment", cfg.play_sounds)

                    # Trigger homing during inter-episode reset via singleton publisher (instant)
                    try:
                        target_state = web_events.get("target_home_state")
                        if target_state is not None:
                            if len(target_state) == 16:
                                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15], "lock_all": True}
                            elif len(target_state) == 8:
                                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7], "lock_all": True}
                            else:
                                set_home = {"action": "home_all"}
                        else:
                            set_home = {"action": "home_all"}
                        _send_ui_command(set_home)
                    except Exception as e:
                        logger.error(f"Failed to publish homing command during reset: {e}")

                    # Reset exit_early flag at the start of each phase
                    web_events["exit_early"] = False
                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Reset phase - calling record_loop with events: {_loggable}")

                    custom_record_loop(
                        robot=robot,
                        events=web_events,
                        fps=cfg.dataset.fps,
                        teleop_action_processor=teleop_action_processor,
                        robot_action_processor=robot_action_processor,
                        robot_observation_processor=robot_observation_processor,
                        teleop=teleop,
                        # NOTE: NO dataset parameter here - matches LeRobot CLI exactly
                        # This means NO recording happens during reset phase
                        control_time_s=cfg.dataset.reset_time_s,
                        single_task=cfg.dataset.single_task,
                        display_data=cfg.display_data,
                    )

                    _loggable = {k: v for k, v in _events_state_for_response(web_events).items() if k != "current_robot_state"}
                    logger.info(f"Reset phase completed - events state: {_loggable}")

                    # Check if reset was interrupted by exit_early
                    if web_events["exit_early"]:
                        logger.info("🟡 RESET PHASE INTERRUPTED BY EXIT_EARLY - proceeding to next episode")
                        print("🟡 STATUS CHANGE: Reset phase interrupted by user - proceeding to next episode")
                        web_events["exit_early"] = False

                    # Check if stop recording was requested during reset phase
                    if web_events["stop_recording"]:
                        logger.info("🛑 STOP RECORDING requested during reset phase - ending session")
                        print("🛑 STATUS CHANGE: Stop recording requested during reset - ending session")
                        break

        # Recording completed
        current_phase = "completed"
        phase_start_time = None
        print("🏁 STATUS CHANGE: Recording session completed - all episodes finished")
        log_say("Stop recording", cfg.play_sounds, blocking=True)

    finally:
        robot.disconnect()
        if teleop:
            teleop.disconnect()

    if cfg.dataset.push_to_hub:
        dataset.push_to_hub(tags=cfg.dataset.tags, private=cfg.dataset.private)

    log_say("Exiting", cfg.play_sounds)
    return dataset
