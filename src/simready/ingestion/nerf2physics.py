"""Evidence-bound CPU regression over cached NeRF2Physics features.

This adapter implements the published cosine/softmax regression equations; it
does not train NeRFs, generate CLIP features, or call a caption/material LLM.
Third-party code, weights, and datasets stay outside the runtime package.
"""

import argparse
import json
import re
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import ensure_unchanged, reference, resolve_ref
from simready.core.types import PathLike
from simready.core.validation import number, require

REPOSITORY = "https://github.com/ajzhai/NeRF2Physics"
PROPERTIES = {
    "density": ("density_kg_m3", "kg/m^3"),
    "mass": ("mass_kg", "kg"),
    "friction": ("dynamic_friction", "1"),
}
MODES = {"feature_regression", "official_prediction_replay", "synthetic_fixture"}


def _interval(value):
    require(isinstance(value, list) and len(value) == 2, "Expected [low, high] range")
    low, high = (number(item, "property interval") for item in value)
    require(low <= high, "Property interval is reversed")
    return [low, high]


def _upstream(value, mode, freeze):
    require(isinstance(value, dict), "upstream provenance is required")
    require(value.get("repository") == REPOSITORY, "Unexpected upstream repository")
    require(value.get("license") == "MIT", "Upstream code license must be recorded")
    commit = value.get("commit")
    if mode == "synthetic_fixture" and commit is None:
        return
    require(
        isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit),
        "Non-synthetic inputs require a full pinned upstream commit",
    )
    refs = value.get("source_files")
    require(
        isinstance(refs, list) and refs, "Pinned upstream source files are required"
    )
    for ref in [*refs, value.get("license_file")]:
        freeze(ref)


def regress_features(
    patch_features, visible, text_features, material_intervals, *, temperature=0.1
):
    """Published visibility aggregation and cosine softmax; returns visible-point ranges."""
    import numpy as np

    temperature = number(temperature, "temperature", 1e-12)
    patches = np.asarray(patch_features, dtype=float)
    visible = np.asarray(visible)
    texts = np.asarray(text_features, dtype=float)
    ranges = np.asarray([_interval(row) for row in material_intervals], dtype=float)
    require(patches.ndim == 3 and min(patches.shape) > 0, "Patch features need V,N,D")
    require(
        visible.dtype == np.bool_ and visible.shape == patches.shape[:2],
        "Visibility must be boolean V,N",
    )
    require(
        texts.ndim == 2 and texts.shape == (len(ranges), patches.shape[2]),
        "Text features must match material count and feature dimension",
    )
    require(
        np.isfinite(patches).all() and np.isfinite(texts).all(),
        "Features must be finite",
    )
    counts = visible.sum(axis=0)
    valid = counts > 0
    require(valid.any(), "No observed points; cannot estimate physical properties")
    aggregated = (patches * visible[..., None]).sum(axis=0)[valid] / counts[valid, None]
    norms, text_norms = (
        np.linalg.norm(aggregated, axis=1),
        np.linalg.norm(texts, axis=1),
    )
    require((norms > 0).all() and (text_norms > 0).all(), "Zero feature norm")
    logits = (
        (aggregated / norms[:, None]) @ (texts / text_norms[:, None]).T / temperature
    )
    weights = np.exp(logits - logits.max(axis=1, keepdims=True))
    weights /= weights.sum(axis=1, keepdims=True)
    return weights @ ranges, weights, valid


def _feature_prediction(payload, property_name, freeze):
    import numpy as np

    materials = payload.get("materials")
    require(
        isinstance(materials, list) and materials, "Material proposals are required"
    )
    names = [row.get("name") for row in materials]
    require(
        all(isinstance(name, str) and name for name in names)
        and len(set(names)) == len(names),
        "Material names must be nonempty and unique",
    )
    expected_unit = "1" if property_name == "friction" else "kg/m^3"
    require(
        all(row.get("unit") == expected_unit for row in materials),
        "Material interval units do not match the requested property",
    )
    feature_path = freeze(payload["features"])
    with np.load(feature_path, allow_pickle=False) as arrays:
        require(
            arrays["material_names"].tolist() == names,
            "Text feature material order differs from property proposals",
        )
        temperature = payload.get("temperature", 0.1)
        intervals, probabilities, valid = regress_features(
            arrays["patch_features"],
            arrays["visible"],
            arrays["text_features"],
            [row["interval"] for row in materials],
            temperature=temperature,
        )
        parameters = {
            "temperature": temperature,
            "aggregation": "observed_surface_mean",
        }
        if property_name == "mass":
            # Only explicit carved-volume integration is supported. Surface
            # density alone is never silently converted into object mass.
            require(
                payload.get("volume_method") == "carving",
                "Mass requires explicit carved-volume evidence",
            )
            freeze(payload["volume_evidence"])
            source = np.asarray(arrays["source_points"], dtype=float)
            query = np.asarray(arrays["query_points"], dtype=float)
            require(
                source.shape == (len(valid), 3)
                and query.ndim == 2
                and query.shape[1] == 3
                and len(query) > 0,
                "Carved/source point arrays need N,3",
            )
            require(
                np.isfinite(source).all() and np.isfinite(query).all(),
                "Point coordinates must be finite",
            )
            source = source[valid]
            voxel_volume = number(payload["voxel_volume_m3"], "voxel_volume_m3", 1e-18)
            correction = number(
                payload.get("correction_factor", 0.6), "correction_factor", 1e-12
            )
            total = np.zeros(2)
            for start in range(0, len(query), 256):
                distances = (
                    (query[start : start + 256, None] - source[None]) ** 2
                ).sum(axis=2)
                total += intervals[distances.argmin(axis=1)].sum(axis=0)
            result = (total * voxel_volume * correction).tolist()
            parameters.update(
                aggregation="carved_volume_integral",
                volume_method="carving",
                voxel_volume_m3=voxel_volume,
                correction_factor=correction,
                query_points=len(query),
            )
        else:
            result = intervals.mean(axis=0).tolist()
    details = {
        "observed_points": int(valid.sum()),
        "unobserved_points": int((~valid).sum()),
        "material_names": names,
        "mean_material_weights": probabilities.mean(axis=0).tolist(),
        "point_intervals": intervals.tolist(),
    }
    return _interval(result), parameters, details


def run_estimation(request_path: PathLike, output_dir: PathLike) -> Path:
    """Produce one PhysicalCandidate from pinned inputs; no network or model downloads."""
    request_path = Path(request_path).resolve()
    frozen = [reference(request_path)]
    request = read_json(request_path)
    require(
        type(request.get("schema_version")) is int and request["schema_version"] == 1,
        "Estimation request requires schema_version=1",
    )
    object_id, mode = request.get("object_id"), request.get("execution_mode")
    require(
        isinstance(object_id, str) and object_id,
        "Explicit target object_id is required",
    )
    require(mode in MODES, "Unsupported estimation execution mode")
    property_name = request.get("property_name")
    require(
        property_name in PROPERTIES, "Only density, mass and friction are supported"
    )

    def freeze(ref):
        path = resolve_ref(ref, request_path.parent)
        resolved = {"path": str(path), "sha256": ref["sha256"]}
        if resolved not in frozen:
            frozen.append(resolved)
        return path

    association = request.get("association", {})
    require(
        all(
            isinstance(association.get(key), str) and association[key]
            for key in ("source_object_id", "reason")
        ),
        "Explicit source-object association and reason are required",
    )
    association_path = freeze(association.get("evidence"))
    _upstream(request.get("upstream"), mode, freeze)
    model = request.get("model_identity")
    require(
        isinstance(model, dict)
        and all(
            isinstance(model.get(key), str) and model[key]
            for key in ("clip_backbone", "checkpoint")
        ),
        "CLIP model identity is required",
    )
    weights_hash = model.get("weights_sha256")
    require(
        weights_hash is None
        or (
            isinstance(weights_hash, str)
            and re.fullmatch(r"[a-f0-9]{64}", weights_hash)
        ),
        "Invalid model weight hash",
    )
    payload = read_json(freeze(request["payload"]))
    scale_ref = (
        reference(freeze(request["scale_evidence"]))
        if property_name == "mass"
        else None
    )
    if mode == "official_prediction_replay":
        require(
            property_name == "mass",
            "Official prediction replay currently supports mass only",
        )
        interval = _interval(payload[association["source_object_id"]])
        parameters, details = {"aggregation": "published_object_mass"}, {}
    else:
        interval, parameters, details = _feature_prediction(
            payload, property_name, freeze
        )
    if scale_ref:
        parameters["scale_evidence"] = scale_ref
    if property_name != "friction":
        require(interval[0] > 0, "Mass/density must be strictly positive")
    key, unit = PROPERTIES[property_name]
    properties = {
        key: {
            "value": sum(interval) / 2,
            "interval": interval,
            "unit": unit,
            "interval_kind": "material_proposal_range",
            "statistical_confidence": False,
        }
    }
    if property_name == "friction":
        properties[key]["context"] = {"counterface": "fabric", "regime": "kinetic"}
    candidate = {
        "schema_version": 1,
        "artifact_type": "physical_candidate",
        "object_id": object_id,
        "association": {**association, "evidence": reference(association_path)},
        "method": {
            "name": "nerf2physics",
            "upstream_repository": REPOSITORY,
            "upstream_commit": request["upstream"].get("commit"),
            "license": "MIT",
            "execution_mode": mode,
            "implementation": "simready_cpu_kernel_port",
            "model_identity": model,
            "parameters": parameters,
            "new_visual_inference_performed": False,
            "upstream_revision_assertion": "caller_supplied_with_local_file_hashes",
        },
        "properties": properties,
        "unknown_fields": [
            name
            for name in (
                "mass_kg",
                "density_kg_m3",
                "static_friction",
                "dynamic_friction",
                "restitution",
                "young_modulus_pa",
                "spring_stiffness",
            )
            if name != key
        ],
        "evidence": frozen,
        "limitations": [
            "Property ranges are material-proposal ranges, not calibrated confidence intervals.",
            "Object association is explicitly supplied; it is not inferred from matching visual identity.",
            "Cached features and material proposals are inputs; no fresh visual or language model was run.",
        ]
        + (
            ["Synthetic fixture: no real-world model accuracy is demonstrated."]
            if mode == "synthetic_fixture"
            else []
        ),
    }
    output = Path(output_dir).resolve()
    require(not output.exists(), "Estimation output already exists")
    require(
        not any(Path(ref["path"]).is_relative_to(output) for ref in frozen),
        "Output cannot contain estimation inputs",
    )
    ensure_unchanged(frozen)
    output.mkdir(parents=True)
    write_json(output / "regression-details.json", details)
    candidate["evidence"] = [*frozen, reference(output / "regression-details.json")]
    destination = output / "physical-candidate.json"
    write_json(destination, candidate)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    path = run_estimation(args.request, args.out)
    print(json.dumps({"candidate": str(path), "sha256": digest(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
