"""Read-only reconstruction declarations and evidence, without model dependencies.

Frame membership and coordinate calibration are operator declarations supported
by hashed evidence. Passing this contract does not independently certify them.
"""

import re
from pathlib import Path

from simready.core.artifacts import digest, read_json


def fields(value, allowed, label, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"Unknown or invalid {label} fields")
    if set(required) - set(value):
        raise ValueError(f"Missing {label} fields")


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a lowercase SHA256 digest")
    return value


def checked_ref(value, root):
    """Resolve a declared file while retaining its originally supplied digest."""
    fields(value, {"path", "sha256"}, "evidence reference", {"path", "sha256"})
    if not isinstance(value["path"], str) or not value["path"]:
        raise ValueError("Evidence needs a file path")
    path = (Path(root) / value["path"]).resolve()
    if not path.is_file() or digest(path) != sha256(value["sha256"]):
        raise ValueError("Evidence file missing or hash changed")
    return {"path": str(path), "sha256": value["sha256"]}


def declarations(algorithm, frames, coordinates, root):
    """Validate metadata and return normalized copies with absolute evidence refs."""
    fields(
        algorithm,
        {"name", "version", "implementation_sha256"},
        "algorithm",
        {"name", "version", "implementation_sha256"},
    )
    if not isinstance(algorithm["name"], str) or not algorithm["name"].strip():
        raise ValueError("Algorithm name is required")
    if algorithm["version"] is not None and (
        not isinstance(algorithm["version"], str) or not algorithm["version"]
    ):
        raise ValueError("Algorithm version must be declared or null")
    if algorithm["implementation_sha256"] is not None:
        sha256(algorithm["implementation_sha256"])
    fields(
        frames,
        {"status", "training", "heldout", "evidence"},
        "frames",
        {"status", "training", "heldout", "evidence"},
    )
    if frames["status"] not in {"unknown", "declared"}:
        raise ValueError("Frame status must be unknown or declared")
    ids, hashes = set(), set()
    for split in ("training", "heldout"):
        if not isinstance(frames[split], list):
            raise ValueError("Frame memberships must be lists")
        for frame in frames[split]:
            fields(frame, {"id", "sha256"}, "frame", {"id", "sha256"})
            if not isinstance(frame["id"], str) or not frame["id"]:
                raise ValueError("Frame id is required")
            sha256(frame["sha256"])
            if frame["id"] in ids or frame["sha256"] in hashes:
                raise ValueError("Repeated or overlapping training/heldout frames")
            ids.add(frame["id"])
            hashes.add(frame["sha256"])
    if frames["status"] == "unknown" and (ids or frames["evidence"] is not None):
        raise ValueError("Unknown frame membership must not claim frames or evidence")
    if frames["status"] == "declared" and frames["evidence"] is None:
        raise ValueError("Declared frames require evidence")
    if frames["status"] == "declared" and not frames["training"]:
        raise ValueError(
            "Declared reconstruction frames need a nonempty training split"
        )
    fields(
        coordinates,
        {"frame", "unit", "scale_status", "evidence"},
        "coordinates",
        {"frame", "unit", "scale_status", "evidence"},
    )
    if not isinstance(coordinates["frame"], str) or not coordinates["frame"]:
        raise ValueError("Coordinate frame is required (or explicit 'unknown')")
    if coordinates["unit"] not in {
        "m",
        "cm",
        "mm",
        "arbitrary",
        "unknown",
    } or coordinates["scale_status"] not in {"unknown", "declared", "verified"}:
        raise ValueError("Unknown coordinate unit or scale status")
    if coordinates["scale_status"] == "verified" and (
        coordinates["evidence"] is None
        or coordinates["unit"] in {"arbitrary", "unknown"}
    ):
        raise ValueError("Verified scale requires metric units and evidence")
    normalized = []
    for value in (frames, coordinates):
        value = dict(value)
        if value["evidence"] is not None:
            value["evidence"] = checked_ref(value["evidence"], root)
        normalized.append(value)
    return dict(algorithm), *normalized


def verify_bundle(path, *, source_video_sha256=None, require_gaussian=False):
    """Verify every declared reference; no reconstruction or calibration is run."""
    path = Path(path).resolve()
    value = read_json(path)
    required = {
        "schema_version",
        "artifact_type",
        "status",
        "representation",
        "source_video_sha256",
        "algorithm",
        "frames",
        "coordinates",
        "artifacts",
        "provenance",
        "declaration_verification",
    }
    fields(value, required, "reconstruction bundle", required)
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["artifact_type"] != "reconstruction_bundle"
        or value["status"] != "COMPLETE"
    ):
        raise ValueError("Expected a complete reconstruction_bundle v1")
    sha256(value["source_video_sha256"])
    if (
        source_video_sha256 is not None
        and value["source_video_sha256"] != source_video_sha256
    ):
        raise ValueError("Reconstruction belongs to another source video")
    if value["representation"] not in {"gaussian_ply", "mesh", "point_cloud"}:
        raise ValueError("Unknown reconstruction representation")
    if require_gaussian and value["representation"] != "gaussian_ply":
        raise ValueError("Current USD route requires gaussian_ply representation")
    declarations(value["algorithm"], value["frames"], value["coordinates"], path.parent)
    if (
        value["declaration_verification"]
        != "operator_evidence_not_independent_certification"
    ):
        raise ValueError(
            "Frame and scale declarations cannot imply independent certification"
        )
    fields(
        value["provenance"],
        {"provider", "execution_mode", "config_sha256", "dependencies"},
        "provenance",
        {"provider", "execution_mode", "config_sha256", "dependencies"},
    )
    if value["provenance"]["provider"] not in {"artifact", "aholo", "command"} or value[
        "provenance"
    ]["execution_mode"] not in {
        "import",
        "fresh_command",
        "cloud_new",
        "cloud_resume",
        "cached",
    }:
        raise ValueError("Unknown provider or execution mode")
    sha256(value["provenance"]["config_sha256"])
    modes = {
        "artifact": {"import"},
        "command": {"fresh_command", "cached"},
        "aholo": {"cloud_new", "cloud_resume", "cached"},
    }
    if (
        value["provenance"]["execution_mode"]
        not in modes[value["provenance"]["provider"]]
    ):
        raise ValueError("Execution mode contradicts its provider")
    if not isinstance(value["provenance"]["dependencies"], list):
        raise ValueError("Dependencies must be a list")
    for ref in value["provenance"]["dependencies"]:
        checked_ref(ref, path.parent)
    if not isinstance(value["artifacts"], list) or not value["artifacts"]:
        raise ValueError("Bundle needs artifacts")
    paths, primary = set(), []
    for record in value["artifacts"]:
        fields(
            record,
            {"role", "kind", "path", "sha256", "bytes"},
            "artifact",
            {"role", "kind", "path", "sha256", "bytes"},
        )
        if (
            record["role"] not in {"primary", "supporting"}
            or not isinstance(record["kind"], str)
            or not record["kind"]
        ):
            raise ValueError("Invalid artifact role or kind")
        ref = checked_ref({k: record[k] for k in ("path", "sha256")}, path.parent)
        resolved = Path(ref["path"])
        if (
            Path(record["path"]).is_absolute()
            or not resolved.is_relative_to(path.parent)
            or resolved in paths
        ):
            raise ValueError("Artifacts must be unique and inside the bundle directory")
        if (
            type(record["bytes"]) is not int
            or record["bytes"] <= 0
            or record["bytes"] != resolved.stat().st_size
        ):
            raise ValueError("Artifact byte count changed")
        paths.add(resolved)
        if record["role"] == "primary":
            primary.append(record)
    if len(primary) != 1 or (
        value["representation"] == "gaussian_ply" and primary[0]["kind"] != "ply"
    ):
        raise ValueError(
            "Bundle requires exactly one primary artifact matching its representation"
        )
    return value
