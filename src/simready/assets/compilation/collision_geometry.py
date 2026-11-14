"""Fit deterministic support and occupancy proxies from observed points."""

import numpy as np

from simready.geometry.transforms_validation import finite_array


def fit_floor_plane(points, threshold=0.05, iterations=128):
    """Deterministic near-Z RANSAC on low points; returns an estimate or None."""
    points = finite_array(points, "floor points")
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 20:
        return None
    rng = np.random.default_rng(20261113)
    sampled = points[rng.choice(len(points), min(len(points), 10_000), replace=False)]
    candidates = sampled[sampled[:, 2] <= np.percentile(sampled[:, 2], 40)]
    if len(candidates) < 20:
        return None
    best, best_count = None, 0
    for _ in range(iterations):
        triangle = candidates[rng.choice(len(candidates), 3, replace=False)]
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-10:
            continue
        normal /= norm
        if normal[2] < 0:
            normal = -normal
        if normal[2] < np.cos(np.deg2rad(20)):
            continue
        offset = -float(normal @ triangle[0])
        mask = np.abs(candidates @ normal + offset) <= threshold
        count = int(mask.sum())
        if count > best_count:
            best, best_count = mask, count
    if best is None or best_count < max(20, int(len(candidates) * 0.15)):
        return None
    inliers = candidates[best]
    center = inliers.mean(0)
    _, singular, vectors = np.linalg.svd(inliers - center, full_matrices=False)
    if singular[1] < 1e-8:
        return None
    normal = vectors[-1]
    if normal[2] < 0:
        normal = -normal
    if normal[2] < np.cos(np.deg2rad(20)):
        return None
    offset = -float(normal @ center)
    return {
        "normal": normal.tolist(),
        "offset": offset,
        "source": "low_point_ransac",
        "estimate_status": "estimated_not_reviewed",
        "sample_count": len(candidates),
        "inlier_count": best_count,
        "rmse": float(np.sqrt(np.mean((inliers @ normal + offset) ** 2))),
    }


def voxel_proxies(points, voxel_size=0.25, max_proxies=2000, min_points=2):
    """Occupied voxel boxes with a hard output cap; coarsening is reported."""
    if len(points) == 0:
        return [], voxel_size
    origin = points.min(0)
    size = voxel_size
    for _ in range(64):
        keys, counts = np.unique(
            np.floor((points - origin) / size).astype(np.int64),
            axis=0,
            return_counts=True,
        )
        keys = keys[counts >= min_points]
        if len(keys) <= max_proxies:
            return [
                {
                    "min": (origin + key * size).tolist(),
                    "max": (origin + (key + 1) * size).tolist(),
                }
                for key in keys
            ], size
        size *= 2
    raise ValueError("Cannot satisfy collision proxy resource cap")
