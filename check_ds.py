import torchvision.io as io
from pathlib import Path

ds_path = Path("/home/jed/.cache/huggingface/lerobot/JedEYE14/openarm_dataset_20260526_123059/videos")
mp4s = list(ds_path.rglob("*.mp4"))
if mp4s:
    frames, _, _ = io.read_video(str(mp4s[0]), pts_unit="sec", end_pts=1)
    frame = frames[0].numpy()
    print("Channel 0 mean:", frame[:,:,0].mean())
    print("Channel 1 mean:", frame[:,:,1].mean())
    print("Channel 2 mean:", frame[:,:,2].mean())
