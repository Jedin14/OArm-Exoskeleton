#!/usr/bin/env python3
"""
Strip the end-effector pose dims from a LeRobot dataset's observation.state.

With include_ee_pose=True the OpenArm ROS backend records, per arm:
    [0:8]   joint positions (7 joints + gripper)   <- real proprioception
    [8:15]  ee_pose (x, y, z, qx, qy, qz, qw)      <- derived from FK
    [15]    gripper_state (measured width)         <- duplicate of dim 7

The ee_pose block is a pure function of the joint angles, so it adds no
information a policy cannot compute itself, and gripper_state duplicates dim 7
exactly (measured correlation 1.000). Dropping both leaves 8 dims that match
`action` 1:1 — and match datasets recorded with include_ee_pose=False.

Writes a NEW dataset; the source is left untouched. Video files are hardlinked,
so the copy costs no extra disk.

    ./strip_ee_pose.py <src_dataset_dir> [dst_dataset_dir]
"""

import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

KEEP = 8  # leading dims to retain


def _slice_list_col(table, col, keep):
    """Slice every row of a list<double> column down to `keep` elements."""
    vals = table[col].to_pylist()
    if not vals or not isinstance(vals[0], list) or len(vals[0]) <= keep:
        return table
    new = pa.array([v[:keep] if v is not None else None for v in vals])
    return table.set_column(table.column_names.index(col), col, new)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else src.parent / (src.name + "_noee")
    if dst.exists():
        sys.exit(f"ERROR: destination already exists: {dst}")

    info = json.loads((src / "meta" / "info.json").read_text())
    feat = info["features"]["observation.state"]
    n = feat["shape"][0]
    if n == KEEP:
        sys.exit(f"observation.state is already {KEEP}-dim; nothing to strip.")
    names = feat.get("names", [])
    dropped = names[KEEP:] if names else [f"dim{i}" for i in range(KEEP, n)]
    print(f"source      : {src.name}  (observation.state = {n} dims)")
    print(f"dropping    : {', '.join(dropped)}")
    print(f"destination : {dst.name}")

    # --- data: slice observation.state -------------------------------------
    for p in sorted((src / "data").rglob("*.parquet")):
        t = pq.read_table(p)
        t = _slice_list_col(t, "observation.state", KEEP)
        out = dst / p.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(t, out)
        print(f"  data   {p.relative_to(src)}  -> {t.num_rows} rows")

    # --- videos: hardlink (identical content, no extra disk) ---------------
    nvid = 0
    for p in sorted((src / "videos").rglob("*")):
        if p.is_dir():
            continue
        out = dst / p.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(p, out)
        except OSError:
            shutil.copy2(p, out)
        nvid += 1
    print(f"  videos {nvid} file(s) hardlinked")

    # --- meta --------------------------------------------------------------
    (dst / "meta").mkdir(parents=True, exist_ok=True)
    for p in sorted((src / "meta").rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.name == "info.json":
            continue  # rewritten below
        if p.name == "stats.json":
            st = json.loads(p.read_text())
            for key, block in st.items():
                if key != "observation.state":
                    continue
                for k, v in list(block.items()):
                    if isinstance(v, list) and len(v) == n:
                        block[k] = v[:KEEP]
            out.write_text(json.dumps(st, indent=1))
            print("  meta   stats.json  (observation.state stats sliced)")
            continue
        if p.suffix == ".parquet":
            t = pq.read_table(p)
            hit = [c for c in t.column_names if c.startswith("stats/observation.state/")]
            for c in hit:
                t = _slice_list_col(t, c, KEEP)
            pq.write_table(t, out)
            if hit:
                print(f"  meta   {rel}  ({len(hit)} per-episode stat columns sliced)")
            continue
        shutil.copy2(p, out)

    feat["shape"] = [KEEP]
    if names:
        feat["names"] = names[:KEEP]
    (dst / "meta" / "info.json").write_text(json.dumps(info, indent=4))
    print(f"  meta   info.json  (shape -> [{KEEP}])")

    for extra in ("calibration.yaml",):
        if (src / extra).exists():
            shutil.copy2(src / extra, dst / extra)

    print("\ndone.")


if __name__ == "__main__":
    main()
