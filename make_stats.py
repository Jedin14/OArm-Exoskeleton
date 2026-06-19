import json
import torch
from safetensors.torch import save_file

with open("/home/jed/.cache/huggingface/lerobot/JedEYE14/BeastVLA_v2.1_20260605_143732/meta/stats.json", "r") as f:
    stats = json.load(f)

tensors = {}
for key in ["action", "observation.state"]:
    for stat in ["min", "max", "mean", "std", "count", "q01", "q10", "q50", "q90", "q99"]:
        if key in stats and stat in stats[key]:
            tensor_name = f"{key}.{stat}"
            tensors[tensor_name] = torch.tensor(stats[key][stat], dtype=torch.float32)

save_file(tensors, "/home/jed/openarm_models/pi0.5/policy_preprocessor_step_3_normalizer_processor.safetensors")
print("Saved policy_preprocessor_step_3_normalizer_processor.safetensors")
