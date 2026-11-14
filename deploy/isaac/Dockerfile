# Source is mounted read-only by the host launcher; no credentials or data in image.
ARG ISAAC_IMAGE=nvcr.io/nvidia/isaac-sim:6.1.0
FROM ${ISAAC_IMAGE} AS runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN /isaac-sim/python.sh -m pip install --no-cache-dir \
    "numpy>=2,<3" "scipy>=1.10" "Pillow>=9.1" "opencv-python-headless>=4.8" \
    "usd-exchange==3.0.1"
# CPU compilation needs the Gaussian schema supplied by this USD distribution.
COPY check_usd.py /opt/simready/check_usd.py
RUN /isaac-sim/python.sh /opt/simready/check_usd.py
WORKDIR /workspace
ENV PYTHONPATH=/workspace/src
ENTRYPOINT ["/isaac-sim/python.sh"]
CMD ["-m", "simready.simulation.environment_check"]
