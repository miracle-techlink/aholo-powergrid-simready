"""Exercise the new-video worker's evidence and failure boundaries on CPU."""

import struct
from types import SimpleNamespace

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.workflows import video_execution


def gaussian_ply(path):
    names = [
        "x",
        "y",
        "z",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
        "scale_0",
        "scale_1",
        "scale_2",
        "opacity",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
    ]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    path.write_bytes(
        header.encode()
        + b"".join(
            struct.pack("<14f", *([xyz] * 3 + [1, 0, 0, 0, -3, -3, -3, 0, 0, 0, 0]))
            for xyz in [0, 1]
        )
    )
    return path


def worker_inputs(tmp_path, *, prepare_only=True):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    ply = gaussian_ply(inputs / "splats.ply")
    write_json(inputs / "scene-spec.json", {"schema_version": 1})
    write_json(
        inputs / "input.json",
        {
            "source_video_sha256": "a" * 64,
            "ply_sha256": digest(ply),
            "spec_sha256": digest(inputs / "scene-spec.json"),
            "prepare_only": prepare_only,
        },
    )
    return inputs


def test_prepare_only_worker_completes_real_cpu_export_and_structure_audit(tmp_path):
    pytest.importorskip("pxr")
    output = tmp_path / "worker"
    inputs = worker_inputs(tmp_path)
    assert video_execution.execute(inputs, output) == 0
    status = read_json(output / "pipeline-status.json")
    result = read_json(output / "delivery.json")
    assert status["status"] == "COMPLETE"
    assert [row["name"] for row in status["stages"]] == [
        "preparation",
        "structure_audit",
    ]
    assert result["delivery_level"] == "SCENE_EXPORTED"
    assert result["video"] is None and result["robot_task_validated"] is False
    assert result["reconstruction_sha256"] == digest(inputs / "splats.ply")
    assert result["source_video_sha256"] == "a" * 64
    assert (output / result["scene"]).is_file()
    for item in result["artifacts"]:
        assert digest(output / item["path"]) == item["sha256"]
    assert not (output / "render").exists()


def test_input_hash_failure_writes_failed_status_without_running_subprocess(
    tmp_path, monkeypatch
):
    inputs = worker_inputs(tmp_path)
    (inputs / "splats.ply").write_bytes(b"different input")
    monkeypatch.setattr(
        video_execution.subprocess,
        "run",
        lambda *a, **k: pytest.fail("Invalid input reached worker"),
    )
    output = tmp_path / "worker"
    assert video_execution.execute(inputs, output) == 2
    status = read_json(output / "pipeline-status.json")
    assert status["status"] == "FAILED" and status["stage"] == "inputs"
    assert "inputs changed" in status["error"]
    assert not (output / "delivery.json").exists()


def test_worker_nonzero_subprocess_is_failed_and_stops_later_stages(
    tmp_path, monkeypatch
):
    calls = []

    def fail(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write("injected preparation failure\n")
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(video_execution.subprocess, "run", fail)
    output = tmp_path / "worker"
    assert video_execution.execute(worker_inputs(tmp_path), output) == 2
    status = read_json(output / "pipeline-status.json")
    assert status["status"] == "FAILED" and status["stage"] == "preparation"
    assert status["stages"][0]["returncode"] == 17
    assert len(calls) == 1
    assert not (output / "delivery.json").exists()
    assert "injected" in (output / "preparation.log").read_text()


def test_zero_exit_without_outputs_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        video_execution.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    output = tmp_path / "worker"
    assert video_execution.execute(worker_inputs(tmp_path), output) == 2
    assert read_json(output / "pipeline-status.json")["status"] == "FAILED"
    assert not (output / "delivery.json").exists()


def test_worker_never_overwrites_previous_evidence(tmp_path, monkeypatch):
    inputs = worker_inputs(tmp_path)
    output = tmp_path / "worker"
    write_json(output / "pipeline-status.json", {"status": "COMPLETE"})
    before = (output / "pipeline-status.json").read_bytes()
    monkeypatch.setattr(
        video_execution.subprocess,
        "run",
        lambda *a, **k: pytest.fail("Old output reran"),
    )
    with pytest.raises(FileExistsError, match="Previous worker evidence"):
        video_execution.execute(inputs, output)
    assert (output / "pipeline-status.json").read_bytes() == before


def test_structure_audit_rejects_unresolved_authored_reference(tmp_path):
    pytest.importorskip("pxr")
    from simready.evaluation.scene_package import inspect_scene
    from simready.simulation.scene_preparation import prepare_scene

    scene = tmp_path / "scene"
    prepare_scene(gaussian_ply(tmp_path / "source.ply"), scene)
    (scene / "objects.usda").unlink()
    with pytest.raises(ValueError, match="composition|missing|dependency"):
        inspect_scene(scene)


@pytest.mark.parametrize("tamper", ["video", "scene", "spec"])
def test_render_receipt_must_bind_video_scene_and_spec(tmp_path, monkeypatch, tamper):
    inputs = worker_inputs(tmp_path, prepare_only=False)
    output = tmp_path / "worker"

    def step(argv, **kwargs):
        module = argv[2]
        if module.endswith("scene_preparation"):
            (output / "scene").mkdir()
            (output / "scene/scene.usda").write_text("test scene")
            write_json(
                output / "scene/preparation.json",
                {
                    "metric_scale_verified": False,
                    "physics_ready": False,
                    "limitations": [],
                },
            )
        elif module.endswith("scene_package"):
            write_json(output / "structure-audit.json", {"passed": True})
        elif module.endswith("scene_video"):
            (output / "render").mkdir()
            (output / "render/scene_survey.mp4").write_bytes(b"encoded fixture")
            receipt = {
                "video_sha256": digest(output / "render/scene_survey.mp4"),
                "scene_sha256": digest(output / "scene/scene.usda"),
                "spec_sha256": digest(inputs / "scene-spec.json"),
            }
            receipt[tamper + "_sha256"] = "different"
            write_json(output / "render/render.json", receipt)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(video_execution.subprocess, "run", step)
    assert video_execution.execute(inputs, output) == 2
    status = read_json(output / "pipeline-status.json")
    assert status["status"] == "FAILED"
    assert "bound" in status["error"]
    assert not (output / "delivery.json").exists()
