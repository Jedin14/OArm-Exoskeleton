import os
import json
import subprocess
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

def convert_v3_to_v21(repo_id: str):
    from lerobot.utils.constants import HF_LEROBOT_HOME
    root = Path(HF_LEROBOT_HOME).expanduser() / repo_id
    if not root.exists():
        raise FileNotFoundError(f"Dataset root {root} does not exist.")

    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        raise FileNotFoundError(f"meta/info.json not found in {root}")

    with open(info_path, "r") as f:
        info = json.load(f)

    if info.get("codebase_version") == "v2.1":
        return  # Already v2.1

    # 1. Convert info.json
    info["codebase_version"] = "v2.1"
    
    # Check if episodes metadata exists
    episodes_meta_dir = root / "meta" / "episodes"
    if episodes_meta_dir.exists():
        episodes_df = pd.concat([pd.read_parquet(p) for p in episodes_meta_dir.glob("*/*.parquet")])
    else:
        episodes_df = None

    if episodes_df is not None:
        num_episodes = len(episodes_df)
        info["total_episodes"] = num_episodes
        
        # Determine total chunks/videos (for simple case, just 1 chunk)
        info["total_chunks"] = 1
        info["total_videos"] = num_episodes * sum([1 for f in info["features"].values() if f["dtype"] == "video"])
        info["data_path"] = "data/episode_{episode_index:06d}.parquet"
        info["video_path"] = "videos/{video_key}_episode_{episode_index:06d}.mp4"

        # 2. Convert episodes.jsonl and episodes_stats.jsonl
        episodes_list = []
        stats_list = []
        for _, row in episodes_df.iterrows():
            ep_idx = int(row["episode_index"])
            tasks = row["tasks"] if "tasks" in row else []
            if hasattr(tasks, "tolist"):
                tasks = tasks.tolist()
            elif not isinstance(tasks, list):
                tasks = [tasks]
                
            episodes_list.append({
                "episode_index": ep_idx,
                "tasks": tasks,
                "length": int(row["length"]) if "length" in row else int(row.get("dataset_to_index", 0) - row.get("dataset_from_index", 0))
            })
            
            # Reconstruct stats if needed, simplified
            stats_dict = {}
            for col in episodes_df.columns:
                if col.startswith("stats/"):
                    parts = col.split("/")
                    if len(parts) >= 3:
                        feat = "/".join(parts[1:-1])  # in case feature has /
                        stat = parts[-1]
                        if feat not in stats_dict:
                            stats_dict[feat] = {}
                        val = row[col]
                        if hasattr(val, "tolist"):
                            val = val.tolist()
                        elif hasattr(val, "item"):
                            val = val.item()
                        stats_dict[feat][stat] = val
            if stats_dict:
                stats_list.append({
                    "episode_index": ep_idx,
                    "stats": stats_dict
                })
                
        with open(root / "meta" / "episodes.jsonl", "w") as f:
            for ep in episodes_list:
                f.write(json.dumps(ep, cls=NpEncoder) + "\n")
                
        if stats_list:
            with open(root / "meta" / "episodes_stats.jsonl", "w") as f:
                for st in stats_list:
                    f.write(json.dumps(st, cls=NpEncoder) + "\n")

    # 3. Convert tasks.parquet to tasks.jsonl
    tasks_pq = root / "meta" / "tasks.parquet"
    if tasks_pq.exists():
        tasks_df = pd.read_parquet(tasks_pq)
        with open(root / "meta" / "tasks.jsonl", "w") as f:
            for idx, row in tasks_df.iterrows():
                # Handling both when task is an index or a column
                task_str = row["task"] if "task" in row else idx
                task_idx = int(row["task_index"]) if "task_index" in row else 0
                f.write(json.dumps({"task_index": task_idx, "task": str(task_str)}, cls=NpEncoder) + "\n")
        # Rename or remove tasks.parquet to match v2.1
        os.remove(tasks_pq)

    # 4. Split data/chunk-XXX/file_YYY.parquet to episode_ZZZ.parquet
    data_dir = root / "data"
    if data_dir.exists() and episodes_df is not None:
        for chunk_dir in data_dir.glob("chunk-*"):
            for file_pq in chunk_dir.glob("file-*.parquet"):
                df = pd.read_parquet(file_pq)
                # Group by episode_index
                if "episode_index" in df.columns:
                    for ep_idx, group in df.groupby("episode_index"):
                        ep_str = f"episode_{int(ep_idx):06d}.parquet"
                        group.to_parquet(data_dir / ep_str)
                else:
                    # Use dataset_from_index from episodes_df
                    for _, row in episodes_df.iterrows():
                        ep_idx = int(row["episode_index"])
                        from_idx = int(row["dataset_from_index"])
                        to_idx = int(row["dataset_to_index"])
                        if from_idx < len(df):
                            ep_df = df.iloc[from_idx:to_idx]
                            ep_str = f"episode_{ep_idx:06d}.parquet"
                            ep_df.to_parquet(data_dir / ep_str)
                os.remove(file_pq)

    # 5. Split videos using ffmpeg
    videos_dir = root / "videos"
    if videos_dir.exists() and episodes_df is not None:
        for cam_dir in videos_dir.glob("*"):
            for chunk_dir in cam_dir.glob("chunk-*"):
                for file_mp4 in chunk_dir.glob("file-*.mp4"):
                    # Find all episodes that use this chunk
                    cam = cam_dir.name
                    for _, row in episodes_df.iterrows():
                        ep_idx = int(row["episode_index"])
                        from_ts = float(row.get(f"videos/{cam}/from_timestamp", 0.0))
                        to_ts = float(row.get(f"videos/{cam}/to_timestamp", 0.0))
                        if to_ts > from_ts:
                            ep_str = f"{cam}_episode_{ep_idx:06d}.mp4"
                            out_path = videos_dir / ep_str
                            
                            cmd = [
                                "ffmpeg", "-y", "-i", str(file_mp4),
                                "-ss", str(from_ts), "-to", str(to_ts),
                                "-c", "copy", str(out_path)
                            ]
                            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.remove(file_mp4)
                    
            # Clean up empty camera dirs like videos/CAMERA/chunk-000
            for chunk_dir in cam_dir.glob("chunk-*"):
                import shutil
                shutil.rmtree(chunk_dir, ignore_errors=True)
            import shutil
            shutil.rmtree(cam_dir, ignore_errors=True)
            
        for chunk_dir in data_dir.glob("chunk-*"):
            import shutil
            shutil.rmtree(chunk_dir, ignore_errors=True)

    # Save final info.json
    with open(info_path, "w") as f:
        json.dump(info, f, indent=4, cls=NpEncoder)
