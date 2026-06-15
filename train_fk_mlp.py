import torch
import torch.nn as nn
import torch.optim as optim
import pyarrow.parquet as pq
import numpy as np

print("Loading dataset...")
df = pq.read_table('/home/jed/.cache/huggingface/lerobot/JedEYE14/BeastVLA_v3/data/chunk-000/file-000.parquet').to_pandas()

# We need Left and Right separate. The kinematic mapping is different because of base frames!
# State: [0:8] left joints, [8:16] right joints, [16:23] left ee, [23:30] right ee
states = np.stack(df['observation.state'].values)

left_j = states[:, 0:7]
right_j = states[:, 8:15]
left_ee = states[:, 16:23]
right_ee = states[:, 23:30]

X = np.vstack([left_j, right_j])
# Add an indicator feature: 0 for left, 1 for right
indicator = np.vstack([np.zeros((len(left_j), 1)), np.ones((len(right_j), 1))])
X = np.hstack([X, indicator])
Y = np.vstack([left_ee, right_ee])

X = torch.tensor(X, dtype=torch.float32).cuda()
Y = torch.tensor(Y, dtype=torch.float32).cuda()

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

model = FKNet().cuda()
optimizer = optim.Adam(model.parameters(), lr=1e-3)
loss_fn = nn.MSELoss()

print("Training MLP FK...")
batch_size = 1024
dataset = torch.utils.data.TensorDataset(X, Y)
loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

for epoch in range(20):
    total_loss = 0
    for bx, by in loader:
        optimizer.zero_grad()
        pred = model(bx)
        loss = loss_fn(pred, by)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    print(f"Epoch {epoch}: Loss = {total_loss/len(loader):.6f}")

# Eval
model.eval()
with torch.no_grad():
    err = torch.abs(model(X) - Y).mean(dim=0)
    print("Mean absolute error per dim:", err.cpu().numpy())

torch.save(model.state_dict(), '/home/jed/openarm_teleop/fk_mlp.pth')
print("Saved to fk_mlp.pth")
