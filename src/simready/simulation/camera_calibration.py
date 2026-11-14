"""Exact pinhole metadata for authored render cameras, not real-camera fitting."""

import math
import re
from pathlib import Path
from typing import Any

from PIL import Image

from simready.core.artifacts import digest, object_digest
from simready.geometry.cameras import look_at_c2w
from simready.geometry.transforms_validation import validate_sim3


def rendered_camera_calibration(
    image: str | Path,
    *,
    ply_sha256: str,
    source_to_world: list[list[float]],
    eye: list[float],
    target: list[float],
    focal_length_mm: float,
    horizontal_aperture_mm: float,
    evidence: str,
) -> dict[str, Any]:
    """Bind an undistorted, Z-up render camera to its exact image and point file.

    Assumes zero lens shift and vertical aperture = horizontal aperture * H/W,
    matching scene_video. Pixel centers use integer coordinates (principal point
    (W-1)/2, (H-1)/2). External images require separately established calibration.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", ply_sha256):
        raise ValueError("A source PLY SHA256 is required")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("A calibration evidence description is required")
    for value in (focal_length_mm, horizontal_aperture_mm):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Focal length and aperture must be finite and positive")
    transform = validate_sim3(source_to_world)
    with Image.open(image) as raster:
        width, height = raster.size
    focal_pixels = width * focal_length_mm / horizontal_aperture_mm
    return {
        "schema_version": 1,
        "camera_convention": "opencv_c2w",
        "distortion": "none",
        "image_size": [width, height],
        "intrinsics": [
            [focal_pixels, 0, (width - 1) / 2],
            [0, focal_pixels, (height - 1) / 2],
            [0, 0, 1],
        ],
        "camera_to_world": look_at_c2w(eye, target).tolist(),
        "image_sha256": digest(image),
        "ply_sha256": ply_sha256,
        "source_to_world_sha256": object_digest(transform.tolist()),
        "evidence": evidence,
        "calibration_kind": "authored_render_camera",
        "real_camera_calibrated": False,
    }
