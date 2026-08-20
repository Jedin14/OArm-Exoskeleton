"""
oarm7dof_fk.py  –  Neural Forward Kinematics for 7DOF-OArm

Computes end-effector pose (xyz + quaternion [xyzw]) for the left and right
arms given 7 joint angles in RADIANS.
Instead of an explicit URDF chain (which requires exact base/tf calibration),
we use a small PyTorch MLP trained on the BeastVLA dataset to perfectly map
joint angles to the exact ee_pose reference frame that the ACT model expects.

State vector layout:
  [0:8]   left  joint positions  (rad)
  [8:16]  right joint positions  (rad)
  [16:23] left  ee pose  [x, y, z, qx, qy, qz, qw]
  [23:30] right ee pose  [x, y, z, qx, qy, qz, qw]
  [30]    left  gripper  (= left_state[7])
  [31]    right gripper  (= right_state[7])
"""

import os
import torch
import torch.nn as nn
import numpy as np

# Define the network architecture
class FKNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(8, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 7)
        )
    def forward(self, x):
        return self.net(x)

# Singleton instances
_model = None
_device = None

def _ensure_loaded():
    global _model, _device
    if _model is not None:
        return
        
    _device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    _model = FKNet().to(_device)
    
    weights_path = os.path.join(os.path.dirname(__file__), "fk_mlp.pth")
    if not os.path.exists(weights_path):
        raise FileNotFoundError(f"Missing neural FK weights: {weights_path}")
        
    _model.load_state_dict(torch.load(weights_path, map_location=_device))
    _model.eval()

def fk(joint_angles_rad, is_right_arm=False) -> tuple[np.ndarray, np.ndarray]:
    """
    Forward kinematics using neural MLP.

    Args:
        joint_angles_rad: 7 floats [j1..j7] in radians
        is_right_arm: bool indicating which arm (determines base transform)

    Returns:
        pos  : (3,) xyz in the dataset world frame
        quat : (4,) [qx, qy, qz, qw] in the dataset world frame
    """
    _ensure_loaded()
    
    # Input is 8 dims: [j1..j7, indicator]
    x = list(joint_angles_rad[:7]) + [1.0 if is_right_arm else 0.0]
    x_tensor = torch.tensor([x], dtype=torch.float32, device=_device)
    
    with torch.no_grad():
        y = _model(x_tensor)[0].cpu().numpy()
        
    pos = y[:3]
    quat = y[3:]
    return pos, quat

def ee_pose_vec(joint_angles_rad, is_right_arm=False) -> np.ndarray:
    """Return [x, y, z, qx, qy, qz, qw] (7 values) for one arm."""
    pos, quat = fk(joint_angles_rad, is_right_arm)
    return np.concatenate([pos, quat])

def build_state(left_state, right_state) -> np.ndarray:
    """
    Build full 32-dim observation state vector.

    Args:
        left_state  : 8 floats [j1..j7, gripper] in radians
        right_state : 8 floats [j1..j7, gripper] in radians

    Returns:
        np.float32 array of shape (32,)
    """
    state = np.zeros(32, dtype=np.float32)
    state[0:8]   = left_state
    state[8:16]  = right_state
    
    # Fill in Neural FK generated ee_poses
    state[16:23] = ee_pose_vec(left_state[:7], is_right_arm=False)
    state[23:30] = ee_pose_vec(right_state[:7], is_right_arm=True)
    
    state[30]    = left_state[7]
    state[31]    = right_state[7]
    return state

if __name__ == "__main__":
    # Validation against dataset
    import pyarrow.parquet as pq
    df = pq.read_table(
        "/home/jed/.cache/huggingface/lerobot/JedEYE14/BeastVLA_v3/"
        "data/chunk-000/file-000.parquet"
    ).to_pandas()

    print("Validating Neural FK against BeastVLA dataset...\n")
    errs = []
    for i in range(0, 500, 25):
        s = np.array(df.iloc[i]["observation.state"])
        computed = build_state(list(s[0:8]), list(s[8:16]))
        pos_err = np.linalg.norm(computed[23:26] - s[23:26])
        errs.append(pos_err)
        print(f"Frame {i:4d}: dataset={[round(float(v),3) for v in s[23:26]]}  "
              f"computed={[round(v,3) for v in computed[23:26]]}  "
              f"err={pos_err*1000:.1f}mm")

    print(f"\nMean position error: {np.mean(errs)*1000:.1f} mm")
    print(f"Max  position error: {np.max(errs)*1000:.1f} mm")
