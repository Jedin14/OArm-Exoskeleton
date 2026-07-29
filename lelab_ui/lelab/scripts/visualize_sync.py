#!/usr/bin/env python3
"""Open a dataset synchronization sidecar as Rerun plots."""
import argparse
from pathlib import Path

import pandas as pd
import rerun as rr
import cv2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    sidecar = args.dataset / "meta" / "sync_timestamps.parquet"
    if not sidecar.exists():
        raise SystemExit(f"Missing synchronization sidecar: {sidecar}")

    df = pd.read_parquet(sidecar).sort_values(["episode_index", "frame_index"])
    data_files = sorted((args.dataset / "data").rglob("*.parquet"))
    if not data_files:
        raise SystemExit("No data parquet files found")
    data = pd.concat([pd.read_parquet(f) for f in data_files], ignore_index=True)
    data = data.sort_values(["episode_index", "frame_index"])
    videos = {
        "main_camera": sorted((args.dataset / "videos/observation.images.main_camera").rglob("*.mp4")),
        "right_camera": sorted((args.dataset / "videos/observation.images.right_camera").rglob("*.mp4")),
    }
    captures = {name: (cv2.VideoCapture(str(files[0])) if files else None) for name, files in videos.items()}
    rr.init(f"sync_{args.dataset.name}", spawn=True)
    for (_, row), (_, data_row) in zip(df.iterrows(), data.iterrows()):
        frame = int(row["frame_index"])
        rr.set_time_sequence("frame", frame)
        rr.log("sync/delta/state_action_ms", rr.Scalar(float(row["state_action_delta"]) * 1000))
        rr.log("sync/delta/state_camera_ms", rr.Scalar(float(row["state_camera_delta"]) * 1000))
        rr.log("sync/delta/action_camera_ms", rr.Scalar(float(row["action_camera_delta"]) * 1000))
        rr.log("sync/delta/max_ms", rr.Scalar(float(row["max_delta"]) * 1000))
        rr.log("sync/threshold_ms", rr.Scalar(20.0), static=True)
        action = data_row["action"]
        if hasattr(action, "tolist"):
            action = action.tolist()
        for index, value in enumerate(action):
            rr.log(f"action/dim_{index:02d}", rr.Scalar(float(value)))
        for name, capture in captures.items():
            if capture is not None:
                ok, image = capture.read()
                if ok:
                    rr.log(f"camera/{name}", rr.Image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))

    for capture in captures.values():
        if capture is not None:
            capture.release()

    print(f"Logged {len(df)} synchronization frames to Rerun.")
    print("Open the Time Series view and inspect sync/delta/max_ms against sync/threshold_ms.")


if __name__ == "__main__":
    main()
