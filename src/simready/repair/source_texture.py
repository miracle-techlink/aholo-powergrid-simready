"""Coherent sharp-source atlases, extracted from the substation detail repair.

Calibrated projection supplies registration here; the original cabinet workflow
used planar SIFT/MAGSAC. Held-out cameras cannot contribute texture pixels.
"""

from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

from simready.core.artifacts import digest, write_json
from simready.repair.structural_completion import project


def sharpness_score(image, valid):
    """The substation rank: absolute Laplacian detail times sqrt coverage."""
    valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if valid.sum() < 64:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    detail = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    return float(detail[valid].mean() * np.sqrt(valid.mean()))


def compose_sources(entries, fallback, feather_px=4):
    """One coherent source owns interiors; only visibility boundaries blend."""
    merged = np.asarray(fallback, dtype=np.float32).copy()
    owner = np.full(merged.shape[:2], -1, np.int16)
    observed = np.zeros(merged.shape[:2], bool)
    for entry in sorted(entries, key=lambda x: (x["score"], x["camera_index"])):
        valid = cv2.erode(entry["valid"].astype(np.uint8), np.ones((3, 3), np.uint8))
        distance = cv2.distanceTransform(valid, cv2.DIST_L2, 3)
        alpha = np.clip(distance / feather_px, 0, 1)
        merged = merged * (1 - alpha[..., None]) + entry["image"] * alpha[..., None]
        owner[alpha > 0.99] = entry["camera_index"]
        observed |= alpha > 0.99
    return np.uint8(np.clip(merged, 0, 255)), owner, observed


def align_planar_sources(entries):
    """Refine calibrated warps with the substation SIFT/MAGSAC acceptance gate."""
    if not entries:
        return []
    base = max(entries, key=lambda e: e["score"] * np.sqrt(e["valid"].mean()))
    h, w = base["valid"].shape
    scale = min(1.0, 768 / max(h, w))
    sift = cv2.SIFT_create(nfeatures=3500)

    def features(entry):
        gray = cv2.cvtColor(
            cv2.resize(entry["image"], None, fx=scale, fy=scale), cv2.COLOR_RGB2GRAY
        )
        mask = cv2.resize(
            entry["valid"].astype(np.uint8) * 255,
            (gray.shape[1], gray.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
        return sift.detectAndCompute(gray, mask)

    target_kp, target_desc = features(base)
    records = []
    for entry in entries:
        report = {
            "camera_index": entry["camera_index"],
            "anchor_index": base["camera_index"],
            "refined": False,
        }
        if entry is base or target_desc is None:
            records.append(report)
            continue
        kp, desc = features(entry)
        if desc is None:
            records.append(report)
            continue
        matches = cv2.BFMatcher().knnMatch(desc, target_desc, k=2)
        matches = [
            a
            for pair in matches
            if len(pair) == 2
            for a, b in [pair]
            if a.distance < 0.70 * b.distance
        ]
        if len(matches) >= 25:
            src = np.float32([kp[m.queryIdx].pt for m in matches]) / scale
            dst = np.float32([target_kp[m.trainIdx].pt for m in matches]) / scale
            matrix, inliers = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 2.5 / scale)
            if matrix is not None:
                keep = inliers.ravel().astype(bool)
                prediction = cv2.perspectiveTransform(src[:, None], matrix)[:, 0]
                error = float(
                    np.median(np.linalg.norm(prediction[keep] - dst[keep], axis=1))
                )
                corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
                shift = np.linalg.norm(
                    cv2.perspectiveTransform(corners[:, None], matrix)[:, 0] - corners,
                    axis=1,
                ).max()
                if (
                    keep.sum() >= 25
                    and error <= 2.5 / scale
                    and shift < 0.12 * np.hypot(w, h)
                ):
                    entry["image"] = cv2.warpPerspective(
                        entry["image"], matrix, (w, h), flags=cv2.INTER_CUBIC
                    )
                    entry["valid"] = (
                        cv2.warpPerspective(
                            entry["valid"].astype(np.uint8),
                            matrix,
                            (w, h),
                            flags=cv2.INTER_NEAREST,
                        )
                        > 0
                    )
                    report.update(
                        refined=True,
                        inliers=int(keep.sum()),
                        median_error_px=error,
                        atlas_homography=matrix.tolist(),
                    )
        records.append(report)
    return records


class SourceTextureBaker:
    """Reuse calibrated cameras and cached sparse depths across scene surfaces."""

    def __init__(self, cameras, images, cloud_sfm, *, heldout_stride=7):
        self.cameras = cameras
        self.images = Path(images)
        self.cloud = np.asarray(cloud_sfm)
        self.heldout = {i for i in range(len(cameras)) if i % heldout_stride == 0}
        self.cache = {}

    def image_depth(self, index):
        if index in self.cache:
            return self.cache[index]
        camera = self.cameras[index]
        path = self.images / camera["image_name"]
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(path)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width = image.shape[:2]
        uv, z = project(self.cloud, camera)
        inside = (
            (z > 0.05)
            & (uv[:, 0] >= 0)
            & (uv[:, 0] < width)
            & (uv[:, 1] >= 0)
            & (uv[:, 1] < height)
        )
        ij = (uv[inside] / 4).astype(int)
        depth = np.full(((height + 3) // 4, (width + 3) // 4), np.inf)
        np.minimum.at(depth, (ij[:, 1], ij[:, 0]), z[inside])
        # Isolated foreground Gaussians are reconstruction noise, not a solid
        # occluder. A neighborhood median avoids expanding one stray point
        # into a 12-pixel rectangular hole in an otherwise observed wall.
        depth = ndimage.median_filter(depth, size=3)
        value = image, depth
        self.cache[index] = value
        return value

    def sample(self, points, index, shape, depth_tolerance):
        image, depth = self.image_depth(index)
        height, width = image.shape[:2]
        uv, z = project(points, self.cameras[index])
        valid = (
            (z > 0.05)
            & (uv[:, 0] >= 4)
            & (uv[:, 0] < width - 4)
            & (uv[:, 1] >= 4)
            & (uv[:, 1] < height - 4)
        )
        ij = np.clip(uv / 4, [0, 0], [depth.shape[1] - 1, depth.shape[0] - 1]).astype(
            int
        )
        nearest = depth[ij[:, 1], ij[:, 0]]
        # A sparse depth map is an occluder test, not a completeness test.
        # Missing points or points behind the modeled surface do not invalidate
        # an actual source pixel. Foreground observations still exclude it.
        valid &= ~np.isfinite(nearest) | (nearest >= z - depth_tolerance)
        # Reject grazing-angle projection; these pixels turn a distant image
        # edge into long streaks even when they pass a field-of-view check.
        grid = np.asarray(points).reshape(*shape, 3)
        normal = np.cross(grid[0, -1] - grid[0, 0], grid[-1, 0] - grid[0, 0])
        normal /= max(np.linalg.norm(normal), 1e-12)
        center = np.asarray(self.cameras[index]["camera_to_world"])[:3, 3]
        direction = center - points
        incidence = abs(direction @ normal) / np.maximum(
            np.linalg.norm(direction, axis=1), 1e-9
        )
        valid &= incidence > 0.22
        warped = cv2.remap(
            image,
            uv[:, 0].astype(np.float32).reshape(shape),
            uv[:, 1].astype(np.float32).reshape(shape),
            cv2.INTER_CUBIC,
        )
        return warped, valid.reshape(shape)

    def bake(
        self,
        points,
        shape,
        output,
        *,
        fallback_srgb=(130, 120, 105),
        depth_tolerance=0.12,
        max_sources=16,
        allowed_times=None,
    ):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        height, width = shape
        grid = np.asarray(points).reshape(height, width, 3)
        coarse = grid[:: max(1, height // 128), :: max(1, width // 128)]
        ranked = []
        for i, camera in enumerate(self.cameras):
            if i in self.heldout:
                continue
            if (
                allowed_times
                and not allowed_times[0] <= camera["timestamp_s"] <= allowed_times[1]
            ):
                continue
            uv, z = project(coarse.reshape(-1, 3), camera)
            iw, ih = camera["resolution"]
            visible = (
                (z > 0.1)
                & (uv[:, 0] > 5)
                & (uv[:, 0] < iw - 5)
                & (uv[:, 1] > 5)
                & (uv[:, 1] < ih - 5)
            )
            if visible.mean() > 0.04:
                ranked.append(
                    (float(visible.mean() / np.maximum(np.median(z[visible]), 0.1)), i)
                )
        # First choose support, then use the accepted-view sharpness objective.
        candidates = []
        for _, i in sorted(ranked, reverse=True)[:64]:
            sample, valid = self.sample(
                coarse.reshape(-1, 3), i, coarse.shape[:2], depth_tolerance
            )
            score = sharpness_score(sample, valid)
            if score > 0:
                candidates.append((score, i, valid))
        # Keep coverage contributors as well as sharp close views. Ranking
        # sharpness alone can select many nearly identical partial crops.
        chosen = []
        covered = np.zeros(coarse.shape[:2], bool)
        while candidates and len(chosen) < max_sources:
            best = max(
                range(len(candidates)),
                key=lambda k: (
                    candidates[k][0] * (0.15 + (candidates[k][2] & ~covered).mean())
                ),
            )
            score, i, valid = candidates.pop(best)
            chosen.append((score, i))
            covered |= valid
        entries = []
        for score, i in chosen:
            image, valid = self.sample(grid.reshape(-1, 3), i, shape, depth_tolerance)
            entries.append(
                {"camera_index": i, "score": score, "image": image, "valid": valid}
            )
        registration = align_planar_sources(entries)
        fallback = np.empty((height, width, 3), np.uint8)
        fallback[:] = fallback_srgb
        merged, owner, observed = compose_sources(entries, fallback)
        # Unsupported samples stay material-colored; never stretch nearest image
        # pixels across an unseen region or report them as source observations.
        cv2.imwrite(str(output), cv2.cvtColor(merged, cv2.COLOR_RGB2BGR))
        cv2.imwrite(
            str(output.with_name(output.stem + "_observed.png")),
            observed.astype(np.uint8) * 255,
        )
        np.save(output.with_name(output.stem + "_source_index.npy"), owner)
        record = {
            "method": "substation coherent sharp-source selection adapted to calibrated 3D surfaces",
            "visibility": "projected source pixels with foreground-depth rejection; sparse missing depths are not occluders; registration is estimated",
            "heldout_indices": sorted(self.heldout),
            "heldout_used_for_selection": False,
            "observed_fraction": float(observed.mean()),
            "estimated_fraction": float(1 - observed.mean()),
            "fallback": {
                "method": "explicit uniform material color",
                "srgb_8bit": list(fallback_srgb),
            },
            "texture_sha256": digest(output),
            "feather_pixels": 4,
            "planar_refinement": registration,
            "sources": [
                {
                    "camera_index": e["camera_index"],
                    "score": e["score"],
                    "image": self.cameras[e["camera_index"]]["image_name"],
                    "timestamp_s": self.cameras[e["camera_index"]]["timestamp_s"],
                    "source_sha256": digest(
                        self.images / self.cameras[e["camera_index"]]["image_name"]
                    ),
                }
                for e in entries
            ],
        }
        write_json(output.with_suffix(".json"), record)
        return record
