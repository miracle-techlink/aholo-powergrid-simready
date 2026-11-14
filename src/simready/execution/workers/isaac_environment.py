"""SDK process environment, without importing CUDA or altering the parent."""

import os
import platform
from pathlib import Path


def isaac_environment():
    env = os.environ.copy()
    env.setdefault("PYTORCH_JIT", "0")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("MKL_NUM_THREADS", "4")
    env["PYTHONUNBUFFERED"] = "1"
    if platform.machine() in ("aarch64", "arm64"):
        library = Path("/lib/aarch64-linux-gnu/libgomp.so.1")
        if library.is_file() and str(library) not in env.get("LD_PRELOAD", "").split(
            ":"
        ):
            env["LD_PRELOAD"] = ":".join(
                filter(None, (env.get("LD_PRELOAD"), str(library)))
            )
    return env
