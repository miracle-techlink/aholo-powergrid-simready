"""Source-preserving surface completion and conservative static obstacle meshes.

Completion is a simulation hypothesis: unobserved texels are recorded, not
promoted to captured evidence. No image-generation model is used.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def robust_plane(points, axis=2, tolerance=0.035):
    """Fit the selected dependent coordinate with deterministic RANSAC + IRLS."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 30:
        raise ValueError("At least 30 finite 3D points are required")
    if not np.isfinite(points).all() or axis not in (0, 1, 2):
        raise ValueError("Invalid plane inputs")
    other = [i for i in range(3) if i != axis]
    x = np.c_[points[:, other], np.ones(len(points))]
    y = points[:, axis]
    rng = np.random.default_rng(67)
    ids = rng.choice(len(points), min(len(points), 15000), replace=False)
    best, count = None, -1
    for _ in range(100):
        sample = rng.choice(ids, 3, replace=False)
        if abs(np.linalg.det(x[sample])) < 1e-9:
            continue
        model = np.linalg.solve(x[sample], y[sample])
        n = int((abs(x[ids] @ model - y[ids]) < tolerance).sum())
        if n > count:
            best, count = model, n
    if best is None or count < 30:
        raise ValueError("No supported plane")
    for _ in range(5):
        keep = abs(x @ best - y) < tolerance
        best = np.linalg.lstsq(x[keep], y[keep], rcond=None)[0]
    return best, keep


def surface_grid(axis, coefficients, bounds, width=1024, height=1024):
    """Return a rectangular plane grid; image rows run from high v to low v."""
    other = [i for i in range(3) if i != axis]
    u, v = np.meshgrid(
        np.linspace(*bounds[0], width), np.linspace(*bounds[1][::-1], height)
    )
    p = np.empty((height * width, 3), dtype=np.float64)
    p[:, other] = np.c_[u.ravel(), v.ravel()]
    p[:, axis] = np.c_[p[:, other], np.ones(len(p))] @ coefficients
    corners = p.reshape(height, width, 3)[
        [height - 1, height - 1, 0, 0], [0, width - 1, width - 1, 0]
    ]
    return p, corners


def project(points, camera):
    """COLMAP SIMPLE_RADIAL camera, in its registered world coordinate frame."""
    t = np.asarray(camera["world_to_camera"])
    cam = points @ t[:3, :3].T + t[:3, 3]
    xy = cam[:, :2] / np.maximum(cam[:, 2:], 1e-9)
    params = camera["camera_params"]
    k1 = (
        params[-1]
        if camera.get("camera_model", "SIMPLE_RADIAL").endswith("SIMPLE_RADIAL")
        else 0
    )
    xy *= 1 + k1 * np.sum(xy * xy, axis=1)[:, None]
    k = np.asarray(camera["K"])
    return xy @ k[:2, :2].T + k[:2, 2], cam[:, 2]


def bake_observed_texture(points, cloud, cameras, image_root, *, width, height, output):
    """Select source pixels with cloud depth occlusion checks; fill unsupported texels.

    The validity map is saved separately. Nearest valid texture propagation is
    explicit estimated completion, never a new observed texture sample.
    """
    from pathlib import Path

    import cv2
    from PIL import Image

    output = Path(output)
    image_root = Path(image_root)
    rgb = np.zeros((len(points), 3), dtype=np.uint8)
    best = np.zeros(len(points), dtype=np.float64)
    source_index = np.full(len(points), -1, dtype=np.int16)
    records = []
    # Project a coarse sample first; only decode the best covering cameras.
    ranked = []
    for i, camera in enumerate(cameras):
        uv, z = project(points[::32], camera)
        k = np.asarray(camera["K"])
        w, h = int(round(2 * k[0, 2])), int(round(2 * k[1, 2]))
        valid = (
            (z > 0.05)
            & (uv[:, 0] >= 0)
            & (uv[:, 0] < w)
            & (uv[:, 1] >= 0)
            & (uv[:, 1] < h)
        )
        ranked.append((int(valid.sum()), i))
    for _, i in sorted(ranked, reverse=True)[:16]:
        camera = cameras[i]
        image = cv2.imread(str(image_root / camera["image_name"]))
        if image is None:
            raise FileNotFoundError(camera["image_name"])
        ih, iw = image.shape[:2]
        uv, z = project(points, camera)
        valid = (
            (z > 0.05)
            & (uv[:, 0] >= 1)
            & (uv[:, 0] < iw - 1)
            & (uv[:, 1] >= 1)
            & (uv[:, 1] < ih - 1)
        )
        cp, cz = project(cloud, camera)
        cw, ch = (iw + 3) // 4, (ih + 3) // 4
        inside = (
            (cz > 0.05)
            & (cp[:, 0] >= 0)
            & (cp[:, 0] < iw)
            & (cp[:, 1] >= 0)
            & (cp[:, 1] < ih)
        )
        cp = (cp[inside] / 4).astype(int)
        depth = np.full(ch * cw, np.inf)
        np.minimum.at(depth, cp[:, 1] * cw + cp[:, 0], cz[inside])
        depth = ndimage.minimum_filter(depth.reshape(ch, cw), size=3)
        ij = np.clip(uv / 4, [0, 0], [cw - 1, ch - 1]).astype(int)
        observed_depth = depth[ij[:, 1], ij[:, 0]]
        # Reject foreground people/furniture projected onto walls/floor.
        valid &= np.isfinite(observed_depth) & (abs(observed_depth - z) < 0.16)
        central = 1 / (
            1 + ((uv[:, 0] - iw / 2) / iw) ** 2 + ((uv[:, 1] - ih / 2) / ih) ** 2
        )
        score = central / np.maximum(z, 0.1) ** 2
        update = valid & (score > best)
        sampled = cv2.remap(
            image,
            uv[:, 0].astype(np.float32).reshape(height, width),
            uv[:, 1].astype(np.float32).reshape(height, width),
            cv2.INTER_LINEAR,
        ).reshape(-1, 3)
        rgb[update] = sampled[update, ::-1]
        best[update] = score[update]
        source_index[update] = i
        records.append(
            {
                "camera_index": i,
                "image": camera["image_name"],
                "candidate_valid_texels": int(valid.sum()),
            }
        )
    valid = (source_index >= 0).reshape(height, width)
    if valid.mean() < 0.01:
        raise ValueError(
            f"Insufficient source coverage for {output.name}: {valid.mean():.4f}"
        )
    nearest = ndimage.distance_transform_edt(
        ~valid, return_distances=False, return_indices=True
    )
    tex = rgb.reshape(height, width, 3)
    tex[~valid] = tex[tuple(nearest[:, ~valid])]
    Image.fromarray(tex).save(output)
    Image.fromarray(valid.astype(np.uint8) * 255).save(
        output.with_name(output.stem + "_observed.png")
    )
    np.save(
        output.with_name(output.stem + "_source_index.npy"),
        source_index.reshape(height, width),
    )
    return {
        "observed_fraction": float(valid.mean()),
        "estimated_fraction": float(1 - valid.mean()),
        "sources": records,
        "fill_method": "nearest observed texture propagation; unmeasured",
    }


def obstacle_voxels(points, bounds, *, resolution=0.10, minimum_points=5):
    """Conservative occupied cells; no automatic object/material identity claims."""
    low, high = np.asarray(bounds, dtype=float)
    points = np.asarray(points)
    if not 0.03 <= resolution <= 0.5 or np.any(high <= low):
        raise ValueError("Invalid obstacle grid")
    shape = np.ceil((high - low) / resolution).astype(int)
    if np.prod(shape) > 3_000_000:
        raise ValueError("Obstacle grid exceeds size budget")
    inside = ((points >= low) & (points < high)).all(axis=1)
    cells = np.floor((points[inside] - low) / resolution).astype(int)
    count = np.zeros(tuple(shape), dtype=np.int32)
    np.add.at(count, tuple(cells.T), 1)
    occupied = count >= minimum_points
    labels, _ = ndimage.label(occupied)
    sizes = np.bincount(labels.ravel())
    occupied &= sizes[labels] >= 8
    return occupied, low, resolution


def voxel_boundary(occupied, origin, resolution):
    """Emit only exposed voxel faces as a closed, static triangle collision mesh."""
    points, faces = [], []
    corners = [
        [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
        [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
        [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
        [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
        [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
        [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
    ]
    for direction, quad in zip(
        [(-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1)],
        corners,
        strict=True,
    ):
        neighbor = np.roll(occupied, tuple(-v for v in direction), axis=(0, 1, 2))
        axis = np.flatnonzero(direction)[0]
        index = [slice(None)] * 3
        index[axis] = 0 if direction[axis] < 0 else -1
        neighbor[tuple(index)] = False
        cells = np.argwhere(occupied & ~neighbor)
        verts = (cells[:, None, :] + np.asarray(quad)[None]) * resolution + origin
        offset = len(points)
        points.extend(verts.reshape(-1, 3).tolist())
        for i in range(len(cells)):
            a = offset + 4 * i
            faces.extend([(a, a + 1, a + 2), (a, a + 2, a + 3)])
    return np.asarray(points, np.float32), np.asarray(faces, np.int32)
