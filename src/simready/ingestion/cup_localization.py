"""Known-cup RGB-D localization without reading the simulated object pose.

The support surface and reconstructed object model are configured priors. Depth
proposes regions; text-grounded SAM supplies an independent image mask; robust
surface registration estimates translation. Upright yaw is intentionally not
estimated for this approximately axisymmetric grasp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import label
from scipy.spatial import cKDTree

from simready.core.artifacts import digest


def backproject(depth, calibration):
    k = np.asarray(calibration["intrinsics"], float)
    transform = np.asarray(calibration["camera_to_world"], float)
    if k.shape != (3, 3) or transform.shape != (4, 4):
        raise ValueError("Invalid camera calibration")
    if min(k[0, 0], k[1, 1]) <= 0 or not np.isfinite(k).all():
        raise ValueError("Invalid intrinsics")
    y, x = np.indices(depth.shape)
    safe = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan)
    camera = np.stack(
        [(x - k[0, 2]) * safe / k[0, 0], (y - k[1, 2]) * safe / k[1, 1], safe], -1
    )
    return camera @ transform[:3, :3].T + transform[:3, 3]


def read_observation(path):
    calibration = json.loads((path / "calibration.json").read_text())
    for name in ("image", "depth"):
        suffix = ".png" if name == "image" else ".npy"
        if digest(path / (name + suffix)) != calibration[name + "_sha256"]:
            raise ValueError("Observation hash mismatch")
    depth = np.load(path / "depth.npy", allow_pickle=False)
    image = Image.open(path / "image.png").convert("RGB")
    if (
        image.size != tuple(calibration["image_size"])
        or depth.shape != image.size[::-1]
    ):
        raise ValueError("Observation dimensions disagree")
    return image, depth, calibration, backproject(depth, calibration)


def propose(observation, output, center, radius, top):
    image, depth, calibration, points = read_observation(observation)
    mask = (
        np.isfinite(points).all(-1)
        & (np.linalg.norm(points[:, :, :2] - center, axis=-1) < radius)
        & (points[:, :, 2] > top + 0.015)
        & (points[:, :, 2] < top + 0.17)
    )
    components, _ = label(mask)
    counts = np.bincount(components.ravel())
    candidates = []
    for index in np.flatnonzero(counts[1:] > 100) + 1:
        cloud = points[components == index]
        extent = np.ptp(cloud, axis=0)
        if np.all((extent > [0.035, 0.035, 0.04]) & (extent < [0.13, 0.13, 0.16])):
            candidates.append(int(index))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one size-compatible cup candidate, got {len(candidates)}"
        )
    yy, xx = np.nonzero(components == candidates[0])
    middle = np.array([(xx.min() + xx.max()) / 2, (yy.min() + yy.max()) / 2])
    side = int(max(xx.max() - xx.min(), yy.max() - yy.min()) * 1.8)
    lo = np.maximum(np.floor(middle - side / 2).astype(int), 0)
    hi = np.minimum(lo + side, image.size)
    scale = 5
    output.mkdir(parents=True, exist_ok=False)
    image.crop((*lo, *hi)).resize(tuple((hi - lo) * scale)).save(output / "image.png")
    cropped = depth[lo[1] : hi[1], lo[0] : hi[0]]
    np.save(output / "depth.npy", np.repeat(np.repeat(cropped, scale, 0), scale, 1))
    k = np.asarray(calibration["intrinsics"])
    # Nearest depth resampling preserves the original pixel's center ray.
    k[:2, 2] -= lo
    k[:2] *= scale
    k[:2, 2] += (scale - 1) / 2
    calibration.update(
        intrinsics=k.tolist(),
        image_size=((hi - lo) * scale).tolist(),
        crop_xyxy=[*map(int, lo), *map(int, hi)],
        scale=scale,
        source_observation=str(observation.resolve()),
        proposal_source="connected above-support RGB-D components and size prior",
        support={"center_xy_m": list(center), "radius_m": radius, "top_m": top},
        image_sha256=digest(output / "image.png"),
        depth_sha256=digest(output / "depth.npy"),
    )
    (output / "calibration.json").write_text(json.dumps(calibration, indent=2) + "\n")


def model_points(path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(path))
    cache = UsdGeom.XformCache()
    clouds = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and "collision" not in str(prim.GetPath()).lower():
            pts = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get())
            transform = np.array(cache.GetLocalToWorldTransform(prim)).T
            clouds.append(pts @ transform[:3, :3].T + transform[:3, 3])
    if not clouds:
        raise ValueError("No visible mesh in object model")
    return np.concatenate(clouds)


def fit_translation(observed, model, top):
    if observed.shape[0] < 100 or not np.isfinite(observed).all():
        raise ValueError("Insufficient finite observed points")
    tree = cKDTree(model)
    observed = observed[:: max(1, int(np.ceil(len(observed) / 3000)))]
    seed = np.r_[np.median(observed[:, :2], axis=0), top]

    # Trimmed ICP updates correspondences explicitly. A numerical Jacobian of
    # nearest-neighbor distance is discontinuous and can stall on partial views.
    initial = seed.copy()
    for _ in range(100):
        distances, indices = tree.query(observed - seed)
        keep = distances <= np.percentile(distances, 85)
        delta = np.mean(observed[keep] - seed - model[indices[keep]], axis=0)
        delta[2] = 0  # Cup is known to rest on the configured support plane.
        seed += delta
        if np.linalg.norm(delta) < 1e-7:
            break
    distances = tree.query(observed - seed)[0]
    p95 = float(np.percentile(distances, 95))
    if np.linalg.norm(seed - initial) > 0.06 or p95 > 0.008:
        raise ValueError(f"Model fit rejected, p95 surface error={p95:.4f} m")
    return seed, p95


def localize(observation, segmentation, model, output):
    image, _, calibration, points = read_observation(observation)
    manifest = json.loads((segmentation / "manifest.json").read_text())
    if manifest["source_image"]["sha256"] != calibration["image_sha256"]:
        raise ValueError("Mask and camera image differ")
    candidates = [x for x in manifest["instances"] if x["category"] == "coffee_cup"]
    if len(candidates) != 1:
        raise ValueError("Cup image identity is ambiguous")
    mask_file = segmentation / candidates[0]["mask"]["path"]
    if digest(mask_file) != candidates[0]["mask"]["sha256"]:
        raise ValueError("Mask hash mismatch")
    mask = np.array(Image.open(mask_file)) > 0
    support = calibration["support"]
    top = support["top_m"]
    valid = mask & np.isfinite(points).all(-1) & (points[:, :, 2] > top + 0.01)
    cloud = np.unique(points[valid], axis=0)
    if len(cloud) < 100:
        raise ValueError("Cup segmentation has insufficient valid depth")
    extent = np.ptp(cloud, axis=0)
    if np.any(extent > [0.13, 0.13, 0.16]):
        raise ValueError("Cup mask fails metric size gate")
    position, error = fit_translation(cloud, model_points(model), top)
    if (
        np.linalg.norm(position[:2] - support["center_xy_m"])
        > support["radius_m"] - 0.05
    ):
        raise ValueError("Estimated cup is outside support margin")
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "status": "PASS",
        "cup_bottom_xyz_m": position.tolist(),
        "surface_fit_p95_m": error,
        "observed_points": len(cloud),
        "method": "support-plane RGB-D proposal -> Grounding DINO + SAM -> reconstructed mesh registration",
        "ground_truth_object_pose_read": False,
        "yaw_estimated": False,
        "assumptions": [
            "known upright cup model and scale",
            "known support plane",
            "synthetic RGB-D calibration",
        ],
        "real_world_validated": False,
        "evidence": {
            str(p.resolve()): digest(p)
            for p in [
                observation / "image.png",
                observation / "depth.npy",
                observation / "calibration.json",
                segmentation / "manifest.json",
                mask_file,
                model,
            ]
        },
    }
    (output / "localization.json").write_text(json.dumps(record, indent=2) + "\n")
    overlay = np.array(image)
    overlay[mask] = (overlay[mask] * 0.6 + np.array([0, 255, 120]) * 0.4).astype(
        np.uint8
    )
    image = Image.fromarray(overlay)
    ImageDraw.Draw(image).text(
        (12, 12), f"RGB-D cup fit: p95 {error * 1000:.1f} mm", fill="white"
    )
    image.save(output / "overlay.png")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    proposal = sub.add_parser("propose")
    proposal.add_argument("--center", nargs=2, type=float, default=[0.65, -0.13])
    proposal.add_argument("--radius", type=float, default=0.32)
    proposal.add_argument("--top", type=float, default=0.85)
    fit = sub.add_parser("fit")
    fit.add_argument("--segmentation", type=Path, required=True)
    fit.add_argument("--model", type=Path, required=True)
    for command in (proposal, fit):
        command.add_argument("--observation", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "propose":
        propose(args.observation, args.out, args.center, args.radius, args.top)
    else:
        print(
            json.dumps(
                localize(args.observation, args.segmentation, args.model, args.out),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
