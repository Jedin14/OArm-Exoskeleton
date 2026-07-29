#!/usr/bin/env python3
"""Validate the synchronization sidecar written by the recorder.

Usage:
    python3 lelab_ui/lelab/scripts/validate_sync.py DATASET_PATH
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


REQUIRED_COLUMNS = {
    "episode_index",
    "frame_index",
    "state_timestamp",
    "action_timestamp",
    "camera_timestamp",
    "max_delta",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--threshold", type=float, default=0.020)
    parser.add_argument(
        "--max-capture-gap",
        type=float,
        default=0.100,
        help="Maximum allowed inter-frame capture gap within an episode (seconds).",
    )
    args = parser.parse_args()

    root = args.dataset.expanduser().resolve()
    sync_path = root / "meta" / "sync_timestamps.parquet"
    if not sync_path.exists():
        print(f"FAIL: missing {sync_path}")
        print("This dataset has no persisted source-timestamp sidecar and cannot be verified.")
        return 2

    sync = pd.read_parquet(sync_path)
    missing = REQUIRED_COLUMNS - set(sync.columns)
    if missing:
        print(f"FAIL: sidecar is missing columns: {sorted(missing)}")
        return 2

    data_files = sorted((root / "data").glob("chunk-*/*.parquet"))
    if not data_files:
        print("FAIL: no data parquet files found")
        return 2
    data = pd.concat((pd.read_parquet(path) for path in data_files), ignore_index=True)

    numeric_columns = list(REQUIRED_COLUMNS - {"episode_index", "frame_index"})
    finite = sync[numeric_columns].apply(
        lambda column: column.map(lambda value: pd.notna(value) and float(value) == float(value))
    ).all().all()
    deltas = sync["max_delta"].astype(float)
    bad = deltas > args.threshold
    capture_gaps = sync.groupby("episode_index")["sync_timestamp"].diff().dropna()
    large_capture_gaps = capture_gaps > args.max_capture_gap

    print(f"dataset: {root}")
    print(f"data rows: {len(data)}")
    print(f"sync rows: {len(sync)}")
    print(f"episodes: {sync['episode_index'].nunique()}")
    print(f"max delta: {deltas.max():.6f} s")
    print(f"mean delta: {deltas.mean():.6f} s")
    print(f"p99 delta: {deltas.quantile(0.99):.6f} s")
    print(f"rows over {args.threshold:.3f} s: {int(bad.sum())}")
    if not capture_gaps.empty:
        print(f"max capture gap: {capture_gaps.max():.6f} s")
        print(
            f"capture gaps over {args.max_capture_gap:.3f} s: "
            f"{int(large_capture_gaps.sum())}"
        )

    if len(data) != len(sync):
        print("FAIL: data and synchronization row counts differ")
        return 1
    if sync.duplicated(["episode_index", "frame_index"]).any():
        print("FAIL: synchronization sidecar contains duplicate episode/frame keys")
        return 1
    data_counts = data.groupby("episode_index").size().sort_index()
    sync_counts = sync.groupby("episode_index").size().sort_index()
    if not data_counts.equals(sync_counts):
        print("FAIL: per-episode data and synchronization row counts differ")
        print("data counts:", data_counts.to_dict())
        print("sync counts:", sync_counts.to_dict())
        return 1
    if not finite:
        print("FAIL: synchronization timestamps contain NaN or infinite values")
        return 1
    if bad.any():
        print("FAIL: synchronization error exceeds threshold")
        return 1
    if large_capture_gaps.any():
        print("FAIL: capture cadence contains gaps larger than the allowed threshold")
        return 1
    print("PASS: synchronization sidecar is complete and within threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
