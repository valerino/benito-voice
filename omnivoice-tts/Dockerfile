FROM pytorch/pytorch:2.7.1-cuda11.8-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    OMNIVOICE_LOG_LEVEL=info

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        git \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# PyTorch 2.7.1 is the newest release with official CUDA 11.8 wheels. This is
# required by the host's NVIDIA driver and still satisfies OmniVoice's
# torch>=2.4 requirement. Pin the server so API changes cannot break the stack.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install omnivoice-server==0.2.4

EXPOSE 8880
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8880/health')" || exit 1

CMD ["omnivoice-server", "--host", "0.0.0.0", "--port", "8880", "--profile-dir", "/profiles", "--device", "cuda"]
