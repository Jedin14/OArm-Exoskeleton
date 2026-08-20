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
"""Tests for lelab.record — request schemas and handler entry points."""

from __future__ import annotations

from collections import deque
import threading

import pytest


def test_recording_request_rejects_missing_required_fields() -> None:
    from pydantic import ValidationError

    from lelab.record import RecordingRequest

    with pytest.raises(ValidationError):
        RecordingRequest()


def test_recording_status_handler_exposes_state_fields() -> None:
    from lelab.record import handle_recording_status

    result = handle_recording_status()
    assert isinstance(result, dict)
    # Pinning the exact keys so a rename in handle_recording_status surfaces here.
    assert "recording_active" in result
    assert "current_phase" in result
    assert "session_ended" in result
    assert "available_controls" in result


def test_handle_stop_recording_when_idle_returns_dict(tmp_lerobot_home) -> None:
    from lelab.record import handle_stop_recording

    result = handle_stop_recording()
    assert isinstance(result, dict)


def _timestamp_test_robot(action_history: list[tuple[float, dict, dict]], target: float):
    from lelab.robots.oarm7dof_ros import OArm7DOFRosRobot

    robot = OArm7DOFRosRobot.__new__(OArm7DOFRosRobot)
    robot._data_lock = threading.Lock()
    robot._action_history = deque(action_history, maxlen=2000)
    robot._latest_action = {"joint.pos": 99.0}
    robot._last_sync_timestamp = target
    robot._last_sync_diagnostics = {}
    return robot


def test_action_matching_uses_latest_sample_before_sync_time() -> None:
    robot = _timestamp_test_robot(
        [
            (9.90, {"joint.pos": 1.0}, {"joint": 9.90}),
            (9.98, {"joint.pos": 2.0}, {"joint": 9.98}),
            (10.01, {"joint.pos": 3.0}, {"joint": 10.01}),
        ],
        10.0,
    )

    assert robot.get_action_at_sync_time() == {"joint.pos": 2.0}
    assert robot._last_sync_diagnostics["action_future_ms"] == 0.0


def test_action_matching_rejects_startup_future_sample() -> None:
    robot = _timestamp_test_robot(
        [(10.01, {"joint.pos": 3.0}, {"joint": 10.01})],
        10.0,
    )

    assert robot.get_action_at_sync_time() == {"joint.pos": 3.0}
    assert robot._last_sync_diagnostics["action_future_ms"] > 0.0
    assert not robot.sync_within_tolerance(0.020)
