#!/usr/bin/env python3
"""Evidence-gated Starbucks evaluation; not the official Video2World score.

No cloud calls, simulator startup, image resizing, or camera fitting occurs here.
Reference/candidate landmark coordinates are normalized image coordinates [0,1].
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_file(item, root):
    path = root / item["path"]
    if not path.is_file() or sha256(path) != item["sha256"]:
        raise ValueError(f"Missing or changed evidence: {path}")
    return path


def candidate_digest(candidate):
    """All scene layers/dependencies must be listed in candidate assets."""
    assets = sorted(
        candidate["assets"], key=lambda item: (item["path"], item["sha256"])
    )
    return hashlib.sha256(
        json.dumps(assets, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_pair(pair, root):
    if pair.get("split") not in ("dev", "heldout"):
        raise ValueError("A fixed dev/heldout split is required")
    # Independent calibrated correspondence is required, not an arbitrary orbit view.
    camera = pair.get("camera", {})
    if camera.get("status") not in (
        "verified",
        "conditional_estimated",
    ) or not camera.get("reviewer"):
        raise ValueError("Camera correspondence has not been independently reviewed")
    evidence_file(camera["evidence"], root)
    if camera["status"] == "conditional_estimated":
        review = json.loads(evidence_file(camera["review"], root).read_text())
        if (
            review.get("status") != "CONDITIONAL_SINGLE_VIEW_APPEARANCE_BASELINE"
            or review.get("reviewer") != camera["reviewer"]
            or review.get("reference", {}).get("sha256") != pair["reference"]["sha256"]
            or review.get("render", {}).get("sha256") != pair["render"]["sha256"]
        ):
            raise ValueError(
                "Conditional review does not approve this exact reference/render pair"
            )
        region = pair.get("region", "full_image")
        approval = review.get("approved_regions", {}).get(region)
        if approval is None or approval.get("mask_sha256") != (
            pair.get("mask") or {}
        ).get("sha256"):
            raise ValueError(
                "Conditional review does not approve this exact region/mask"
            )
    ref = (
        np.asarray(
            Image.open(evidence_file(pair["reference"], root)).convert("RGB"),
            dtype=np.float64,
        )
        / 255
    )
    render = (
        np.asarray(
            Image.open(evidence_file(pair["render"], root)).convert("RGB"),
            dtype=np.float64,
        )
        / 255
    )
    if ref.shape != render.shape:
        raise ValueError("Reference/render sizes differ: no automatic resizing allowed")
    mask = np.ones(ref.shape[:2], dtype=bool)
    if pair.get("mask"):
        mask = (
            np.asarray(Image.open(evidence_file(pair["mask"], root)).convert("L")) > 0
        )
        if mask.shape != ref.shape[:2]:
            raise ValueError("Mask shape mismatch")
    if not mask.any():
        raise ValueError("Empty evaluation mask")
    difference = ref[mask] - render[mask]
    mse = float(np.mean(difference**2))
    anchors = pair.get("anchors", [])
    distances, pixel_distances = [], []
    for anchor in anchors:
        a, b = np.asarray(anchor["reference_xy"]), np.asarray(anchor["render_xy"])
        if (
            a.shape != (2,)
            or b.shape != (2,)
            or not np.isfinite([a, b]).all()
            or (np.asarray([a, b]) < 0).any()
            or (np.asarray([a, b]) > 1).any()
        ):
            raise ValueError("Invalid normalized anchor coordinates")
        if not anchor.get("label") or not anchor.get("reviewer"):
            raise ValueError("Landmarks need semantic labels and reviewer provenance")
        distances.append(float(np.linalg.norm(a - b)))
        pixel_distances.append(
            float(np.linalg.norm((a - b) * [ref.shape[1], ref.shape[0]]))
        )
    return {
        "id": pair["id"],
        "split": pair["split"],
        "region": pair.get("region", "full_image"),
        "camera_status": camera["status"],
        "scope": "Conditional estimated-pose appearance diagnostic; not independent scene geometry GT"
        if camera["status"] == "conditional_estimated"
        else "Reviewed camera correspondence",
        "coverage": float(mask.mean()),
        "rgb_mae": float(np.mean(np.abs(difference))),
        "psnr_db": float(-10 * math.log10(mse)) if mse else None,
        "identical_pixels": mse == 0,
        "anchor_count": len(distances),
        "anchor_mean_error_normalized": float(np.mean(distances))
        if distances
        else None,
        "anchor_mean_error_px": float(np.mean(pixel_distances))
        if pixel_distances
        else None,
        "note": "Photometric diagnostics alone do not establish geometric fidelity or sharpness.",
    }


def evaluate(config_path):
    config_path = Path(config_path).resolve()
    root = config_path.parent
    config = json.loads(config_path.read_text())
    report = {
        "schema_version": 1,
        "benchmark": "Starbucks Scene Bench v1 (adapted, not official V2WScore)",
        "config_sha256": sha256(config_path),
        "candidates": [],
        "cloud_calls_this_run": 0,
        "overall_score": None,
        "promotion": "BLOCKED_PENDING_REFERENCE_CALIBRATION",
        "limitations": [
            "Same-video heldout frames test view generalization, not independent scene acquisition.",
            "Simulation replay, collisions and navigation are not validated by a USD load check.",
            "No metric threshold is calibrated yet; no automatic promotion.",
        ],
    }
    report["references"] = {"status": "MISSING", "frames": 0}
    reference_path = root / config["reference_manifest"]
    reference_frames = {}
    if reference_path.is_file():
        reference_manifest = json.loads(reference_path.read_text())
        expected = config.get("reference_manifest_sha256")
        if not expected or sha256(reference_path) != expected:
            raise ValueError("Reference manifest is not pinned or has changed")
        for frame in reference_manifest["frames"]:
            evidence_file(
                {"path": frame["filename"], "sha256": frame["sha256"]},
                reference_path.parent,
            )
            if frame["sha256"] in reference_frames:
                raise ValueError(
                    "Duplicate reference image hashes require deduplication before freezing splits"
                )
            reference_frames[frame["sha256"]] = frame
        report["references"] = {
            "status": "VERIFIED_HASH",
            "frames": len(reference_frames),
            "manifest_sha256": expected,
            "split_counts": {
                split: sum(f["split"] == split for f in reference_frames.values())
                for split in ("dev", "guard", "heldout")
            },
        }
    for candidate in config["candidates"]:
        result = {
            "id": candidate["id"],
            "role": candidate["role"],
            "assets": [],
            "pairs": [],
            "errors": [],
        }
        for item in candidate["assets"]:
            try:
                evidence_file(item, root)
                result["assets"].append({**item, "status": "VERIFIED_HASH"})
            except (ValueError, KeyError, OSError) as exc:
                result["errors"].append(str(exc))
        for pair in candidate.get("matched_views", []):
            try:
                frozen_frame = reference_frames.get(
                    pair.get("reference", {}).get("sha256")
                )
                if (
                    not frozen_frame
                    or frozen_frame["split"] != pair.get("split")
                    or frozen_frame["split"] == "guard"
                ):
                    raise ValueError("Pair does not match frozen reference frame/split")
                if result["errors"]:
                    raise ValueError(
                        "Candidate has invalid assets or prior evidence errors"
                    )
                provenance = json.loads(
                    evidence_file(pair["render_provenance"], root).read_text()
                )
                if (
                    provenance.get("candidate_digest") != candidate_digest(candidate)
                    or provenance.get("render_sha256") != pair["render"]["sha256"]
                    or provenance.get("camera_evidence_sha256")
                    != pair.get("camera", {}).get("evidence", {}).get("sha256")
                ):
                    raise ValueError(
                        "Render provenance does not bind candidate, output and camera evidence"
                    )
                result["pairs"].append(evaluate_pair(pair, root))
            except (ValueError, KeyError, OSError) as exc:
                result["errors"].append(f"{pair.get('id', '?')}: {exc}")
        result["layout_fidelity"] = "NOT_MEASURED"
        result["appearance_fidelity"] = (
            "NOT_MEASURED" if not result["pairs"] else "DIAGNOSTICS_ONLY"
        )
        result["simulation_replay"] = "NOT_RUN"
        result["user_review"] = candidate.get("user_review")
        report["candidates"].append(result)
    if any(candidate["pairs"] for candidate in report["candidates"]):
        report["promotion"] = "BLOCKED_PENDING_THRESHOLD_CALIBRATION_AND_VIEW_COVERAGE"
    report["rejected_views"] = []
    for rejection in config.get("rejected_views", []):
        evidence_file(rejection["evidence"], root)
        report["rejected_views"].append(rejection)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "report": str(args.output),
                "promotion": report["promotion"],
                "candidates": len(report["candidates"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
