"""Evidence-bound CPU estimation through real USD export, without model claims."""

import copy
import runpy
import shutil
import struct
from pathlib import Path

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference
from simready.ingestion.nerf2physics import run_estimation
from simready.simulation.physical_candidate import compile_candidate
from simready.workflows.physical_estimation import (
    estimation_plan,
    produce_estimation,
    verify_compilation,
)
from simready.workflows.video_execution import execute
from simready.workflows.video_pipeline import VideoStages


@pytest.fixture
def request_file(tmp_path):
    factory = runpy.run_path(
        str(Path(__file__).parents[2] / "examples/nerf2physics/create_fixture.py")
    )
    path = factory["create_request"](tmp_path / "source")
    request = read_json(path)
    request["object_id"] = "Cup"
    association_path = Path(request["association"]["evidence"]["path"])
    association = read_json(association_path)
    association["target_object_id"] = "Cup"
    write_json(association_path, association)
    request["association"]["evidence"] = reference(association_path)
    write_json(path, request)
    return path


def metric_spec():
    return {
        "schema_version": 1,
        "source_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "scale_provenance": {
            "status": "verified",
            "method": "synthetic metres",
            "evidence": "fixture",
        },
        "objects": [
            {
                "id": "Cup",
                "type": "box",
                "bounds_world": {"min": [0, 0, 0], "max": [0.1, 0.1, 0.1]},
                "physics": {"motion_type": "dynamic"},
            }
        ],
    }


def compile_test(spec, candidate):
    return compile_candidate(
        spec,
        candidate,
        candidate_sha256="a" * 64,
        source_video_sha256="b" * 64,
        reconstruction_sha256="c" * 64,
    )


def test_frozen_nested_features_cannot_change_between_plan_and_estimation(
    request_file, tmp_path
):
    plan = estimation_plan(None, request_file, tmp_path / "run")
    feature = request_file.parent / "features.npz"
    assert reference(feature) in plan["dependencies"]
    feature.write_bytes(b"changed after planning")
    with pytest.raises(ValueError, match="SHA256"):
        produce_estimation(plan, metric_spec(), "b" * 64, "c" * 64, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


def test_estimator_stage_accepts_empty_diagnostic_logs_and_resumes(
    request_file, tmp_path
):
    root = tmp_path / "run"
    root.mkdir()
    plan = estimation_plan(None, request_file, root)
    stages = VideoStages(root, "test-identity")
    stages.run(
        "physical_estimation",
        lambda: produce_estimation(
            plan, metric_spec(), "b" * 64, "c" * 64, root / "stage"
        ),
    )
    assert (root / "stage/estimation.stderr.log").stat().st_size == 0
    stages.run("physical_estimation", lambda: pytest.fail("Valid estimation reran"))
    assert stages.history[-1]["reused"] is True


@pytest.mark.parametrize("field", ["evidence", "association", "scale"])
def test_import_cannot_replace_required_file_references_with_claims(
    request_file, tmp_path, field
):
    candidate_path = run_estimation(request_file, tmp_path / "estimate")
    candidate = read_json(candidate_path)
    if field == "evidence":
        candidate["evidence"] = [{"note": "trust me"}]
    elif field == "association":
        candidate["association"]["evidence"] = {"note": "trust me"}
    else:
        candidate["method"]["parameters"]["scale_evidence"] = {}
    write_json(candidate_path, candidate)
    plan = estimation_plan(candidate_path, None, tmp_path / "run")
    with pytest.raises(ValueError, match="reference"):
        produce_estimation(plan, metric_spec(), "b" * 64, "c" * 64, tmp_path / "stage")


@pytest.mark.parametrize(
    "case",
    [
        "scale",
        "conflict",
        "body",
        "ambiguous",
        "unit",
        "range",
        "friction_only",
        "malformed",
    ],
)
def test_compile_rejects_ambiguous_or_unsupported_adoption(
    request_file, tmp_path, case
):
    candidate = read_json(run_estimation(request_file, tmp_path / "estimate"))
    spec = metric_spec()
    if case == "scale":
        del spec["scale_provenance"]
    elif case == "conflict":
        spec["objects"][0]["physics"]["mass_kg"] = 999
    elif case == "body":
        spec["objects"][0]["type"] = "button"
    elif case == "ambiguous":
        spec["objects"].append(copy.deepcopy(spec["objects"][0]))
    elif case == "unit":
        candidate["properties"]["mass_kg"]["unit"] = "g"
    elif case == "range":
        candidate["properties"]["mass_kg"]["interval"] = [10, 20]
    elif case == "friction_only":
        candidate["properties"] = {"kinetic_friction": {"value": 0.5, "unit": "1"}}
    else:
        candidate["properties"] = []
    with pytest.raises(ValueError):
        compile_test(spec, candidate)


def test_compile_preserves_motion_kind_and_does_not_apply_other_properties(
    request_file, tmp_path
):
    candidate = read_json(run_estimation(request_file, tmp_path / "estimate"))
    candidate["properties"]["fabric_relative_friction"] = {"value": 0.5, "unit": "1"}
    spec = metric_spec()
    del spec["objects"][0]["physics"]
    compiled, report = compile_test(spec, candidate)
    assert "motion_type" not in compiled["objects"][0]["physics"]
    assert "contact" not in compiled["objects"][0]["physics"]
    assert report["not_applied"] == ["fabric_relative_friction"]
    assert report["fresh_visual_inference"] is report["real_world_validated"] is False
    assert "physics" not in spec["objects"][0]


def test_estimate_package_relocates_and_exports_mass_to_composed_usd(
    request_file, tmp_path
):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    inputs = tmp_path / "worker-inputs"
    inputs.mkdir()
    names = "x y z rot_0 rot_1 rot_2 rot_3 scale_0 scale_1 scale_2 opacity f_dc_0 f_dc_1 f_dc_2".split()
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    ply = inputs / "splats.ply"
    ply.write_bytes(
        header.encode()
        + b"".join(
            struct.pack("<14f", *([x] * 3 + [1, 0, 0, 0, -3, -3, -3, 0, 0, 0, 0]))
            for x in [0, 1]
        )
    )
    plan = estimation_plan(None, request_file, tmp_path / "stage")
    result, artifacts = produce_estimation(
        plan, metric_spec(), "b" * 64, digest(ply), tmp_path / "stage"
    )
    assert artifacts and Path(result["directory"]).is_dir()
    shutil.copytree(result["directory"], inputs / "physical")
    spec = read_json(inputs / "physical/compiled-spec.json")
    write_json(inputs / "scene-spec.json", spec)
    # Original inputs can disappear: the delivered evidence must be self-contained.
    shutil.rmtree(request_file.parent)
    shutil.rmtree(tmp_path / "stage")
    binding = verify_compilation(inputs / "physical", spec, "b" * 64, digest(ply))
    write_json(
        inputs / "input.json",
        {
            "source_video_sha256": "b" * 64,
            "ply_sha256": digest(ply),
            "spec_sha256": digest(inputs / "scene-spec.json"),
            "prepare_only": True,
            "physical_compilation": binding,
        },
    )
    output = tmp_path / "worker"
    assert execute(inputs, output) == 0
    delivery = read_json(output / "delivery.json")
    assert delivery["physical_estimation"] == "synthetic_fixture"
    assert delivery["physical_asset_validation"] == "PASS"
    assert delivery["robot_task_validated"] is False
    stage = Usd.Stage.Open(str(output / "scene/scene.usda"))
    masses = [
        UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        for prim in stage.Traverse()
        if prim.HasAPI(UsdPhysics.MassAPI)
    ]
    expected = spec["objects"][0]["physics"]["mass_kg"]
    assert any(mass == pytest.approx(expected) for mass in masses)
    report = output / "physical/compilation.json"
    altered = read_json(report)
    altered["adopted"]["mass_kg"]["value"] = 2
    write_json(report, altered)
    with pytest.raises(ValueError, match="not bound"):
        verify_compilation(output / "physical", spec, "b" * 64, digest(ply))
