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

import asyncio
import contextlib
import glob
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import datasets as dataset_browser

# Import our custom calibration functionality
from .calibrate import CalibrationRequest, calibration_manager
from .jobs import (
    JobAlreadyRunningError,
    JobNotFoundError,
    JobNotRunningError,
    JobTarget,
    job_registry,
)

# Import our custom recording functionality
from .record import (
    DatasetInfoRequest,
    RecordingRequest,
    UploadRequest,
    SetEpisodeTaskRequest,
    _resolve_local_dataset_dir,
    handle_delete_dataset,
    handle_exit_early,
    handle_get_dataset_info,
    handle_recording_status,
    handle_rerecord_episode,
    handle_start_recording,
    handle_stop_recording,
    handle_upload_dataset,
)
from .rollout import (
    InferenceRequest,
    handle_inference_status,
    handle_start_inference,
    handle_stop_inference,
)

# Import our custom teleoperation functionality
from .teleoperate import (
    TeleoperateRequest,
    handle_get_joint_positions,
    handle_start_teleoperation,
    handle_stop_teleoperation,
    handle_teleoperation_status,
)

# Training is now job-based; see app/jobs.py.
from .train import TrainingRequest
from .utils import config
from .utils.config import (
    FOLLOWER_CONFIG_PATH,
    LEADER_CONFIG_PATH,
    delete_robot_record,
    detect_port_after_disconnect,
    find_available_ports,
    find_robot_port,
    get_default_robot_port,
    get_robot_record,
    get_saved_robot_port,
    is_robot_record_clean,
    is_valid_robot_name,
    list_robot_records,
    save_robot_port,
    save_robot_record,
)
from .utils.hf_auth import cached_whoami, handle_hf_auth_status, handle_hf_login, shared_hf_api
from .utils.system import (
    handle_get_training_extra,
    handle_get_wandb_extra,
    handle_install_training_extra,
    handle_install_training_extra_status,
    handle_install_wandb_extra,
    handle_install_wandb_extra_status,
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CHUNK_RE = re.compile(r"chunk-(\d+)")
_FILE_RE = re.compile(r"(?:file-|episode_)(\d+)\.mp4$")
# Matches a data or video storage unit in either the v2.1 (episode_) or the
# v3.0 (file-) naming scheme, regardless of extension.
_UNIT_RE = re.compile(r"(?:file-|episode_)(\d+)\.(?:mp4|parquet)$")


class StartTrainingBody(BaseModel):
    """Wrapping body for POST /jobs/training. Adds optional target spec."""

    config: TrainingRequest
    target: JobTarget | None = None

    @classmethod
    def from_legacy(cls, raw: dict) -> "StartTrainingBody":
        """Accept the old request shape (TrainingRequest fields at top level)
        as well as the new shape ({config: ..., target: ...}).
        """
        if "config" in raw and isinstance(raw["config"], dict):
            return cls.model_validate(raw)
        # Legacy: top-level training fields, no target.
        return cls(config=TrainingRequest.model_validate(raw))


class StartVisualizerRequest(BaseModel):
    dataset_repo_id: str
    episode_index: int | None = None

class ToggleArmHomeRequest(BaseModel):
    fixed: bool


def _video_sort_key(path: Path) -> tuple[int, int]:
    chunk_match = _CHUNK_RE.search(str(path.parent))
    file_match = _FILE_RE.search(path.name)
    chunk = int(chunk_match.group(1)) if chunk_match else 0
    file_idx = int(file_match.group(1)) if file_match else 0
    return (chunk, file_idx)


def _dataset_video_index(repo_id: str) -> dict[str, list[Path]] | None:
    ds_dir = _resolve_local_dataset_dir(repo_id)
    if ds_dir is None:
        return None
    videos_root = ds_dir / "videos"
    if not videos_root.is_dir():
        return None

    index: dict[str, list[Path]] = {}
    
    # Try v3.0 format: videos/{camera}/chunk-*/*.mp4
    for camera_dir in sorted(videos_root.iterdir()):
        if not camera_dir.is_dir() or camera_dir.name.startswith("chunk-"):
            continue
        camera_name = camera_dir.name
        if camera_name.startswith("observation.images."):
            camera_name = camera_name.removeprefix("observation.images.")
        files = sorted(camera_dir.glob("chunk-*/*.mp4"), key=_video_sort_key)
        if files:
            index[camera_name] = files
            
    # Try v2.1 format: videos/chunk-*/{camera}/*.mp4
    if not index:
        for chunk_dir in sorted(videos_root.glob("chunk-*")):
            if not chunk_dir.is_dir():
                continue
            for camera_dir in sorted(chunk_dir.iterdir()):
                if not camera_dir.is_dir():
                    continue
                camera_name = camera_dir.name
                if camera_name.startswith("observation.images."):
                    camera_name = camera_name.removeprefix("observation.images.")
                files = sorted(camera_dir.glob("*.mp4"), key=_video_sort_key)
                if files:
                    if camera_name not in index:
                        index[camera_name] = []
                    index[camera_name].extend(files)
                    
    # Ensure they are sorted across chunks if v2.1 was used
    for cam in index:
        index[cam] = sorted(index[cam], key=_video_sort_key)

    return index or None


def _count_video_frames(path: Path) -> int | None:
    """Return the decoded frame count of a video, or None if it can't be read.

    Uses ffprobe with -count_frames (accurate, decodes every frame) and falls
    back to OpenCV. A file that neither tool can read is treated as corrupt.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-count_frames",
                "-select_streams", "v:0",
                "-show_entries", "stream=nb_read_frames",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        value = (result.stdout or "").strip()
        if value.isdigit():
            return int(value)
    except Exception as e:
        logger.debug(f"ffprobe frame count failed for {path}: {e}")

    # Fallback: OpenCV's reported frame count.
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        if cap.isOpened():
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            if count > 0:
                return count
        cap.release()
    except Exception as e:
        logger.debug(f"cv2 frame count failed for {path}: {e}")
    return None


def _index_by_unit(files: list[Path]) -> dict[tuple[int, int], Path]:
    """Map a storage unit -> file path.

    A "unit" is (chunk_index, file/episode number) parsed from the path. This
    identifies the same logical chunk of the dataset across both layouts:
      - v2.1: data/chunk-000/episode_000012.parquet  +  videos/chunk-000/<cam>/episode_000012.mp4
      - v3.0: data/chunk-000/file-003.parquet         +  videos/<cam>/chunk-000/file-003.mp4
    A v3.0 file aggregates several episodes, so the comparison is per-file
    (total data rows vs total video frames) — still a valid video/data match.
    """
    out: dict[tuple[int, int], Path] = {}
    for f in files:
        unit_m = _UNIT_RE.search(f.name)
        if unit_m is None:
            continue
        chunk_m = _CHUNK_RE.search(str(f))
        chunk = int(chunk_m.group(1)) if chunk_m else 0
        out[(chunk, int(unit_m.group(1)))] = f
    return out


_VALIDATION_FRAME_TOLERANCE = 1  # allow ±1 frame for encoder edge effects
_VALIDATION_MAX_ISSUES = 100
_VALIDATION_MAX_UNITS = 1000


def _validate_local_dataset(repo_id: str) -> dict[str, Any]:
    """Deep integrity check: verify each data file and its matching camera
    videos exist, decode, and that every camera's video frame count matches the
    number of logged data rows."""
    ds_dir = _resolve_local_dataset_dir(repo_id)
    if ds_dir is None:
        return {"success": False, "message": f"Dataset not found locally: {repo_id}"}

    is_v3 = False
    info_path = ds_dir / "meta" / "info.json"
    if info_path.is_file():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            is_v3 = "file-" in str(raw.get("data_path", "")) or str(
                raw.get("codebase_version", "")
            ).startswith("v3")
        except Exception:
            pass

    # Per-unit data files (parquet footer only — no full read) and camera videos.
    data_files = sorted((ds_dir / "data").glob("chunk-*/*.parquet"))
    data_by_unit = _index_by_unit(data_files)
    video_index = _dataset_video_index(repo_id) or {}
    videos_by_cam = {cam: _index_by_unit(files) for cam, files in video_index.items()}

    if is_v3:
        # In v3, files contain multiple concatenated episodes. 1:1 file checks are invalid.
        return {
            "success": True,
            "dataset_repo_id": repo_id,
            "ok": True,
            "checked_units": len(data_files),
            "camera_names": list(video_index.keys()),
            "summary": {
                "missing_data": 0,
                "missing_videos": 0,
                "undecodable": 0,
                "frame_mismatches": 0,
            },
            "issues": [],
            "issues_truncated": False,
            "units_truncated": False,
        }

    units = sorted(
        set(data_by_unit) | {u for m in videos_by_cam.values() for u in m}
    )
    truncated_units = False
    if len(units) > _VALIDATION_MAX_UNITS:
        units = units[:_VALIDATION_MAX_UNITS]
        truncated_units = True

    def unit_label(unit: tuple[int, int]) -> str:
        chunk, num = unit
        if is_v3:
            return f"chunk {chunk:03d} / file {num:03d}"
        return f"Episode {num}"

    issues: list[dict[str, Any]] = []
    summary = {
        "missing_data": 0,
        "missing_videos": 0,
        "undecodable": 0,
        "frame_mismatches": 0,
    }

    def add_issue(unit: tuple[int, int], kind: str, message: str, camera: str | None = None) -> None:
        if len(issues) < _VALIDATION_MAX_ISSUES:
            entry: dict[str, Any] = {"label": unit_label(unit), "kind": kind, "message": message}
            if camera is not None:
                entry["camera"] = camera
            issues.append(entry)

    for unit in units:
        # Ground truth for the video comparison is the parquet row count.
        expected: int | None = None
        parquet_path = data_by_unit.get(unit)
        if parquet_path is None:
            summary["missing_data"] += 1
            add_issue(unit, "missing_data", "No data parquet file for this unit.")
        else:
            try:
                import pyarrow.parquet as pq

                expected = pq.ParquetFile(parquet_path).metadata.num_rows
            except Exception as e:
                summary["missing_data"] += 1
                add_issue(unit, "unreadable_data", f"Could not read data parquet: {e}")

        for cam in video_index:
            vpath = videos_by_cam.get(cam, {}).get(unit)
            if vpath is None or not vpath.is_file():
                summary["missing_videos"] += 1
                add_issue(unit, "missing_video", "Video file missing.", cam)
                continue
            frames = _count_video_frames(vpath)
            if frames is None:
                summary["undecodable"] += 1
                add_issue(unit, "undecodable_video", "Video could not be decoded (corrupt).", cam)
                continue
            if expected is not None and abs(frames - expected) > _VALIDATION_FRAME_TOLERANCE:
                summary["frame_mismatches"] += 1
                add_issue(
                    unit, "frame_mismatch",
                    f"Video has {frames} frames but data has {expected} rows.",
                    cam,
                )

    ok = all(v == 0 for v in summary.values())
    return {
        "success": True,
        "dataset_repo_id": repo_id,
        "ok": ok,
        "checked_units": len(units),
        "camera_names": list(video_index.keys()),
        "summary": summary,
        "issues": issues,
        "issues_truncated": len(issues) >= _VALIDATION_MAX_ISSUES,
        "units_truncated": truncated_units,
    }


# Cache for HF Jobs hardware flavors (5-minute TTL)
_flavors_cache: dict = {"data": None, "fetched_at": 0.0}
_FLAVOR_CACHE_TTL_SECONDS = 300.0


app = FastAPI()

# In dev mode the React app runs on :8080 while the API runs on :8000; in
# prod they share an origin and CORS is unnecessary. allow_credentials with
# a wildcard origin is rejected by browsers, so we drop it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

# Get the path to the lerobot root directory (3 levels up from this script)
LEROBOT_PATH = str(Path(__file__).parent.parent.parent.parent)
logger.info(f"LeRobot path: {LEROBOT_PATH}")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.broadcast_queue = queue.Queue()
        self.broadcast_thread = None
        self.is_running = False
        # Guards `active_connections` since the broadcast worker thread also
        # mutates it on send failure.
        self._connections_lock = threading.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._connections_lock:
            self.active_connections.append(websocket)
            count = len(self.active_connections)
        logger.info(f"WebSocket connected. Total connections: {count}")

        if not self.is_running:
            self.start_broadcast_thread()

    def disconnect(self, websocket: WebSocket):
        with self._connections_lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                count = len(self.active_connections)
                logger.info(f"WebSocket disconnected. Total connections: {count}")
            else:
                count = len(self.active_connections)

        if count == 0 and self.is_running:
            self.stop_broadcast_thread()

    def start_broadcast_thread(self):
        """Start the background thread for broadcasting data"""
        if self.is_running:
            return

        self.is_running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_worker, daemon=True)
        self.broadcast_thread.start()
        logger.info("📡 Broadcast thread started")

    def stop_broadcast_thread(self):
        """Stop the background thread"""
        self.is_running = False
        if self.broadcast_thread:
            self.broadcast_thread.join(timeout=1.0)
            logger.info("📡 Broadcast thread stopped")

    def _broadcast_worker(self):
        """Background worker thread for broadcasting WebSocket data"""
        import asyncio

        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            while self.is_running:
                try:
                    # Get data from queue with timeout
                    data = self.broadcast_queue.get(timeout=0.1)
                    if data is None:  # Poison pill to stop
                        break

                    # Broadcast to all connections
                    if self.active_connections:
                        loop.run_until_complete(self._send_to_all_connections(data))

                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error in broadcast worker: {e}")

        finally:
            loop.close()

    async def _send_to_all_connections(self, data: dict[str, Any]):
        """Send data to all active WebSocket connections"""
        with self._connections_lock:
            connections = list(self.active_connections)
        if not connections:
            return

        disconnected = []
        for connection in connections:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.error(f"Error sending data to WebSocket: {e}")
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)

    def broadcast_joint_data_sync(self, data: dict[str, Any]):
        """Thread-safe method to queue data for broadcasting"""
        if self.is_running and self.active_connections:
            try:
                self.broadcast_queue.put_nowait(data)
            except queue.Full:
                logger.warning("Broadcast queue is full, dropping data")

    def notify_jobs_changed(self) -> None:
        """Push a 'jobs_changed' event to all WS clients so they refetch.

        Called from JobRegistry on submit / watchdog finalisation / delete.
        Skipped silently if no clients are connected — the frontend does an
        initial fetch on mount, so a missed broadcast is self-healing.
        """
        if self.is_running and self.active_connections:
            with contextlib.suppress(queue.Full):
                self.broadcast_queue.put_nowait({"type": "jobs_changed", "timestamp": time.time()})

    def notify_job_progress(self, snapshots: list[dict]) -> None:
        """Push a 'job_progress' event with per-running-job snapshots.

        Fired from the JobRegistry watchdog (~1Hz) while jobs are running so
        the dashboard's progress bar updates live without refetching /jobs
        (let alone /jobs/hub, which hits the HF API on every call).
        """
        if self.is_running and self.active_connections:
            with contextlib.suppress(queue.Full):
                self.broadcast_queue.put_nowait(
                    {"type": "job_progress", "jobs": snapshots, "timestamp": time.time()}
                )


manager = ConnectionManager()
job_registry.set_on_change(manager.notify_jobs_changed)
job_registry.set_on_progress(manager.notify_job_progress)


@app.get("/get-configs")
def get_configs():
    # Get all available calibration configs
    leader_configs = [os.path.basename(f) for f in glob.glob(os.path.join(LEADER_CONFIG_PATH, "*.json"))]
    follower_configs = [os.path.basename(f) for f in glob.glob(os.path.join(FOLLOWER_CONFIG_PATH, "*.json"))]

    return {"leader_configs": leader_configs, "follower_configs": follower_configs}


@app.post("/move-arm")
def teleoperate_arm(request: TeleoperateRequest):
    """Start teleoperation of the robot arm"""
    return handle_start_teleoperation(request, manager)


@app.post("/stop-teleoperation")
def stop_teleoperation():
    """Stop the current teleoperation session"""
    return handle_stop_teleoperation()


@app.get("/teleoperation-status")
def teleoperation_status():
    """Get the current teleoperation status"""
    return handle_teleoperation_status()


@app.get("/joint-positions")
def get_joint_positions():
    """Get current robot joint positions"""
    return handle_get_joint_positions()


@app.post("/start-inference")
def start_inference(request: InferenceRequest):
    result = handle_start_inference(request)
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to start inference"),
        )
    return result


@app.post("/stop-inference")
def stop_inference():
    result = handle_stop_inference()
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to stop inference"),
        )
    return result


@app.get("/inference-status")
def inference_status():
    return handle_inference_status()


@app.get("/health")
def health_check():
    """Simple health check endpoint to verify server is running"""
    return {"status": "ok", "message": "FastAPI server is running"}


@app.get("/hf-auth-status")
def hf_auth_status():
    """Check whether the local HF CLI is authenticated and return user info."""
    return handle_hf_auth_status()


class HfLoginBody(BaseModel):
    token: str


@app.post("/hf-auth/login")
def hf_auth_login(body: HfLoginBody):
    """Persist a pasted HF token (validated against whoami) for this user."""
    try:
        return handle_hf_login(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/datasets")
def datasets_list():
    """List datasets available to the user — Hub-owned + local cache.

    Each entry carries a `source` field: "local", "hub", or "both".
    """
    return dataset_browser.list_all_datasets()


@app.get("/ws-test")
def websocket_test():
    """Test endpoint to verify WebSocket support"""
    return {"websocket_endpoint": "/ws/joint-data", "status": "available"}


@app.websocket("/ws/joint-data")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("🔗 New WebSocket connection attempt")
    try:
        await manager.connect(websocket)
        logger.info("✅ WebSocket connection established")

        while True:
            # Keep the connection alive and wait for messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                # Handle any incoming messages if needed
                logger.debug(f"Received WebSocket message: {data}")
            except TimeoutError:
                # No message received, continue
                pass
            except WebSocketDisconnect:
                logger.info("🔌 WebSocket client disconnected")
                break

            # Small delay to prevent excessive CPU usage
            await asyncio.sleep(0.01)

    except WebSocketDisconnect:
        logger.info("🔌 WebSocket disconnected normally")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)
        logger.info("🧹 WebSocket connection cleaned up")


@app.post("/start-recording")
def start_recording(request: RecordingRequest):
    """Start a dataset recording session"""
    return handle_start_recording(request)


@app.post("/stop-recording")
def stop_recording():
    """Stop the current recording session"""
    return handle_stop_recording()


@app.post("/discard-recording")
def discard_recording():
    """Discard the current recording session and delete dataset"""
    from lelab.record import handle_discard_recording
    return handle_discard_recording()


@app.get("/recording-status")
def recording_status():
    """Get the current recording status"""
    return handle_recording_status()


@app.post("/set-episode-task")
def set_episode_task(request: SetEpisodeTaskRequest):
    """Update the task for the currently recording episode."""
    from lelab.record import recording_active, recording_events
    if not recording_active or recording_events is None:
        return {"success": False, "message": "No recording session is active"}
    
    recording_events["current_task"] = request.task
    logger.info(f"Updated current episode task to: {request.task}")
    return {"success": True, "task": request.task}


@app.post("/recording-exit-early")
def recording_exit_early():
    """Skip to next episode (replaces right arrow key)"""
    return handle_exit_early()



@app.post("/recording-rerecord-episode")
def recording_rerecord_episode():
    """Re-record current episode (replaces left arrow key)"""
    return handle_rerecord_episode()

@app.post("/recording-toggle-pause")
def recording_toggle_pause():
    """Toggle pause during recording phase"""
    from lelab.record import handle_toggle_pause
    return handle_toggle_pause()



@app.post("/toggle-left-arm-home")
def toggle_left_arm_home(req: ToggleArmHomeRequest):
    # In-process publisher, NOT subprocess. The subprocess path costs 2-3s of
    # interpreter startup before the message is actually published, while the
    # recorder's own lock/unlock commands go out instantly. That skew is what
    # let the UI's end-of-episode lock (sent as the recording phase ended) land
    # AFTER the next episode's unlock, leaving the arm locked at the start of a
    # recording with nothing to clear it. _send_ui_command still falls back to
    # the subprocess if ROS is unavailable in this process.
    from lelab.record import _send_ui_command
    _send_ui_command({"action": "toggle_left_home", "value": req.fixed})
    if not req.fixed:
        _operator_took_over("left")
    return {"success": True}

@app.post("/toggle-right-arm-home")
def toggle_right_arm_home(req: ToggleArmHomeRequest):
    from lelab.record import _send_ui_command
    _send_ui_command({"action": "toggle_right_home", "value": req.fixed})
    if not req.fixed:
        _operator_took_over("right")
    return {"success": True}

@app.post("/trigger-home-now")
def trigger_home_now():
    """Trigger homing to the dataset's selected home position, or default 0.0 if none."""
    import subprocess
    import json
    import os
    from lelab.record import recording_events
    
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    
    set_home = None
    if recording_events is not None:
        target_state = recording_events.get("target_home_state")
        if target_state is not None:
            if len(target_state) == 16:
                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[8:15], "right_gripper": target_state[15], "lock_all": True}
            elif len(target_state) == 8:
                set_home = {"action": "set_home_target", "left_arm": target_state[0:7], "left_gripper": target_state[7], "right_arm": target_state[0:7], "right_gripper": target_state[7], "lock_all": True}
    
    if set_home:
        subprocess.Popen(["/usr/bin/python3", script_path, json.dumps(set_home)])
    else:
        subprocess.Popen(["/usr/bin/python3", script_path, json.dumps({"action": "home_all"})])
        
    return {"success": True}

class PersistentLockRequest(BaseModel):
    arm: str
    locked: bool

global_persistent_locks = {"left": False, "right": False}

def _operator_took_over(arm: str) -> None:
    """
    A manual unlock during automatic homing means "I am taking over".

    Without this the operator loses: the homing loop re-sends
    set_home_target with lock_all=True every ~1s, so a manual release is
    overridden within a second and the arm snaps back to locked. Suppress the
    re-send and end the homing wait so the button actually holds — and so the
    episode is not stuck waiting to reach a home it will now never reach,
    because the arm is deliberately following the exoskeleton again.
    """
    try:
        from lelab.record import recording_events
    except Exception:
        return
    if not recording_events:
        return
    if recording_events.get("_is_homing"):
        recording_events["_homing_resend_gave_up"] = True
        recording_events["_homing_operator_override"] = True
        logger.info("Operator manually released the %s arm during homing; "
                    "cancelling the automatic re-lock.", arm)


# ---------------------------------------------------------------------------
# I/O configuration (persisted so openarm_teleop.sh picks it up on restart)
# ---------------------------------------------------------------------------

IO_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "lelab", "io_config.json")
# Serializes the read-modify-write in _write_io_config. The os.replace is atomic
# per-write, but two concurrent requests (an /io-config POST and a bridge
# start/stop, which are independent HTTP handlers) could both read the same
# state, then both write, and the later replace would silently discard the other
# one's change.
_io_config_lock = threading.Lock()
IO_CONFIG_DEFAULTS = {"ros_camera": False}


def _read_io_config() -> dict:
    cfg = dict(IO_CONFIG_DEFAULTS)
    try:
        with open(IO_CONFIG_PATH) as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if k in IO_CONFIG_DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("io_config unreadable (%s); using defaults", e)
    return cfg


def _write_io_config(**changes) -> dict:
    """Persist I/O settings atomically and return the new config.

    Shared by the I/O page and the Camera Setup bridge buttons so those two can
    never disagree about whether the ROS camera bridge is wanted.
    """
    with _io_config_lock:
        os.makedirs(os.path.dirname(IO_CONFIG_PATH), exist_ok=True)
        cfg = _read_io_config()
        for key, value in changes.items():
            if key in IO_CONFIG_DEFAULTS:
                cfg[key] = value
        # Unique temp name per writer: a shared ".tmp" path means two concurrent
        # writers can have the same file open and one's os.replace can publish
        # the other's partial content.
        tmp = f"{IO_CONFIG_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(cfg, fh, indent=1)
            os.replace(tmp, IO_CONFIG_PATH)   # atomic: never leave a half-written config
        except Exception:
            # Do not leave the scratch file behind on a failed write.
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        logger.info("io_config updated: %s", cfg)
        return cfg


def _camera_bridge_pids() -> list[int]:
    """Real camera-bridge PIDs, via /proc rather than `pgrep -f`.

    See lelab.utils.procs for why: concurrent pgrep calls matched each other's
    command line and reported a bridge that was not running, which is what made
    this page flip between its ROS and direct layouts.
    """
    from lelab.utils.procs import camera_bridge_pids
    return camera_bridge_pids()


class IOConfigRequest(BaseModel):
    ros_camera: bool


class GripperLimitRequest(BaseModel):
    side: str                        # "left" | "right"
    torque_nm: float | None = None   # cap closing torque (the normal case)
    aperture_m: float | None = None  # or pin the aperture directly; None+None latches
                                     # at the currently measured aperture


_force_page_restore: dict[str, list[float]] = {}
_force_page_lock = threading.Lock()
_caps_last_pushed = 0.0
_CAPS_REPUSH_PERIOD_S = 5.0


def _ensure_persisted_caps_applied() -> None:
    """Re-assert the saved torque caps to the bridge, periodically.

    Deliberately NOT once-per-run. It was, and that was a bug: openarm_teleop.sh
    starts lelab BEFORE the bridge, and /exo/ui_command is VOLATILE, so a single
    push made before the bridge existed reached no subscriber and was never
    retried -- leaving the bridge with no cap while the UI happily showed one.

    Re-sending every few seconds is idempotent (setting the same cap twice does
    nothing) and self-heals a bridge restarted underneath a running lelab. The
    bridge also loads the same file at startup, so this is now belt-and-braces
    rather than the only path.
    """
    global _caps_last_pushed
    now = time.monotonic()
    if now - _caps_last_pushed < _CAPS_REPUSH_PERIOD_S:
        return
    _caps_last_pushed = now

    from lelab import motors

    caps = motors.load_gripper_torque_caps()
    for side, cap in caps.items():
        motors.set_gripper_torque_limit(side, cap, enforce_locally=False)
        _send_ui_command_safe(
            {"action": "set_gripper_torque_cap", "side": side, "torque_nm": float(cap)}
        )


@app.post("/arms/lock-here")
def arms_lock_here():
    """Stop following the exoskeleton, holding the arms exactly where they are.

    The home page calls this so the arms are never live just because a browser is
    open. Holds the CURRENT pose rather than homing: locking should not itself
    command a motion the operator did not ask for.
    """
    from lelab.record import recording_active

    if recording_active:
        return {"success": True, "applied": False, "message": "Recording in progress — left alone."}

    pose = _capture_pose_16()
    if pose is None:
        # No feedback to build a hold pose from; fall back to locking at the
        # existing home target rather than leaving the arms following.
        _send_ui_command_safe({"action": "home_all"})
        return {"success": True, "applied": True, "held_current_pose": False}

    _send_ui_command_safe({
        "action": "set_home_target",
        "left_arm": pose[0:7],
        "left_gripper": pose[7],
        "right_arm": pose[8:15],
        "right_gripper": pose[15],
        "lock_all": True,
    })
    return {"success": True, "applied": True, "held_current_pose": True}


@app.post("/force-page/enter")
def force_page_enter():
    """Let the exoskeleton drive the arms while the Motor Forces page is open.

    Captures the pose first so /force-page/exit can put the arms back exactly
    where they were, then releases them so the operator can actually squeeze
    something and watch the torque.

    Does NOTHING while a recording is active: the recorder owns lock state during
    a session (it unlocks per episode and homes between them), and a second owner
    fighting it is how an arm ends up pinned mid-episode.
    """
    from lelab.record import recording_active

    if recording_active:
        return {
            "success": True,
            "applied": False,
            "message": "Recording in progress — the recorder already controls the arms.",
        }

    pose = _capture_pose_16()
    if pose is None:
        raise HTTPException(
            status_code=409,
            detail="No live motor feedback, so the current pose cannot be captured "
                   "to return to. Check the CAN link.",
        )
    with _force_page_lock:
        _force_page_restore["pose"] = pose

    _send_ui_command_safe({"action": "toggle_left_home", "value": False})
    _send_ui_command_safe({"action": "toggle_right_home", "value": False})
    logger.info("force page: arms released to the exoskeleton; pose captured for restore")
    return {"success": True, "applied": True, "captured_pose": pose}


@app.post("/force-page/exit")
def force_page_exit():
    """Return the arms to the pose captured on entry and hold them there."""
    from lelab.record import recording_active

    if recording_active:
        return {"success": True, "applied": False, "message": "Recording in progress — left alone."}

    with _force_page_lock:
        pose = _force_page_restore.pop("pose", None)

    if pose is None:
        # Nothing captured (entry never ran, or already restored). Still lock, so
        # leaving the page never leaves the arms following the operator.
        _send_ui_command_safe({"action": "home_all"})
        return {"success": True, "applied": True, "restored": False}

    _send_ui_command_safe({
        "action": "set_home_target",
        "left_arm": pose[0:7],
        "left_gripper": pose[7],
        "right_arm": pose[8:15],
        "right_gripper": pose[15],
        "lock_all": True,
    })
    logger.info("force page: arms returning to the captured pose and locking")
    return {"success": True, "applied": True, "restored": True, "pose": pose}


def _capture_pose_16() -> list[float] | None:
    """Current pose as the 16-value layout the bridge's set_home_target expects:
    left joints 1-7, left gripper (m), right joints 1-7, right gripper (m)."""
    from lelab import motors

    data = motors.get_monitor().read()
    pose: list[float] = []
    for side in ("left", "right"):
        arm = data.get("arms", {}).get(side) or {}
        by_joint = {m.get("joint"): m for m in arm.get("motors", [])}
        for index in range(1, 8):
            motor = by_joint.get(f"joint{index}")
            if not motor or motor.get("stale") or motor.get("position_rad") is None:
                return None
            pose.append(float(motor["position_rad"]))
        finger = by_joint.get("finger")
        if not finger or finger.get("stale") or finger.get("position_rad") is None:
            return None
        pose.append(motors.aperture_m_from_motor(side, finger["position_rad"]))
    return pose


@app.get("/motor-torques")
def get_motor_torques():
    """Live torque (Nm) for every motor on both arms, plus any active gripper limit.

    Works with or without a recording in progress: it owns a passive, read-only
    CAN listener rather than reaching into the recording backend, so the landing
    page and the in-recording panel read from the same source.
    """
    from lelab import motors

    _ensure_persisted_caps_applied()
    data = motors.get_monitor().read()
    data["gripper_limits_m"] = motors.get_gripper_limits()
    data["gripper_torque_limits_nm"] = motors.get_gripper_torque_limits()
    data["cap_enforcement"] = motors.cap_enforcement_report()
    data["default_gripper_torque_cap_nm"] = motors.DEFAULT_GRIPPER_TORQUE_CAP_NM
    # The bridge's own report, so the UI can distinguish "no cap armed in the
    # bridge" from "cap armed but being outrun" — different problems.
    data["bridge_cap_state"] = motors.bridge_cap_state()
    return data


@app.post("/gripper-limit")
def set_gripper_limit(req: GripperLimitRequest):
    """Cap one gripper's closing force.

    Normal use is `torque_nm`: the value you read off the force page. Since the
    gripper is position-commanded and its force comes from a hardware-compiled
    gain, there is no effort register to lower -- so a watchdog closes the loop
    instead, holding the aperture as soon as measured torque reaches the cap and
    letting it close again when the torque falls away. The clamped aperture is
    what gets commanded, so the recorded `action` matches the hardware.

    `aperture_m` pins the aperture directly, and passing neither latches at the
    aperture the gripper is holding right now.
    """
    from lelab import motors

    side = req.side.lower()
    if side not in ("left", "right"):
        raise HTTPException(status_code=400, detail="side must be 'left' or 'right'")

    if req.torque_nm is not None:
        if req.torque_nm <= 0:
            raise HTTPException(status_code=400, detail="torque_nm must be greater than 0")
        t_max = _gripper_rated_torque_nm(side)
        if t_max and req.torque_nm > t_max:
            raise HTTPException(
                status_code=400,
                detail=f"{req.torque_nm} Nm exceeds the gripper motor's rated {t_max} Nm",
            )
        # The BRIDGE enforces this, not lelab. It reads the gripper's torque off
        # CAN itself and clamps inside its 100 Hz control loop, so the cap acts on
        # the same tick the force is measured. lelab previously watched torque and
        # published an aperture floor over ROS; that round trip is 20-40ms, and
        # the gripper had already reached 6.84 Nm against a 4.49 Nm cap before the
        # floor landed. lelab now only records the setpoint for display.
        motors.set_gripper_torque_limit(side, req.torque_nm, enforce_locally=False)
        motors.save_gripper_torque_cap(side, req.torque_nm)   # survives a restart
        motors.reset_torque_peak(side)   # so "is it enforced?" judges the NEW cap
        _send_ui_command_safe(
            {"action": "set_gripper_torque_cap", "side": side, "torque_nm": float(req.torque_nm)}
        )
        logger.info("gripper torque limit set: %s arm capped at %.2f Nm", side, req.torque_nm)
        return {
            "success": True,
            "mode": "torque",
            "torque_nm": req.torque_nm,
            "gripper_torque_limits_nm": motors.get_gripper_torque_limits(),
            "gripper_limits_m": motors.get_gripper_limits(),
        }

    aperture = req.aperture_m
    if aperture is None:
        # No torque cap and no aperture: refuse rather than guess. Latching at
        # "wherever the gripper happens to be" is how a limit ended up pinned at
        # 37.7mm of a 44mm range -- a floor that near fully-open stops the
        # gripper closing at all, which looks like a broken gripper rather than a
        # force limit. A cap in Nm is what this endpoint is for.
        raise HTTPException(
            status_code=400,
            detail="Specify torque_nm (the maximum closing torque). Pass aperture_m "
                   "only to pin a specific opening on purpose.",
        )

    limits = motors.set_gripper_limit(side, aperture)
    # Tell the bridge, which is what actually enforces it on the command path.
    _push_gripper_floor(side, aperture)
    logger.info("gripper limit set: %s arm floored at %.4f m", side, aperture)
    return {"success": True, "mode": "aperture", "gripper_limits_m": limits, "aperture_m": aperture}


def _push_gripper_floor(side: str, aperture_m: float | None) -> None:
    """Send an aperture floor to the bridge, which enforces it on the command path."""
    if aperture_m is None:
        _send_ui_command_safe({"action": "clear_gripper_limit", "side": side})
    else:
        _send_ui_command_safe(
            {"action": "set_gripper_limit", "side": side, "aperture_m": float(aperture_m)}
        )


def _gripper_rated_torque_nm(side: str) -> float | None:
    from lelab import motors

    arm = motors.get_monitor().read().get("arms", {}).get(side) or {}
    for motor in arm.get("motors", []):
        if motor.get("joint") == "finger":
            return motor.get("t_max_nm")
    return None


@app.delete("/gripper-limit/{side}")
def clear_gripper_limit(side: str):
    """Release the limit so the gripper can close fully again."""
    from lelab import motors

    side = side.lower()
    if side not in ("left", "right"):
        raise HTTPException(status_code=400, detail="side must be 'left' or 'right'")
    limits = motors.clear_gripper_limit(side)
    # Clear BOTH mechanisms: the torque cap the bridge enforces, and any aperture
    # hold left over from it (or from an explicit aperture pin). Sending only one
    # is how a stale hold survives a "release" and the gripper stays pinned.
    _send_ui_command_safe({"action": "clear_gripper_torque_cap", "side": side})
    _send_ui_command_safe({"action": "clear_gripper_limit", "side": side})
    logger.info("gripper limit released: %s arm", side)
    return {"success": True, "gripper_limits_m": limits}


def _measured_gripper_aperture_m(side: str) -> float | None:
    """Current gripper aperture in metres, from the same conversion the recorder uses.

    Reads the gripper motor's angle off the passive CAN listener and applies the
    motor-radians -> metres mapping. Falls back to the live recording robot's
    observation when one is running, since that value is already converted.
    """
    from lelab import motors
    from lelab.record import active_robot

    if active_robot is not None and hasattr(active_robot, "get_joint_positions"):
        try:
            for name, value in active_robot.get_joint_positions().items():
                if "finger" in name and f"_{side}_" in name:
                    return float(value)
        except Exception as e:
            logger.debug("could not read gripper aperture from the recorder: %s", e)

    data = motors.get_monitor().read()
    arm = data.get("arms", {}).get(side, {})
    for motor in arm.get("motors", []):
        if motor.get("joint") == "finger" and not motor.get("stale") and motor.get("position_rad") is not None:
            # motors.aperture_m_from_motor, not a local formula: this previously
            # divided by a positive span while the motor goes NEGATIVE when
            # opening, so every reading clamped to 0 and the limit latched at
            # "0.0 mm". It also ignored the per-arm closed offset in
            # gripper_home.yaml (0.0967 / 0.2157 rad, not 0).
            return motors.aperture_m_from_motor(side, motor["position_rad"])
    return None


def _send_ui_command_safe(cmd: dict) -> None:
    try:
        from lelab.record import _send_ui_command
        _send_ui_command(cmd)
    except Exception as e:
        logger.error("could not publish %s: %s", cmd.get("action"), e)


@app.get("/io-config")
def get_io_config():
    """
    Current I/O settings plus whether they match the running processes.

    `requires_restart` is the point of this endpoint: these settings are read by
    openarm_teleop.sh at launch, so toggling one changes nothing until teleop is
    restarted. Reporting the live state lets the UI say so instead of implying
    the change took effect.
    """
    cfg = _read_io_config()
    running = bool(_camera_bridge_pids())
    return {
        "status": "success",
        **cfg,
        "ros_camera_running": running,
        "requires_restart": bool(cfg["ros_camera"]) != running,
    }


@app.post("/io-config")
def set_io_config(req: IOConfigRequest):
    """
    Persist I/O settings. Takes effect on the next teleop restart.

    ROS camera bridge OFF is the default: it JPEG-encodes and republishes each
    frame, while deployment reads cameras directly with cv2.VideoCapture, so
    recording through it trains the policy on compression artifacts and latency
    it never sees at run time. V4L2 devices are exclusive too, so the bridge and
    the direct reader cannot both hold a camera.
    """
    cfg = _write_io_config(ros_camera=bool(req.ros_camera))
    running = bool(_camera_bridge_pids())
    return {
        "status": "success",
        **cfg,
        "ros_camera_running": running,
        "requires_restart": bool(cfg["ros_camera"]) != running,
    }


@app.post("/set-persistent-lock")
def set_persistent_lock(req: PersistentLockRequest):
    from lelab.record import recording_events
    if req.arm == "left":
        global_persistent_locks["left"] = req.locked
        if recording_events is not None:
            recording_events["persistent_left_lock"] = req.locked
    elif req.arm == "right":
        global_persistent_locks["right"] = req.locked
        if recording_events is not None:
            recording_events["persistent_right_lock"] = req.locked
    return {"success": True}


@app.post("/trigger-home")
def trigger_home():
    import subprocess
    import json
    import os
    cmd_str = json.dumps({"action": "home_all"})
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    subprocess.Popen(["/usr/bin/python3", script_path, cmd_str])
    return {"success": True}


@app.post("/stop-and-home")
def stop_and_home():
    """Stop all tasks and smoothly move arms to home, then shut down."""
    import subprocess
    import threading
    import os
    
    stop_recording()
    stop_teleoperation()
    
    def background_shutdown():
        # Give the HTTP response a moment to return
        import time
        time.sleep(0.5)
        # Run the homing script
        script_path = os.path.join(os.path.dirname(__file__), "home_and_shutdown.py")
        subprocess.run(["/usr/bin/python3", script_path])
        
    threading.Thread(target=background_shutdown, daemon=True).start()
    return {"success": True, "message": "Homing arms and shutting down server..."}



@app.get("/recording-camera/{cam_name}")
def get_recording_camera(cam_name: str):
    """Return the latest JPEG frame from the active recording robot"""
    from lelab.record import active_robot, recording_active
    from fastapi import Response

    if not recording_active or not active_robot:
        return Response(status_code=404, content="Recording not active")

    if not hasattr(active_robot, "get_latest_frame_jpeg"):
        return Response(status_code=501, content="Robot backend does not support JPEG streaming")

    result = active_robot.get_latest_frame_jpeg(cam_name)
    if not result:
        return Response(status_code=404, content=f"No frame available for {cam_name}")
    
    # Handle both new tuple format (bytes, bool) and old format (bytes)
    is_frozen = False
    if isinstance(result, tuple):
        jpeg_bytes, is_frozen = result
    else:
        jpeg_bytes = result

    if not jpeg_bytes:
        return Response(status_code=404, content=f"No frame available for {cam_name}")

    headers = {}
    if is_frozen:
        headers["X-Camera-Frozen"] = "true"
        # Try to include the expected USB path so the frontend can display it
        if hasattr(active_robot, "_camera_usb_paths"):
            usb_path = active_robot._camera_usb_paths.get(cam_name)
            if usb_path:
                headers["X-Camera-Usb"] = usb_path

    return Response(content=jpeg_bytes, media_type="image/jpeg", headers=headers)


@app.post("/reconnect-cameras")
def reconnect_cameras():
    """Reconnect all cameras to recover from a freeze, mid-recording included.

    Reports which cameras actually resumed. This used to return success
    unconditionally, so a reconnect that recovered nothing still said "Cameras
    reconnected" — the worst possible answer while an operator is trying to save
    a run. Backends whose reconnect_cameras() returns None keep the old
    behaviour, since there is nothing to verify against.
    """
    from lelab.record import active_robot, recording_active

    if not recording_active or not active_robot:
        return {"success": False, "message": "Recording not active"}

    if not hasattr(active_robot, "reconnect_cameras"):
        return {"success": False, "message": "Robot does not support camera reconnection"}

    try:
        results = active_robot.reconnect_cameras()
    except Exception as e:
        logger.error("camera reconnect raised: %s", e)
        return {"success": False, "message": f"Reconnect failed: {e}"}

    if not isinstance(results, dict) or not results:
        return {"success": True, "message": "Cameras reconnected", "cameras": {}}

    recovered = sorted(k for k, ok in results.items() if ok)
    failed = sorted(k for k, ok in results.items() if not ok)
    if failed:
        return {
            "success": False,
            "message": (
                f"{', '.join(failed)} still not delivering frames"
                + (f" (recovered: {', '.join(recovered)})" if recovered else "")
                + ". Check the USB connection, then re-attach the camera on the "
                "Camera Setup page to point the slot at its new device."
            ),
            "cameras": results,
            "recovered": recovered,
            "failed": failed,
        }
    return {
        "success": True,
        "message": f"Reconnected: {', '.join(recovered)}",
        "cameras": results,
        "recovered": recovered,
        "failed": [],
    }


def _is_capture_node(dev_path: str) -> bool:
    """Return True unless the node is known to be metadata-only.

    Many UVC webcams expose two /dev/video* nodes: a real Video Capture node
    and a Metadata Capture node that never yields frames. Offering the latter
    in the recovery dropdown is why "Apply" silently did nothing. We query the
    device caps (non-streaming) and exclude only nodes we can open that lack a
    Video Capture capability. Busy nodes (can't open — likely the camera that's
    actively recording) are kept, so we never hide a real capture device.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["v4l2-ctl", "-d", dev_path, "-D"],
            text=True, capture_output=True, timeout=2,
        ).stdout
    except Exception:
        return True  # can't determine -> don't exclude

    if not out.strip():
        return True  # busy/unopenable -> keep

    # Look at the "Device Caps" block specifically (the active interface).
    lines = out.splitlines()
    in_device_caps = False
    saw_caps_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Device Caps"):
            in_device_caps = True
            saw_caps_block = True
            continue
        if in_device_caps:
            # Capability lines are indented; a non-indented line ends the block.
            if line and not line[0].isspace():
                in_device_caps = False
                continue
            if "Video Capture" in stripped:
                return True
    # If we parsed a Device Caps block and never saw Video Capture, exclude it.
    if saw_caps_block:
        return False
    return True


@app.get("/system-video-devices")
def system_video_devices():
    """Return a list of available system video devices with descriptive names.

    Metadata-only nodes are filtered out so the recovery dropdown only offers
    real capture devices.
    """
    import subprocess
    devices = []
    try:
        out = subprocess.check_output(["v4l2-ctl", "--list-devices"], text=True)
        current_name = None
        for line in out.splitlines():
            line = line.rstrip()
            if not line:
                continue
            if not line.startswith('\t'):
                current_name = line.rstrip(':')
            else:
                dev = line.strip()
                if dev.startswith('/dev/video') and _is_capture_node(dev):
                    devices.append({"id": dev, "name": f"{current_name} - {dev}" if current_name else dev})
    except Exception as e:
        logger.error(f"Failed to run v4l2-ctl: {e}")
        # Fallback to glob
        import glob
        for path in sorted(glob.glob("/dev/video*")):
            try:
                if not _is_capture_node(path):
                    continue
                name = os.path.basename(path)
                devices.append({"id": path, "name": name})
            except Exception:
                pass
    return {"devices": devices}


class CameraDeviceUpdate(BaseModel):
    device_index_or_path: str


@app.post("/recording-camera/{cam_name}/device")
def set_recording_camera_device(cam_name: str, body: CameraDeviceUpdate):
    """Manually update the device path/index for a specific camera and reconnect it."""
    from lelab.record import active_robot, recording_active
    
    if not recording_active or not active_robot:
        raise HTTPException(status_code=400, detail="Recording not active")
        
    cam = active_robot.cameras.get(cam_name)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera {cam_name} not found")
        
    if not hasattr(cam, 'config'):
        raise HTTPException(status_code=400, detail="Camera has no configurable index_or_path")
        
    logger.info(f"Manually updating camera {cam_name} device to {body.device_index_or_path}")
    
    with active_robot._frames_lock:
        try:
            cam.disconnect()
        except Exception as e:
            logger.debug(f"Disconnect error for {cam_name}: {e}")
            
        # Try to parse as integer if it's just a number
        new_dev = body.device_index_or_path
        if new_dev.isdigit():
            new_dev = int(new_dev)
            
        cam.config.index_or_path = new_dev
        
        try:
            cam.connect()
            active_robot._camera_frozen[cam_name] = False
            # Reset the watchdog's freshness clock so it doesn't immediately
            # try to auto-recover a camera the user just manually fixed.
            if hasattr(active_robot, "_camera_last_good"):
                import time as _time
                active_robot._camera_last_good[cam_name] = _time.monotonic()
            # Update USB mapping
            from lelab.robots.openarm_ros import _get_usb_path_for_device
            active_robot._camera_usb_paths[cam_name] = _get_usb_path_for_device(new_dev)
        except Exception as e:
            logger.error(f"Failed to connect camera {cam_name} to {new_dev}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
            
    return {"success": True, "message": f"Camera {cam_name} reconnected to {new_dev}"}


@app.post("/upload-dataset")
def upload_dataset(request: UploadRequest):
    """Upload dataset to HuggingFace Hub"""
    return handle_upload_dataset(request)


@app.post("/dataset-info")
def get_dataset_info(request: DatasetInfoRequest):
    """Get information about a saved dataset"""
    return handle_get_dataset_info(request)


@app.post("/delete-dataset")
def delete_dataset(request: DatasetInfoRequest):
    """Remove a recorded dataset directory from local disk."""
    return handle_delete_dataset(request)


@app.post("/dataset-preview-info")
def get_dataset_preview_info(request: DatasetInfoRequest):
    """Return available local preview videos for a dataset."""
    index = _dataset_video_index(request.dataset_repo_id)
    if index is None:
        return {
            "success": False,
            "message": f"No local preview videos found for {request.dataset_repo_id}",
        }

    all_counts = [len(files) for files in index.values() if files]
    max_episodes = max(all_counts) if all_counts else 0
    
    codebase_version = "v3.0"
    try:
        from lelab.record import _load_local_dataset_info
        info = _load_local_dataset_info(request.dataset_repo_id)
        if info and "codebase_version" in info:
            codebase_version = info["codebase_version"]
    except Exception:
        pass

    return {
        "success": True,
        "dataset_repo_id": request.dataset_repo_id,
        "codebase_version": codebase_version,
        "camera_names": list(index.keys()),
        "available_episode_indices": list(range(max_episodes)),
        "episodes_per_camera": {name: len(files) for name, files in index.items()},
    }


@app.post("/validate-dataset")
def validate_dataset(request: DatasetInfoRequest):
    """Deep-check a local dataset for corruption and video/data frame mismatches."""
    return _validate_local_dataset(request.dataset_repo_id)


@app.get("/dataset-video")
def get_dataset_video(dataset_repo_id: str, camera_name: str, episode_index: int):
    """Serve a specific local dataset video for browser preview."""
    index = _dataset_video_index(dataset_repo_id)
    if index is None:
        raise HTTPException(status_code=404, detail="Dataset videos not found")
    if camera_name not in index:
        raise HTTPException(status_code=404, detail="Camera video not found")
    if episode_index < 0 or episode_index >= len(index[camera_name]):
        raise HTTPException(status_code=404, detail="Episode video not found")
    return FileResponse(index[camera_name][episode_index], media_type="video/mp4")


# ============================================================================
# VISUALIZER ENDPOINTS
# ============================================================================

import subprocess
import os

viz_process = None

@app.post("/start-visualizer")
def start_visualizer(request: StartVisualizerRequest):
    """Start the lerobot_dataset_viz.py server for the requested dataset."""
    global viz_process
    
    # If it's already running, kill it first
    if viz_process is not None:
        try:
            viz_process.terminate()
            viz_process.wait(timeout=2)
        except Exception:
            try:
                viz_process.kill()
            except Exception:
                pass
        viz_process = None

    # Force kill any lingering orphaned visualization processes to free up ports 9090 and 9876
    import subprocess
    subprocess.run(["pkill", "-f", "lerobot.scripts.lerobot_dataset_viz"], capture_output=True)

    # Construct the command
    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "venv", "bin", "python3")
    cmd = [
        venv_python,
        "-m", "lerobot.scripts.lerobot_dataset_viz",
        "--repo-id", request.dataset_repo_id,
        "--mode", "distant",
        "--web-port", "9090"
    ]
    if request.episode_index is not None:
        cmd.extend(["--episode-index", str(request.episode_index)])
    
    # Run the background process
    # We do not wait for it to finish because it's a persistent server
    viz_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True # detach
    )
    
    # Give it a tiny bit of time to bind to the port so the frontend doesn't immediately 404
    import time
    time.sleep(2.0)
    
    # Rerun web viewer at 9090 needs to be told to connect to the gRPC endpoint at 9876
    # Note: We must use %2B for the '+' sign so the browser doesn't decode it into a space
    return {
        "success": True,
        "url": "http://localhost:9090/?url=rerun%2Bhttp://127.0.0.1:9876/proxy&renderer=webgl",
    }

@app.post("/stop-visualizer")
def stop_visualizer():
    """Stop the visualizer process."""
    global viz_process
    if viz_process is not None:
        try:
            viz_process.terminate()
        except Exception:
            pass
        viz_process = None
    return {"success": True}

# ============================================================================
# JOB ENDPOINTS
# ============================================================================


@app.post("/jobs/training", status_code=201)
async def create_training_job(req: Request):
    raw = await req.json()
    body = StartTrainingBody.from_legacy(raw)
    try:
        record = job_registry.start(body.config, body.target)
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job already running: {exc}") from exc
    except ValueError as exc:
        # e.g. "flavor is required when runner is hf_cloud"
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record


@app.get("/jobs")
def list_jobs(limit: int = 10):
    return {"jobs": job_registry.list(limit=limit)}


@app.get("/jobs/hub")
def list_hub_jobs():
    """List the user's HF Cloud compute Jobs and their uploaded LeRobot model
    repos on huggingface.co.

    Returns 200 with empty lists when no token is configured so the frontend
    can render an unauthenticated empty state without surfacing an error.

    Declared before `/jobs/{job_id}` so FastAPI's first-match routing doesn't
    treat "hub" as a job id.
    """
    info = cached_whoami()
    if info is None:
        return {"authenticated": False, "jobs": [], "models": []}
    api = shared_hf_api()

    authors: list[str] = []
    if info.get("name"):
        authors.append(info["name"])
    for o in info.get("orgs", []) or []:
        if isinstance(o, dict) and o.get("name"):
            authors.append(o["name"])

    try:
        jobs = api.list_jobs()
    except Exception as exc:
        logger.warning("list_jobs failed: %s", exc)
        jobs = []

    seen_models: set[str] = set()
    models: list[dict] = []
    for author in authors:
        try:
            for m in api.list_models(author=author, filter="LeRobot", limit=200):
                if m.id in seen_models:
                    continue
                seen_models.add(m.id)
                models.append(
                    {
                        "repo_id": m.id,
                        "last_modified": m.last_modified.isoformat() if m.last_modified else None,
                        "private": bool(getattr(m, "private", False)),
                    }
                )
        except Exception as exc:
            logger.warning("list_models(%s) failed: %s", author, exc)
    models.sort(key=lambda m: m["last_modified"] or "", reverse=True)

    return {
        "authenticated": True,
        "jobs": [
            {
                "id": ji.id,
                "created_at": ji.created_at.isoformat() if ji.created_at else None,
                "docker_image": ji.docker_image,
                "space_id": ji.space_id,
                "flavor": ji.flavor,
                "status": ({"stage": ji.status.stage, "message": ji.status.message} if ji.status else None),
                "owner": ji.owner.name if ji.owner else None,
                "url": ji.url,
            }
            for ji in jobs
        ],
        "models": models,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return job_registry.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc


@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    try:
        logs = job_registry.drain_logs(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    return {"logs": logs}


@app.get("/jobs/{job_id}/log-file")
def get_job_log_file(job_id: str):
    """Return the entire on-disk log file for a job. Drains the live queue too
    so the next /logs poll returns only lines that arrived after this call."""
    try:
        logs = job_registry.read_persisted_logs(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    # Best-effort drain so the frontend doesn't double-display.
    with contextlib.suppress(JobNotFoundError):
        job_registry.drain_logs(job_id)
    return {"logs": logs}


@app.get("/jobs/{job_id}/metrics-history")
def get_job_metrics_history(job_id: str):
    """Return the per-step loss/lr/grad-norm series reconstructed from the
    job's log.jsonl. Used to seed the monitoring charts so curves persist
    across page reloads, navigation, and lelab restarts."""
    try:
        points = job_registry.read_metrics_history(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    return {"points": points}


@app.get("/jobs/{job_id}/checkpoints")
def get_job_checkpoints(job_id: str):
    """List the checkpoints saved for this job, ascending by step."""
    try:
        return {"checkpoints": job_registry.list_checkpoints(job_id)}
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc


@app.get("/jobs/{job_id}/checkpoints/{step}/policy-config")
def get_checkpoint_policy_config(job_id: str, step: int):
    """Return the UX-relevant slice of a checkpoint's pretrained_model config:
    policy_type, image_features (per-camera height/width), and requires_task."""
    try:
        return job_registry.get_policy_config_summary(job_id, step)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/jobs/{job_id}/stop")
def stop_job(job_id: str):
    try:
        return job_registry.stop(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except JobNotRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is not running") from exc


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        job_registry.delete(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except JobNotRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is running; stop it first") from exc


@app.get("/jobs/runners/hardware")
def get_runners_hardware():
    """Return HF Jobs flavor catalog + auth state for the TargetCard.

    Both the flavors list and the whoami result are cached in-process to
    keep this endpoint cheap (it can be re-fetched whenever auth state
    changes). The whoami cache is invalidated on login.
    """
    info = cached_whoami()
    if info is None or not info.get("name"):
        return {"authenticated": False, "username": None, "flavors": []}
    username: str = info["name"]
    api = shared_hf_api()

    now = time.time()
    if _flavors_cache["data"] is None or now - _flavors_cache["fetched_at"] > _FLAVOR_CACHE_TTL_SECONDS:
        try:
            hw_list = api.list_jobs_hardware()
        except Exception as exc:
            logger.warning("list_jobs_hardware failed: %s", exc)
            return {"authenticated": True, "username": username, "flavors": []}
        _flavors_cache["data"] = [
            {
                "name": h.name,
                "pretty_name": h.pretty_name,
                "cpu": h.cpu,
                "ram": h.ram,
                "accelerator": h.accelerator,
                "unit_cost_usd": h.unit_cost_usd,
                "unit_label": h.unit_label,
            }
            for h in hw_list
        ]
        _flavors_cache["fetched_at"] = now

    return {
        "authenticated": True,
        "username": username,
        "flavors": _flavors_cache["data"],
    }


# ============================================================================
# SYSTEM ENDPOINTS
# ============================================================================


@app.get("/system/training-extra")
def get_training_extra():
    """Return whether the LeRobot training extra (accelerate) is importable."""
    return handle_get_training_extra()


@app.post("/system/training-extra/install")
def install_training_extra():
    """Spawn `pip install accelerate` as a background subprocess. No-op if already running."""
    return handle_install_training_extra()


@app.get("/system/training-extra/install-status")
def install_training_extra_status():
    """Return current install state plus any pending log lines (drained on read)."""
    return handle_install_training_extra_status()


@app.get("/system/wandb-extra")
def get_wandb_extra():
    """Return whether the `wandb` package is importable in this lelab process."""
    return handle_get_wandb_extra()


@app.post("/system/wandb-extra/install")
def install_wandb_extra():
    """Spawn `pip install wandb` as a background subprocess. No-op if already running."""
    return handle_install_wandb_extra()


@app.get("/system/wandb-extra/install-status")
def install_wandb_extra_status():
    """Return current wandb install state plus any pending log lines (drained on read)."""
    return handle_install_wandb_extra_status()


# Replay is rendered by the embedded lerobot/visualize_dataset Space; no backend routes needed.


# ============================================================================
# Calibration endpoints
@app.post("/start-calibration")
def start_calibration(request: CalibrationRequest):
    """Start calibration process"""
    return calibration_manager.start_calibration(request)


@app.post("/stop-calibration")
def stop_calibration():
    """Stop calibration process"""
    return calibration_manager.stop_calibration_process()


@app.get("/calibration-status")
def calibration_status():
    """Get current calibration status"""
    from dataclasses import asdict

    status = calibration_manager.get_status()
    return asdict(status)


@app.post("/complete-calibration-step")
def complete_calibration_step():
    """Complete the current calibration step"""
    return calibration_manager.complete_step()


@app.get("/calibration-configs/{device_type}")
def get_calibration_configs(device_type: str):
    """Get all calibration config files for a specific device type"""
    try:
        if device_type == "robot":
            config_path = FOLLOWER_CONFIG_PATH
        elif device_type == "teleop":
            config_path = LEADER_CONFIG_PATH
        else:
            return {"success": False, "message": "Invalid device type"}

        # Get all JSON files in the config directory
        configs = []
        if os.path.exists(config_path):
            for file in os.listdir(config_path):
                if file.endswith(".json"):
                    config_name = os.path.splitext(file)[0]
                    file_path = os.path.join(config_path, file)
                    file_size = os.path.getsize(file_path)
                    modified_time = os.path.getmtime(file_path)

                    configs.append(
                        {
                            "name": config_name,
                            "filename": file,
                            "size": file_size,
                            "modified": modified_time,
                        }
                    )

        return {"success": True, "configs": configs, "device_type": device_type}

    except Exception as e:
        logger.error(f"Error getting calibration configs: {e}")
        return {"success": False, "message": str(e)}


@app.delete("/calibration-configs/{device_type}/{config_name}")
def delete_calibration_config(device_type: str, config_name: str):
    """Delete a calibration config file"""
    try:
        if device_type == "robot":
            config_path = FOLLOWER_CONFIG_PATH
        elif device_type == "teleop":
            config_path = LEADER_CONFIG_PATH
        else:
            return {"success": False, "message": "Invalid device type"}

        # Construct the file path
        filename = f"{config_name}.json"
        file_path = os.path.join(config_path, filename)

        # Check if file exists
        if not os.path.exists(file_path):
            return {"success": False, "message": "Configuration file not found"}

        # Delete the file
        os.remove(file_path)
        logger.info(f"Deleted calibration config: {file_path}")

        return {
            "success": True,
            "message": f"Configuration '{config_name}' deleted successfully",
        }

    except Exception as e:
        logger.error(f"Error deleting calibration config: {e}")
        return {"success": False, "message": str(e)}


# ============================================================================
# ============================================================================
# OPENARM CAN-FD CALIBRATION (Enactic OpenArm via PEAK CAN-FD board)
# ============================================================================

class OpenArmCanStatusBody(BaseModel):
    channel: str = "PCAN_USBBUS1"   # PEAK CAN channel
    bitrate: int = 1000000
    bitrate_fd: int = 8000000


class OpenArmCanZeroBody(BaseModel):
    channel: str = "PCAN_USBBUS1"
    joint_names: list[str]
    positions: dict[str, float]  # current raw positions to treat as zero


class OpenArmCanSaveBody(BaseModel):
    config_name: str
    joint_names: list[str]
    zero_positions: dict[str, float]
    range_min: dict[str, float]
    range_max: dict[str, float]
    channel: str = "PCAN_USBBUS1"


def _read_openarm_positions(channel: str, bitrate: int, bitrate_fd: int) -> dict[str, float]:
    """Read current joint positions from OpenArm via CAN-FD using python-can."""
    try:
        import can
        # On Linux, PEAK adapters appear as can0/can1 via SocketCAN by default
        interface_type = "socketcan" if channel.startswith("can") or channel.startswith("vcan") else "pcan"
        bus = can.interface.Bus(
            channel=channel,
            interface=interface_type,
            bitrate=bitrate,
            fd=True,
            data_bitrate=bitrate_fd,
        )
        try:
            # Send a broadcast position request (0x7FF = broadcast)
            msg = can.Message(arbitration_id=0x7FF, data=[0x01], is_extended_id=False, is_fd=True)
            bus.send(msg)
            positions: dict[str, float] = {}
            deadline = time.time() + 0.5  # 500ms timeout
            while time.time() < deadline:
                recv = bus.recv(timeout=0.05)
                if recv is None:
                    break
                # OpenArm CAN protocol: ID encodes joint, data encodes float32 position
                joint_id = recv.arbitration_id & 0xFF
                if len(recv.data) >= 4:
                    import struct
                    pos = struct.unpack("<f", recv.data[:4])[0]
                    positions[f"joint_{joint_id}"] = round(pos, 4)
            return positions
        finally:
            bus.shutdown()
    except ImportError:
        # python-can not installed — return a simulated response for UI testing
        logger.warning("python-can not installed; returning simulated positions")
        return {f"joint_{i}": 0.0 for i in range(1, 8)}
    except OSError as e:
        if e.errno == 22 and interface_type == "socketcan":
            raise RuntimeError(
                f"SocketCAN Error 22: The interface {channel} is not configured for CAN-FD. "
                f"Please run this in your terminal to fix it:\n"
                f"sudo ip link set {channel} down && sudo ip link set {channel} up type can bitrate {bitrate} dbitrate {bitrate_fd} fd on"
            )
        raise
    except Exception as e:
        logger.error(f"CAN read error: {e}")
        raise


@app.post("/openarm-can/status")
def openarm_can_status(body: OpenArmCanStatusBody):
    """Read current joint positions from OpenArm via CAN-FD."""
    try:
        positions = _read_openarm_positions(body.channel, body.bitrate, body.bitrate_fd)
        return {"status": "success", "positions": positions, "joint_count": len(positions)}
    except Exception as e:
        return {"status": "error", "message": str(e), "positions": {}}


@app.post("/openarm-can/save-calibration")
def openarm_can_save_calibration(body: OpenArmCanSaveBody):
    """Save OpenArm CAN calibration result as a JSON config file.

    The saved file is placed in leLab's follower config directory so it shows
    up in the existing calibration config browser.
    """
    import json as _json

    calibration_data = {
        "robot_type": "openarm_can",
        "channel": body.channel,
        "joint_names": body.joint_names,
        "zero_positions": body.zero_positions,
        "range_min": body.range_min,
        "range_max": body.range_max,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    config_path = Path(FOLLOWER_CONFIG_PATH)
    config_path.mkdir(parents=True, exist_ok=True)
    out_file = config_path / f"{body.config_name}.json"
    with open(out_file, "w") as f:
        _json.dump(calibration_data, f, indent=2)
    logger.info(f"OpenArm CAN calibration saved to {out_file}")

    return {"status": "success", "path": str(out_file), "config_name": body.config_name}


@app.get("/openarm-can/calibration/{config_name}")
def openarm_can_get_calibration(config_name: str):
    """Load a previously saved OpenArm CAN calibration."""
    import json as _json

    file_path = Path(FOLLOWER_CONFIG_PATH) / f"{config_name}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Calibration '{config_name}' not found")
    with open(file_path) as f:
        data = _json.load(f)
    if data.get("robot_type") != "openarm_can":
        raise HTTPException(status_code=400, detail="Config is not an OpenArm CAN calibration file")
    return {"status": "success", "calibration": data}


# ============================================================================
# PORT DETECTION ENDPOINTS
# ============================================================================


@app.get("/available-ports")
def get_available_ports():
    """Get all available serial ports"""
    try:
        ports = find_available_ports()
        ports.append("PCAN_USBBUS1")
        ports.append("PCAN_USBBUS2")
        try:
            import can

            ports.append("can0 (SocketCAN)")
            ports.append("can1 (SocketCAN)")
        except ImportError:
            pass

        ports.append("openarm_ros (ROS 2 Bridge)")

        return {"status": "success", "ports": ports}
    except Exception as e:
        logger.error(f"Error getting available ports: {e}")
        return {"status": "error", "message": str(e)}


# Runs in a fresh Python — see _avfoundation_cameras_in_cv2_order for why.
# Mirrors OpenCV's macOS enumeration: video + muxed devices sorted by
# uniqueID (cap_avfoundation_mac.mm), so the returned index matches what
# cv2.VideoCapture will open.
_AVF_ENUM_SCRIPT = """
import json, objc
from Foundation import NSBundle
bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/AVFoundation.framework")
bundle.load()
types = []
for name in (
    "AVCaptureDeviceTypeBuiltInWideAngleCamera",
    "AVCaptureDeviceTypeExternalUnknown",   # macOS < 14
    "AVCaptureDeviceTypeExternal",          # macOS >= 14
    "AVCaptureDeviceTypeContinuityCamera",  # macOS >= 14
    "AVCaptureDeviceTypeDeskViewCamera",    # macOS >= 13
):
    loaded = {}
    try:
        objc.loadBundleVariables(bundle, loaded, [(name, b"@")])
    except objc.error:
        continue
    if loaded.get(name) is not None:
        types.append(loaded[name])
cls = objc.lookUpClass("AVCaptureDeviceDiscoverySession")
devs = []
for mt in ("vide", "muxx"):
    devs.extend(cls.discoverySessionWithDeviceTypes_mediaType_position_(types, mt, 0).devices() or [])
devs.sort(key=lambda d: d.uniqueID())
print(json.dumps([
    {"index": i, "name": str(d.localizedName()), "unique_id": str(d.uniqueID())}
    for i, d in enumerate(devs)
]))
"""


def _avfoundation_cameras_in_cv2_order() -> list[dict[str, Any]]:
    """Enumerate macOS cameras in a fresh Python subprocess.

    AVFoundation's in-process device cache doesn't refresh on USB
    hotplug. Both the deprecated ``+devicesWithMediaType:`` and a
    long-lived ``AVCaptureDeviceDiscoverySession`` go stale, because
    device-connection notifications are delivered via
    ``NSNotificationCenter`` on a thread that needs an active
    ``NSRunLoop`` — uvicorn workers don't run one. A fresh subprocess
    re-initializes AVFoundation, which reads IOKit's live device state
    at startup.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _AVF_ENUM_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("AVFoundation enumeration subprocess failed: %s", e)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning("AVFoundation enumeration returned invalid JSON: %s", e)
        return []


# ---------------------------------------------------------------------------
# ROS Camera Mappings + Bridge Control
# ---------------------------------------------------------------------------

import subprocess as _subprocess

_ROS_CAMERA_MAPPINGS_PATH = Path.home() / ".config" / "lelab" / "ros_camera_mappings.json"
_bridge_proc: _subprocess.Popen | None = None
_bridge_proc_lock = threading.Lock()
# Bridge's own stdout/stderr. A crash AFTER the startup check used to be
# invisible: output went to a subprocess.PIPE nobody drained, so once the
# bridge exited there was no way to see why -- exactly the situation of a
# bridge that died mid-session with a stale PID still shown in the UI. A file
# survives the process exiting and can be read at any time, not just in the
# few seconds after Popen.
_BRIDGE_LOG_PATH = Path("/tmp/lelab_camera_bridge.log")


class RosCameraMappingEntry(BaseModel):
    name: str  # "main_camera", "right_camera", or "left_camera"
    device_index: int | str
    width: int = 640
    height: int = 480
    fps: int = 30


def _load_ros_camera_mappings() -> list[dict]:
    if not _ROS_CAMERA_MAPPINGS_PATH.is_file():
        return []
    try:
        return json.loads(_ROS_CAMERA_MAPPINGS_PATH.read_text())
    except Exception:
        return []


def _save_ros_camera_mappings(mappings: list[dict]) -> None:
    _ROS_CAMERA_MAPPINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ROS_CAMERA_MAPPINGS_PATH.write_text(json.dumps(mappings, indent=2))


def _stable_device_path(device_index) -> str | None:
    """Best /dev/v4l/by-path symlink for a camera, or None.

    /dev/videoN is assigned in enumeration order and moves when cables move or
    another camera is plugged in first (observed here: a USB camera went video4
    -> video10). by-path is stable per USB port, so mappings persist that and
    both readers re-resolve it at open time.
    """
    import glob as _glob

    s = str(device_index)
    target = s if s.startswith("/dev/video") else f"/dev/video{s}" if s.isdigit() else None
    if target is None:
        return s if s.startswith("/dev/v4l/by-path/") else None
    real = os.path.realpath(target)
    for link in _glob.glob("/dev/v4l/by-path/*"):
        if os.path.realpath(link) == real:
            return link
    return None


def _annotate_mappings(mappings: list[dict]) -> list[dict]:
    """Add live device info so the UI can show a real status without a bridge.

    In direct-capture mode nothing is streaming until a recording starts, so
    "does this device still exist" is the only honest readiness signal we can
    give beforehand.
    """
    out = []
    for m in mappings:
        entry = dict(m)
        dev = str(m.get("device_index"))
        path = dev if dev.startswith("/dev/") else f"/dev/video{dev}" if dev.isdigit() else dev
        entry["resolved_path"] = os.path.realpath(path) if path.startswith("/dev/") else path
        entry["device_present"] = os.path.exists(path)
        out.append(entry)
    return out


@app.get("/ros-camera-mappings")
def get_ros_camera_mappings():
    """Return the persisted camera→USB device mappings, with live device info."""
    return {"mappings": _annotate_mappings(_load_ros_camera_mappings())}


@app.post("/ros-camera-mappings")
def add_ros_camera_mapping(entry: RosCameraMappingEntry):
    """Add or update a camera mapping and persist it."""
    VALID_NAMES = {"main_camera", "right_camera", "left_camera"}
    if entry.name not in VALID_NAMES:
        raise HTTPException(status_code=400, detail=f"name must be one of {VALID_NAMES}")
    record = entry.model_dump()
    # Persist the port-stable path when we can find one; a bare index recorded
    # today points at a different camera after the next replug.
    stable = _stable_device_path(entry.device_index)
    if stable:
        record["device_index"] = stable
        logger.info("camera %s: pinned %s -> %s", entry.name, entry.device_index, stable)
    mappings = _load_ros_camera_mappings()
    # Replace if name already exists
    mappings = [m for m in mappings if m["name"] != entry.name]
    mappings.append(record)
    _save_ros_camera_mappings(mappings)
    return {"success": True, "mappings": _annotate_mappings(mappings)}


@app.delete("/ros-camera-mappings/{name}")
def delete_ros_camera_mapping(name: str):
    """Remove one camera mapping by name."""
    mappings = _load_ros_camera_mappings()
    mappings = [m for m in mappings if m["name"] != name]
    _save_ros_camera_mappings(mappings)
    return {"success": True, "mappings": _annotate_mappings(mappings)}


@app.get("/ros-camera-status")
def get_ros_camera_status():
    """Return per-camera live FPS read from the bridge heartbeat file."""
    status_path = Path("/tmp/lelab_camera_status.json")
    if not status_path.is_file():
        return {"status": {}}
    try:
        return {"status": json.loads(status_path.read_text())}
    except Exception:
        return {"status": {}}


def _tail_bridge_log(n: int = 20) -> list[str]:
    try:
        lines = _BRIDGE_LOG_PATH.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return lines[-n:]


@app.get("/ros-camera-bridge/log")
def get_ros_camera_bridge_log(lines: int = 40):
    """Tail the bridge's own log, including any crash that happened after it
    was already confirmed running (a startup failure is reported inline by
    /ros-camera-bridge/start; this is for a bridge that died later, mid-session,
    which the start response can never see)."""
    return {"log": _tail_bridge_log(lines), "path": str(_BRIDGE_LOG_PATH)}


@app.post("/ros-camera-bridge/start")
def start_ros_camera_bridge():
    """Launch openarm_camera_bridge_node.py, and report whether it SURVIVED.

    Refuses outright unless ROS camera mode is enabled on the I/O Configuration
    page. That page is meant to be the one place deciding record vs. ROS camera
    capture; this endpoint used to start the bridge unconditionally and then
    persist ros_camera=True as a side effect, which is how the bridge ended up
    running with nobody having deliberately turned ROS camera mode on. Direct
    capture needs no "start" step at all — recording just opens the devices —
    so there is nothing for this button to do while that mode is off.

    The previous version also returned success as soon as Popen returned. The
    bridge exits immediately on a bad setup — no camera mappings, a device
    already held by something else — so the UI reported "Started (PID n)" for a
    process that was already dead, and the status dot then said "not running"
    with no reason given. It now waits, confirms the process is alive, and hands
    back the child's own output when it is not.
    """
    global _bridge_proc
    import time

    with _bridge_proc_lock:
        if not _read_io_config().get("ros_camera", False):
            raise HTTPException(
                status_code=400,
                detail="ROS camera mode is disabled on the I/O Configuration page. "
                       "Enable it there first — direct capture needs no bridge and "
                       "is already active.",
            )

        # Kill any existing bridge processes to avoid camera locks and JSON
        # conflicts. Targeted signals, not `pkill -f` — that pattern also matches
        # unrelated processes that merely mention the script name.
        from lelab.utils.procs import stop_camera_bridge
        stop_camera_bridge()
        time.sleep(0.2)

        bridge_script = Path(__file__).parent.parent.parent / "src" / "qnbot_teleoperator" / "scripts" / "openarm_camera_bridge_node.py"
        if not bridge_script.is_file():
            raise HTTPException(status_code=404, detail=f"Bridge script not found: {bridge_script}")

        if not _load_ros_camera_mappings():
            raise HTTPException(
                status_code=400,
                detail="Attach at least one camera before starting the bridge — it exits immediately with no mappings.",
            )

        log_fh = open(_BRIDGE_LOG_PATH, "w")
        log_fh.write(f"=== bridge started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log_fh.flush()
        _bridge_proc = _subprocess.Popen(
            ["/usr/bin/python3", str(bridge_script)],
            stdout=log_fh,
            stderr=_subprocess.STDOUT,
        )
        log_fh.close()  # the child keeps its own fd; this process no longer needs one

        # Give it long enough to fail on startup (device open, mappings parse).
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            if _bridge_proc.poll() is not None:
                code = _bridge_proc.returncode
                _bridge_proc = None
                tail = _tail_bridge_log()
                logger.error("Camera bridge exited immediately (rc=%s): %s", code, tail)
                raise HTTPException(
                    status_code=500,
                    detail=f"Bridge exited immediately (rc={code}): " + " | ".join(tail),
                )
            time.sleep(0.15)

        # No write here: io_config.ros_camera was already True (checked above),
        # so it already reflects what the operator chose on the I/O page.
        logger.info("ROS camera bridge started (PID %s)", _bridge_proc.pid)
        return {
            "success": True,
            "pid": _bridge_proc.pid,
            "message": f"Bridge running (PID {_bridge_proc.pid}). Recording will use ROS camera topics.",
        }


@app.post("/ros-camera-bridge/stop")
def stop_ros_camera_bridge():
    """Stop the camera bridge — whoever started it — and verify it is gone.

    Escalates TERM -> KILL against the exact PIDs found in /proc and confirms
    they are gone. The old version fired one `pkill -f` and reported success
    unconditionally, so a process that ignored the signal left the UI claiming
    the bridge was stopped while it still held the cameras, blocking direct
    capture.
    """
    global _bridge_proc

    with _bridge_proc_lock:
        if not _camera_bridge_pids():
            _bridge_proc = None
            _write_io_config(ros_camera=False)
            # Acknowledge whatever the log says here, in this response, and
            # then clear it -- otherwise status would keep reporting
            # died_unexpectedly for a bridge the operator has now explicitly
            # dealt with, long after this click.
            crash_tail = _tail_bridge_log(10)
            _BRIDGE_LOG_PATH.unlink(missing_ok=True)
            return {
                "success": True,
                "running": False,
                "message": "Bridge was not running" + (
                    " (it exited on its own — see log_tail)" if crash_tail else ""
                ),
                "log_tail": crash_tail,
            }

        # TERM -> wait -> KILL -> wait, against the PIDs themselves.
        from lelab.utils.procs import stop_camera_bridge
        remaining = stop_camera_bridge()
        _bridge_proc = None
        if remaining:
            # Do NOT persist ros_camera=False here: the cameras are still held,
            # so claiming direct capture is available would be a lie.
            logger.error("Camera bridge still running after SIGKILL: %s", remaining)
            raise HTTPException(
                status_code=500,
                detail=f"Could not stop the bridge (PIDs still alive: {remaining}). "
                       "It may be owned by another user — stop it from the shell that started it.",
            )

        _write_io_config(ros_camera=False)
        _BRIDGE_LOG_PATH.unlink(missing_ok=True)  # clean stop, acknowledged -- not a crash to flag later
        logger.info("ROS camera bridge stopped; direct capture is now available")
        return {
            "success": True,
            "running": False,
            "message": "Bridge stopped. Recording will read the cameras directly.",
        }


@app.get("/ros-camera-bridge/status")
def get_ros_camera_bridge_status():
    """Return running state and PID of the camera bridge, whoever started it.

    `died_unexpectedly` flags exactly the situation that made a dead bridge
    look alive in the UI: this endpoint's log file exists (something started
    it) and pgrep finds nothing (it is not running now), but nobody has called
    /ros-camera-bridge/stop to acknowledge that -- i.e. it exited on its own
    mid-session rather than being stopped. `log_tail` is included so the UI can
    show the crash reason without a second round trip.
    """
    with _bridge_proc_lock:
        pids = _camera_bridge_pids()
        running = bool(pids)
        result = {"running": running, "pid": pids[0] if pids else None}
        if not running and _BRIDGE_LOG_PATH.is_file():
            result["died_unexpectedly"] = True
            result["log_tail"] = _tail_bridge_log(10)
        return result


@app.get("/available-cameras")
def get_available_cameras():
    """List cameras with the same index ordering cv2 will use to record.

    On macOS we mirror OpenCV's AVFoundation enumeration via PyObjC so each
    index comes with the AVFoundation ``localizedName``. The browser's
    ``MediaDeviceInfo.label`` is that same ``localizedName``, so the
    frontend can match by name to find the matching browser deviceId for the
    live preview while we record by cv2 index.
    """
    try:
        import platform

        system = platform.system()

        if system == "Darwin":
            cameras = _avfoundation_cameras_in_cv2_order()
            for cam in cameras:
                cam["available"] = True
            return {"status": "success", "cameras": cameras}

        # Linux: enumerate /dev/video* nodes via V4L2 to get real device
        # names and skip metadata-only nodes (which OpenCV can't open for
        # video). Each physical USB camera exposes multiple /dev/videoN nodes
        # (e.g. video0+video1 for MJPEG+metadata on the same device); we keep
        # only the first capture-capable node per physical device.
        import cv2

        backend = cv2.CAP_V4L2 if system == "Linux" else cv2.CAP_ANY

        if system == "Linux":
            import glob
            import subprocess as _sp

            seen_cards: set[str] = set()
            cameras = []

            video_nodes = sorted(
                glob.glob("/dev/video*"),
                key=lambda p: int(p.replace("/dev/video", "")) if p.replace("/dev/video", "").isdigit() else 999,
            )

            for node in video_nodes:
                idx_str = node.replace("/dev/video", "")
                if not idx_str.isdigit():
                    continue
                i = int(idx_str)

                # Use v4l2-ctl to get the card name (physical device identity)
                # and capability flags without opening via OpenCV first.
                card_name = f"Camera {i}"
                bus_info = ""
                is_capture_capable = False
                try:
                    info = _sp.run(
                        ["v4l2-ctl", "--device", node, "--info"],
                        capture_output=True, text=True, timeout=2,
                    )
                    
                    device_caps_section = False
                    for line in info.stdout.splitlines():
                        if "Card type" in line or "card" in line.lower():
                            card_name = line.split(":", 1)[-1].strip()
                        elif "Bus info" in line or "bus_info" in line.lower():
                            bus_info = line.split(":", 1)[-1].strip()
                        elif "Device Caps" in line:
                            device_caps_section = True
                        elif device_caps_section and "Video Capture" in line:
                            is_capture_capable = True
                except Exception:
                    pass

                if not is_capture_capable:
                    continue

                # Verify OpenCV can actually open it (in a subprocess to prevent uvicorn hangs)
                test_script = f"import cv2; cap=cv2.VideoCapture({i}, {backend}); exit(0 if cap.isOpened() else 1)"
                try:
                    res = _sp.run([sys.executable, "-c", test_script], timeout=2)
                    if res.returncode != 0:
                        continue
                except Exception:
                    continue

                # Make name unique if another device already has this card name
                display_name = card_name
                existing_names = {c["name"] for c in cameras}
                if display_name in existing_names:
                    display_name = f"{card_name} ({i})"

                # find by-path if available
                by_path = None
                try:
                    by_path_dir = Path("/dev/v4l/by-path")
                    if by_path_dir.exists():
                        for entry in by_path_dir.iterdir():
                            if entry.is_symlink() and entry.resolve() == Path(node).resolve():
                                by_path = str(entry)
                                break
                except Exception:
                    pass

                cameras.append(
                    {
                        "index": by_path if by_path else i,
                        "name": display_name,
                        "device_path": bus_info,
                        "available": True,
                        "symlink_names": [],
                    }
                )

            # Scan /dev/ for named symlinks that resolve to a videoN node
            # (e.g. /dev/left_camera -> video13). Attach them to the matching
            # camera entry so the frontend can display the friendly alias.
            try:
                dev_dir = Path("/dev")
                index_map = {cam["index"]: cam for cam in cameras}
                for entry in dev_dir.iterdir():
                    if not entry.is_symlink():
                        continue
                    target = entry.resolve()
                    target_name = target.name  # e.g. "video13"
                    if not target_name.startswith("video"):
                        continue
                    num_str = target_name[len("video"):]
                    if not num_str.isdigit():
                        continue
                    cam_index = int(num_str)
                    
                    # Match by finding the camera that resolves to this videoN node
                    for cam in cameras:
                        c_idx = cam["index"]
                        c_node = None
                        if isinstance(c_idx, int):
                            c_node = f"/dev/video{c_idx}"
                        elif isinstance(c_idx, str):
                            try:
                                c_node = str(Path(c_idx).resolve())
                            except Exception:
                                pass
                        
                        if c_node == str(target):
                            cam.setdefault("symlink_names", []).append(entry.name)
                # Sort symlink aliases for deterministic output
                for cam in cameras:
                    cam["symlink_names"] = sorted(cam.get("symlink_names", []))
            except Exception as _e:
                logger.warning("Failed to scan /dev/ for camera symlinks: %s", _e)

            return {"status": "success", "cameras": cameras}

        cameras = []
        for i in range(10):
            cap = cv2.VideoCapture(i, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cameras.append(
                {
                    "index": i,
                    "name": f"Camera {i}",
                    "available": True,
                }
            )
            cap.release()
        return {"status": "success", "cameras": cameras}
    except ImportError:
        logger.warning("OpenCV not available for camera detection")
        return {"status": "success", "cameras": []}
    except Exception as e:
        logger.error(f"Error detecting cameras: {e}")
        return {"status": "error", "message": str(e), "cameras": []}


RobotSideLiteral = Literal["leader", "follower"]


class PortDetectionBody(BaseModel):
    robot_type: RobotSideLiteral = "follower"


class PortDisconnectBody(BaseModel):
    ports_before: list[str]


class SaveRobotPortBody(BaseModel):
    robot_type: RobotSideLiteral
    port: str


class SaveRobotConfigBody(BaseModel):
    robot_type: RobotSideLiteral
    config_name: str


@app.post("/start-port-detection")
def start_port_detection(body: PortDetectionBody):
    """Snapshot available ports so the follow-up /detect-port-after-disconnect
    call can diff them."""
    result = find_robot_port(body.robot_type)
    return {"status": "success", "data": result}


@app.post("/detect-port-after-disconnect")
def detect_port_after_disconnect_endpoint(body: PortDisconnectBody):
    """Block up to 15s waiting for one port from `ports_before` to disappear."""
    try:
        detected_port = detect_port_after_disconnect(body.ports_before)
    except OSError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    return {"status": "success", "port": detected_port}


@app.post("/save-robot-port")
def save_robot_port_endpoint(body: SaveRobotPortBody):
    """Save a robot port for future use"""
    save_robot_port(body.robot_type, body.port)
    return {"status": "success", "message": f"Port {body.port} saved for {body.robot_type}"}


@app.get("/robot-port/{robot_type}")
def get_robot_port(robot_type: RobotSideLiteral):
    """Get the saved port for a robot type"""
    saved_port = get_saved_robot_port(robot_type)
    default_port = get_default_robot_port(robot_type)
    return {"status": "success", "saved_port": saved_port, "default_port": default_port}


@app.post("/save-robot-config")
def save_robot_config_endpoint(body: SaveRobotConfigBody):
    """Save a robot configuration for future use"""
    if not config.save_robot_config(body.robot_type, body.config_name):
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    return {"status": "success", "message": f"Configuration saved for {body.robot_type}"}


@app.get("/robot-config/{robot_type}")
def get_robot_config(robot_type: RobotSideLiteral, available_configs: str = ""):
    """Get the saved configuration for a robot type"""
    available_configs_list = [c.strip() for c in available_configs.split(",") if c.strip()]
    saved_config = config.get_saved_robot_config(robot_type)
    default_config = config.get_default_robot_config(robot_type, available_configs_list)
    return {"status": "success", "saved_config": saved_config, "default_config": default_config}


# ============================================================================
# Robot config records (named robots)


def _record_with_clean(record: dict) -> dict:
    """Attach `is_clean` to a record for API responses."""
    return {**record, "is_clean": is_robot_record_clean(record)}


@app.get("/robots")
def get_robots():
    """List all saved robot records."""
    try:
        records = [_record_with_clean(r) for r in list_robot_records()]
        return {"status": "success", "robots": records}
    except Exception as e:
        logger.error(f"Error listing robots: {e}")
        return {"status": "error", "message": str(e), "robots": []}


@app.get("/robots/{name}")
def get_robot(name: str):
    """Get a single robot record by name."""
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    record = get_robot_record(name)
    if record is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Robot not found"})
    return {"status": "success", "robot": _record_with_clean(record)}


@app.post("/robots/{name}")
def upsert_robot(name: str, data: dict, create: bool = False):
    """
    Upsert a robot record.

    - `?create=true` is the "Add Robot" path: returns 409 if a record with that
      name already exists; otherwise creates with empty fields then merges body.
    - Without `?create=true` is the "patch" path (e.g., calibration write-back):
      merges body into existing record. If no record exists, no-ops and returns
      success — see deletion-during-calibration edge case in the spec.
    """
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    try:
        if create:
            if get_robot_record(name) is not None:
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "message": "A robot with this name already exists"},
                )
            save_robot_record(name, data or {}, allow_create=True)
        else:
            save_robot_record(name, data or {}, allow_create=False)
        record = get_robot_record(name)
        if record is None:
            return {"status": "success", "robot": None}
        return {"status": "success", "robot": _record_with_clean(record)}
    except Exception as e:
        logger.error(f"Error upserting robot {name}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.delete("/robots/{name}")
def delete_robot(name: str):
    """Delete a robot record."""
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    if delete_robot_record(name):
        return {"status": "success"}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Robot not found"})


# ---------------------------------------------------------------------------
# Arm Positions Management
# ---------------------------------------------------------------------------

from .utils.config import (
    get_arm_positions,
    save_arm_position,
    delete_arm_position,
    ensure_default_position
)

class ArmPositionRequest(BaseModel):
    name: str
    joint_values: list[float]

@app.get("/robots/{name}/positions")
def list_positions(name: str):
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    ensure_default_position(name)
    positions = get_arm_positions(name)
    return {"status": "success", "positions": positions}

@app.post("/robots/{name}/positions")
def add_position(name: str, request: ArmPositionRequest):
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    ensure_default_position(name)
    try:
        new_pos = save_arm_position(name, request.name, request.joint_values)
        return {"status": "success", "position": new_pos}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.put("/robots/{name}/positions/{pos_id}")
def update_position(name: str, pos_id: str, request: ArmPositionRequest):
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    try:
        updated_pos = save_arm_position(name, request.name, request.joint_values, pos_id=pos_id)
        return {"status": "success", "position": updated_pos}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})

@app.delete("/robots/{name}/positions/{pos_id}")
def remove_position(name: str, pos_id: str):
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    try:
        success = delete_arm_position(name, pos_id)
        if success:
            return {"status": "success"}
        return JSONResponse(status_code=404, content={"status": "error", "message": "Position not found"})
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})

@app.post("/robots/{name}/positions/{pos_id}/move-to")
def move_to_position(name: str, pos_id: str):
    import subprocess
    import json
    
    positions = get_arm_positions(name)
    target_pos = next((p for p in positions if p["id"] == pos_id), None)
    if not target_pos:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Position not found"})
    
    joint_values = target_pos["joint_values"]
    if len(joint_values) < 16:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid joint values length"})
        
    # lock_all must be set here. Without it the bridge receives a
    # set_home_target while the arm is still unlocked, and (before the bridge
    # was hardened) that started an UNLOCK transition whose target is the live
    # exoskeleton pose — the arm briefly tracked the operator instead of
    # homing, with rate limiting bypassed, so a fast operator motion produced a
    # visible lurch. Every other call site already passes lock_all.
    set_home = {
        "action": "set_home_target",
        "left_arm": joint_values[0:7],
        "left_gripper": joint_values[7],
        "right_arm": joint_values[8:15],
        "right_gripper": joint_values[15],
        "lock_all": True,
    }
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    # Sequential, not two racing subprocesses: these were previously launched
    # concurrently with no ordering guarantee between them.
    subprocess.run(["/usr/bin/python3", script_path, json.dumps(set_home)], check=False)
    subprocess.Popen(["/usr/bin/python3", script_path, json.dumps({"action": "home_all"})])

    return {"status": "success"}

@app.post("/robots/{name}/positions/set-target")
def set_live_target(name: str, request: ArmPositionRequest):
    import subprocess
    import json
    
    joint_values = request.joint_values
    if len(joint_values) < 16:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid joint values length"})
        
    set_home = {
        "action": "set_home_target", 
        "left_arm": joint_values[0:7], 
        "left_gripper": joint_values[7], 
        "right_arm": joint_values[8:15], 
        "right_gripper": joint_values[15]
    }
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    subprocess.Popen(["/usr/bin/python3", script_path, json.dumps(set_home)])
    return {"status": "success"}

@app.post("/robots/{name}/positions/capture")
def capture_position(name: str):
    import socket
    import json
    # Try to listen for one packet from the ROS UDP publisher to get current joint positions
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("127.0.0.1", 19092))
        sock.settimeout(1.0)
        
        data, _ = sock.recvfrom(65535)
        payload = json.loads(data.decode('utf-8'))
        obs_payload = payload.get("observation", {})
        jp = obs_payload.get("joint_position", [])
        
        sock.close()
        
        if len(jp) == 16:
            return {"status": "success", "joint_values": jp}
        else:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Did not receive full 16-DOF joint_position array from ROS"})
            
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Failed to capture joint positions: {e}"})

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources when FastAPI shuts down"""
    logger.info("🔄 FastAPI shutting down, cleaning up...")

    # Stop any active recording - handled by recording module cleanup

    if manager:
        manager.stop_broadcast_thread()
    logger.info("✅ Cleanup completed")


from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def catch_all_for_react(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        # If the browser is asking for HTML, serve the React app
        if "text/html" in request.headers.get("accept", "") and FRONTEND_DIST.exists():
            return FileResponse(FRONTEND_DIST / "index.html")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

# Serve the built frontend at /. Must be mounted last so API routes win.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning(
        f"frontend/dist not found at {FRONTEND_DIST}; run `npm run build` in frontend/ or use `lelab --dev`."
    )
