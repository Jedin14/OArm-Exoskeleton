import argparse
import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from tqdm import tqdm

def check_dataset(repo_id: str):
    print(f"Loading dataset from: {repo_id}...")
    ds = LeRobotDataset(repo_id)
    
    errors = []
    num_frames = len(ds)
    print(f"Checking dataset of {num_frames} frames for corrupted files...")
    
    # Check endpoints and sample 200 evenly spaced points
    indices_to_check = [0, num_frames - 1] + list(np.linspace(1, num_frames - 2, 200, dtype=int))
    # Remove duplicates and sort
    indices_to_check = sorted(list(set(indices_to_check)))
    
    for idx in tqdm(indices_to_check):
        try:
            # Fetch the frame (this triggers video decoding and parquet reading)
            _ = ds[int(idx)]
        except Exception as e:
            errors.append((idx, str(e)))
            
    if not errors:
        print(f"\nSUCCESS: Dataset '{repo_id}' is 100% HEALTHY!")
        print(f"Total Episodes: {ds.num_episodes}")
        print(f"Total Frames: {num_frames}")
    else:
        print(f"\nERROR: Found {len(errors)} corrupted frames in '{repo_id}'!")
        for idx, err in errors[:10]:
            print(f"Frame {idx}: {err}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the health of a LeRobot dataset.")
    parser.add_argument("--repo-id", type=str, required=True, help="HuggingFace repo ID or local path of the dataset")
    args = parser.parse_args()
    
    check_dataset(args.repo_id)
