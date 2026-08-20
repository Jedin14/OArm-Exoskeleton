#!/usr/bin/env python3
"""
Gate a LeRobot dataset before it is used for VLA/ACT training.

Checks each action dimension against the 7DOF-OArm v10 mechanical limits and
fails on the failure mode that silently corrupted Pick_Drop_500: joints pinned
at their limit by np.clip during retargeting. Those frames carry a confident,
scene-independent label, and a policy trained on them cannot predict the joint
on clean data (measured: joint6 beat a constant predictor by only 1.9%).

    ./validate_dataset.py ~/.cache/huggingface/lerobot/USER/DATASET
    ./validate_dataset.py <path> --max-episode-pct 5 --max-overall-pct 0.5
    ./validate_dataset.py <path> --write-mask mask.npy   # per-frame validity

Exit status is 0 when clean and 1 when it fails a threshold, so it can be used
directly as a pre-training gate in a script or CI job.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

# openarm_description/config/arm/v10/joint_limits.yaml
LIMITS = [
    ("joint1", -1.396263, 3.490659),
    ("joint2", -0.174533, 3.316125),
    ("joint3", -1.570796, 1.570796),
    ("joint4",  0.0,      2.443461),
    ("joint5", -1.570796, 1.570796),
    ("joint6", -0.785398, 0.785398),   # narrowest joint on the arm
    ("joint7", -1.570796, 1.570796),
]


def load(path):
    import pyarrow.parquet as pq

    files = sorted(glob.glob(os.path.join(path, "data", "**", "*.parquet"),
                             recursive=True))
    if not files:
        sys.exit(f"ERROR: no parquet files under {path}/data")
    acts, eps = [], []
    for f in files:
        t = pq.read_table(f, columns=["action", "episode_index"])
        acts.append(np.stack(t["action"].to_pylist()))
        eps.append(np.asarray(t["episode_index"].to_pylist()))
    return np.concatenate(acts), np.concatenate(eps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--tol", type=float, default=0.005,
                    help="rad from the limit considered 'near' it")
    ap.add_argument("--min-repeats", type=int, default=20,
                    help="a near-limit value repeated at least this many times "
                         "counts as a clipping point mass (soft-limited data "
                         "produces near-unique values and is not flagged)")
    ap.add_argument("--max-overall-pct", type=float, default=0.5,
                    help="fail if more than this %% of all frames are pinned")
    ap.add_argument("--max-episode-pct", type=float, default=5.0,
                    help="fail if any episode exceeds this %% pinned")
    ap.add_argument("--write-mask", metavar="NPY",
                    help="save a per-frame, per-joint validity mask to mask.npy "
                         "(True = usable) so training can drop only the bad "
                         "labels instead of whole episodes")
    ap.add_argument("--json", metavar="OUT", help="write a full report")
    args = ap.parse_args()

    actions, episodes = load(args.dataset)
    n, dim = actions.shape
    print(f"{os.path.basename(args.dataset.rstrip('/'))}: "
          f"{n} frames, {len(np.unique(episodes))} episodes, {dim} action dims\n")

    mask = np.ones((n, dim), dtype=bool)
    failures, report = [], {}

    hdr = f"{'joint':<8}{'range used':>12}{'%pinned':>9}{'worst ep':>10}{'ep %':>7}  status"
    print(hdr)
    print("-" * len(hdr))

    for i, (name, lo, hi) in enumerate(LIMITS):
        if i >= dim:
            break
        col = actions[:, i]
        near = (col <= lo + args.tol) | (col >= hi - args.tol)

        # Proximity to a limit is NOT the defect — soft limiting legitimately
        # produces values just inside it. The defect is CONCENTRATION: hard
        # clipping collapses many frames onto a single repeated value, and that
        # point mass is what a policy learns as a confident, scene-independent
        # label. Measure repetition, not nearness.
        #
        # Observed on real data: Pick_Drop_500 (hard clip) put 9631 near-limit
        # frames on 28 distinct values (344 frames/value); the same rig after
        # soft limiting put 106 frames on 90 values (1.2 frames/value).
        pinned = np.zeros_like(near)
        if near.any():
            vals, counts = np.unique(np.round(col[near], 9), return_counts=True)
            repeated = vals[counts >= args.min_repeats]
            if repeated.size:
                pinned = near & np.isin(np.round(col, 9), repeated)

        mask[:, i] = ~pinned
        overall = 100.0 * pinned.mean()

        per_ep = {int(e): 100.0 * pinned[episodes == e].mean()
                  for e in np.unique(episodes)}
        worst_ep = max(per_ep, key=per_ep.get)
        worst = per_ep[worst_ep]
        used = 100.0 * (col.max() - col.min()) / (hi - lo)

        bad = overall > args.max_overall_pct or worst > args.max_episode_pct
        if bad:
            failures.append(name)
        print(f"{name:<8}{used:11.1f}%{overall:8.2f}%{worst_ep:10d}{worst:6.1f}%"
              f"  {'FAIL' if bad else 'ok'}")

        report[name] = {
            "pct_frames_pinned": round(overall, 4),
            "worst_episode": worst_ep,
            "worst_episode_pct": round(worst, 2),
            "episodes_over_threshold": sorted(
                int(e) for e, v in per_ep.items() if v > args.max_episode_pct),
        }

    if args.write_mask:
        np.save(args.write_mask, mask)
        print(f"\nvalidity mask -> {args.write_mask}  "
              f"({100 * (~mask).mean():.2f}% of labels masked out)")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"dataset": args.dataset, "frames": int(n),
                       "thresholds": {"overall_pct": args.max_overall_pct,
                                      "episode_pct": args.max_episode_pct},
                       "joints": report, "failed": failures}, fh, indent=1)
        print(f"report -> {args.json}")

    if failures:
        print(f"\nFAILED: {', '.join(failures)} exceed the saturation thresholds.")
        print("Fix the retargeting (see retargeting_params.limit_mode) and "
              "re-record, or train with --write-mask to drop just the bad labels.")
        return 1

    print("\nPASS: no joint is pinned beyond the thresholds. Safe to train.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
