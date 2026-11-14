import copy
import runpy
from pathlib import Path

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference
from simready.execution.processes.local import ProcessCancelledError
from simready.workflows.pipelines import baseline_matrix as matrix
from tests.paths import REPOSITORY_ROOT


@pytest.fixture
def prepared(tmp_path):
    prepare = runpy.run_path(
        str(REPOSITORY_ROOT / "examples/reconstruction/prepare_fixture.py")
    )["prepare"]
    path = prepare(tmp_path / "inputs")
    return path, tmp_path / "run"


def test_dry_run_is_read_only_and_freezes_two_distinct_backend_modes(
    prepared, monkeypatch
):
    path, out = prepared
    monkeypatch.setattr(
        matrix, "execute_video", lambda *a: pytest.fail("Planning executed")
    )
    plan = matrix.plan_matrix(path, out)
    assert not out.exists()
    assert plan == matrix.plan_matrix(path, out)
    assert plan["until"] == "reconstruction"
    assert plan["comparison"]["status"] == "COMPARABILITY_UNCONFIRMED"
    assert (
        len(
            {
                row["plan"]["stage_contracts"]["identities"]["reconstruction"]
                for row in plan["members"]
            }
        )
        == 2
    )
    assert matrix.main(["--matrix", str(path), "--out", str(out), "--dry-run"]) == 0
    assert not out.exists()


def test_real_cpu_matrix_runs_command_then_import_without_quality_claim(prepared):
    path, out = prepared
    report = matrix.execute_matrix(matrix.plan_matrix(path, out))
    assert report["status"] == "COMPLETE"
    assert report["counts"] == {"planned": 2, "not_run": 0, "quality_measured": 0}
    assert [row["reconstruction"]["execution_mode"] for row in report["members"]] == [
        "fresh_command",
        "import",
    ]
    assert all(
        row["status"] == "STAGE_READY" and not row["completed_pipeline"]
        for row in report["members"]
    )
    assert all(row["quality"]["status"] == "NOT_EVALUATED" for row in report["members"])
    assert report["ranking"] is report["default_provider_promotion"] is None
    assert read_json(out / "matrix-result.json") == report
    with pytest.raises(FileExistsError):
        matrix.execute_matrix(matrix.plan_matrix(path, out))


@pytest.mark.parametrize("mutation", ["video", "duplicate", "escape", "unknown"])
def test_invalid_matrix_contracts_fail_before_output(prepared, mutation):
    path, out = prepared
    raw = read_json(path)
    if mutation == "video":
        other = path.parent / "other.mp4"
        other.write_bytes(b"other")
        raw["video"] = reference(other)
    elif mutation == "duplicate":
        raw["members"][1]["id"] = raw["members"][0]["id"]
    elif mutation == "escape":
        raw["members"][0]["id"] = "../escape"
    else:
        raw["unexpected"] = True
    write_json(path, raw)
    with pytest.raises(ValueError):
        matrix.plan_matrix(path, out)
    assert not out.exists()


def test_frozen_input_changes_rejected_before_matrix_directory(prepared):
    path, out = prepared
    plan = matrix.plan_matrix(path, out)
    Path(plan["video"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        matrix.execute_matrix(plan)
    assert not out.exists()


def test_member_failure_is_retained_and_next_member_runs(prepared, monkeypatch):
    path, out = prepared
    calls = []

    def fail(args, plan):
        calls.append(plan["out"])
        return {"status": "FAILED", "error": "deliberate fixture failure"}, 2

    monkeypatch.setattr(matrix, "execute_video", fail)
    report = matrix.execute_matrix(matrix.plan_matrix(path, out))
    assert len(calls) == 2
    assert report["status"] == "COMPLETED_WITH_FAILURES"
    assert report["counts"]["planned"] == 2
    assert all(
        row["error"]["message"] == "deliberate fixture failure"
        for row in report["members"]
    )


@pytest.mark.parametrize("raised", [False, True])
def test_cancellation_preserves_not_run_rows_and_propagates(
    prepared, monkeypatch, raised
):
    path, out = prepared
    calls = []

    def cancel(args, plan):
        calls.append(plan["out"])
        if raised:
            raise ProcessCancelledError(0)
        return {"status": "CANCELLED"}, 2

    monkeypatch.setattr(matrix, "execute_video", cancel)
    with pytest.raises(ProcessCancelledError):
        matrix.execute_matrix(matrix.plan_matrix(path, out))
    report = read_json(out / "matrix-result.json")
    assert len(calls) == 1
    assert report["status"] == "CANCELLED"
    assert [row["status"] for row in report["members"]] == ["CANCELLED", "NOT_RUN"]


def test_declared_match_is_not_independent_training_isolation(prepared):
    path, out = prepared
    members = matrix.plan_matrix(path, out)["members"]
    evidence = reference(path)
    for member in members:
        config = member["plan"]["pipeline"]["reconstruction"]["config"]
        config["algorithm"] = {
            "name": "declared",
            "version": "1",
            "implementation_sha256": "1" * 64,
        }
        config["frames"] = {
            "status": "declared",
            "training": [{"id": "train", "sha256": "2" * 64}],
            "heldout": [{"id": "test", "sha256": "3" * 64}],
            "evidence": evidence,
        }
        config["coordinates"] = {
            "frame": "z_up",
            "unit": "m",
            "scale_status": "verified",
            "evidence": evidence,
        }
    result = matrix.comparison_status(members, "fixed_observations", evidence)
    assert result["status"] == "DECLARED_INPUTS_MATCH"
    assert not result["independent_training_isolation_verified"]
    changed = copy.deepcopy(members)
    changed[1]["plan"]["stage_contracts"]["recipes"]["observations"]["config"][
        "fps"
    ] = 7
    assert (
        matrix.comparison_status(changed, "fixed_observations", evidence)["status"]
        == "COMPARABILITY_UNCONFIRMED"
    )


def test_unknown_quality_does_not_invoke_verifier(prepared, monkeypatch):
    path, out = prepared
    plan = matrix.plan_matrix(path, out)
    monkeypatch.setattr(
        matrix, "verify_candidate", lambda *a, **k: pytest.fail("No quality evidence")
    )
    result = matrix._quality(plan["members"][0], plan, out / "quality", {})
    assert result["status"] == "NOT_EVALUATED"


def test_real_reference_quality_binding_and_sealed_summary(tmp_path, monkeypatch):
    from tests.fixtures.scene_candidate import make_scene_candidate

    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    protocol["phase"] = "sealed_release"
    protocol["views"][0].update(split="sealed_release", reference_kind="real_capture")
    protocol["fit_frame_ids"] = ["training"]
    protocol["fit_image_sha256"] = ["1" * 64]
    write_json(p, protocol)
    candidate.update(
        protocol_sha256=digest(p),
        fit_frame_ids=["training"],
        fit_image_sha256=["1" * 64],
    )
    model_hash = digest(tmp_path / "model.bin")
    camera = tmp_path / "camera.json"
    write_json(camera, {"camera": "caller declaration"})
    candidate["comparison_evidence"] = {
        "source_video_sha256": "2" * 64,
        "reconstruction_sha256": model_hash,
        "camera_sha256": digest(camera),
    }
    write_json(c, candidate)
    bundle = {
        "algorithm": {"name": "operator_model"},
        "frames": {
            "status": "declared",
            "training": [{"id": "training", "sha256": "1" * 64}],
            "heldout": [
                {"id": "test-frame", "sha256": protocol["views"][0]["rgb"]["sha256"]}
            ],
        },
    }
    monkeypatch.setattr(matrix, "verify_bundle", lambda *a, **k: bundle)
    member = {"candidate": reference(c)}
    manifest = {
        "purpose": "quality_comparison",
        "video": {"sha256": "2" * 64},
        "evaluation": {
            "protocol": reference(p),
            "camera_evidence": reference(camera),
            "timeout_s": 30,
        },
    }
    reconstruction = {
        "bundle_path": "unused-in-controlled-test",
        "ply_sha256": model_hash,
    }
    result = matrix._quality(
        member, manifest, tmp_path / "evaluation/one", reconstruction
    )
    assert result["status"] == "MEASURED"
    assert result["feedback_status"] == "WITHHELD"
    assert result["release_authorized"] is False
    reconstruction["ply_sha256"] = "3" * 64
    with pytest.raises(ValueError, match="not bound"):
        matrix._quality(member, manifest, tmp_path / "evaluation/two", reconstruction)
