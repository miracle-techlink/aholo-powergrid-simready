"""Real CPU USD delivery and failures at the independent inspector boundary."""

import sys

import pytest

from examples.semantic_assets.create_fixture import create_fixture
from simready.core.artifacts import digest, read_json, write_json
from simready.evaluation.physical_receipt import verify_physical_receipt
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.workflows import semantic_assets, video_execution, video_pipeline
from tests.workflows.test_video_execution import worker_inputs


@pytest.fixture
def delivered(tmp_path):
    pytest.importorskip("pxr")
    inputs = worker_inputs(tmp_path)
    spec = inputs / "scene-spec.json"
    write_json(
        spec,
        {
            "schema_version": 1,
            "objects": [
                {
                    "id": "Cabinet",
                    "type": "box",
                    "category": "cabinet",
                    "bounds_world": {"min": [0, 0, 0], "max": [1, 1, 1]},
                    "physics": {
                        "contact": {"static_friction": 0.5, "dynamic_friction": 0.3}
                    },
                }
            ],
        },
    )
    source = read_json(inputs / "input.json")
    source["spec_sha256"] = digest(spec)
    write_json(inputs / "input.json", source)
    output = tmp_path / "worker"
    assert video_execution.execute(inputs, output) == 0
    return inputs, output, source


def test_video_declared_objects_get_real_readback_and_parent_verification(delivered):
    inputs, output, source = delivered
    evidence = read_json(output / "delivery.json")
    assert evidence["physical_asset_validation"] == "PASS"
    assert [row["name"] for row in evidence["stages"]] == [
        "preparation",
        "structure_audit",
        "physical_audit",
    ]
    video_pipeline.verify_delivery(
        output, evidence, source, {}, inputs / "scene-spec.json"
    )
    audit = read_json(output / "physical-audit.json")
    assert audit["real_world_calibration"]["status"] == "NOT_EVALUATED"
    assert evidence["robot_task_validated"] is False


@pytest.mark.parametrize(
    "corruption",
    [
        "failed",
        "empty_checks",
        "failed_check",
        "wrong_spec",
        "missing_asset",
        "changed_catalog",
        "changed_layer",
        "missing_dependencies",
        "escape",
    ],
)
def test_inspector_receipt_rejects_stale_or_incomplete_evidence(delivered, corruption):
    inputs, output, _ = delivered
    audit = read_json(output / "physical-audit.json")
    if corruption == "failed":
        audit["status"] = "FAIL"
    elif corruption == "empty_checks":
        audit["checks"] = []
    elif corruption == "failed_check":
        audit["checks"][0]["passed"] = False
    elif corruption == "wrong_spec":
        audit["inputs"]["scene_spec"]["sha256"] = "wrong"
    elif corruption == "missing_asset":
        audit["assets"] = []
    elif corruption == "changed_catalog":
        (output / "scene/asset_catalog.json").write_text("{}")
    elif corruption == "changed_layer":
        with (output / "scene/objects.usda").open("a") as stream:
            stream.write("\n# Changed after audit\n")
    elif corruption == "missing_dependencies":
        audit["dependencies"] = []
    elif corruption == "escape":
        audit["dependencies"][0]["path"] = "../../elsewhere.usda"
    with pytest.raises(ValueError, match="Physical audit"):
        verify_physical_receipt(
            audit, directory=output / "scene", scene_spec=inputs / "scene-spec.json"
        )


def test_parent_rejects_removed_audit_even_with_valid_worker_status(delivered):
    inputs, output, source = delivered
    evidence = read_json(output / "delivery.json")
    evidence["artifacts"] = [
        r for r in evidence["artifacts"] if r["path"] != "physical-audit.json"
    ]
    with pytest.raises(ValueError, match="independent physical audit"):
        video_pipeline.verify_delivery(
            output, evidence, source, {}, inputs / "scene-spec.json"
        )


def test_semantic_workflow_zero_exit_cannot_promote_a_failed_audit(
    tmp_path, monkeypatch
):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")
    out = tmp_path / "workflow"
    original = semantic_assets._worker

    def invalid_audit(*args):
        if "simready.evaluation.physical_assets" not in args[0]:
            return original(*args)
        write_json(out / "physical_audit.json", {"status": "FAIL", "passed": False})
        return 0

    monkeypatch.setattr(semantic_assets, "_worker", invalid_audit)
    with pytest.raises(ValueError, match="did not pass"):
        semantic_assets.run_semantic_assets(**inputs, output_dir=out)
    assert read_json(out / "workflow.json")["status"] == "FAILED"


def test_semantic_cancellation_is_recorded_as_interrupted(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(semantic_assets, "_worker", interrupt)
    with pytest.raises(KeyboardInterrupt):
        semantic_assets.run_semantic_assets(**inputs, output_dir=tmp_path / "workflow")
    assert read_json(tmp_path / "workflow/workflow.json")["status"] == "INTERRUPTED"


def test_semantic_worker_uses_bounded_process_execution(tmp_path):
    with pytest.raises(ProcessTimeoutError):
        semantic_assets._worker(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            tmp_path / "worker.log",
            0.1,
        )
    assert (tmp_path / "worker.stderr.log").is_file()


@pytest.mark.parametrize(
    "error,status",
    [
        (ProcessTimeoutError(0.1, 0.1), "TIMEOUT"),
        (ProcessCancelledError(0.1), "CANCELLED"),
    ],
)
def test_semantic_terminal_state_preserves_failed_stage_time(
    tmp_path, monkeypatch, error, status
):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")

    def fail(*args):
        raise error

    monkeypatch.setattr(semantic_assets, "_worker", fail)
    with pytest.raises(type(error)):
        semantic_assets.run_semantic_assets(**inputs, output_dir=tmp_path / "workflow")
    report = read_json(tmp_path / "workflow/workflow.json")
    assert report["status"] == report["steps"][-1]["status"] == status
    assert report["steps"][-1]["wall_s"] >= 0


def test_semantic_cuda_request_remaps_selected_physical_device(tmp_path, monkeypatch):
    inputs = create_fixture(tmp_path / "inputs")
    inputs.pop("segmentation_manifest")
    inputs["image"] = tmp_path / "inputs/image.png"

    def fail(command, log, timeout, **kwargs):
        assert command[command.index("--device") + 1] == "cuda:0"
        assert kwargs["gpu"] == "3"
        assert timeout <= 0.5
        raise ProcessTimeoutError(timeout, 0)

    monkeypatch.setattr(semantic_assets, "_worker", fail)
    with pytest.raises(ProcessTimeoutError):
        semantic_assets.run_semantic_assets(
            **inputs, output_dir=tmp_path / "workflow", device="cuda:3", timeout_s=0.5
        )
    assert (
        read_json(tmp_path / "workflow/workflow.json")["failed_stage"] == "segmentation"
    )


def test_semantic_worker_owns_fake_inventory_lease_without_using_gpu(tmp_path):
    inventory = {
        "schema_version": 1,
        "cpu_slots": 1,
        "ram_gb": 1,
        "gpus": [
            {
                "index": 3,
                "uuid": "GPU-semantic-fixture",
                "vram_gb": 1,
                "capabilities": ["cuda"],
            }
        ],
    }
    log = tmp_path / "segmentation.log"
    assert (
        semantic_assets._worker(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ['CUDA_VISIBLE_DEVICES'])",
            ],
            log,
            5,
            gpu="3",
            device_inventory=inventory,
            lock_dir=tmp_path / "locks",
        )
        == 0
    )
    assert log.read_text().strip() == "GPU-semantic-fixture"
    assert (
        read_json(tmp_path / "segmentation.lease.json")["gpu_uuid"]
        == "GPU-semantic-fixture"
    )


@pytest.mark.parametrize(
    "changed", ["ply", "calibration", "segmentation_manifest", "mask"]
)
def test_semantic_workflow_preserves_projection_evidence_through_authoring(
    tmp_path, monkeypatch, changed
):
    pytest.importorskip("pxr")
    inputs = create_fixture(tmp_path / "inputs")
    out = tmp_path / "workflow"
    original = semantic_assets._worker

    def mutate_source(*args, **kwargs):
        result = original(*args, **kwargs)
        if "authoring" not in args[0]:
            return result
        if changed == "mask":
            manifest = read_json(inputs["segmentation_manifest"])
            path = (
                inputs["segmentation_manifest"].parent
                / manifest["instances"][0]["mask"]["path"]
            )
        else:
            path = inputs[changed]
        with path.open("ab") as stream:
            stream.write(b"\n")
        return result

    monkeypatch.setattr(semantic_assets, "_worker", mutate_source)
    with pytest.raises(ValueError, match="Frozen campaign evidence changed"):
        semantic_assets.run_semantic_assets(**inputs, output_dir=out)
    assert read_json(out / "workflow.json")["status"] == "FAILED"
