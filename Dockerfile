FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3.10-venv \
    git \
    cmake \
    build-essential \
    can-utils \
    iproute2 \
    net-tools \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    pybind11-dev \
    ninja-build \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install core ML libraries matching CUDA 12.1
RUN python3.10 -m pip install --upgrade pip setuptools wheel
RUN python3.10 -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
RUN python3.10 -m pip install pybind11 accelerate transformers safetensors numpy==1.26.4 opencv-python pyarrow

# Build and install openarm_can
COPY src/openarm_can /workspace/src/openarm_can
WORKDIR /workspace/src/openarm_can/python
RUN python3.10 -m pip install .

# Clone and install LeRobot
WORKDIR /workspace
RUN git clone https://github.com/huggingface/lerobot.git && \
    cd lerobot && \
    python3.10 -m pip install -e .

# Copy deployment scripts
COPY deploy_pi.py /workspace/deploy_pi.py
COPY openarm_fk.py /workspace/openarm_fk.py
COPY fk_mlp.pth /workspace/fk_mlp.pth

WORKDIR /workspace
ENTRYPOINT ["/bin/bash"]
