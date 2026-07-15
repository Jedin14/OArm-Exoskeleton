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

# Global variables for recording state
recording_active = False
recording_thread: threading.Thread | None = None
recording_events = None  # Events dict for controlling recording session
recording_config = None  # Store recording configuration
recording_start_time = None  # Track when recording started
session_end_elapsed_seconds = None  # Final session duration after the run ends
current_episode = 1  # Track current episode number
saved_episodes = 0  # Track how many episodes have been saved
current_phase = "preparing"  # Track current phase: "preparing", "recording", "resetting", "completed"
phase_start_time = None  # Track when current phase started
last_recording_info: dict[str, Any] | None = (
    None  # Snapshot of the most recently completed dataset (for /dataset-info)
)
# Reference to the active robot during recording, so the server can query
# joint positions and camera frames for the live dashboard.
active_robot = None
# Guards the start path so two concurrent POST /start-recording calls cannot
# both pass the active-flag check.
_state_lock = threading.Lock()


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
    streaming_encoding: bool = True
    cameras: dict = {}
    test_mode: bool = False  # Skip robot connection for testing
    dataset_version: str = "v3.0"  # Target version to save dataset in (v2.1 or v3.0)
    arm_mode: str = "both"  # "left", "right", or "both"


class UploadRequest(BaseModel):
    dataset_repo_id: str
    tags: list[str] = []
    private: bool = False


class DatasetInfoRequest(BaseModel):
    dataset_repo_id: str


class SetEpisodeTaskRequest(BaseModel):
    task: str


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
    if "openarm_ros" in request.follower_port:
        from lelab.robots.openarm_ros import OpenArmRosRobotConfig, PassiveROSTeleopConfig
        # Skip calibration for ROS bridge — it doesn't use lerobot hardware calibration
        robot_config = OpenArmRosRobotConfig(
            cameras=camera_configs,
            arm_mode=request.arm_mode,
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
        active_robot

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
        }

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
                        if len(btns) >= 8 and len(last_buttons) >= 8:
                            # Button A (index 5) -> exit_early (Skip to next episode / start recording)
                            if btns[5] == 1 and last_buttons[5] == 0:
                                handle_exit_early()
                            # Button B (index 6) -> rerecord_episode
                            if btns[6] == 1 and last_buttons[6] == 0:
                                handle_rerecord_episode()
                            # Button C (index 7) -> stop_recording
                            if btns[7] == 1 and last_buttons[7] == 0:
                                stop_recording()
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
                        shutil.rmtree(dataset.root, ignore_errors=True)
                        logger.info(f"Deleted dataset directory {dataset.root}")
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
                        "fps": dataset.fps,
                        "features": features,
                        "camera_names": _extract_camera_names_from_features(features),
                        "total_frames": dataset.num_frames,
                        "robot_type": getattr(dataset.meta, "robot_type", "Unknown robot"),
                        "codebase_version": request.dataset_version,
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

    recording_events["stop_recording"] = True
    recording_events["exit_early"] = True
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
    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}
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
        "events_state": dict(recording_events),
    }


def handle_rerecord_episode() -> dict[str, Any]:
    """Handle rerecord episode request - replaces left arrow key"""
    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}
    recording_events["rerecord_episode"] = True
    recording_events["exit_early"] = True
    logger.info("Re-record episode triggered")
    return {
        "success": True,
        "message": "Re-record episode requested successfully",
        "events_state": dict(recording_events),
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
        "current_phase": current_phase,  # "preparing", "recording", "resetting", "completed"
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
    }

    if recording_active and recording_events:
        status["events_state"] = dict(recording_events)

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
                status["phase_time_limit_s"] = recording_config.episode_time_s
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

        dataset = LeRobotDataset(request.dataset_repo_id)
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

        logger.info(f"Loading dataset {request.dataset_repo_id} for upload")

        # Load the dataset from local storage
        dataset = LeRobotDataset(request.dataset_repo_id)

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
            log_rerun_data(
                observation=obs_processed, action=action_values, compress_images=display_compressed_images
            )

        dt_s = time.perf_counter() - start_loop_t
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
    
    # Auto-unlock arms precisely when the recording loop starts (unless manually locked by user)
    # We ONLY do this during the recording phase (dataset is not None), NOT the resetting phase.
    if dataset is not None:
        try:
            import subprocess
            import os
            import json
            script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
            if not events.get("persistent_left_lock", False):
                subprocess.Popen(["/usr/bin/python3", script_path, json.dumps({"action": "toggle_left_home", "value": False})])
            if not events.get("persistent_right_lock", False):
                subprocess.Popen(["/usr/bin/python3", script_path, json.dumps({"action": "toggle_right_home", "value": False})])
        except Exception as e:
            logger.error(f"Failed to publish auto-unlock commands: {e}")

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
            # Double tap: Force exit immediately
            events["exit_early"] = False
            break

        if (events.get("exit_early") or (control_time_s - timestamp <= 0.2)) and not events.get("_is_homing", False):
            if dataset is not None:
                events["_is_homing"] = True
                events["_homing_start_time"] = timestamp
                
                if events.get("exit_early"):
                    events["exit_early"] = False
                    logger.info("Early exit triggered! Sending arms home and recording until target reached...")
                    print("\n🛑 End episode pressed. Sending arms home. Continuing recording...")
                    print("   (Press Space again to forcefully exit immediately)")
                else:
                    logger.info("Natural episode end approaching. Sending arms home and recording until target reached...")
                
                # Send home commands via UI command publisher
                try:
                    import subprocess
                    import os
                    import json
                    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
                    
                    target_state = events.get("target_home_state")
                    if target_state is not None:
                        if len(target_state) == 16:
                            set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15]}
                        elif len(target_state) == 8:
                            # Send to both for single arm setups so it catches whichever is active
                            set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7]}
                        else:
                            set_home = None
                            
                        if set_home:
                            subprocess.Popen(["/usr/bin/python3", script_path, json.dumps(set_home)])

                    cmd1 = json.dumps({"action": "toggle_left_home", "value": True})
                    cmd2 = json.dumps({"action": "toggle_right_home", "value": True})
                    subprocess.Popen(["/usr/bin/python3", script_path, cmd1])
                    subprocess.Popen(["/usr/bin/python3", script_path, cmd2])
                except Exception as e:
                    logger.error(f"Failed to publish homing command: {e}")
                    
                # Extend loop indefinitely to wait for arms to reach home
                control_time_s = float('inf')
            else:
                # If we're in the resetting phase, just break when time is up or exit_early is pressed
                events["exit_early"] = False
                break

        # Get robot observation
        obs = robot.get_observation()
        
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
            if current_state is not None:
                
                if target_state is not None:
                    # Check if all joints are within tolerance
                    tolerance = 0.05  # radians (balanced to allow for slight physical steady-state error)
                    is_home = True
                    for c, t in zip(current_state, target_state):
                        if abs(c - t) > tolerance:
                            is_home = False
                            break
                    if is_home:
                        logger.info("Arms reached home! Ending episode.")
                        print("\n✅ Arms reached home. Episode complete.")
                        break
                    # Wait at most 4 seconds for safety to avoid hanging
                    if timestamp - events.get("_homing_start_time", timestamp) > 4.0:
                        logger.warning("Homing timeout reached! Ending episode anyway.")
                        print("\n⚠️ Homing timeout reached! Ending episode.")
                        break
                else:
                    # If we don't have a target state, we can't wait for it! Just wait 2 seconds.
                    if timestamp - events.get("_homing_start_time", timestamp) > 2.0:
                        break

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
            continue

        # Applies a pipeline to the raw robot observation, default is IdentityProcessor
        obs_processed = robot_observation_processor(obs)

        if dataset is not None:
            observation_frame = build_dataset_frame(dataset.features, obs_processed, prefix=OBS_STR)

        # Get action from teleop
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

        # Send action to robot
        # Action can eventually be clipped using `max_relative_target`,
        # so action actually sent is saved in the dataset. action = postprocessor.process(action)
        # TODO(steven, pepijn, adil): we should use a pipeline step to clip the action, so the sent action is the action that we input to the robot.
        if not events.get("_is_homing", False):
            _sent_action = robot.send_action(robot_action_to_send)
        else:
            # During homing, the ROS node takes control of the robot.
            # We don't send teleop actions to avoid fighting the ROS homing controller.
            pass

        # Write to dataset
        if dataset is not None:
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

            action_frame = build_dataset_frame(dataset.features, action_values, prefix=ACTION)
            frame = {**observation_frame, **action_frame, "task": single_task}
            dataset.add_frame(frame)

        if display_data:
            log_rerun_data(
                observation=obs_processed, action=action_values, compress_images=display_compressed_images
            )

        dt_s = time.perf_counter() - start_loop_t

        sleep_time_s: float = control_interval - dt_s
        if sleep_time_s < 0:
            logging.warning(
                f"Record loop is running slower ({1 / dt_s:.1f} Hz) than the target FPS ({fps} Hz). Dataset frames might be dropped and robot control might be unstable. Common causes are: 1) Camera FPS not keeping up 2) Policy inference taking too long 3) CPU starvation"
            )

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

    if cfg.resume:
        if not cfg.dataset.root:
            import os
            from pathlib import Path
            lerobot_home = os.environ.get("HF_LEROBOT_HOME", os.environ.get("LEROBOT_HOME", "~/.cache/huggingface/lerobot"))
            cfg.dataset.root = Path(lerobot_home).expanduser() / cfg.dataset.repo_id
            logger.info(f"🔧 RESUME: Set explicit local root directory path: {cfg.dataset.root}")

        num_cameras = len(robot.cameras) if hasattr(robot, "cameras") else 0
        
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
            dataset = LeRobotDataset.resume(
                cfg.dataset.repo_id,
                root=cfg.dataset.root,
                batch_encoding_size=cfg.dataset.video_encoding_batch_size,
                vcodec=cfg.dataset.vcodec,
                streaming_encoding=cfg.dataset.streaming_encoding,
                encoder_queue_maxsize=cfg.dataset.encoder_queue_maxsize,
                encoder_threads=cfg.dataset.encoder_threads,
                image_writer_processes=cfg.dataset.num_image_writer_processes if num_cameras > 0 else 0,
                image_writer_threads=cfg.dataset.num_image_writer_threads_per_camera * num_cameras
                if num_cameras > 0
                else 0,
            )
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
                image_writer_processes=cfg.dataset.num_image_writer_processes,
                image_writer_threads=cfg.dataset.num_image_writer_threads_per_camera * len(robot.cameras),
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
    web_events["target_home_state"] = target_home_state

    # Start with episode 1 - but track it properly
    current_episode = 1
    saved_episodes = 0  # Track how many episodes we've actually saved

    try:
        with VideoEncodingManager(dataset):
            global recording_active
            while saved_episodes < cfg.dataset.num_episodes and recording_active:
                # RECORDING PHASE - with dataset (matches original record.py exactly)
                current_phase = "recording"
                phase_start_time = time.time()
                logger.info(f"Starting recording phase for episode {current_episode}")
                logger.info(f"Events state at start of recording phase: {web_events}")
                print(
                    f"🎬 STATUS CHANGE: Starting recording phase for episode {current_episode}/{cfg.dataset.num_episodes}"
                )

                log_say(f"Recording episode {current_episode}", cfg.play_sounds)

                # Start with a get-ready phase for the first episode
                if current_episode == 1 and not web_events.get("_initial_get_ready_done"):
                    web_events["rerecord_episode"] = True
                    web_events["exit_early"] = True
                    web_events["_initial_get_ready_done"] = True

                # Add a tracking flag that won't be reset by record_loop
                web_events["_exit_early_triggered"] = False
                logger.info(f"Recording phase - calling record_loop with events: {web_events}")

                custom_record_loop(
                    robot=robot,
                    events=web_events,
                    fps=cfg.dataset.fps,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=teleop,
                    dataset=dataset,
                    control_time_s=cfg.dataset.episode_time_s,
                    single_task=web_events.get("current_task", cfg.dataset.single_task),
                    display_data=cfg.display_data,
                )

                logger.info(f"Recording phase completed - events state: {web_events}")

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
                    dataset.clear_episode_buffer()

                    # Go through reset phase before re-recording (don't increment episode counters)
                    # RESET PHASE - without dataset (matches original record.py exactly)
                    current_phase = "resetting"
                    phase_start_time = time.time()
                    logger.info(f"Starting reset phase for re-record of episode {current_episode}")
                    logger.info(f"Events state at start of reset phase: {web_events}")
                    print(f"🔄 STATUS CHANGE: Starting reset phase for episode {current_episode}")

                    log_say("Reset the environment", cfg.play_sounds)

                    # Reset exit_early flag at the start of each phase
                    web_events["exit_early"] = False
                    logger.info(f"Reset phase - calling record_loop with events: {web_events}")

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

                    logger.info(f"Reset phase completed - events state: {web_events}")

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
            
                dataset.save_episode()
            
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
                    logger.info(f"Events state at start of reset phase: {web_events}")
                    print(f"🔄 STATUS CHANGE: Starting reset phase for episode {current_episode}")

                    log_say("Reset the environment", cfg.play_sounds)

                    # Reset exit_early flag at the start of each phase
                    web_events["exit_early"] = False
                    logger.info(f"Reset phase - calling record_loop with events: {web_events}")

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

                    logger.info(f"Reset phase completed - events state: {web_events}")

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
