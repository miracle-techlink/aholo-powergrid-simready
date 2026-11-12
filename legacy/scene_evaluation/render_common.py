"""Shared camera preparation and evidence utilities for historical renderers."""

import math
from pathlib import Path
import numpy as np
from simready.core.artifacts import (
    digest as sha,
    object_digest as digest,
    write_json as save,
)
from simready.geometry.cameras import look_at_c2w


def prepare_camera(view, document):
    w, h = view.get("resolution", document.get("resolution", [720, 1280]))
    if type(w) is not int or type(h) is not int or w < 1 or h < 1:
        raise ValueError("Positive integer camera dimensions required")
    near, far = view.get("near_far", [0.03, 100.0])
    if not 0 < near < far or not np.isfinite([near, far]).all():
        raise ValueError("Invalid clipping range")
    k = np.asarray(view.get("K", document.get("K")), dtype=float)
    if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ValueError("A finite3x3 pinhole K with positive fx/fy is required")
    if abs(k[0, 1]) > 1e-10 or not np.allclose(k[2], [0, 0, 1]):
        raise ValueError("Skew/nonstandard homogeneous K unsupported")
    if "camera_to_world_cv" in view:
        c2w = np.asarray(view["camera_to_world_cv"], dtype=float)
    else:
        c2w = look_at_c2w(view["eye"], view["target"], view.get("up", [0, 0, 1]))
    if (
        c2w.shape != (4, 4)
        or not np.isfinite(c2w).all()
        or not np.allclose(c2w[3], [0, 0, 0, 1])
    ):
        raise ValueError("Invalid camera_to_world_cv")
    rotation = c2w[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not math.isclose(
        np.linalg.det(rotation), 1, abs_tol=1e-6
    ):
        raise ValueError("Camera rotation must be orthonormal and right-handed")
    return {
        "id": view["id"],
        "resolution": [int(w), int(h)],
        "K": k.tolist(),
        "camera_to_world_cv": c2w.tolist(),
        "world_to_camera_cv": np.linalg.inv(c2w).tolist(),
        "camera_evidence": view.get("camera_evidence", document.get("camera_evidence")),
        "camera_status": "unverified_request",
        "near_far": view.get("near_far", [0.03, 100.0]),
        "projection": "pinhole; no lens distortion",
        "pixel_origin": "top left; K as requested",
    }


def check_cpu_quota():
    line = next(
        x
        for x in Path("/proc/self/cgroup").read_text().splitlines()
        if x.startswith("0::")
    )
    directory = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    observed = []
    while str(directory).startswith("/sys/fs/cgroup"):
        p = directory / "cpu.max"
        if p.exists():
            limit = p.read_text().strip()
            observed.append({"path": str(p), "value": limit})
            if not limit.startswith("max "):
                raise RuntimeError(
                    f"Hard CPU quota found at {p}: {limit}; start without hard quota"
                )
        if directory == Path("/sys/fs/cgroup"):
            break
        directory = directory.parent
    return observed
