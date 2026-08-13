"""Process lookup for the ROS camera bridge.

WHY THIS IS NOT `pgrep -f`
--------------------------
`pgrep -f openarm_camera_bridge_node.py` matches any process whose FULL command
line contains that string -- which includes every other `pgrep`/`pkill`
invocation carrying the same pattern as an argument. pgrep excludes only itself,
not its siblings.

The Camera Setup page fetches `/ros-camera-bridge/status` and `/io-config`
concurrently, and both used to shell out to pgrep. Each call could therefore see
the other's process and report a bridge that did not exist. Measured on this
machine: 36 of 36 concurrent probes reported a "running bridge" with none
running, returning the transient pgrep PIDs.

That single false positive produced every one of these symptoms:

  * Camera Setup flipping between its ROS and direct layouts between polls.
  * A PID badge that changed every time (it was a different short-lived pgrep).
  * "Stop Bridge" answering "Bridge was not running" -- true, because the
    process it had just been told about was a pgrep that had already exited.
  * Blank camera previews during recording: record.py picks the ROS backend when
    it believes a bridge is up, so a spurious hit made the recorder subscribe to
    ROS topics nobody was publishing.

Reading /proc directly avoids the whole class of problem: no helper process is
spawned, so nothing exists for the match to collide with.
"""

from __future__ import annotations

import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

BRIDGE_SCRIPT_NAME = "openarm_camera_bridge_node.py"


def _cmdline(pid: str) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return [part.decode(errors="replace") for part in fh.read().split(b"\0") if part]
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        # Process exited between listdir and open, or belongs to another user.
        return []
    except OSError:
        return []


def camera_bridge_pids(script_name: str = BRIDGE_SCRIPT_NAME) -> list[int]:
    """PIDs of genuinely running camera-bridge processes.

    Requires BOTH that the script name appears in the command line AND that the
    program being run is a Python interpreter. The bridge is always launched as
    ``<python> .../openarm_camera_bridge_node.py``, whereas a false match (a
    pgrep/pkill/shell command that merely mentions the name) never has python as
    argv[0]. Our own PID is excluded so a caller can never detect itself.
    """
    own = os.getpid()
    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError as e:
        logger.warning("cannot read /proc to find the camera bridge: %s", e)
        return []

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == own:
            continue
        argv = _cmdline(entry)
        if not argv:
            continue
        if not any(script_name in arg for arg in argv):
            continue
        # argv[0] is the interpreter for a real launch. Reject anything else --
        # that is what keeps a `pgrep -f <script>` sibling from counting.
        if "python" not in os.path.basename(argv[0]).lower():
            continue
        pids.append(pid)
    return sorted(pids)


def stop_camera_bridge(timeout_s: float = 3.0, script_name: str = BRIDGE_SCRIPT_NAME) -> list[int]:
    """Terminate every camera-bridge process; return any PIDs still alive.

    Signals the exact PIDs found above rather than running ``pkill -f``, which
    would match on the same over-broad pattern and could signal an unrelated
    process that merely mentions the script name.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = camera_bridge_pids(script_name)
        if not pids:
            return []
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass  # already gone
            except PermissionError:
                logger.error("not permitted to signal camera bridge PID %s", pid)

        deadline = time.monotonic() + (timeout_s if sig == signal.SIGTERM else 2.0)
        while time.monotonic() < deadline:
            if not camera_bridge_pids(script_name):
                return []
            time.sleep(0.1)
        if sig == signal.SIGTERM:
            logger.warning("camera bridge ignored SIGTERM; escalating to SIGKILL")

    return camera_bridge_pids(script_name)
