#!/bin/bash

IMAGE_NAME="openarm_pi05_deploy:latest"

echo "Building Docker container: $IMAGE_NAME"
docker build -t $IMAGE_NAME .
echo "Build complete! You can now run the container using ./run_container.sh"
