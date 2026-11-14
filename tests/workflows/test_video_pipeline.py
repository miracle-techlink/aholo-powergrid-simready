"""New-video boundaries, resumability and public command routing without a GPU."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from simready.cli import main
from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.workflows import video_pipeline


def test_video_help_does_not_import_numeric_or_gpu_libraries(tmp_path):
    program = """
import importlib.abc,sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy','scipy','torch','isaacsim','pxr','requests'}:
            raise AssertionError('Help imported optional module '+fullname)
sys.meta_path.insert(0,Reject())
from simready.cli import main
main(['run','--video','input.mp4','--help'])
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--submit-reconstruction" in result.stdout
    assert "--worker-timeout" in result.stdout


def test_public_dry_run_is_offline_and_does_not_create_files(
    tmp_path, monkeypatch, capsys
):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"read-only plan does not decode")
    output = tmp_path / "run"
    monkeypatch.setattr(
        video_pipeline, "execute_video", lambda *a: pytest.fail("dry-run executed")
    )
    assert main(["run", "--video", str(video), "--out", str(output), "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "PLANNED" and plan["mode"] == "video_to_scene"
    assert plan["new_cloud_submission_authorized"] is False
    assert plan["worker_timeout_s"] == 7200.0
    assert not output.exists()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_worker_budget_is_rejected_before_execution(tmp_path, value):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source")
    output = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--video",
                str(video),
                "--out",
                str(output),
                "--worker-timeout",
                value,
            ]
        )
        == 3
    )
    assert not output.exists()


def test_scene_and_video_inputs_are_exclusive():
    with pytest.raises(SystemExit) as error:
        main(["run", "--video", "input.mp4", "--scene", "substation"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "render",
    [{"width": 1279}, {"frames": True}, {"subframes": 0}, {"fps": 500}, {"typo": 1}],
)
def test_invalid_render_configuration_is_rejected_before_execution(render):
    with pytest.raises(ValueError):
        video_pipeline.validate_render(render)


def test_resume_reuses_only_verified_completed_stage(tmp_path):
    def work():
        path = tmp_path / "model.ply"
        path.write_bytes(b"model")
        return {"model": str(path)}, [path]

    first = video_pipeline.VideoStages(tmp_path, "identity")
    expected = first.run("reconstruction", work)
    second = video_pipeline.VideoStages(tmp_path, "identity")
    assert (
        second.run("reconstruction", lambda: pytest.fail("completed stage reran"))
        == expected
    )
    assert second.history[0]["reused"] is True
    (tmp_path / "model.ply").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        second.run("reconstruction", lambda: pytest.fail("tampered stage reran"))


def test_failed_stage_is_not_recorded_as_success(tmp_path):
    def fail():
        (tmp_path / "partial.dat").write_bytes(b"partial")
        raise RuntimeError("partial operation")

    with pytest.raises(RuntimeError):
        video_pipeline.VideoStages(tmp_path, "identity").run("prepare", fail)
    assert not (tmp_path / "stages/prepare.json").exists()


@pytest.mark.parametrize("reused", [False, True])
def test_contract_failure_preserves_success_evidence_and_records_attempt(
    tmp_path, reused
):
    artifact = tmp_path / "model.ply"
    artifact.write_bytes(b"model")
    receipt = tmp_path / "stages/reconstruction.json"

    def action():
        return {"model": str(artifact)}, [artifact]

    if reused:
        video_pipeline.VideoStages(tmp_path, "id").run("reconstruction", action)
    before = receipt.read_bytes() if reused else None

    def reject(result, artifacts):
        assert result["model"] == str(artifact)
        assert artifacts[0]["sha256"] == digest(artifact)
        raise ValueError("Semantic contract rejected")

    stages = video_pipeline.VideoStages(tmp_path, "id")
    with pytest.raises(ValueError, match="Semantic contract"):
        stages.run("reconstruction", action, reject)
    assert (receipt.read_bytes() if receipt.exists() else None) == before
    failure = read_json(
        next((tmp_path / "stage-failures/reconstruction").glob("*.json"))
    )
    assert failure["status"] == "FAILED" and failure["reused"] is reused
    assert stages.history == [failure]


def test_stage_receipt_cannot_read_outside_run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    external = tmp_path / "external"
    external.write_text("no")
    write_json(
        root / "stages/test.json",
        {
            "identity": "id",
            "result": {},
            "artifacts": [{"path": "../external", "sha256": "unused", "bytes": 2}],
        },
    )
    with pytest.raises(ValueError, match="escaped"):
        video_pipeline.VideoStages(root, "id").run(
            "test", lambda: pytest.fail("executed")
        )


def test_changed_stage_identity_cannot_resume(tmp_path):
    artifact = tmp_path / "x"
    artifact.write_text("data")
    video_pipeline.VideoStages(tmp_path, "old-code").run(
        "prepare", lambda: ({}, [artifact])
    )
    with pytest.raises(ValueError, match="identity"):
        video_pipeline.VideoStages(tmp_path, "new-code").run(
            "prepare", lambda: pytest.fail("executed")
        )


def test_output_cannot_contain_source(tmp_path, capsys):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"input")
    assert (
        main(["run", "--video", str(video), "--out", str(tmp_path), "--dry-run"]) == 3
    )
    assert "cannot contain" in capsys.readouterr().out


def test_failed_execution_gets_new_attempt_directory(tmp_path):
    first = video_pipeline.fresh_attempt(tmp_path, "execution")
    first.mkdir()
    (first / "result.json").write_text("preserve")
    second = video_pipeline.fresh_attempt(tmp_path, "execution")
    assert first != second and not second.exists()
    assert (first / "result.json").read_text() == "preserve"


def test_prepared_scene_help_still_routes(capsys):
    with pytest.raises(SystemExit) as error:
        main(["run", "--scene", "substation", "--help"])
    assert error.value.code == 0
    assert "--bundle-spec" in capsys.readouterr().out


@pytest.fixture
def offline_video_run(tmp_path, monkeypatch):
    """Keep reconstruction/import and stage ledgers real; replace only media workers."""
    video = tmp_path / "source video.mp4"
    video.write_bytes(b"input video fixture")
    assets = tmp_path / "provided"
    assets.mkdir()
    ply = assets / "splats.ply"
    ply.write_bytes(b"PLY fixture for orchestration")
    manifest = assets / "reconstruction.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "source_video_sha256": digest(video),
            "artifacts": [
                {
                    "kind": "ply",
                    "path": ply.name,
                    "bytes": ply.stat().st_size,
                    "sha256": digest(ply),
                }
            ],
        },
    )
    calls = {"observations": 0, "execution": []}

    def observations(source, target, **kwargs):
        calls["observations"] += 1
        assert source == video
        target.mkdir()
        (target / "frame.png").write_bytes(b"frame fixture")
        result = {
            "schema_version": 1,
            "video_sha256": digest(source),
            "fps": kwargs["fps"],
            "frames": [{"path": "frame.png", "sha256": digest(target / "frame.png")}],
        }
        write_json(target / "manifest.json", result)
        return result

    def launch(argv, **kwargs):
        output, inputs = Path(kwargs["output"]), Path(kwargs["assets"])
        calls["execution"].append(output)
        assert argv[argv.index("--inputs") + 1] == str(inputs)
        assert argv[argv.index("--out") + 1] == str(output)
        source = read_json(inputs / "input.json")
        scene = output / "scene/scene.usda"
        scene.parent.mkdir(parents=True)
        scene.write_text("exported scene fixture")
        write_json(
            output / "scene/preparation.json",
            {
                "schema_version": 1,
                "source_sha256": source["ply_sha256"],
                "spec_sha256": object_digest(read_json(inputs / "scene-spec.json")),
                "metric_scale_verified": False,
                "physics_ready": False,
                "limitations": ["fixture"],
                "files": [
                    {
                        "path": "scene.usda",
                        "sha256": digest(scene),
                        "bytes": scene.stat().st_size,
                    }
                ],
            },
        )
        write_json(
            output / "structure-audit.json",
            {
                "schema_version": 1,
                "passed": True,
                "scope": "USD structure only",
                "dependencies": [{"path": "scene.usda", "sha256": digest(scene)}],
            },
        )
        write_json(
            output / "pipeline-status.json",
            {"schema_version": 1, "status": "COMPLETE", "stage": "summary"},
        )
        write_json(
            output / "delivery.json",
            {
                "schema_version": 1,
                "status": "COMPLETE",
                "delivery_level": "SCENE_EXPORTED",
                "source_video_sha256": source["source_video_sha256"],
                "reconstruction_sha256": source["ply_sha256"],
                "scene_spec_sha256": source["spec_sha256"],
                "scene": "scene/scene.usda",
                "video": None,
                "metric_scale_verified": False,
                "metric_scale_independently_validated": False,
                "physics_configured": False,
                "physical_asset_validation": "NOT_EVALUATED",
                "robot_task_validated": False,
                "limitations": ["fixture"],
                "artifacts": [
                    {
                        "path": str(path.relative_to(output)),
                        "bytes": path.stat().st_size,
                        "sha256": digest(path),
                    }
                    for path in (
                        scene,
                        output / "scene/preparation.json",
                        output / "structure-audit.json",
                    )
                ],
            },
        )
        return 0

    from simready.ingestion import video_frames

    monkeypatch.setattr(video_frames, "extract_frames", observations)
    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    monkeypatch.setattr(
        video_pipeline, "implementation_identity", lambda: "fixed-test-code"
    )
    monkeypatch.delenv("AHOLO_API_KEY", raising=False)
    output = tmp_path / "run"
    args = [
        "run",
        "--video",
        str(video),
        "--out",
        str(output),
        "--backend",
        "local",
        "--prepare-only",
        "--reconstruction-manifest",
        str(manifest),
    ]
    return {
        "args": args,
        "output": output,
        "video": video,
        "calls": calls,
        "launch": launch,
    }


def test_public_video_pipeline_offline_import_and_complete_resume(offline_video_run):
    job = offline_video_run
    assert main(job["args"]) == 0
    first = read_json(job["output"] / "summary.json")
    assert first["status"] == "COMPLETE"
    assert first["delivery_level"] == "SCENE_EXPORTED"
    assert first["reconstruction_new_submission"] is False
    assert first["task_validated"] is False
    assert Path(first["scene"]).is_file()
    assert main([*job["args"], "--resume"]) == 0
    resumed = read_json(job["output"] / "summary.json")
    assert all(item["reused"] for item in resumed["stages"])
    assert job["calls"]["observations"] == 1
    assert len(job["calls"]["execution"]) == 1


@pytest.mark.parametrize("status", ["TIMEOUT", "CANCELLED"])
def test_inner_worker_terminal_state_reaches_video_summary(
    offline_video_run, monkeypatch, status
):
    job = offline_video_run

    def launch(*args, **kwargs):
        write_json(
            Path(kwargs["output"]) / "pipeline-status.json",
            {"status": status, "stage": "preparation", "wall_s": 1},
        )
        return 2

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    assert main(job["args"]) == 2
    assert read_json(job["output"] / "summary.json")["status"] == status
    failures = list((job["output"] / "stage-failures/scene_delivery").glob("*.json"))
    assert len(failures) == 1 and read_json(failures[0])["status"] == status


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ProcessTimeoutError(0.25, 0.25), "TIMEOUT"),
        (ProcessCancelledError(0.1), "CANCELLED"),
        (KeyboardInterrupt(), "INTERRUPTED"),
    ],
)
def test_worker_budget_and_terminal_state_survive_resume(
    offline_video_run, monkeypatch, error, status
):
    job = offline_video_run
    args = [*job["args"], "--worker-timeout", "0.25"]
    failed_attempts = []

    def launch(argv, **kwargs):
        assert kwargs["timeout_s"] == 0.25
        current = read_json(job["output"] / "summary.json")
        assert current["status"] == "RUNNING" and current["stage"] == "scene_delivery"
        output = Path(kwargs["output"])
        output.mkdir(parents=True)
        (output / "stderr.log").write_text("partial worker evidence")
        failed_attempts.append(output)
        raise error

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            main(args)
    else:
        assert main(args) == 2
    assert read_json(job["output"] / "run-plan.json")["worker_timeout_s"] == 0.25
    source = read_json(job["output"] / "worker_inputs/attempt-0001/input.json")
    assert source["worker_timeout_s"] == 0.25
    summary = read_json(job["output"] / "summary.json")
    assert summary["status"] == status and summary["stage"] == "scene_delivery"
    failure_path = next(
        (job["output"] / "stage-failures/scene_delivery").glob("*.json")
    )
    before = failure_path.read_bytes()
    assert read_json(failure_path)["status"] == status
    assert summary["stages"][-1]["status"] == status
    assert not (job["output"] / "stages/scene_delivery.json").exists()
    monkeypatch.setattr(video_pipeline, "launch_worker", job["launch"])
    assert main([*args, "--resume"]) == 0
    assert failure_path.read_bytes() == before
    assert (failed_attempts[0] / "stderr.log").read_text() == "partial worker evidence"
    assert job["calls"]["execution"][0] != failed_attempts[0]


def test_worker_budget_change_invalidates_resume_plan(offline_video_run):
    job = offline_video_run
    assert main([*job["args"], "--worker-timeout", "37"]) == 0
    before = (job["output"] / "summary.json").read_bytes()
    assert main([*job["args"], "--worker-timeout", "38", "--resume"]) == 3
    assert (job["output"] / "summary.json").read_bytes() == before
    assert len(job["calls"]["execution"]) == 1


def test_worker_input_budget_cannot_drift_from_plan(offline_video_run):
    job = offline_video_run
    assert main(job["args"]) == 0
    source_path = job["output"] / "worker_inputs/attempt-0001/input.json"
    source = read_json(source_path)
    source["worker_timeout_s"] += 1
    write_json(source_path, source)
    receipt_path = job["output"] / "stages/worker_inputs.json"
    receipt = read_json(receipt_path)
    for item in receipt["artifacts"]:
        if item["path"].endswith("/input.json"):
            item.update(sha256=digest(source_path), bytes=source_path.stat().st_size)
    write_json(receipt_path, receipt)
    assert main([*job["args"], "--resume"]) == 2
    summary = read_json(job["output"] / "summary.json")
    assert summary["stage"] == "worker_inputs" and summary["status"] == "FAILED"
    assert "frozen run plan" in summary["error"]
    assert len(job["calls"]["execution"]) == 1


@pytest.mark.parametrize("field", ["scene", "delivery_level", "execution"])
def test_resume_rejects_delivery_result_drift_from_verified_receipt(
    offline_video_run, field
):
    job = offline_video_run
    assert main(job["args"]) == 0
    path = job["output"] / "stages/scene_delivery.json"
    receipt = read_json(path)
    receipt["result"][field] = "unbacked-result"
    write_json(path, receipt)
    assert main([*job["args"], "--resume"]) == 2
    assert len(job["calls"]["execution"]) == 1
    assert read_json(job["output"] / "summary.json")["status"] == "FAILED"


@pytest.mark.parametrize(
    "field", ["ply_sha256", "source_video_sha256", "manifest_path"]
)
def test_resume_rejects_reconstruction_result_drift(offline_video_run, field):
    job = offline_video_run
    assert main(job["args"]) == 0
    path = job["output"] / "stages/reconstruction.json"
    receipt = read_json(path)
    receipt["result"][field] = "unbacked-result"
    write_json(path, receipt)
    assert main([*job["args"], "--resume"]) == 2
    assert len(job["calls"]["execution"]) == 1


def test_resume_after_published_reconstruction_allows_new_cache_invocation_flags(
    offline_video_run,
):
    job = offline_video_run
    assert main(job["args"]) == 0
    # Simulate the crash window after a completion manifest was published but
    # before its workflow-stage receipt. The artifact content remains identical.
    completion = job["output"] / "reconstruction/reconstruction.json"
    published = read_json(completion)
    published.update(cache_hit=False, new_submission=True, resumed=False)
    write_json(completion, published)
    (job["output"] / "stages/reconstruction.json").unlink()
    assert main([*job["args"], "--resume"]) == 0
    restored = read_json(job["output"] / "stages/reconstruction.json")["result"]
    assert restored["cache_hit"] is True
    assert restored["new_submission"] is False
    assert read_json(completion)["new_submission"] is True
    assert len(job["calls"]["execution"]) == 1


def test_worker_cannot_rebind_input_to_another_video(offline_video_run, monkeypatch):
    job = offline_video_run

    def launch(argv, **kwargs):
        path = Path(kwargs["assets"]) / "input.json"
        source = read_json(path)
        source["source_video_sha256"] = "another-video"
        write_json(path, source)
        return job["launch"](argv, **kwargs)

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    assert main(job["args"]) == 2
    assert "frozen inputs" in read_json(job["output"] / "summary.json")["error"]
    assert not (job["output"] / "stages/scene_delivery.json").exists()


@pytest.mark.parametrize("corruption", ["hash", "missing", "unlisted"])
def test_parent_rechecks_structure_audit_dependencies(
    offline_video_run, monkeypatch, corruption
):
    job = offline_video_run

    def launch(argv, **kwargs):
        code = job["launch"](argv, **kwargs)
        output = Path(kwargs["output"])
        audit_path = output / "structure-audit.json"
        audit = read_json(audit_path)
        if corruption == "missing":
            audit["dependencies"] = []
        elif corruption == "hash":
            audit["dependencies"][0]["sha256"] = "another-scene"
        else:
            hidden = output / "scene/unlisted.usda"
            hidden.write_text("# a dependency omitted from the delivery manifest")
            audit["dependencies"].append(
                {"path": hidden.name, "sha256": digest(hidden)}
            )
        write_json(audit_path, audit)
        evidence = read_json(output / "delivery.json")
        for item in evidence["artifacts"]:
            if item["path"] == "structure-audit.json":
                item["sha256"] = digest(audit_path)
                item["bytes"] = audit_path.stat().st_size
        write_json(output / "delivery.json", evidence)
        return code

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    assert main(job["args"]) == 2
    assert "Structure audit" in read_json(job["output"] / "summary.json")["error"]


@pytest.mark.parametrize(
    "failure",
    [
        "nonzero",
        "missing_status",
        "missing_delivery",
        "artifact_changed",
        "source_changed",
    ],
)
def test_worker_failure_or_invalid_artifact_cannot_complete_pipeline(
    offline_video_run, monkeypatch, failure
):
    job = offline_video_run

    def launch(argv, **kwargs):
        job["launch"](argv, **kwargs)
        output = Path(kwargs["output"])
        if failure == "nonzero":
            return 17
        if failure == "missing_status":
            (output / "pipeline-status.json").unlink()
        elif failure == "missing_delivery":
            (output / "delivery.json").unlink()
        elif failure == "artifact_changed":
            (output / "scene/scene.usda").write_text("tampered")
        else:
            data = read_json(output / "delivery.json")
            data["source_video_sha256"] = "different video"
            write_json(output / "delivery.json", data)
        return 0

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    assert main(job["args"]) == 2
    summary = read_json(job["output"] / "summary.json")
    assert summary["status"] == "FAILED" and summary["stage"] == "scene_delivery"
    assert not (job["output"] / "stages/scene_delivery.json").exists()


def test_resume_after_worker_failure_preserves_first_attempt_and_reuses_inputs(
    offline_video_run, monkeypatch
):
    job = offline_video_run

    def failed_launch(argv, **kwargs):
        job["launch"](argv, **kwargs)
        return 17

    monkeypatch.setattr(video_pipeline, "launch_worker", failed_launch)
    assert main(job["args"]) == 2
    first = job["calls"]["execution"][0]
    original = (first / "delivery.json").read_bytes()
    monkeypatch.setattr(video_pipeline, "launch_worker", job["launch"])
    assert main([*job["args"], "--resume"]) == 0
    summary = read_json(job["output"] / "summary.json")
    assert [row["reused"] for row in summary["stages"]] == [True, True, True, False]
    assert job["calls"]["observations"] == 1
    assert job["calls"]["execution"][1] != first
    assert (first / "delivery.json").read_bytes() == original


def test_resume_rejects_changed_source_without_launching_another_worker(
    offline_video_run,
):
    job = offline_video_run
    assert main(job["args"]) == 0
    summary = (job["output"] / "summary.json").read_bytes()
    job["video"].write_bytes(b"new source video")
    assert main([*job["args"], "--resume"]) == 3
    assert len(job["calls"]["execution"]) == 1
    assert (job["output"] / "summary.json").read_bytes() == summary


def test_resume_artifact_tamper_fails_without_reexecuting_completed_stage(
    offline_video_run,
):
    job = offline_video_run
    assert main(job["args"]) == 0
    receipt = read_json(job["output"] / "stages/observations.json")
    artifact = job["output"] / receipt["artifacts"][0]["path"]
    artifact.write_bytes(b"modified artifact")
    assert main([*job["args"], "--resume"]) == 2
    summary = read_json(job["output"] / "summary.json")
    assert summary["status"] == "FAILED" and summary["stage"] == "observations"
    assert job["calls"]["observations"] == 1
    assert len(job["calls"]["execution"]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "empty_artifacts",
        "reconstruction_changed",
        "scene_escape",
        "failed_delivery",
        "spec_changed",
        "missing_preparation",
        "failed_audit",
        "preparation_source_changed",
    ],
)
def test_complete_worker_status_requires_bound_delivery(
    offline_video_run, monkeypatch, failure
):
    job = offline_video_run

    def launch(argv, **kwargs):
        job["launch"](argv, **kwargs)
        output = Path(kwargs["output"])
        data = read_json(output / "delivery.json")
        if failure == "empty_artifacts":
            data["artifacts"] = []
            (output / "scene/scene.usda").unlink()
        elif failure == "reconstruction_changed":
            data["reconstruction_sha256"] = "another reconstruction of same video"
        elif failure == "scene_escape":
            data["scene"] = "../../external.usda"
        elif failure == "failed_delivery":
            data["status"] = "FAILED"
        elif failure == "spec_changed":
            data["scene_spec_sha256"] = "different configuration"
        elif failure == "missing_preparation":
            (output / "scene/preparation.json").unlink()
            data["artifacts"] = [
                item
                for item in data["artifacts"]
                if item["path"] != "scene/preparation.json"
            ]
        else:
            path = output / (
                "structure-audit.json"
                if failure == "failed_audit"
                else "scene/preparation.json"
            )
            receipt = read_json(path)
            receipt["passed" if failure == "failed_audit" else "source_sha256"] = (
                False if failure == "failed_audit" else "different PLY"
            )
            write_json(path, receipt)
            # Keep byte hashes consistent: acceptance must also check semantics.
            for item in data["artifacts"]:
                item["sha256"] = digest(output / item["path"])
                item["bytes"] = (output / item["path"]).stat().st_size
        write_json(output / "delivery.json", data)
        return 0

    monkeypatch.setattr(video_pipeline, "launch_worker", launch)
    assert main(job["args"]) == 2
    assert read_json(job["output"] / "summary.json")["status"] == "FAILED"
    assert not (job["output"] / "stages/scene_delivery.json").exists()


def test_attempt_parent_cannot_escape_run_via_symlink(tmp_path):
    root, outside = tmp_path / "run", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "execution").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|outside|escape"):
        video_pipeline.fresh_attempt(root, "execution")
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "corruption", ["empty_artifacts", "wrong_stage_name", "bad_result"]
)
def test_resume_rejects_malformed_completed_stage_receipt(tmp_path, corruption):
    artifact = tmp_path / "artifact.dat"
    artifact.write_bytes(b"verified artifact")
    video_pipeline.VideoStages(tmp_path, "id").run(
        "preparation", lambda: ({"ok": True}, [artifact])
    )
    receipt_path = tmp_path / "stages/preparation.json"
    receipt = read_json(receipt_path)
    if corruption == "empty_artifacts":
        receipt["artifacts"] = []
    elif corruption == "wrong_stage_name":
        receipt["name"] = "different_stage"
    else:
        receipt["result"] = ["not a result object"]
    write_json(receipt_path, receipt)
    with pytest.raises(ValueError):
        video_pipeline.VideoStages(tmp_path, "id").run(
            "preparation", lambda: pytest.fail("Corrupt stage was rerun")
        )


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "failed_render",
        "wrong_spec",
        "wrong_fps",
        "wrong_count",
        "missing_camera",
        "changed_video",
    ],
)
def test_parent_verifies_render_receipt_and_required_artifacts(
    offline_video_run, corruption
):
    job = offline_video_run
    assert main(job["args"]) == 0
    output = job["calls"]["execution"][0]
    evidence = read_json(output / "delivery.json")
    source = read_json(job["output"] / "worker_inputs/attempt-0001/input.json")
    source["prepare_only"] = False
    config = read_json(job["output"] / "run-plan.json")["spec"]["render"]
    render_dir = output / "render"
    render_dir.mkdir()
    movie = render_dir / "scene_survey.mp4"
    movie.write_bytes(b"encoded bytes are bound by hash at this boundary")
    camera = render_dir / "camera.json"
    write_json(camera, {"poses": []})
    receipt = {
        "schema_version": 1,
        "status": "COMPLETE",
        "video_sha256": digest(movie),
        "scene_sha256": digest(output / "scene/scene.usda"),
        "spec_sha256": source["spec_sha256"],
        **{key: config[key] for key in ("width", "height", "fps", "frames")},
    }
    if corruption == "failed_render":
        receipt["status"] = "FAILED"
    elif corruption == "wrong_spec":
        receipt["spec_sha256"] = "different scene configuration"
    elif corruption == "wrong_fps":
        receipt["fps"] += 1
    elif corruption == "wrong_count":
        receipt["frames"] += 1
    elif corruption == "changed_video":
        movie.write_bytes(b"different encoded video")
    write_json(render_dir / "render.json", receipt)
    evidence.update(
        delivery_level="SCENE_SURVEY_READY", video="render/scene_survey.mp4"
    )
    evidence["artifacts"].extend(
        {
            "path": str(path.relative_to(output)),
            "sha256": digest(path),
            "bytes": path.stat().st_size,
        }
        for path in render_dir.iterdir()
    )
    if corruption == "missing_camera":
        camera.unlink()
        evidence["artifacts"] = [
            item
            for item in evidence["artifacts"]
            if item["path"] != "render/camera.json"
        ]
    if corruption is None:
        video_pipeline.verify_delivery(
            output,
            evidence,
            source,
            config,
            job["output"] / "worker_inputs/attempt-0001/scene-spec.json",
        )
    else:
        with pytest.raises(ValueError):
            video_pipeline.verify_delivery(
                output,
                evidence,
                source,
                config,
                job["output"] / "worker_inputs/attempt-0001/scene-spec.json",
            )
