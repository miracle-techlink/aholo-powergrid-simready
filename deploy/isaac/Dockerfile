# NVIDIA images are obtained separately and retain their original license.
ARG ISAAC_IMAGE=nvcr.io/nvidia/isaac-sim:6.1.0
FROM ${ISAAC_IMAGE} AS render
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN /isaac-sim/python.sh -m pip install --no-cache-dir \
    "numpy>=2,<3" "scipy>=1.10" "Pillow>=9.1" "opencv-python-headless>=4.8" "requests>=2.31"
WORKDIR /workspace
ENV PYTHONPATH=/workspace/src
ENTRYPOINT ["/isaac-sim/python.sh"]
CMD ["-m", "simready.simulation.environment_check"]

FROM render AS physics
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ARG ISAACLAB_VERSION=3.0.0
RUN /isaac-sim/python.sh -m pip install --no-cache-dir \
    "isaaclab==${ISAACLAB_VERSION}" "onnxruntime>=1.18"
RUN /isaac-sim/python.sh -m pip install --no-cache-dir joblib
COPY repair_sdk_urllib3.py /opt/simready/repair_sdk_urllib3.py
RUN /isaac-sim/python.sh /opt/simready/repair_sdk_urllib3.py
# The SDK prebundles optional packages without complete pip metadata. Validate
# the actual worker imports and GPU path with doctor and the scene smoke tests.
CMD ["-m", "simready.simulation.environment_check", "--require-lab"]
