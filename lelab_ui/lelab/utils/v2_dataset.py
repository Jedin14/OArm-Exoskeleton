import os
import json
import threading
import queue
from pathlib import Path
import numpy as np
import imageio
import pandas as pd
from lerobot.utils.constants import HF_LEROBOT_HOME
from lerobot.datasets.compute_stats import compute_episode_stats

# NpEncoder handles numpy types for json serialization
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

class VideoWriterThread(threading.Thread):
    def __init__(self, filepath, fps):
        super().__init__()
        self.filepath = filepath
        self.fps = fps
        self.q = queue.Queue()
        self.writer = None
        self.running = True

    def run(self):
        while self.running or not self.q.empty():
            try:
                frame = self.q.get(timeout=0.1)
                if self.writer is None:
                    # Initialize writer lazily
                    self.writer = imageio.get_writer(
                        self.filepath, fps=self.fps, codec='libx264', macro_block_size=None, quality=9
                    )
                self.writer.append_data(frame)
            except queue.Empty:
                pass
        if self.writer is not None:
            self.writer.close()

    def append(self, frame):
        self.q.put(frame)

    def stop(self):
        self.running = False
        self.join()

class DummyMeta:
    def __init__(self, robot_type):
        self.robot_type = robot_type
        self.codebase_version = "v2.1"

class LeRobotDatasetV2:
    def __init__(self, repo_id: str, fps: int, features: dict, root: Path | str | None = None, **kwargs):
        self.repo_id = repo_id
        self.fps = fps
        
        # Add lerobot's DEFAULT_FEATURES so sanity_check_dataset_robot_compatibility passes when resuming
        from lerobot.utils.constants import DEFAULT_FEATURES
        self.features = {**features, **DEFAULT_FEATURES}
        
        self.root = Path(root).expanduser() if root else Path(HF_LEROBOT_HOME).expanduser() / repo_id
        self.use_videos = kwargs.get('use_videos', True)
        
        self.data_dir = self.root / "data"
        self.videos_dir = self.root / "videos"
        self.meta_dir = self.root / "meta"
        
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        
        self.meta = DummyMeta(kwargs.get("robot_type", "Unknown robot"))
        
        self.camera_keys = [k for k, v in features.items() if v.get("dtype") in ["video", "image"]]
        
        self._load_metadata()
        self._write_info()
        self._reset_buffer()
        self.video_writers = {}
        
    def _load_metadata(self):
        self.episodes_metadata = []
        self.episodes_stats = []
        self.tasks = []
        self.total_frames = 0
        
        if (self.meta_dir / "episodes.jsonl").exists():
            with open(self.meta_dir / "episodes.jsonl", "r") as f:
                for line in f:
                    meta = json.loads(line)
                    self.episodes_metadata.append(meta)
                    self.total_frames += meta.get("length", 0)
        
        if (self.meta_dir / "episodes_stats.jsonl").exists():
            with open(self.meta_dir / "episodes_stats.jsonl", "r") as f:
                for line in f:
                    data = json.loads(line)
                    if "stats" in data:
                        self.episodes_stats.append(data["stats"])
                    else:
                        self.episodes_stats.append(data)
                    
        if (self.meta_dir / "tasks.jsonl").exists():
            with open(self.meta_dir / "tasks.jsonl", "r") as f:
                for line in f:
                    self.tasks.append(json.loads(line).get("task", ""))
                    
    def _write_info(self):
        total_episodes = self.num_episodes
        total_videos = total_episodes * len(self.camera_keys) if self.use_videos else 0

        # Build a corrected copy of features:
        # - scalar index-type features get shape []
        # - video features get a video_info block probed from the first available mp4
        import subprocess
        scalar_keys = {"timestamp", "frame_index", "episode_index", "index", "task_index"}
        features_out = {}
        for k, v in self.features.items():
            feat = dict(v)
            if k in scalar_keys:
                feat["shape"] = []
                feat["names"] = None
            elif feat.get("dtype") == "video" and self.use_videos and "video_info" not in feat:
                # Try to probe first available mp4 for this camera key
                chunk_idx = self.num_episodes // 1000 if self.num_episodes > 0 else 0
                vid_dir = self.videos_dir / f"chunk-{chunk_idx:03d}" / k
                mp4s = sorted(vid_dir.glob("episode_*.mp4")) if vid_dir.exists() else []
                if mp4s:
                    try:
                        result = subprocess.run(
                            ["ffprobe", "-v", "quiet", "-print_format", "json",
                             "-show_streams", str(mp4s[0])],
                            capture_output=True, text=True, timeout=10
                        )
                        import json as _json
                        probe = _json.loads(result.stdout)
                        vstream = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), None)
                        if vstream:
                            fps_str = vstream.get("avg_frame_rate", f"{self.fps}/1")
                            num, den = fps_str.split("/")
                            fps_val = round(int(num) / max(int(den), 1), 6)
                            feat["video_info"] = {
                                "video.fps": fps_val,
                                "video.codec": vstream.get("codec_name", "h264"),
                                "video.pix_fmt": vstream.get("pix_fmt", "yuv420p"),
                                "video.is_depth_map": False,
                                "has_audio": False,
                            }
                    except Exception:
                        pass
                if "video_info" not in feat:
                    # Fallback defaults
                    feat["video_info"] = {
                        "video.fps": float(self.fps),
                        "video.codec": "h264",
                        "video.pix_fmt": "yuv420p",
                        "video.is_depth_map": False,
                        "has_audio": False,
                    }
            features_out[k] = feat

        info = {
            "codebase_version": getattr(self.meta, "codebase_version", "v2.1"),
            "fps": self.fps,
            "video": self.use_videos,
            "robot_type": getattr(self.meta, "robot_type", "Unknown robot"),
            "total_episodes": total_episodes,
            "total_frames": self.num_frames,
            "total_tasks": len(self.tasks),
            "total_videos": total_videos,
            "total_chunks": (total_episodes // 1000) + 1 if total_episodes > 0 else 0,
            "chunks_size": 1000,
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
            "features": features_out,
            "splits": {
                "train": f"0:{total_episodes}"
            }
        }
        with open(self.meta_dir / "info.json", "w") as f:
            json.dump(info, f, indent=4)

            
    def _reset_buffer(self):
        self.buffer = {k: [] for k in self.features}
        self.buffer["frame_index"] = []
        self.buffer["timestamp"] = []
        self.buffer["episode_index"] = []
        self.buffer["index"] = []
        self.buffer["task_index"] = []
        self.current_tasks = []
        self.video_writers = {}
        
    @property
    def num_episodes(self):
        return len(self.episodes_metadata)
        
    @property
    def num_frames(self):
        return self.total_frames
        
    def clear_episode_buffer(self):
        for w in self.video_writers.values():
            w.stop()
            if os.path.exists(w.filepath):
                os.remove(w.filepath)
        self._reset_buffer()

    def add_frame(self, frame: dict):
        import torch
        if not self.video_writers and self.use_videos:
            chunk_idx = self.num_episodes // 1000
            for cam in self.camera_keys:
                if cam in frame:
                    vid_path = self.videos_dir / f"chunk-{chunk_idx:03d}" / cam / f"episode_{self.num_episodes:06d}.mp4"
                    vid_path.parent.mkdir(parents=True, exist_ok=True)
                    writer = VideoWriterThread(str(vid_path), self.fps)
                    writer.start()
                    self.video_writers[cam] = writer
                    
        task = frame.pop("task", "")
        if task not in self.tasks:
            self.tasks.append(task)
            with open(self.meta_dir / "tasks.jsonl", "a") as f:
                f.write(json.dumps({"task_index": len(self.tasks)-1, "task": task}) + "\n")
        task_idx = self.tasks.index(task)
        self.current_tasks.append(task)
        
        idx = self.total_frames + len(self.buffer["frame_index"])
        self.buffer["frame_index"].append(len(self.buffer["frame_index"]))
        self.buffer["timestamp"].append(len(self.buffer["frame_index"]) / self.fps)
        self.buffer["episode_index"].append(self.num_episodes)
        self.buffer["index"].append(idx)
        self.buffer["task_index"].append(task_idx)
        
        for k in self.features:
            if k in ["index", "episode_index", "frame_index", "timestamp", "task_index"]:
                continue
                
            val = frame.get(k)
            if isinstance(val, torch.Tensor):
                val = val.detach().cpu().numpy()
            
            if k in self.camera_keys:
                if self.use_videos:
                    # imageio expects HWC and uint8
                    if val.ndim == 3 and val.shape[0] in [1, 3, 4]:
                        val = np.transpose(val, (1, 2, 0))
                    if val.dtype == np.float32 or val.dtype == np.float64:
                        val = (val * 255).astype(np.uint8)
                    self.video_writers[k].append(val)
                self.buffer[k].append(None) # don't write video to parquet in v2.1 natively
            else:
                self.buffer[k].append(val)
                
    def save_episode(self):
        for w in self.video_writers.values():
            w.stop()
            
        length = len(self.buffer["frame_index"])
        if length == 0:
            return
            
        parquet_data = {}
        for k in ["index", "episode_index", "frame_index", "timestamp", "task_index"]:
            parquet_data[k] = self.buffer[k]
        for k in self.features:
            if k in ["index", "episode_index", "frame_index", "timestamp", "task_index"]:
                continue
            if k not in self.camera_keys:
                parquet_data[k] = self.buffer[k]
                
        df = pd.DataFrame(parquet_data)
        chunk_idx = self.num_episodes // 1000
        pq_path = self.data_dir / f"chunk-{chunk_idx:03d}" / f"episode_{self.num_episodes:06d}.parquet"
        pq_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(pq_path, engine='pyarrow', index=False)
        
        # Meta
        tasks_in_ep = list(set(self.current_tasks))
        meta = {
            "episode_index": self.num_episodes,
            "tasks": tasks_in_ep,
            "length": length,
            "episode_chunk": self.num_episodes // 1000
        }
        self.episodes_metadata.append(meta)
        with open(self.meta_dir / "episodes.jsonl", "a") as f:
            f.write(json.dumps(meta) + "\n")
            
        # Stats
        episode_dict = {}
        for k in self.features:
            if k not in self.camera_keys:
                episode_dict[k] = np.stack(self.buffer[k])
        stats = compute_episode_stats(episode_dict, self.features)
        
        self.episodes_stats.append(stats)
        
        with open(self.meta_dir / "episodes_stats.jsonl", "a") as f:
            f.write(json.dumps({"episode_index": self.num_episodes, "stats": stats}, cls=NpEncoder) + "\n")
            
        self.total_frames += length
        self._write_info()
        self._reset_buffer()
        
    def finalize(self):
        if len(self.episodes_stats) > 0:
            from lerobot.datasets.compute_stats import aggregate_stats
            
            def dict_to_numpy(d):
                res = {}
                for k, v in d.items():
                    if isinstance(v, dict):
                        res[k] = dict_to_numpy(v)
                    elif isinstance(v, list):
                        res[k] = np.array(v)
                    else:
                        res[k] = v
                return res
                
            np_stats = [dict_to_numpy(s) for s in self.episodes_stats]
            global_stats = aggregate_stats(np_stats)
            with open(self.meta_dir / "stats.json", "w") as f:
                json.dump(global_stats, f, indent=4, cls=NpEncoder)
        
    def push_to_hub(self, tags=None, private=False):
        pass # Ignored since LeLab pushes it directly via huggingface_hub

    @classmethod
    def create(cls, repo_id, fps, features, root=None, robot_type=None, use_videos=True, **kwargs):
        return cls(repo_id, fps, features, root=root, robot_type=robot_type, use_videos=use_videos, **kwargs)

    @classmethod
    def resume(cls, repo_id, fps=None, features=None, root=None, robot_type=None, use_videos=True, **kwargs):
        from pathlib import Path
        import json
        from lerobot.utils.constants import HF_LEROBOT_HOME
        base = Path(root).expanduser() if root else Path(HF_LEROBOT_HOME).expanduser() / repo_id
        if features is None:
            info_path = base / "meta" / "info.json"
            if info_path.exists():
                with open(info_path, "r") as f:
                    info = json.load(f)
                features = info.get("features", {})
                fps = fps or info.get("fps", 30)
                use_videos = info.get("video", True)
        return cls(repo_id, fps, features, root=root, robot_type=robot_type, use_videos=use_videos, **kwargs)
