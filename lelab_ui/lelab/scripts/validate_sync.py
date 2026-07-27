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

    finite = sync[list(REQUIRED_COLUMNS - {"episode_index", "frame_index"})].applymap(
        lambda value: pd.notna(value) and float(value) == float(value)
    ).all().all()
    deltas = sync["max_delta"].astype(float)
    bad = deltas > args.threshold

    print(f"dataset: {root}")
    print(f"data rows: {len(data)}")
    print(f"sync rows: {len(sync)}")
    print(f"episodes: {sync['episode_index'].nunique()}")
    print(f"max delta: {deltas.max():.6f} s")
    print(f"mean delta: {deltas.mean():.6f} s")
    print(f"p99 delta: {deltas.quantile(0.99):.6f} s")
    print(f"rows over {args.threshold:.3f} s: {int(bad.sum())}")

    if len(data) != len(sync):
        print("FAIL: data and synchronization row counts differ")
        return 1
    if not finite:
        print("FAIL: synchronization timestamps contain NaN or infinite values")
        return 1
    if bad.any():
        print("FAIL: synchronization error exceeds threshold")
        return 1
    print("PASS: synchronization sidecar is complete and within threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
