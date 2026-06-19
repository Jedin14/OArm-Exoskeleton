#!/bin/bash

# Configuration variables
IMAGE_NAME="openarm_pi05_deploy:latest"
MODELS_DIR="/home/jed/openarm_models"

echo "Launching PI0.5 Deployment Container..."
echo " - GPU Passthrough Enabled"
echo " - CAN Bus Privileges Enabled"
echo " - Mounting Models from: $MODELS_DIR"

docker run -it --rm \
    --gpus all \
    --network host \
    --privileged \
    -v /dev:/dev \
    -v $MODELS_DIR:/home/jed/openarm_models \
    $IMAGE_NAME \
    python3.10 deploy_pi.py --right --8gb_vram --task "pick a packet from box 1 and place it in box 2" --model "/home/jed/openarm_models/pi0.5"
