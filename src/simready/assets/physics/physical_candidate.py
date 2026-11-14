"""Compile explicitly associated mass candidates into existing scene spec v1.

This adoption rule does not infer object association, measure scale or validate
model accuracy. Density, counterface friction and hardness remain candidates.
"""

import copy
import math
import re
from typing import Any

from simready.assets.physics.physical_spec import normalize_physics
from simready.core.artifacts import object_digest


def _mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def compile_candidate(
    scene_spec: dict[str, Any],
    candidate: dict[str, Any],
    *,
    candidate_sha256: str,
    source_video_sha256: str,
    reconstruction_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adopt a mass estimate with provenance; reject ambiguous or conflicting input."""
    _mapping(scene_spec, "scene_spec")
    _mapping(candidate, "candidate")
    if (
        type(candidate.get("schema_version")) is not int
        or candidate["schema_version"] != 1
        or candidate.get("artifact_type") != "physical_candidate"
    ):
        raise ValueError("Expected physical_candidate schema_version=1")
    method = _mapping(candidate.get("method", {}), "method")
    mode = method.get("execution_mode")
    if method.get("name") != "nerf2physics" or mode not in {
        "feature_regression",
        "official_prediction_replay",
        "synthetic_fixture",
    }:
        raise ValueError("Unsupported physical candidate method or execution mode")
    if mode != "synthetic_fixture" and not re.fullmatch(
        r"[a-f0-9]{40}", str(method.get("upstream_commit"))
    ):
        raise ValueError("Non-fixture estimates require a pinned upstream commit")
    parameters = _mapping(method.get("parameters", {}), "parameters")
    if not isinstance(parameters.get("scale_evidence"), dict):
        raise ValueError("Mass candidate requires its own scale evidence")
    association = _mapping(candidate.get("association", {}), "association")
    if not all(
        isinstance(association.get(key), str) and association[key].strip()
        for key in ("source_object_id", "reason")
    ):
        raise ValueError("Candidate needs an explicit source-object association")
    properties = _mapping(candidate.get("properties", {}), "properties")
    mass = _mapping(properties.get("mass_kg", {}), "mass_kg")
    value = mass.get("value")
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 1e-9 <= value <= 1e12
        or mass.get("unit") != "kg"
    ):
        raise ValueError("Compilation requires an available finite mass in kg")
    interval = mass.get("interval")
    if interval is not None and (
        not isinstance(interval, list)
        or len(interval) != 2
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in interval
        )
        or not interval[0] <= value <= interval[1]
        or not isinstance(mass.get("interval_kind"), str)
        or not mass["interval_kind"].strip()
    ):
        raise ValueError("Mass interval must contain the value and declare its meaning")
    scale = _mapping(scene_spec.get("scale_provenance", {}), "scale_provenance")
    if (
        "source_to_world" not in scene_spec
        or scale.get("status") != "verified"
        or not all(
            isinstance(scale.get(k), str) and scale[k].strip()
            for k in ("method", "evidence")
        )
    ):
        raise ValueError("Mass adoption requires explicit metric scale provenance")
    compiled = copy.deepcopy(scene_spec)
    matches = [
        o
        for o in compiled.get("objects", [])
        if o.get("id") == candidate.get("object_id")
    ]
    if len(matches) != 1 or matches[0].get("type") != "box":
        raise ValueError(
            "Mass candidate must identify one box body; whole-object mass is not button-cap mass"
        )
    obj = matches[0]
    physics = _mapping(obj.setdefault("physics", {}), "physics")
    if "mass_kg" in physics and physics["mass_kg"] != value:
        raise ValueError("Candidate mass conflicts with an explicit scene value")
    physics["mass_kg"] = value
    physics.setdefault("provenance", {})["mass_kg"] = {
        "source_kind": "derived",
        "source": f"nerf2physics {mode}; candidate sha256:{candidate_sha256}; operator-associated, not measured",
        "unit": "kg",
        "interval": interval,
    }
    normalize_physics(
        physics,
        geometry_type="box",
        fallback_contact=scene_spec.get("collision", {}),
        metric_scale_declared=True,
    )
    report = {
        "schema_version": 1,
        "artifact_type": "physical_compilation",
        "status": "COMPILED",
        "source_video_sha256": source_video_sha256,
        "reconstruction_sha256": reconstruction_sha256,
        "base_spec_sha256": object_digest(scene_spec),
        "compiled_spec_sha256": object_digest(compiled),
        "candidate_sha256": candidate_sha256,
        "object_id": candidate["object_id"],
        "association_kind": "operator_declared",
        "execution_mode": mode,
        "pipeline_operation": candidate.get("packaging", {}).get(
            "operation", "candidate_adoption"
        ),
        "upstream_revision_independently_verified": False,
        "adopted": {"mass_kg": copy.deepcopy(mass)},
        "not_applied": sorted(set(properties) - {"mass_kg"}),
        "unknown_fields": candidate.get("unknown_fields", []),
        "real_world_validated": False,
        "fresh_visual_inference": False,
        "limitations": [
            "Mass is an adopted estimate, not an independently measured value.",
            "Object association and metric scale are operator declarations.",
            "Density does not imply solid volume; fabric-relative friction and Shore hardness are not compiled.",
        ],
    }
    return compiled, report
