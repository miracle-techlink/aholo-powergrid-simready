"""Lift source-bound semantic masks to conservative, static 3D proxies.

This is an explicit calibrated single-view adapter, not camera registration or
closed-surface reconstruction. A nearest-center z-buffer rejects occluded PLY
points; Gaussian transparency and splat footprints are not modeled. The original
point indices are preserved so a later multi-view estimator can replace it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.geometry.cameras import rigid_inverse
from simready.simulation.category_priors import PRIOR_VERSION, category_priors
from simready.simulation.depth_projection import backproject_depth
from simready.simulation.gaussian_asset import (
    decode_vertices,
    finite_array,
    read_ply,
    validate_sim3,
)
from simready.simulation.physical_spec import normalize_physics


def visible_point_pixels(
    points_world: np.ndarray,
    camera_to_world: np.ndarray,
    intrinsics: np.ndarray,
    image_size: tuple[int, int],
    *,
    relative_depth_tolerance: float = 0.01,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return source indices and integer pixel x/y of front-visible centers.

    Calibration uses OpenCV right/down/forward axes, integer pixel centers and
    column-vector poses; image_size is (width, height). Only an undistorted pinhole
    camera is supported. O(N + width*height) time and memory, without N-by-pixel
    matrices. The tolerance is dimensionless and works with unverified scales.
    """
    points = finite_array(points_world, "points_world")
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_world must be an N x 3 array")
    if len(image_size) != 2 or any(type(v) is not int or v < 1 for v in image_size):
        raise ValueError("image_size must contain positive integer width and height")
    width, height = image_size
    if width * height > 16_000_000:
        raise ValueError("Projection exceeds the 16 megapixel limit")
    if (
        not np.isfinite(relative_depth_tolerance)
        or not 0 <= relative_depth_tolerance <= 0.1
    ):
        raise ValueError("relative_depth_tolerance must be in 0..0.1")
    matrix = finite_array(intrinsics, "intrinsics", (3, 3))
    if (
        not np.allclose(matrix[2], [0, 0, 1])
        or matrix[0, 1] != 0
        or matrix[1, 0] != 0
        or matrix[0, 0] <= 0
        or matrix[1, 1] <= 0
    ):
        raise ValueError("intrinsics must be an unskewed pinhole matrix")
    pose = finite_array(camera_to_world, "camera_to_world", (4, 4))
    world_to_camera = rigid_inverse(pose)
    camera = points @ world_to_camera[:3, :3].T + world_to_camera[:3, 3]
    index = np.flatnonzero(camera[:, 2] > 1e-8)
    projected = camera[index] @ matrix.T
    coordinates = projected[:, :2] / projected[:, 2, None]
    # Filter before casting so extreme but finite inputs cannot overflow int64.
    inside = (
        (coordinates[:, 0] >= -0.5)
        & (coordinates[:, 0] < width - 0.5)
        & (coordinates[:, 1] >= -0.5)
        & (coordinates[:, 1] < height - 0.5)
    )
    index = index[inside]
    pixels = np.floor(coordinates[inside] + 0.5).astype(np.int64)
    linear = pixels[:, 1] * width + pixels[:, 0]
    depth = camera[index, 2]
    nearest = np.full(width * height, np.inf)
    np.minimum.at(nearest, linear, depth)
    visible = depth <= nearest[linear] * (1 + relative_depth_tolerance)
    return index[visible], pixels[visible, 0], pixels[visible, 1]


def _evidence_file(base: Path, record: dict[str, Any], name: str) -> Path:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise ValueError(f"Missing {name} path")
    path = (base / record["path"]).resolve(strict=True)
    if digest(path) != record.get("sha256"):
        raise ValueError(f"{name} hash mismatch")
    return path


def _declared_bounds(obj: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Protect existing explicit assets, especially a button's travel volume."""
    if obj.get("type") == "button":
        center = finite_array(obj.get("position_world"), "button position", (3,))
        axis = obj.get("axis", "Z")
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("Button axis must be X/Y/Z")
        extent = np.full(3, float(obj.get("radius", 0.02)) * 1.5)
        extent["XYZ".index(axis)] = float(obj.get("travel", 0.004)) + 2 * float(
            obj.get("cap_height", 0.01)
        )
        return center - extent, center + extent
    bounds = obj.get("bounds_world", {})
    return finite_array(bounds.get("min"), "declared bounds min", (3,)), finite_array(
        bounds.get("max"), "declared bounds max", (3,)
    )


def _intersection(
    low: np.ndarray, high: np.ndarray, other: tuple[np.ndarray, np.ndarray]
) -> float:
    return float(
        np.maximum(0, np.minimum(high, other[1]) - np.maximum(low, other[0])).prod()
    )


def build_semantic_spec(
    ply: str | Path,
    segmentation_manifest: str | Path,
    calibration: str | Path,
    output_dir: str | Path,
    *,
    scene_spec: str | Path | None = None,
    priors_file: str | Path | None = None,
    min_points: int = 16,
    min_score: float = 0.25,
) -> dict[str, Any]:
    """Write scene_spec.json and projection evidence from one calibrated view.

    Calibration must bind image/PLY hashes and the exact source-to-world matrix.
    Low coverage and dominant-class warnings are blocking review flags, not a
    claim of semantic correctness. Output proxies remain invisible and static.
    Existing declared scene objects (including buttons) retain their parameters.
    """
    if type(min_points) is not int or min_points < 1:
        raise ValueError("min_points must be a positive integer")
    if (
        type(min_score) not in (int, float)
        or not np.isfinite(min_score)
        or not 0 <= min_score <= 1
    ):
        raise ValueError("min_score must be finite within 0..1")
    ply, manifest_path, camera_path = (
        Path(path).resolve(strict=True)
        for path in (ply, segmentation_manifest, calibration)
    )
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Projection output already exists: {out}")
    frozen = {path: digest(path) for path in (ply, manifest_path, camera_path)}
    for optional in (scene_spec, priors_file):
        if optional is not None:
            path = Path(optional).resolve(strict=True)
            frozen[path] = digest(path)
    spec = read_json(scene_spec) if scene_spec is not None else {"schema_version": 1}
    if not isinstance(spec, dict) or spec.get("schema_version", 1) != 1:
        raise ValueError("Scene spec schema_version must be 1")
    transform = validate_sim3(spec.get("source_to_world", np.eye(4)))
    # This workflow defaults to observed semantic proxies only. Existing
    # explicitly requested occupancy is retained, with overlapping floor review.
    collision = dict(spec.get("collision", {}))
    collision.setdefault("enabled", False)
    spec["collision"] = collision
    manifest, camera = read_json(manifest_path), read_json(camera_path)
    if manifest.get("schema_version") != 1 or camera.get("schema_version") != 1:
        raise ValueError("Segmentation and calibration schema_version must be 1")
    if (
        camera.get("camera_convention") != "opencv_c2w"
        or camera.get("distortion") != "none"
    ):
        raise ValueError("Explicit opencv_c2w convention and distortion=none required")
    if not isinstance(camera.get("evidence"), str) or not camera["evidence"].strip():
        raise ValueError("Calibration requires a nonempty evidence description")
    source = manifest.get("source_image", {})
    image_path = _evidence_file(manifest_path.parent, source, "Source image")
    frozen[image_path] = source["sha256"]
    with Image.open(image_path) as image:
        width, height = image.size
    if [width, height] != camera.get("image_size") or (width, height) != (
        source.get("width"),
        source.get("height"),
    ):
        raise ValueError("Calibration and segmentation image dimensions differ")
    source_sha = frozen[ply]
    if (
        camera.get("image_sha256") != source["sha256"]
        or camera.get("ply_sha256") != source_sha
    ):
        raise ValueError("Calibration image/PLY hash mismatch")
    if camera.get("source_to_world_sha256") != object_digest(transform.tolist()):
        raise ValueError("Calibration source_to_world hash mismatch")
    if (
        not isinstance(manifest.get("instances"), list)
        or len(manifest["instances"]) > 1024
    ):
        raise ValueError("Segmentation instances must contain at most 1024 regions")
    priors = category_priors() if priors_file is None else read_json(priors_file)
    if not isinstance(priors, dict):
        raise ValueError(
            "Category priors must map category names to physics declarations"
        )
    for category, prior in priors.items():
        if not isinstance(category, str) or not category:
            raise ValueError("Prior categories must be nonempty strings")
        normalized = normalize_physics(
            prior, geometry_type="box", fallback_contact={}, metric_scale_declared=False
        )
        if normalized["motion_type"] != "static" or any(
            key in normalized
            for key in ("mass_kg", "inertia_kg_m2", "center_of_mass_m")
        ):
            raise ValueError(
                "Automatic priors must be static and cannot infer mass or inertia"
            )
    geometry_source, index_space = "visible_ply_centers", "ply_vertex_index"
    if "depth" in camera:
        depth_record = camera["depth"]
        if not isinstance(depth_record, dict):
            raise ValueError("Depth evidence must be a mapping")
        if (
            depth_record.get("kind") != "camera_z"
            or depth_record.get("unit") != "world_units"
        ):
            raise ValueError(
                "Depth must declare camera_z in world_units; ray distances are not interchangeable"
            )
        depth_path = _evidence_file(camera_path.parent, depth_record, "Depth")
        frozen[depth_path] = depth_record["sha256"]
        depth = np.load(depth_path, allow_pickle=False, mmap_mode="r")
        if depth.shape != (height, width):
            raise ValueError("Depth dimensions must match source image")
        world, source_indices, px, py, source_point_count = backproject_depth(
            depth,
            np.asarray(camera.get("camera_to_world")),
            np.asarray(camera.get("intrinsics")),
        )
        indices = np.arange(len(world))
        geometry_source, index_space = (
            "registered_camera_z_depth",
            "depth_raster_linear_index",
        )
    else:
        data = decode_vertices(read_ply(ply))
        world = (
            data["points"].astype(np.float64) @ transform[:3, :3].T + transform[:3, 3]
        )
        source_point_count = len(world)
        source_indices = np.arange(len(world))
        usable = source_indices
        if data["kind"] == "gaussian_splats":
            usable = usable[data["opacities"] >= 0.1]
        indices, px, py = visible_point_pixels(
            world[usable],
            np.asarray(camera.get("camera_to_world")),
            np.asarray(camera.get("intrinsics")),
            (width, height),
        )
        indices = usable[indices]
    # Masks may not overlap: ambiguity must be resolved upstream, not by order.
    occupied = np.zeros((height, width), dtype=bool)
    if not isinstance(spec.get("objects", []), list):
        raise ValueError("Scene objects must be a list")
    objects = list(spec.get("objects", []))
    protected = [(obj["id"], _declared_bounds(obj)) for obj in objects]
    generated_bounds: list[tuple[str, tuple[np.ndarray, np.ndarray]]] = []
    names = {obj["id"] for obj in objects}
    accepted, skipped, assignment, masks, seen = [], [], {}, [], set()
    for row in manifest["instances"]:
        name, category = row.get("asset_id"), row.get("category")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,95}", name
        ):
            raise ValueError("Invalid semantic asset_id")
        if name in seen:
            raise ValueError("Duplicate semantic asset_id")
        seen.add(name)
        if not isinstance(category, str) or not category:
            raise ValueError("Every semantic region needs a category")
        mask_path = _evidence_file(manifest_path.parent, row.get("mask"), "Mask")
        frozen[mask_path] = row["mask"]["sha256"]
        with Image.open(mask_path) as encoded:
            mask_values = np.asarray(encoded)
        if (
            mask_values.shape != (height, width)
            or not np.isin(mask_values, [0, 255]).all()
        ):
            raise ValueError(
                "Masks must be binary 0/255 and match source image dimensions"
            )
        mask = mask_values == 255
        if np.any(occupied & mask):
            raise ValueError("Overlapping semantic masks are ambiguous")
        occupied |= mask
        area = int(mask.sum())
        if area != row.get("area_px") or area == 0:
            raise ValueError("Mask area disagrees with segmentation manifest")
        masks.append({"asset_id": name, "category": category, "area_px": area})
        reason = None
        score = row.get("score")
        if score is not None and (
            type(score) not in (int, float)
            or not np.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError(
                "Semantic score must be null or a finite number within 0..1"
            )
        point_ids = indices[mask[py, px]]
        if category not in priors:
            reason = "category_has_no_physical_prior"
        elif score is not None and score < min_score:
            reason = "low_segmentation_score"
        elif len(point_ids) < min_points:
            reason = "insufficient_visible_3d_support"
        if reason:
            skipped.append(
                {
                    "asset_id": name,
                    "category": category,
                    "reason": reason,
                    "point_count": len(point_ids),
                    "score": score,
                }
            )
            continue
        object_id = "semantic_" + name.replace("-", "_")
        if object_id in names:
            raise ValueError(
                "Semantic object id conflicts with an existing scene object"
            )
        names.add(object_id)
        points = world[point_ids]
        # Robust visible-surface bounds are not full-object dimensions. A minimum
        # relative thickness makes planar masks representable as static boxes.
        low, high = np.quantile(points, [0.02, 0.98], axis=0)
        span = float(np.linalg.norm(high - low))
        padding = max(span * 0.002, 1e-5)
        low, high = low - padding, high + padding
        # The semantic box is only a surface approximation. It must not block
        # pre-existing controls or silently introduce intersecting rigid bodies.
        conflict = next(
            (key for key, box in protected if _intersection(low, high, box) > 0), None
        )
        reason = "overlaps_declared_asset" if conflict else None
        if not reason:
            volume = float(np.prod(high - low))
            for key, box in generated_bounds:
                other_volume = float(np.prod(box[1] - box[0]))
                if _intersection(low, high, box) / min(volume, other_volume) > 0.25:
                    reason, conflict = "overlaps_semantic_proxy", key
                    break
        if reason:
            skipped.append(
                {
                    "asset_id": name,
                    "category": category,
                    "reason": reason,
                    "conflicting_object": conflict,
                    "point_count": len(point_ids),
                }
            )
            continue
        generated_bounds.append((object_id, (low, high)))
        objects.append(
            {
                "id": object_id,
                "type": "box",
                "category": category,
                "visible": False,
                "bounds_world": {"min": low.tolist(), "max": high.tolist()},
                "evidence": f"{name}; mask sha256={row['mask']['sha256']}; {geometry_source}, unvalidated surface AABB",
                "physics": priors[category],
            }
        )
        assignment[object_id] = source_indices[point_ids]
        accepted.append(
            {
                "asset_id": name,
                "object_id": object_id,
                "category": category,
                "point_count": len(point_ids),
                "area_px": area,
                "ambiguous": bool(row.get("ambiguous", False)),
                "mask_sha256": row["mask"]["sha256"],
                "point_indices_key": object_id,
                "bounds_world": objects[-1]["bounds_world"],
                "geometry_status": "unvalidated_observed_surface_proxy",
                "score": score,
            }
        )
    if len(objects) > 256:
        raise ValueError(
            "Combined scene has more than 256 objects; filter regions explicitly"
        )
    # No semantics are inferred from successful USD authoring. These checks only
    # reject obvious insufficient observations; they cannot establish accuracy.
    by_category: dict[str, int] = {}
    for row in masks:
        by_category[row["category"]] = (
            by_category.get(row["category"], 0) + row["area_px"]
        )
    dominant_fraction = max(by_category.values(), default=0) / (width * height)
    review = []
    if manifest.get("disambiguation", {}).get("semantic_review_required", False):
        review.append("semantic_review_requested_by_model")
    if not accepted:
        review.append("no_supported_3d_asset")
    if dominant_fraction > 0.95:
        review.append("single_category_dominates_more_than_95_percent")
    if not any(
        row["category"] not in {"wall", "floor", "ceiling", "unknown"}
        for row in accepted
    ):
        review.append("no_supported_nonstructural_asset")
    accepted_area = sum(row["area_px"] for row in accepted)
    assigned_centers = sum(row["point_count"] for row in accepted)
    if assigned_centers / max(1, len(indices)) < 0.1:
        review.append("less_than_10_percent_visible_centers_assigned")
    if accepted_area / (width * height) < 0.01:
        review.append("less_than_1_percent_image_has_supported_assets")
    if any(row["reason"].startswith("overlaps_") for row in skipped):
        review.append("proxy_intersection_requires_geometry_repair")
    if any(row["ambiguous"] for row in accepted):
        review.append("ambiguous_semantic_masks")
    if collision["enabled"] and any(row["category"] == "floor" for row in accepted):
        review.append("background_floor_may_overlap_semantic_floor")
    if any(
        row.get("ambiguous", False) for row in manifest.get("rejected_candidates", [])
    ):
        review.append("ambiguous_candidates_rejected_upstream")
    for path, expected_hash in frozen.items():
        if digest(path) != expected_hash:
            raise ValueError(f"Input evidence changed during projection: {path.name}")
    out.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(out / "point_assignments.npz", **assignment)
    spec["objects"] = objects
    write_json(out / "scene_spec.json", spec)
    result = {
        "schema_version": 1,
        "status": "REVIEW_REQUIRED" if review else "CANDIDATE_READY",
        "inputs": {
            "ply_sha256": source_sha,
            "segmentation_sha256": frozen[manifest_path],
            "calibration_sha256": frozen[camera_path],
            "source_image_sha256": source["sha256"],
            "base_scene_spec_sha256": frozen[Path(scene_spec).resolve()]
            if scene_spec
            else None,
            "priors_file_sha256": frozen[Path(priors_file).resolve()]
            if priors_file
            else None,
        },
        "prior": {
            "version": PRIOR_VERSION if priors_file is None else "user_supplied",
            "sha256": object_digest(priors),
            "parameters_measured": False,
        },
        "accepted": accepted,
        "skipped": skipped,
        "observations": {
            "view_count": 1,
            "source_points": source_point_count,
            "geometry_source": geometry_source,
            "point_assignment_index_space": index_space,
            "image_size": [width, height],
            "source_index_domain_size": width * height
            if "depth" in camera
            else len(world),
            "valid_depth_pixels": source_point_count if "depth" in camera else None,
            "visible_centers": len(indices),
            "dominant_category_fraction": dominant_fraction,
            "category_pixels": by_category,
            "accepted_image_fraction": accepted_area / (width * height),
            "assigned_visible_center_fraction": assigned_centers / max(1, len(indices)),
        },
        "review_reasons": review,
        "semantic_accuracy": "NOT_EVALUATED",
        "geometry_accuracy": "NOT_EVALUATED",
        "real_world_physics": "NOT_EVALUATED",
        "release_authorized": False,
        "limitations": [
            "Single-view connected semantic regions can merge objects or split one object.",
            "Calibration identity is checked; calibration accuracy is not independently established.",
            "PLY-center visibility approximates Gaussian rendering; registered z-depth instead describes the observed scene surface.",
            "Bounds enclose observed surfaces only, not full meshes or verified free space.",
            "Mass, material identity, joints and stiffness are not inferred from semantic categories.",
            "Unseen regions remain uncovered; no scene-wide completeness claim is made.",
            "The candidate scene retains source PLY appearance; observed repaired USD geometry is not automatically composed into the output.",
        ],
        "files": {
            "scene_spec": "scene_spec.json",
            "point_assignments": "point_assignments.npz",
        },
    }
    write_json(out / "projection.json", result)
    return result
