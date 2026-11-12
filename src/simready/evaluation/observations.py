"""Recompute observations from pinned pixels/depth/instances, never passed flags.

No implicit resizing, exposure fitting, pose fitting or masked error removal.
Camera/producer authenticity and scene coverage require additional collectors.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter

from simready.core.artifacts import read_json
from simready.core.evidence import resolve_ref
from simready.core.validation import indexed, require
from simready.evaluation.asset_graph import asset_metrics


def rgb(path):
    from PIL import Image

    with Image.open(path) as image:
        require(
            image.mode == "RGB",
            f"Expected explicit 8-bit RGB, got {image.mode}: {path}",
        )
        return np.asarray(image, dtype=np.float64) / 255.0


def image_metrics(a, b):
    require(
        a.shape == b.shape and a.ndim == 3 and a.shape[2] == 3,
        "RGB size/channel mismatch",
    )
    require(min(a.shape[:2]) >= 11, "RGB region too small for 11x11 SSIM")
    require(np.isfinite(a).all() and np.isfinite(b).all(), "Nonfinite RGB")
    mse = float(np.mean((a - b) ** 2))

    # Gaussian 11x11, sigma=1.5, population covariance, cropped valid window.
    def smooth(x):
        return gaussian_filter(x, sigma=(1.5, 1.5, 0), truncate=3.5)

    ma, mb = smooth(a), smooth(b)
    va, vb, cov = (
        smooth(a * a) - ma * ma,
        smooth(b * b) - mb * mb,
        smooth(a * b) - ma * mb,
    )
    ssim = (
        (2 * ma * mb + 0.01**2)
        * (2 * cov + 0.03**2)
        / ((ma * ma + mb * mb + 0.01**2) * (va + vb + 0.03**2))
    )
    return {
        "psnr_db": -10 * math.log10(max(mse, 1e-12)),
        "ssim": float(ssim[5:-5, 5:-5].mean()),
        "mse": mse,
    }


def depth_metrics(expected, actual):
    require(
        expected.shape == actual.shape and expected.ndim == 2, "Depth shape mismatch"
    )
    valid = np.isfinite(expected) & (expected > 0)
    require(bool(valid.any()), "No valid reference depth")
    available = np.isfinite(actual) & (actual > 0)
    paired = valid & available
    return {
        "valid_fraction": float(paired.sum() / valid.sum()),
        "mae_m": float(np.abs(actual[paired] - expected[paired]).mean())
        if paired.any()
        else None,
    }


def instance_metrics(reference, predicted):
    expected = indexed(reference, "instance_id", "reference instances")
    found = indexed(predicted, "instance_id", "predicted instances")
    require(bool(expected), "Empty instance ground truth cannot certify fidelity")
    for records in (expected, found):
        for row in records.values():
            xy = row.get("center_px")
            require(
                isinstance(xy, list)
                and len(xy) == 2
                and all(type(x) in (int, float) and math.isfinite(x) for x in xy),
                "Invalid instance center",
            )
            require(
                isinstance(row.get("category"), str) and row["category"],
                "Missing instance category",
            )
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    common = set(expected) & set(found)
    wrong = sorted(k for k in common if expected[k]["category"] != found[k]["category"])
    offsets = [
        float(
            np.linalg.norm(np.array(expected[k]["center_px"]) - found[k]["center_px"])
        )
        for k in common
    ]
    return {
        "missing_count": len(missing),
        "extra_count": len(extra),
        "wrong_category_count": len(wrong),
        "missing_critical_count": sum(
            expected[k].get("critical") is True for k in missing
        ),
        "center_error_p95_px": float(np.quantile(offsets, 0.95)) if offsets else None,
        "missing_ids": missing,
        "extra_ids": extra,
        "wrong_category_ids": wrong,
    }


def observe(protocol, candidate, protocol_root, candidate_root):
    results, issues, used_refs, metrics = [], [], [], {}

    def checked(ref, root):
        path = resolve_ref(ref, root)
        used_refs.append({"path": str(path), "sha256": ref["sha256"]})
        return path

    for artifact in candidate["artifacts"]:
        checked(artifact, candidate_root)
    for artifact in protocol.get("evidence_files", []):
        checked(artifact, protocol_root)
    graph_result = None
    if protocol.get("asset_graph"):
        expected = read_json(checked(protocol["asset_graph"], protocol_root))
        if candidate.get("asset_graph"):
            predicted = read_json(checked(candidate["asset_graph"], candidate_root))
            graph_result = asset_metrics(
                expected, predicted, protocol.get("asset_policy")
            )
            metrics.update(
                {
                    f"assets.{key}": value
                    for key, value in graph_result["metrics"].items()
                    if value is not None
                }
            )
        else:
            issues.append({"code": "MISSING_ASSET_GRAPH", "asset_id": "scene"})
    supplied = {v["view_id"]: v for v in candidate["views"]}
    expected_regions = sum(1 + len(v.get("regions", [])) for v in protocol["views"])
    for view in protocol["views"]:
        ref = rgb(checked(view["rgb"], protocol_root))
        for field in ("camera",):
            if view.get(field):
                checked(view[field], protocol_root)
        if view["view_id"] not in supplied:
            issues.append(
                {
                    "code": "MISSING_VIEW",
                    "view_id": view["view_id"],
                    "asset_id": view["asset_id"],
                }
            )
            continue
        actual = supplied[view["view_id"]]
        image = rgb(checked(actual["rgb"], candidate_root))
        require(
            ref.shape == image.shape,
            "Reference/render dimensions differ; preprocessing must be frozen",
        )
        regions = [
            {
                "asset_id": view["asset_id"],
                "bbox_xyxy": [0, 0, ref.shape[1], ref.shape[0]],
            }
        ]
        regions += view.get("regions", [])
        for region in regions:
            x0, y0, x1, y1 = region["bbox_xyxy"]
            require(
                x1 <= ref.shape[1] and y1 <= ref.shape[0], "ROI exceeds image bounds"
            )
            measured = image_metrics(ref[y0:y1, x0:x1], image[y0:y1, x0:x1])
            results.append(
                {
                    "view_id": view["view_id"],
                    "asset_id": region["asset_id"],
                    "bbox_xyxy": region["bbox_xyxy"],
                    "full_frame": region is regions[0],
                    "metrics": measured,
                }
            )
        if view.get("depth"):
            require(
                view.get("depth_unit") == "m", "Reference depth unit must be metres"
            )
            expected = np.load(
                checked(view["depth"], protocol_root), allow_pickle=False
            )
            if actual.get("depth"):
                observed = np.load(
                    checked(actual["depth"], candidate_root), allow_pickle=False
                )
                d = depth_metrics(expected, observed)
                results[-len(regions)]["depth"] = d
            else:
                issues.append(
                    {
                        "code": "MISSING_DEPTH",
                        "view_id": view["view_id"],
                        "asset_id": view["asset_id"],
                    }
                )
        if view.get("instances"):
            expected = read_json(checked(view["instances"], protocol_root))
            if actual.get("instances"):
                predicted = read_json(checked(actual["instances"], candidate_root))
                results[-len(regions)]["instances"] = instance_metrics(
                    expected, predicted
                )
            else:
                issues.append(
                    {
                        "code": "MISSING_INSTANCES",
                        "view_id": view["view_id"],
                        "asset_id": view["asset_id"],
                    }
                )
    metrics["coverage.observation_fraction"] = len(results) / expected_regions
    if results:
        full = [r for r in results if r["full_frame"]]
        for name in ("psnr_db", "ssim"):
            values = [r["metrics"][name] for r in full]
            metrics[f"appearance.{name}.mean"] = float(np.mean(values))
            metrics[f"appearance.{name}.p10"] = float(np.quantile(values, 0.1))
            metrics[f"appearance.{name}.region_min"] = min(
                r["metrics"][name] for r in results
            )
        depths = [r["depth"] for r in full if "depth" in r]
        if len(depths) == len(protocol["views"]):
            metrics["geometry.depth_valid_fraction.min"] = min(
                d["valid_fraction"] for d in depths
            )
            if all(d["mae_m"] is not None for d in depths):
                metrics["geometry.depth_mae_m.max"] = max(d["mae_m"] for d in depths)
        instances = [r["instances"] for r in full if "instances" in r]
        if len(instances) == len(protocol["views"]):
            for name in (
                "missing_count",
                "extra_count",
                "wrong_category_count",
                "missing_critical_count",
            ):
                metrics[f"instances.{name}.max"] = max(d[name] for d in instances)
            if all(d["center_error_p95_px"] is not None for d in instances):
                metrics["instances.center_error_p95_px.max"] = max(
                    d["center_error_p95_px"] for d in instances
                )
    return {
        "metrics": metrics,
        "regions": results,
        "issues": issues,
        "input_files": used_refs,
        "asset_graph": graph_result,
        "scope": "Pinned RGB, declared pixel ROIs, optional metric depth and instance observations",
        "limitations": [
            "No automatic source/pose authenticity or geometric visibility proof",
            "Observation fraction is declared-view completion, not surface coverage",
            "No physical rollout, LPIPS, temporal-flow or package closure collector yet",
        ],
    }
