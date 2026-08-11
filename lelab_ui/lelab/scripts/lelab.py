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

"""
LeLab launcher.

Default mode: starts the FastAPI backend on :8000, which serves the
pre-built frontend at /. Opens the user's browser to the local app.

--dev mode: spawns the Vite dev server (frontend/, port 8080) for HMR
and starts uvicorn with --reload. Opens the browser to :8080.
"""

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
FRONTEND_PATH = PROJECT_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_PATH / "dist"
# Defaults; `main()` overrides both from --host/--port. Binding 0.0.0.0 rather
# than 127.0.0.1 is what makes the UI reachable from other machines on the LAN.
# LELAB_PORT is preferred over the bare PORT, which collides with a very common
# ambient convention; PORT stays supported for backwards compatibility.
BACKEND_HOST = os.environ.get("LELAB_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("LELAB_PORT") or os.environ.get("PORT") or 8000)
FRONTEND_DEV_PORT = 8080


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """True if a TCP connection to host:port succeeds within `timeout`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, timeout: int = 30) -> bool:
    for _ in range(timeout):
        if _port_open(port, host="localhost"):
            return True
        time.sleep(1)
    return False


def _open_browser_when_ready():
    """Background-thread helper: poll the port, open the browser when up."""
    for _ in range(60):
        if not _port_open(BACKEND_PORT, timeout=0.5):
            time.sleep(0.5)
            continue
        logger.info("🌐 Opening browser...")
        webbrowser.open(f"http://localhost:{BACKEND_PORT}/")
        return


def _already_running() -> bool:
    """True if an earlier leLab instance is already answering on BACKEND_PORT,
    so we can reuse it instead of starting a duplicate / reopening a tab."""
    return _port_open(BACKEND_PORT, timeout=0.5)


def _run_prod():
    """Serve built frontend from backend on a single port."""
    if not FRONTEND_DIST.exists():
        logger.error(f"❌ Built frontend not found at {FRONTEND_DIST}")
        logger.error("   Run `npm run build` in frontend/ first, or use `lelab --dev`.")
        sys.exit(1)

    if _already_running():
        logger.info(
            "✅ LeLab is already running on http://localhost:%d — reusing it, not opening a new tab.",
            BACKEND_PORT,
        )
        return

    logger.info("🚀 Starting LeLab on http://localhost:%d ...", BACKEND_PORT)

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    # Run uvicorn in the main thread so its native SIGINT handler works,
    # and bound graceful shutdown so a stuck WebSocket can't hang Ctrl+C.
    uvicorn.run(
        "lelab.server:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
        log_level="info",
        reload=False,
        timeout_graceful_shutdown=2,
    )


def _run_dev():
    """Vite dev server (HMR) + uvicorn --reload."""
    if not FRONTEND_PATH.exists():
        logger.error(f"❌ Frontend not found at {FRONTEND_PATH}")
        sys.exit(1)

    logger.info("📦 Installing frontend deps...")
    subprocess.run(["npm", "install"], check=True, cwd=FRONTEND_PATH)

    logger.info("🎨 Starting Vite dev server (port %d)...", FRONTEND_DEV_PORT)
    frontend_process = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=FRONTEND_PATH,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    if not _wait_for_port(FRONTEND_DEV_PORT):
        logger.error("❌ Frontend never came up")
        frontend_process.terminate()
        sys.exit(1)

    logger.info("🚀 Starting backend (port %d) with --reload...", BACKEND_PORT)
    backend_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lelab.server:app",
            "--host",
            BACKEND_HOST,
            "--port",
            str(BACKEND_PORT),
            "--reload",
        ],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        start_new_session=True,
    )

    if not _wait_for_port(BACKEND_PORT, timeout=15):
        logger.error("❌ Backend never came up")
        for p in (backend_process, frontend_process):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                p.terminate()
        sys.exit(1)

    logger.info("🌐 Opening browser...")
    webbrowser.open(f"http://localhost:{FRONTEND_DEV_PORT}/")

    logger.info("✅ Dev mode running — Ctrl+C to stop")
    logger.info("   Frontend: http://localhost:%d", FRONTEND_DEV_PORT)
    logger.info("   Backend:  http://localhost:%d", BACKEND_PORT)

    def shutdown(signum, frame):
        logger.info("🛑 Shutting down...")
        for name, p in [("backend", backend_process), ("frontend", frontend_process)]:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    p.kill()
            except Exception:
                pass
            logger.info(f"  ✅ {name} stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(2)
        if backend_process.poll() is not None:
            logger.error("❌ Backend died")
            shutdown(None, None)
        if frontend_process.poll() is not None:
            logger.error("❌ Frontend died")
            shutdown(None, None)


def main():
    # The run helpers and the browser-opener thread read these module-level
    # values, so --host/--port are applied by rebinding them here rather than
    # threading two more parameters through each function.
    global BACKEND_HOST, BACKEND_PORT

    parser = argparse.ArgumentParser(prog="lelab", description="Run LeLab")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: Vite HMR + uvicorn --reload (requires Node.js)",
    )
    parser.add_argument(
        "--host",
        default=BACKEND_HOST,
        help=f"Address to bind (default: {BACKEND_HOST}; env: LELAB_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=BACKEND_PORT,
        help=f"Port to serve on (default: {BACKEND_PORT}; env: LELAB_PORT)",
    )
    args = parser.parse_args()
    BACKEND_HOST, BACKEND_PORT = args.host, args.port

    if args.dev:
        _run_dev()
    else:
        _run_prod()


if __name__ == "__main__":
    main()
