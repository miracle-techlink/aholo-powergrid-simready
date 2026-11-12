"""RGB topology detector + calibrated RGB-D localization, without USD IDs.

This deliberately narrow detector recognizes the modeled white-panel family.
It is not a general VLM, OCR system, or electrical-function classifier.
"""

import cv2
import numpy as np


def detect_stop_candidate(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = cv2.split(hsv)
    masks = {
        "red": (((h < 12) | (h > 170)) & (s > 125) & (v > 65)),
        "green": ((h > 35) & (h < 95) & (s > 95) & (v > 45)),
        "yellow": ((h >= 12) & (h < 36) & (s > 130) & (v > 90)),
    }
    blobs = {}
    for color, mask in masks.items():
        count, labels, stats, centers = cv2.connectedComponentsWithStats(
            mask.astype("uint8"), 8
        )
        blobs[color] = []
        for i in range(1, count):
            x, y, w, height, area = stats[i]
            if area < 5 or w < 2 or height < 2 or not 0.25 < w / height < 4:
                continue
            blobs[color].append(
                dict(
                    center=centers[i].tolist(),
                    bbox=[int(x), int(y), int(w), int(height)],
                    area=int(area),
                    label=i,
                )
            )
    candidates = []
    for ring in blobs["yellow"]:
        cx, cy = ring["center"]
        _, _, w, height = ring["bbox"]
        scale = max(w, height)
        if scale < 4:
            continue
        inner = [
            b
            for b in blobs["red"]
            if np.linalg.norm(np.array(b["center"]) - [cx, cy]) < scale * 0.55
        ]
        left = [
            b
            for b in blobs["red"]
            if 0.6 * scale < cx - b["center"][0] < 3.5 * scale
            and abs(b["center"][1] - cy) < 0.65 * scale
        ]
        right = [
            b
            for b in blobs["green"]
            if 0.6 * scale < b["center"][0] - cx < 3.5 * scale
            and abs(b["center"][1] - cy) < 0.65 * scale
        ]
        if not inner or not left or not right:
            continue
        for red in left:
            for green in right:
                dx1 = cx - red["center"][0]
                dx2 = green["center"][0] - cx
                if not 0.45 < dx1 / dx2 < 2.2:
                    continue
                # Require a separate upper row, preventing a single red lamp
                # next to a green lamp from satisfying the panel topology.
                upper = {
                    color: [
                        b
                        for b in blobs[color]
                        if 0.6 * scale < cy - b["center"][1] < 3.2 * scale
                        and abs(b["center"][0] - cx) < 3.5 * scale
                    ]
                    for color in ["red", "green", "yellow"]
                }
                if not all(upper.values()):
                    continue
                score = (
                    abs(np.log(dx1 / dx2))
                    + abs(red["center"][1] - green["center"][1]) / scale
                )
                candidates.append(
                    dict(
                        pixel=red["center"],
                        bbox=red["bbox"],
                        ring=ring["center"],
                        green=green["center"],
                        score=float(score),
                        scale_px=float(scale),
                        classification="red momentary candidate; electrical function unverified",
                    )
                )
    candidates.sort(key=lambda c: c["score"])
    if len(candidates) != 1:
        return dict(
            found=False,
            reason="none_or_ambiguous_panel",
            candidates=candidates,
            blobs=blobs,
        )
    return dict(found=True, **candidates[0], candidates=candidates, blobs=blobs)


def localize_rgbd(detection, depth, world_from_camera, focal_px):
    if not detection["found"]:
        raise ValueError("Unique image detection required")
    height, width = depth.shape
    x, y, w, h = detection["bbox"]
    u, v = detection["pixel"]
    # Use the center of the cap, excluding bezel and depth discontinuities.
    radius = max(1, int(min(w, h) * 0.18))
    px, py = int(round(u)), int(round(v))
    patch = depth[
        max(0, py - radius) : py + radius + 1, max(0, px - radius) : px + radius + 1
    ]
    values = patch[np.isfinite(patch) & (patch > 0.05) & (patch < 4.0)]
    if len(values) < 3:
        raise ValueError("Insufficient measured depth at the detected cap")
    distance = float(np.median(values))
    camera_point = np.array(
        [
            (u - width / 2) / focal_px * distance,
            -(v - height / 2) / focal_px * distance,
            -distance,
            1.0,
        ]
    )
    position = np.asarray(world_from_camera) @ camera_point
    return dict(
        surface_world_m=position[:3].tolist(),
        depth_m=distance,
        pixel=[float(u), float(v)],
        localization="RGB pixel + rendered metric depth + calibrated camera",
        uses_target_prim_pose=False,
        electrical_function_verified=False,
    )
