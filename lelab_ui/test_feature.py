from lerobot.utils.feature_utils import hw_to_dataset_features
features = {f"joint_{i}.pos": float for i in range(16)}
for i in range(14): features[f"ee_pose_{i}.pos"] = float
for i in range(2): features[f"gripper_state_{i}.pos"] = float
dataset_features = hw_to_dataset_features(features, "observation", use_video=True)
print(dataset_features["observation.state"]["shape"])
print(dataset_features["observation.state"]["names"])
