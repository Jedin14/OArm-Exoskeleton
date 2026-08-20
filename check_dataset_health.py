#!/usr/bin/env python3
"""Health check for a LeRobot v2.1/v3.0 dataset recorded from the 7DOF-OArm rig.

Reports what is IN the dataset and whether it is trustworthy, grouped into:

  STRUCTURE   totals agree across info.json / episode meta / data / sidecar
  TIMING      frame contiguity, dt regularity, dropped frames
  SIGNALS     per-dimension ranges, NaN/inf, dead channels
  GRIPPER     units, and the grip force implied by the recorded commands
  SYNC        the recorder's own timestamp sidecar, if present
  VIDEO       files present, and frame-count parity if ffprobe can read them

Written against the failure modes this rig has actually produced, because a
checker that only samples frames for decode errors passed every one of them:

  * action in normalised 0..1 while observation.state was in metres (a policy
    trained on two encodings of "open")
  * the gripper aperture offset by ~9mm because the recorder assumed closed=0
    rather than reading gripper_home.yaml
  * commands buried 20-35mm past a rigid object -- an unenforced force limit,
    implying up to 20 Nm on a 2 Nm cap
  * per-motor torque decoded with one motor profile for all eight

Runs on pyarrow alone. lerobot is optional and only used for --decode.

    python check_dataset_health.py <path-or-repo-id>
    python check_dataset_health.py <path> --cap-nm 2.0     # verify a force cap
    python check_dataset_health.py <path> --decode 200     # also decode frames
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    import pyarrow.parquet as pq
except ImportError:
    # The container's system python3 has neither, but the lelab venv has both
    # (they come with lerobot). Point at that interpreter rather than telling
    # someone to pip install into a ROS python.
    _bin = Path(__file__).resolve().parent / "lelab_ui" / "venv" / "bin"
    # os.path.lexists, not .exists(): the venv's python3.12 is a symlink to an
    # interpreter that only exists inside the container, so resolving it from
    # the host would wrongly report the venv as missing.
    _venv = next((p for p in (_bin / "python3.12", _bin / "python3", _bin / "python")
                  if os.path.lexists(p)), None)
    _hint = f"\n\n    {_venv} {' '.join(sys.argv)}" if _venv else \
            "\n\n    pip install pyarrow numpy"
    sys.exit(f"needs pyarrow and numpy — run this with the lelab venv instead:{_hint}")

# ── Rig constants ────────────────────────────────────────────────────────────
# Gripper force is position error x gain, so a recorded command that sits below
# the measured aperture implies a squeeze. From v10_simple_hardware.hpp:
#   GRIPPER_DEFAULT_KP = 20.0 Nm/rad
#   GRIPPER_MOTOR_1_RADIANS / GRIPPER_JOINT_0_POSITION = 1.0472 rad / 0.044 m
GRIPPER_KP = 20.0
GRIPPER_RAD_PER_M = 1.0472 / 0.044
NM_PER_M = GRIPPER_KP * GRIPPER_RAD_PER_M      # 476 Nm per metre of error
GRIPPER_OPEN_M = 0.044
GRIPPER_DIM = 7                                 # j1..j7 then finger, per arm

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MARK = {PASS: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, section: str, msg: str) -> None:
        self.rows.append((level, section, msg))

    def section(self, name: str) -> None:
        print(f"\n\033[1m{name}\033[0m")

    def line(self, level: str, msg: str) -> None:
        colour = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}[level]
        print(f"  {colour}[{MARK[level]}]\033[0m {msg}")

    def emit(self, level: str, section: str, msg: str) -> None:
        self.add(level, section, msg)
        self.line(level, msg)

    @property
    def failed(self) -> int:
        return sum(1 for lvl, _, _ in self.rows if lvl == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for lvl, _, _ in self.rows if lvl == WARN)


def resolve_root(target: str) -> Path:
    """Accept a path, or a repo id resolved under the local lerobot cache."""
    path = Path(target).expanduser()
    if (path / "meta" / "info.json").is_file():
        return path
    home = os.environ.get("HF_LEROBOT_HOME", os.environ.get("LEROBOT_HOME", "~/.cache/huggingface/lerobot"))
    candidate = Path(home).expanduser() / target
    if (candidate / "meta" / "info.json").is_file():
        return candidate
    sys.exit(f"no dataset at {path} or {candidate}")


def read_parquets(pattern: str) -> dict | None:
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        return None
    merged: dict = {}
    for f in files:
        for key, values in pq.read_table(f).to_pydict().items():
            merged.setdefault(key, []).extend(values)
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", help="path to the dataset, or a repo id under the lerobot cache")
    ap.add_argument("--cap-nm", type=float, default=None,
                    help="expected gripper torque cap; flags commands implying more force")
    ap.add_argument("--decode", type=int, default=0, metavar="N",
                    help="also decode N frames through lerobot (slow, needs lerobot)")
    ap.add_argument("--fps-tolerance", type=float, default=1.5,
                    help="dt gap threshold as a multiple of 1/fps (default 1.5)")
    args = ap.parse_args()

    root = resolve_root(args.dataset)
    rep = Report()
    print(f"\033[1mDataset\033[0m {root}")

    info = json.loads((root / "meta" / "info.json").read_text())
    features = info.get("features", {})
    fps = info.get("fps") or 30
    cameras = [k for k in features if k.startswith("observation.images.")]

    # ── what this dataset IS ────────────────────────────────────────────────
    rep.section("OVERVIEW")
    print(f"    codebase_version  {info.get('codebase_version')}")
    print(f"    robot_type        {info.get('robot_type')}"
          + ("   (direct CAN + direct cameras)" if info.get("robot_type") == "oarm7dof_direct"
             else "   (ROS bridge path)" if info.get("robot_type") == "oarm7dof_ros" else ""))
    print(f"    fps               {fps}")
    print(f"    episodes/frames   {info.get('total_episodes')} / {info.get('total_frames')}")
    print(f"    splits            {info.get('splits')}")
    print(f"    cameras           {[c.split('.')[-1] for c in cameras] or 'none'}")
    for name, spec in features.items():
        if name in ("action", "observation.state"):
            print(f"    {name:<17} {spec.get('dtype')} {spec.get('shape')}"
                  f"  names={(spec.get('names') or ['-'])[:3]}{'...' if spec.get('names') and len(spec['names'])>3 else ''}")
    extra = root / "meta" / "action_cap.json"
    if extra.is_file():
        prov = json.loads(extra.read_text())
        rep.emit(WARN, "OVERVIEW",
                 f"post-processed: action retro-capped at {prov.get('gripper_torque_cap_nm')} Nm "
                 f"({prov.get('rows_clamped')} rows) — approach dynamics are the ORIGINAL uncapped ones")

    data = read_parquets(str(root / "data" / "**" / "*.parquet"))
    if data is None:
        sys.exit("no data parquet found")
    n = len(data["index"])
    ep_meta = read_parquets(str(root / "meta" / "episodes" / "**" / "*.parquet"))

    # ── STRUCTURE ───────────────────────────────────────────────────────────
    rep.section("STRUCTURE")
    if info.get("total_frames") == n:
        rep.emit(PASS, "STRUCTURE", f"data rows match info.total_frames ({n})")
    else:
        rep.emit(FAIL, "STRUCTURE", f"data rows {n} != info.total_frames {info.get('total_frames')}")

    episodes = sorted(set(data["episode_index"]))
    if episodes == list(range(len(episodes))):
        rep.emit(PASS, "STRUCTURE", f"{len(episodes)} episodes, contiguously numbered 0..{len(episodes)-1}")
    else:
        missing = set(range(max(episodes) + 1)) - set(episodes)
        rep.emit(FAIL, "STRUCTURE", f"episode indices not contiguous; missing {sorted(missing)[:10]}")

    if ep_meta:
        total = sum(ep_meta["length"])
        if total == n and len(ep_meta["episode_index"]) == len(episodes):
            rep.emit(PASS, "STRUCTURE", f"episode metadata agrees ({len(episodes)} rows, {total} frames)")
        else:
            rep.emit(FAIL, "STRUCTURE",
                     f"episode meta says {len(ep_meta['episode_index'])} eps / {total} frames, "
                     f"data has {len(episodes)} / {n}")
    else:
        rep.emit(WARN, "STRUCTURE", "no meta/episodes parquet found")

    if list(data["index"]) == sorted(data["index"]):
        rep.emit(PASS, "STRUCTURE", "global index strictly ordered")
    else:
        rep.emit(FAIL, "STRUCTURE", "global index is not ordered")

    # ── TIMING ──────────────────────────────────────────────────────────────
    rep.section("TIMING")
    gap_limit = args.fps_tolerance / fps
    bad_contig, gaps, durations = [], 0, []
    ep_of = np.array(data["episode_index"])
    ts_all = np.array(data["timestamp"], dtype=float)
    fi_all = np.array(data["frame_index"])
    for e in episodes:
        m = ep_of == e
        fi, ts = fi_all[m], ts_all[m]
        if not np.array_equal(fi, np.arange(len(fi))):
            bad_contig.append(e)
        d = np.diff(ts)
        gaps += int((d > gap_limit).sum())
        durations.append(float(ts[-1]) if len(ts) else 0.0)
    if not bad_contig:
        rep.emit(PASS, "TIMING", "every episode has contiguous frame_index")
    else:
        rep.emit(FAIL, "TIMING", f"non-contiguous frame_index in episodes {bad_contig[:8]}")
    if gaps == 0:
        rep.emit(PASS, "TIMING", f"no dt gaps beyond {gap_limit*1000:.0f} ms (expected {1000/fps:.2f} ms)")
    else:
        rep.emit(FAIL, "TIMING", f"{gaps} inter-frame gaps exceed {gap_limit*1000:.0f} ms — dropped frames")
    print(f"    episode length   min {min(len(fi_all[ep_of==e]) for e in episodes)}"
          f"  max {max(len(fi_all[ep_of==e]) for e in episodes)}"
          f"  mean {n/len(episodes):.0f} frames"
          f"   ({min(durations):.1f}-{max(durations):.1f} s)")

    # ── SIGNALS ─────────────────────────────────────────────────────────────
    rep.section("SIGNALS")
    action = np.array([list(v) for v in data["action"]], dtype=np.float64)
    state = np.array([list(v) for v in data["observation.state"]], dtype=np.float64)
    for label, arr in (("action", action), ("observation.state", state)):
        bad = int((~np.isfinite(arr)).sum())
        if bad:
            rep.emit(FAIL, "SIGNALS", f"{label} contains {bad} NaN/inf values")
        else:
            rep.emit(PASS, "SIGNALS", f"{label} finite everywhere, shape {arr.shape}")
        dead = [i for i in range(arr.shape[1]) if np.ptp(arr[:, i]) < 1e-6]
        if dead:
            rep.emit(WARN, "SIGNALS", f"{label} dimensions never change: {dead}")

    print(f"    {'dim':<5}{'state range':>22}{'action range':>22}{'mean|a-s|':>11}")
    names = (features.get("action", {}) or {}).get("names") or []
    for i in range(action.shape[1]):
        nm = names[i] if i < len(names) else f"dim{i}"
        print(f"    {i:<5}[{state[:,i].min():+7.3f},{state[:,i].max():+7.3f}]"
              f"   [{action[:,i].min():+7.3f},{action[:,i].max():+7.3f}]"
              f"{np.abs(action[:,i]-state[:,i]).mean():>11.4f}   {nm}")

    # ── GRIPPER ─────────────────────────────────────────────────────────────
    rep.section("GRIPPER")
    if action.shape[1] > GRIPPER_DIM:
        ga, gs = action[:, GRIPPER_DIM], state[:, GRIPPER_DIM]
        # Units: metres live in 0..0.044; a normalised channel reaches ~1.0.
        if ga.max() > 0.5:
            rep.emit(FAIL, "GRIPPER",
                     f"action gripper looks NORMALISED (max {ga.max():.3f}) while state is in metres "
                     f"(max {gs.max():.4f}) — two encodings of 'open' in one dataset")
        else:
            rep.emit(PASS, "GRIPPER", f"action and state both in metres (max {ga.max():.4f} / {gs.max():.4f})")

        if gs.max() > GRIPPER_OPEN_M * 1.02:
            rep.emit(WARN, "GRIPPER", f"state exceeds the {GRIPPER_OPEN_M} m mechanical range (max {gs.max():.4f})")
        if gs.min() > GRIPPER_OPEN_M * 0.5:
            rep.emit(WARN, "GRIPPER",
                     f"gripper never closed past {gs.min()*1000:.1f} mm — object that wide, or it never gripped")

        implied = np.maximum(0.0, gs - ga) * NM_PER_M
        print(f"    implied grip torque   mean {implied.mean():5.2f}   p90 {np.quantile(implied,0.9):5.2f}"
              f"   max {implied.max():6.2f} Nm      (command-vs-measured x {NM_PER_M:.0f} Nm/m)")
        print(f"    measured travel/ep    {np.mean([np.ptp(gs[ep_of==e]) for e in episodes])*1000:5.1f} mm"
              f"    commanded {np.mean([np.ptp(ga[ep_of==e]) for e in episodes])*1000:5.1f} mm")
        if args.cap_nm:
            over = int((implied > args.cap_nm * 1.15).sum())
            allowed_mm = args.cap_nm / NM_PER_M * 1000
            if over == 0:
                rep.emit(PASS, "GRIPPER",
                         f"force cap {args.cap_nm} Nm respected (allowed error {allowed_mm:.1f} mm)")
            else:
                rep.emit(FAIL, "GRIPPER",
                         f"{over} rows ({100*over/n:.1f}%) imply more than {args.cap_nm} Nm "
                         f"— peak {implied.max():.2f} Nm; the cap was not enforced")
        elif implied.max() > 8.0:
            rep.emit(WARN, "GRIPPER",
                     f"peak implied torque {implied.max():.1f} Nm is high — pass --cap-nm to check a limit")

    # ── SYNC ────────────────────────────────────────────────────────────────
    rep.section("SYNC")
    sc = read_parquets(str(root / "meta" / "sync_timestamps.parquet"))
    if not sc:
        rep.emit(WARN, "SYNC", "no sync_timestamps.parquet — timing provenance unavailable")
    else:
        rows = len(sc["frame_index"])
        if rows == n:
            rep.emit(PASS, "SYNC", f"sidecar has one row per frame ({rows})")
        else:
            rep.emit(FAIL, "SYNC", f"sidecar has {rows} rows for {n} data rows")
        for key in ("state_action_delta", "state_camera_delta", "action_camera_delta", "max_delta"):
            if key in sc:
                v = np.array([x for x in sc[key] if x is not None], dtype=float)
                if len(v):
                    print(f"    {key:<22} mean {v.mean()*1000:6.2f} ms   max {v.max()*1000:7.2f} ms")
        if "max_delta" in sc:
            v = np.array([x for x in sc["max_delta"] if x is not None], dtype=float)
            over = int((v > 0.050).sum())
            lvl = PASS if over == 0 else (WARN if over < len(v) * 0.01 else FAIL)
            rep.emit(lvl, "SYNC", f"{over}/{len(v)} rows exceed the 50 ms alignment tolerance")
        for key in sorted(k for k in sc if k.endswith("_latency_ms")):
            v = np.array([x for x in sc[key] if x is not None], dtype=float)
            if len(v):
                print(f"    {key:<34} mean {v.mean():5.1f} ms   max {v.max():6.1f} ms")

    # ── VIDEO ───────────────────────────────────────────────────────────────
    rep.section("VIDEO")
    vids = sorted(glob.glob(str(root / "videos" / "**" / "*.mp4"), recursive=True))
    if not cameras:
        rep.emit(WARN, "VIDEO", "dataset declares no camera features")
    elif not vids:
        rep.emit(FAIL, "VIDEO", f"{len(cameras)} camera features declared but no mp4 files found")
    else:
        rep.emit(PASS, "VIDEO", f"{len(vids)} video files for {len(cameras)} camera(s)")
        for cam in cameras:
            per_cam = [v for v in vids if cam in v]
            total = 0
            unreadable = False
            for v in per_cam:
                try:
                    out = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-count_frames", "-show_entries", "stream=nb_read_frames",
                         "-of", "csv=p=0", v],
                        capture_output=True, text=True, timeout=600).stdout.strip()
                    total += int(out)
                except Exception:
                    unreadable = True
                    break
            short = cam.split(".")[-1]
            if unreadable:
                rep.emit(WARN, "VIDEO", f"{short}: cannot read frame count (permissions or no ffprobe)")
            elif total == n:
                rep.emit(PASS, "VIDEO", f"{short}: {total} decoded frames == {n} rows")
            else:
                rep.emit(FAIL, "VIDEO", f"{short}: {total} decoded frames != {n} rows")

    # ── optional decode through lerobot ─────────────────────────────────────
    if args.decode:
        rep.section("DECODE")
        try:
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
            ds = LeRobotDataset(str(root))
            idxs = sorted(set(np.linspace(0, len(ds) - 1, args.decode, dtype=int).tolist()))
            errs = []
            for i in idxs:
                try:
                    ds[int(i)]
                except Exception as e:
                    errs.append((i, str(e)[:80]))
            if errs:
                rep.emit(FAIL, "DECODE", f"{len(errs)}/{len(idxs)} sampled frames failed: {errs[:3]}")
            else:
                rep.emit(PASS, "DECODE", f"all {len(idxs)} sampled frames decoded")
        except ImportError:
            rep.emit(WARN, "DECODE", "lerobot not importable here — run with the lelab venv python")

    # ── verdict ─────────────────────────────────────────────────────────────
    print()
    if rep.failed:
        print(f"\033[31m\033[1mVERDICT: {rep.failed} failure(s), {rep.warned} warning(s)\033[0m")
        for lvl, sec, msg in rep.rows:
            if lvl == FAIL:
                print(f"  - [{sec}] {msg}")
        sys.exit(1)
    if rep.warned:
        print(f"\033[33m\033[1mVERDICT: usable, {rep.warned} warning(s)\033[0m")
        for lvl, sec, msg in rep.rows:
            if lvl == WARN:
                print(f"  - [{sec}] {msg}")
        sys.exit(0)
    print("\033[32m\033[1mVERDICT: healthy\033[0m")


if __name__ == "__main__":
    main()
