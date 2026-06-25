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
    import subprocess
    import json
    import os
    cmd_str = json.dumps({"action": "toggle_left_home", "value": req.fixed})
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    subprocess.Popen(["/usr/bin/python3", script_path, cmd_str])
    return {"success": True}

@app.post("/toggle-right-arm-home")
def toggle_right_arm_home(req: ToggleArmHomeRequest):
    import subprocess
    import json
    import os
    cmd_str = json.dumps({"action": "toggle_right_home", "value": req.fixed})
    script_path = os.path.join(os.path.dirname(__file__), "publish_ui_command.py")
    subprocess.Popen(["/usr/bin/python3", script_path, cmd_str])
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

    return Response(content=jpeg_bytes, media_type="image/jpeg", headers=headers)


@app.post("/reconnect-cameras")
def reconnect_cameras():
    """Reconnect all cameras to recover from hardware freezes"""
    from lelab.record import active_robot, recording_active
    
    if not recording_active or not active_robot:
        return {"success": False, "message": "Recording not active"}
        
    if hasattr(active_robot, "reconnect_cameras"):
        active_robot.reconnect_cameras()
        return {"success": True, "message": "Cameras reconnected"}
    
    return {"success": False, "message": "Robot does not support camera reconnection"}


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

                # Removed cv2.VideoCapture test to prevent indefinite hangs on bad V4L2 nodes.
                # Since v4l2-ctl verified it is capture capable, we list it as available.
                # Make name unique if another device already has this card name
                display_name = card_name
                existing_names = {c["name"] for c in cameras}
                if display_name in existing_names:
                    display_name = f"{card_name} ({i})"

                seen_cards.add(card_name)
                cameras.append(
                    {
                        "index": i,
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
                    if cam_index in index_map:
                        index_map[cam_index].setdefault("symlink_names", []).append(entry.name)
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


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources when FastAPI shuts down"""
    logger.info("🔄 FastAPI shutting down, cleaning up...")

    # Stop any active recording - handled by recording module cleanup

    if manager:
        manager.stop_broadcast_thread()
    logger.info("✅ Cleanup completed")


# Serve the built frontend at /. Must be mounted last so API routes win.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning(
        f"frontend/dist not found at {FRONTEND_DIST}; run `npm run build` in frontend/ or use `lelab --dev`."
    )
