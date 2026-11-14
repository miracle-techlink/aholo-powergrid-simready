"""Real CPU evidence boundaries for normalized pipeline upgrades and delivery.

Synthetic masks/geometry exercise contracts only; no model or quality benchmark
runs here. The source fixture and the USD worker both use real files/processes.
"""

import struct
import subprocess
from pathlib import Path

import pytest

from examples.semantic_assets.create_fixture import create_fixture as semantic_fixture
from simready.assets.appearance.gaussian_asset import read_ply
from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference
from simready.workflows import video_pipeline
from simready.workflows.pipeline_spec import load_pipeline
from simready.workflows.semantic_stage import verify_decomposition
from simready.workflows.stage_ledger import artifact_record
from tests.fixtures.pipeline import configured as configured


def _update_manifest(path, ply):
    manifest = read_json(path)
    manifest["artifacts"][0].update(sha256=digest(ply), bytes=ply.stat().st_size)
    write_json(path, manifest)


@pytest.fixture
def semantic_configured(configured):
    config, output = configured
    inputs = semantic_fixture(config.parent.parent / "semantic-inputs")
    # Preserve the analytic center positions, supplying a valid Gaussian payload
    # for the reconstruction provider instead of relabeling an RGB point cloud.
    points = read_ply(inputs["ply"])
    names = "x y z rot_0 rot_1 rot_2 rot_3 scale_0 scale_1 scale_2 opacity f_dc_0 f_dc_1 f_dc_2".split()
    header = (
        "ply\nformat binary_little_endian 1.0\nelement vertex "
        + str(len(points["x"]))
        + "\n"
        + "".join(f"property float {name}\n" for name in names)
        + "end_header\n"
    )
    ply = config.parent / "gaussian.ply"
    ply.write_bytes(
        header.encode()
        + b"".join(
            struct.pack(
                "<14f",
                *(
                    [points[axis][i] for axis in "xyz"]
                    + [1, 0, 0, 0, -3, -3, -3, 0, 0, 0, 0]
                ),
            )
            for i in range(len(points["x"]))
        )
    )
    manifest = config.parent / "reconstruction.json"
    _update_manifest(manifest, ply)
    calibration = read_json(inputs["calibration"])
    calibration["ply_sha256"] = digest(ply)
    write_json(inputs["calibration"], calibration)
    segmentation = read_json(inputs["segmentation_manifest"])
    segmentation["disambiguation"] = {"semantic_review_required": True}
    write_json(inputs["segmentation_manifest"], segmentation)
    association = config.parent / "association.json"
    write_json(
        association,
        {
            "kind": "synthetic_alignment_fixture",
            "source_video_sha256": digest(config.parent / "source.mp4"),
            "reconstruction_sha256": digest(ply),
            "independently_validated": False,
        },
    )
    value = read_json(config)
    value["stages"]["reconstruction"]["manifest"] = reference(manifest)
    value["stages"]["assets"]["scene_spec"] = str(inputs["scene_spec"])
    value["stages"]["decomposition"] = {
        "provider": "semantic_projection",
        "manifest": str(inputs["segmentation_manifest"]),
        "calibration": str(inputs["calibration"]),
        "association": {
            "source_video_sha256": digest(config.parent / "source.mp4"),
            "reconstruction_sha256": digest(ply),
            "reason": "Analytic synthetic fixture; no real-world accuracy claim",
            "evidence": reference(association),
        },
    }
    write_json(config, value)
    return config, output


def _run(config, output, **flags):
    args = load_pipeline(config, output, **flags)
    plan = video_pipeline.make_plan(args)
    result, code = video_pipeline.execute_video(args, plan)
    return plan, result, code


def test_video_changed_after_planning_cannot_publish_observations(configured):
    config, output = configured
    args = load_pipeline(config, output, until="observations")
    plan = video_pipeline.make_plan(args)
    # Deterministic race: replace the valid video between plan freezing and
    # extraction. extract_frames' own before/after hash check cannot detect this.
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=128x96:rate=4",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(args.video),
        ],
        check=True,
        timeout=30,
    )
    assert digest(args.video) != plan["source_video_sha256"]
    result, code = video_pipeline.execute_video(args, plan)
    assert code == 2 and result["status"] == "FAILED"
    assert "frozen source video" in result["error"]
    assert not (output / "stages/observations.json").exists()
    assert not (output / "reconstruction").exists()


def test_real_semantic_usd_delivery_preserves_review_and_unassessed_quality(
    semantic_configured,
):
    config, output = semantic_configured
    _, result, code = _run(config, output)
    assert code == 0 and result["delivery_level"] == "SCENE_EXPORTED"
    assert result["physical_asset_validation"] == "PASS"
    assert result["task_validated"] is False
    assert result["metric_scale_independently_validated"] is False
    semantic = result["decomposition"]
    assert semantic["status"] == "REVIEW_REQUIRED"
    assert semantic["semantic_accuracy"] == "NOT_EVALUATED"
    assert semantic["release_authorized"] is False
    projection = read_json(
        Path(semantic["binding"]).parent / "projection/projection.json"
    )
    assert len(projection["accepted"]) == 4
    assert "semantic_review_requested_by_model" in projection["review_reasons"]
    assert projection["geometry_accuracy"] == "NOT_EVALUATED"
    assert projection["real_world_physics"] == "NOT_EVALUATED"
    assert Path(result["scene"]).is_file()
    launch = read_json(output / "execution/attempt-0001/launch.json")
    assert launch["device_lease"]["resource_kind"] == "cpu"
    assert launch["device_lease"]["gpu_uuid"] is None


@pytest.mark.parametrize("invalid_hash", [None, "not-a-sha256", True])
def test_decomposition_hash_is_required_before_any_execution(
    semantic_configured,
    invalid_hash,
):
    config, output = semantic_configured
    value = read_json(config)
    association = value["stages"]["decomposition"]["association"]
    if invalid_hash is None:
        del association["reconstruction_sha256"]
    else:
        association["reconstruction_sha256"] = invalid_hash
    write_json(config, value)
    with pytest.raises(ValueError, match="reconstruction SHA256"):
        load_pipeline(config, output)
    assert not output.exists()


@pytest.mark.parametrize("missing", ["artifact_inventory", "report_declaration"])
def test_decomposition_requires_point_assignment_evidence(
    semantic_configured,
    missing,
):
    config, output = semantic_configured
    plan, _, code = _run(config, output, until="decomposition")
    assert code == 0
    record = read_json(output / "stages/decomposition.json")
    artifacts = record["artifacts"]
    if missing == "artifact_inventory":
        artifacts = [
            row
            for row in artifacts
            if not row["path"].endswith("point_assignments.npz")
        ]
        assert len(artifacts) + 1 == len(record["artifacts"])
    else:
        directory = Path(record["result"]["directory"])
        path = directory / "projection/projection.json"
        projection = read_json(path)
        del projection["files"]["point_assignments"]
        write_json(path, projection)
        binding = read_json(directory / "binding.json")
        binding["projection_sha256"] = digest(path)
        write_json(directory / "binding.json", binding)
        # Supply valid new hashes so rejection exercises semantic completeness,
        # rather than only detecting stale file hashes. Do not rewrite receipts.
        artifacts = [artifact_record(output, output / row["path"]) for row in artifacts]
    with pytest.raises(ValueError, match="Incomplete decomposition|point assignment"):
        verify_decomposition(
            record["result"],
            artifacts,
            root=output,
            plan=plan["pipeline"]["decomposition"],
            reconstruction=read_json(output / "stages/reconstruction.json")["result"],
            spec=plan["spec"],
        )


def test_alternate_worker_checkout_changes_only_delivery_identity(configured):
    config, output = configured
    repo = config.parent.parent / "worker-repo"
    package = repo / "src/simready"
    (package / "workflows").mkdir(parents=True)
    (package / "cli.py").write_text("# Alternate worker source checkout\n")
    worker = package / "workflows/video_execution.py"
    worker.write_text("# Worker implementation version 1\n")
    value = read_json(config)
    value["execution"]["repo"] = str(repo)
    write_json(config, value)
    first = video_pipeline.make_plan(load_pipeline(config, output))
    worker.write_text("# Worker implementation version 2\n")
    second = video_pipeline.make_plan(load_pipeline(config, output))
    assert first["identity"] != second["identity"]
    assert second["pipeline"]["worker_source"]["files"][
        "workflows/video_execution.py"
    ] == digest(worker)
    for stage in first["stages"]:
        assert (
            first["stage_contracts"]["identities"][stage]
            != second["stage_contracts"]["identities"][stage]
        ) is (stage == "scene_delivery")
    assert not output.exists()


def test_changed_ply_upgrade_refuses_old_semantic_association(semantic_configured):
    config, output = semantic_configured
    first, _, code = _run(config, output, until="decomposition")
    assert code == 0
    old_index = read_json(output / "stages/decomposition.json")
    old_receipt = output / old_index["receipt"]
    old_bytes = old_receipt.read_bytes()
    ply = config.parent / "gaussian.ply"
    data = bytearray(ply.read_bytes())
    offset = data.index(b"end_header\n") + len(b"end_header\n")
    struct.pack_into(
        "<f", data, offset, struct.unpack_from("<f", data, offset)[0] + 0.05
    )
    ply.write_bytes(data)
    manifest = config.parent / "reconstruction.json"
    _update_manifest(manifest, ply)
    value = read_json(config)
    provider = value["stages"]["reconstruction"]
    provider["manifest"] = reference(manifest)
    provider["algorithm"]["version"] = "2"
    write_json(config, value)
    changed, result, code = _run(config, output, until="decomposition", upgrade=True)
    assert code == 2 and result["status"] == "FAILED"
    assert "not associated with this reconstruction" in result["error"]
    history = {row["name"]: row for row in result["stages"]}
    assert history["observations"]["reused"] is True
    assert history["reconstruction"]["reused"] is False
    assert history["decomposition"]["reused"] is False
    assert (
        first["stage_contracts"]["identities"]["decomposition"]
        != changed["stage_contracts"]["identities"]["decomposition"]
    )
    assert old_receipt.read_bytes() == old_bytes
    assert not (output / "worker_inputs").exists()
    assert not (output / "execution").exists()
