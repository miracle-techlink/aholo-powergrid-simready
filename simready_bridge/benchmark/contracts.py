"""Strict, hash-bound contracts shared by observers, planners and verification.

Hashes establish identity, not a trusted producer. This package never grants
release authority; an isolated data/renderer/publisher service is still needed.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..artifacts import digest
from ..artifacts import object_digest as fingerprint
from ..artifacts import read_json as read_json

__all__ = [
    "digest",
    "fingerprint",
    "read_json",
    "require",
    "finite",
    "reference",
    "resolve_ref",
    "indexed",
    "validate_protocol",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path)}


def resolve_ref(ref, root):
    require(isinstance(ref, dict), "File reference must be an object")
    require(
        isinstance(ref.get("path"), str) and ref["path"], "File reference needs a path"
    )
    path = (Path(root) / ref["path"]).resolve()
    require(path.is_file(), f"Missing file: {path}")
    require(digest(path) == ref.get("sha256"), f"SHA256 mismatch: {path}")
    return path


def indexed(rows, key, label):
    require(isinstance(rows, list), f"{label} must be a list")
    require(
        all(
            isinstance(row, dict) and isinstance(row.get(key), str) and row[key]
            for row in rows
        ),
        f"{label} needs nonempty {key}",
    )
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), f"Duplicate {key} in {label}")
    return result


def validate_protocol(p):
    require(isinstance(p, dict), "Protocol must be an object")
    require(
        type(p.get("schema_version")) is int and p["schema_version"] == 1,
        "Unsupported protocol schema",
    )
    require(
        isinstance(p.get("benchmark_id"), str) and p["benchmark_id"],
        "Missing benchmark_id",
    )
    require(
        p.get("phase") in ("development", "sealed_release"), "Invalid benchmark phase"
    )
    require(
        p.get("profile") in ("reconstruction", "simready", "observer_calibration"),
        "Invalid profile",
    )
    if "asset_policy" in p:
        from .asset_consistency import validate_policy

        validate_policy(p["asset_policy"])
    views = indexed(p.get("views"), "view_id", "protocol views")
    require(bool(views), "Empty observation protocol")
    source_ids, source_hashes = [], []
    for v in views.values():
        require(
            isinstance(v.get("asset_id"), str) and v["asset_id"], "Missing asset_id"
        )
        require(v.get("split") == p["phase"], "View split differs from benchmark phase")
        require(
            v.get("reference_kind") in ("real_capture", "synthetic_gt", "estimated"),
            "Invalid reference kind",
        )
        require(
            isinstance(v.get("frame_id"), str) and v["frame_id"],
            "Missing frame identity",
        )
        source_ids.append(v["frame_id"])
        require(
            isinstance(v.get("rgb"), dict) and "sha256" in v["rgb"],
            "Missing reference RGB",
        )
        source_hashes.append(v["rgb"]["sha256"])
        rois = indexed(v.get("regions", []), "asset_id", "view regions")
        for region in rois.values():
            box = region.get("bbox_xyxy")
            require(
                isinstance(box, list)
                and len(box) == 4
                and all(type(x) is int for x in box),
                "ROI must be four integer pixels",
            )
            require(0 <= box[0] < box[2] and 0 <= box[1] < box[3], "Invalid ROI bounds")
    require(
        len(set(source_ids)) == len(source_ids),
        "Duplicate source frame in observation protocol",
    )
    require(
        not set(source_ids) & set(p.get("fit_frame_ids", [])),
        "Fit/evaluation frame overlap",
    )
    require(
        not set(source_hashes) & set(p.get("fit_image_sha256", [])),
        "Fit/evaluation image overlap",
    )
    checks = indexed(p.get("checks"), "check_id", "checks")
    require(bool(checks), "Empty benchmark check list")
    for c in checks.values():
        require(c.get("op") in (">=", "<=", "=="), "Unsupported check operator")
        require(
            c.get("limit") is None or finite(c["limit"]),
            "Check limit must be finite or null",
        )
        require(
            isinstance(c.get("metric"), str) and c["metric"], "Check needs a metric"
        )
        require(type(c.get("required")) is bool, "Check required must be boolean")
        require(c.get("severity") in ("critical", "major", "minor"), "Invalid severity")
        require(isinstance(c.get("track"), str) and c["track"], "Missing check track")
        require(
            c.get("calibration")
            in ("pending", "legacy_development", "calibrated", "structural"),
            "Unknown calibration status",
        )
    require(any(c["required"] for c in checks.values()), "No required benchmark checks")
    required_tracks = {"coverage", "appearance"}
    if p["profile"] != "observer_calibration":
        required_tracks |= {"source", "geometry"}
    if p["profile"] == "simready":
        required_tracks |= {"instances", "physics", "tasks", "delivery"}
    require(
        required_tracks <= {c["track"] for c in checks.values() if c["required"]},
        "Profile is missing required tracks",
    )
    require(
        any(
            c["required"]
            and c["metric"] == "coverage.observation_fraction"
            and c["op"] == ">="
            and c["limit"] == 1
            and c["calibration"] != "pending"
            for c in checks.values()
        ),
        "All declared observations must be required",
    )
    return p


def validate_candidate(c, protocol_sha256, protocol):
    require(isinstance(c, dict), "Candidate must be an object")
    require(
        type(c.get("schema_version")) is int and c["schema_version"] == 1,
        "Unsupported candidate schema",
    )
    require(
        c.get("protocol_sha256") == protocol_sha256,
        "Candidate/protocol binding mismatch",
    )
    require(
        isinstance(c.get("candidate_id"), str) and c["candidate_id"],
        "Missing candidate_id",
    )
    artifacts = indexed(c.get("artifacts"), "artifact_id", "candidate artifacts")
    require(bool(artifacts), "Candidate has no artifacts")
    views = indexed(c.get("views"), "view_id", "candidate views")
    require(
        set(views) <= {v["view_id"] for v in protocol["views"]},
        "Undeclared candidate view",
    )
    # Fit access includes training, pose estimation, texture fitting and skill learning.
    source_ids = {v["frame_id"] for v in protocol["views"]}
    source_hashes = {v["rgb"]["sha256"] for v in protocol["views"]}
    require(
        not source_ids & set(c.get("fit_frame_ids", [])),
        "Candidate fit/evaluation frame overlap",
    )
    require(
        not source_hashes & set(c.get("fit_image_sha256", [])),
        "Candidate fit/evaluation image overlap",
    )
    for v in views.values():
        require(isinstance(v.get("rgb"), dict), "Candidate view needs RGB reference")
    return c
