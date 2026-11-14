"""Read-only contracts for new-video stage artifacts and delivery evidence.

These checks own no run state and launch no workers. Both fresh execution and
resume use these same contracts; the pipeline owns receipt publication.
"""

from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json
from simready.core.types import JsonObject
from simready.evaluation.inspection.physical_receipt import verify_physical_receipt
from simready.workflows.contracts.stage_ledger import artifact_record as artifact_record
from simready.workflows.contracts.stage_ledger import (
    checked_artifact as checked_artifact,
)
from simready.workflows.stages.physical_estimation import verify_compilation


def verify_delivery(
    target: Path,
    evidence: JsonObject,
    source: JsonObject,
    render_config: JsonObject,
    scene_spec: Path,
) -> None:
    """Verify scene/inspection/render evidence without upgrading its validation scope."""
    if digest(scene_spec) != source["spec_sha256"]:
        raise ValueError("Worker scene specification changed")
    expected_level = (
        "SCENE_EXPORTED" if source["prepare_only"] else "SCENE_SURVEY_READY"
    )
    if (
        evidence.get("schema_version") != 1
        or evidence.get("status") != "COMPLETE"
        or evidence.get("delivery_level") != expected_level
        or evidence.get("scene") != "scene/scene.usda"
        or evidence.get("robot_task_validated") is not False
        or evidence.get("metric_scale_independently_validated") is not False
    ):
        raise ValueError(
            "Worker delivery contract is incomplete or has unsupported claims"
        )
    for field, source_field in (
        ("source_video_sha256", "source_video_sha256"),
        ("reconstruction_sha256", "ply_sha256"),
        ("scene_spec_sha256", "spec_sha256"),
    ):
        if evidence.get(field) != source[source_field]:
            raise ValueError("Scene delivery is bound to different input artifacts")
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Delivery requires nonempty artifact evidence")
    paths = set()
    for item in artifacts:
        if item["path"] in paths:
            raise ValueError("Repeated delivery artifact")
        paths.add(item["path"])
        checked_artifact(target, item)
    required = {"scene/scene.usda", "scene/preparation.json", "structure-audit.json"}
    if not source["prepare_only"]:
        required |= {
            "render/render.json",
            "render/camera.json",
            "render/scene_survey.mp4",
        }
    if not required.issubset(paths):
        raise ValueError(
            "Delivery is missing required scene, audit or render artifacts"
        )
    if "physical_compilation" in source:
        binding = verify_compilation(
            target / "physical",
            read_json(scene_spec),
            source["source_video_sha256"],
            source["ply_sha256"],
        )
        mode = read_json(target / "physical/compilation.json")["execution_mode"]
        if (
            binding != source["physical_compilation"]
            or evidence.get("physical_estimation") != mode
            or any(
                str(p.relative_to(target)) not in paths
                for p in (target / "physical").rglob("*")
                if p.is_file()
            )
        ):
            raise ValueError(
                "Physical estimation delivery differs from its compiled evidence"
            )
    elif evidence.get("physical_estimation", "NOT_RUN") != "NOT_RUN":
        raise ValueError("No physical candidate was compiled for this delivery")
    preparation = read_json(target / "scene/preparation.json")
    if preparation["source_sha256"] != source["ply_sha256"]:
        raise ValueError("Prepared scene is bound to another reconstruction")
    if preparation.get("spec_sha256") != object_digest(read_json(scene_spec)):
        raise ValueError("Prepared scene is bound to another specification")
    structure = read_json(target / "structure-audit.json")
    if structure.get("passed") is not True:
        raise ValueError("Generated scene has no passing structure audit")
    dependencies = structure.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Structure audit requires composed USD dependencies")
    dependency_paths = set()
    for dependency in dependencies:
        name = dependency.get("path") if isinstance(dependency, dict) else None
        if (
            not isinstance(name, str)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or name in dependency_paths
            or "scene/" + name not in paths
        ):
            raise ValueError(
                "Structure audit dependency is invalid or missing from delivery"
            )
        dependency_paths.add(name)
        path = (target / "scene" / name).resolve()
        if not path.is_relative_to((target / "scene").resolve()) or digest(
            path
        ) != dependency.get("sha256"):
            raise ValueError("Structure audit dependency changed after inspection")
    if "scene.usda" not in dependency_paths:
        raise ValueError("Structure audit does not bind the root scene")
    if read_json(scene_spec).get("objects"):
        if (
            evidence.get("physical_asset_validation") != "PASS"
            or "physical-audit.json" not in paths
            or "scene/asset_catalog.json" not in paths
        ):
            raise ValueError("Declared objects require an independent physical audit")
        verify_physical_receipt(
            read_json(target / "physical-audit.json"),
            directory=target / "scene",
            scene_spec=scene_spec,
        )
    elif evidence.get("physical_asset_validation") != "NOT_EVALUATED":
        raise ValueError(
            "A scene without declared objects cannot claim physical asset validation"
        )
    if source["prepare_only"]:
        if evidence.get("video") is not None:
            raise ValueError("Prepare-only run cannot claim a rendered video")
        return
    if evidence.get("video") != "render/scene_survey.mp4":
        raise ValueError("Unexpected or missing survey video")
    receipt = read_json(target / "render/render.json")
    if (
        receipt.get("status") != "COMPLETE"
        or receipt["video_sha256"] != digest(target / evidence["video"])
        or receipt["scene_sha256"] != digest(target / evidence["scene"])
        or receipt["spec_sha256"] != source["spec_sha256"]
        or any(
            receipt[key] != render_config[key]
            for key in ("width", "height", "fps", "frames")
        )
    ):
        raise ValueError("Survey receipt does not match requested inputs or video")


def verify_observations_result(result, artifacts, *, root, source_video_sha256, fps):
    """A frame set must identify the same video/configuration as its stage plan."""
    recorded = {checked_artifact(root, item) for item in artifacts}
    manifest_path = Path(result["manifest"]).resolve()
    if manifest_path not in recorded:
        raise ValueError("Observation manifest is not bound to stage artifacts")
    manifest = read_json(manifest_path)
    rows = manifest.get("frames")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("video_sha256") != source_video_sha256
        or manifest.get("fps") != fps
        or not isinstance(rows, list)
        or not rows
        or result.get("frames") != len(rows)
    ):
        raise ValueError(
            "Observations differ from the frozen source video or sampling plan"
        )
    paths = set()
    for row in rows:
        path = (manifest_path.parent / row["path"]).resolve()
        if (
            not path.is_relative_to(manifest_path.parent)
            or path not in recorded
            or path in paths
            or digest(path) != row.get("sha256")
        ):
            raise ValueError("Observation frame evidence is incomplete or changed")
        paths.add(path)


def verify_reconstruction_result(
    result: JsonObject,
    artifacts: list[JsonObject],
    *,
    root: Path,
    source_video_sha256: str,
) -> None:
    """Bind reconstruction content to its published manifest; invocation flags may differ."""
    recorded = {checked_artifact(root, item) for item in artifacts}
    manifest_path = Path(result["manifest_path"]).resolve()
    if manifest_path not in recorded:
        raise ValueError("Reconstruction result is not bound to its stage artifacts")
    manifest = read_json(manifest_path)
    invocation_fields = {"cache_hit", "new_submission", "resumed"}
    if (
        manifest.get("status") != "COMPLETE"
        or manifest.get("source_video_sha256") != source_video_sha256
        or any(
            result.get(key) != value
            for key, value in manifest.items()
            if key not in invocation_fields
        )
        or any(type(result.get(key)) is not bool for key in invocation_fields)
    ):
        raise ValueError("Reconstruction result differs from its verified manifest")
    ply_records = [row for row in manifest["artifacts"] if row["kind"] == "ply"]
    if len(ply_records) != 1:
        raise ValueError("Reconstruction requires one PLY artifact")
    ply_path = checked_artifact(manifest_path.parent, ply_records[0])
    if (
        ply_path not in recorded
        or Path(result["ply_path"]).resolve() != ply_path
        or result["ply_sha256"] != ply_records[0]["sha256"]
    ):
        raise ValueError("Reconstruction PLY result differs from its manifest")


def verify_worker_inputs(
    result: JsonObject,
    artifacts: list[JsonObject],
    *,
    root: Path,
    source_video_sha256: str,
    ply_sha256: str,
    scene_spec: JsonObject,
    prepare_only: bool,
    worker_timeout_s: float,
) -> None:
    """Require packaged worker inputs to match the frozen source and scene contract."""
    assets = Path(result["path"]).resolve()
    recorded = {checked_artifact(root, item) for item in artifacts}
    required = {
        assets / name for name in ("input.json", "scene-spec.json", "splats.ply")
    }
    if not required.issubset(recorded):
        raise ValueError("Worker input result is not bound to its stage artifacts")
    expected = {
        "source_video_sha256": source_video_sha256,
        "ply_sha256": ply_sha256,
        "spec_sha256": digest(assets / "scene-spec.json"),
        "prepare_only": prepare_only,
        "worker_timeout_s": worker_timeout_s,
    }
    if (assets / "physical").exists():
        expected["physical_compilation"] = verify_compilation(
            assets / "physical",
            scene_spec,
            source_video_sha256,
            ply_sha256,
        )
        if any(
            p not in recorded for p in (assets / "physical").rglob("*") if p.is_file()
        ):
            raise ValueError("Worker physical evidence missing from stage artifacts")
    if (
        read_json(assets / "input.json") != expected
        or read_json(assets / "scene-spec.json") != scene_spec
        or digest(assets / "splats.ply") != ply_sha256
    ):
        raise ValueError("Worker inputs do not match the frozen run plan")


def verify_delivery_result(
    result: JsonObject,
    artifacts: list[JsonObject],
    *,
    root: Path,
    inputs: Path,
    render_config: JsonObject,
) -> None:
    """Bind a fresh or resumed delivery result to its complete worker receipt and inputs."""
    target = Path(result["execution"]).resolve()
    recorded = {checked_artifact(root, item) for item in artifacts}
    if not {target / "delivery.json", target / "pipeline-status.json"}.issubset(
        recorded
    ):
        raise ValueError("Delivery result is not bound to its stage artifacts")
    evidence = read_json(target / "delivery.json")
    if result != {"execution": str(target), **evidence}:
        raise ValueError("Delivery stage result differs from its verified receipt")
    if read_json(target / "pipeline-status.json").get("status") != "COMPLETE":
        raise ValueError("Delivery worker is not complete")
    assets = inputs
    verify_delivery(
        target,
        evidence,
        read_json(assets / "input.json"),
        render_config,
        assets / "scene-spec.json",
    )
    if any(target / item["path"] not in recorded for item in evidence["artifacts"]):
        raise ValueError("Delivery artifacts are missing from the reusable stage")
