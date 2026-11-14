"""Bounded OpenCV z-depth backprojection in declared world coordinate units."""

import numpy as np

from simready.geometry.cameras import pinhole_intrinsics, rigid_inverse
from simready.geometry.transforms_validation import finite_array


def backproject_depth(
    depth: np.ndarray,
    camera_to_world: np.ndarray,
    intrinsics: np.ndarray,
    *,
    max_points: int = 200_000,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Return world points, flat source pixel indices, x/y and valid pixel count.

    Depth is camera +Z distance, not Euclidean ray distance. Background
    inf/NaN/zero are excluded. Sampling is deterministic over valid raster pixels;
    points retain their original pixel identity. No metric-scale claim is made.
    """
    value = np.asarray(depth)
    if (
        value.ndim != 2
        or value.dtype.kind != "f"
        or not value.size
        or value.size > 16_000_000
    ):
        raise ValueError(
            "Depth must be a floating-point raster of at most 16 megapixels"
        )
    if type(max_points) is not int or not 1 <= max_points <= 1_000_000:
        raise ValueError("max_points must be within 1..1000000")
    matrix = pinhole_intrinsics(intrinsics)
    pose = finite_array(camera_to_world, "camera_to_world", (4, 4))
    rigid_inverse(pose)
    linear = np.flatnonzero(np.isfinite(value) & (value > 0))
    count = len(linear)
    if count > max_points:
        linear = linear[np.linspace(0, count - 1, max_points).astype(np.int64)]
    y, x = np.divmod(linear, value.shape[1])
    rays = np.column_stack(
        [
            (x - matrix[0, 2]) / matrix[0, 0],
            (y - matrix[1, 2]) / matrix[1, 1],
            np.ones(len(x)),
        ]
    )
    local = rays * value[y, x, None]
    world = local @ pose[:3, :3].T + pose[:3, 3]
    if not np.isfinite(world).all() or (np.abs(world) > 1e9).any():
        raise ValueError(
            "Depth backprojection exceeds bounded finite world coordinates"
        )
    return world, linear, x, y, count
