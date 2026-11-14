"""Real CPU backend substitution and evidence/recovery boundaries, without models."""

import struct
from pathlib import Path

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.core.reconstruction_contract import verify_bundle
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.ingestion import reconstruction_providers as providers


def ref(path):
    return {"path": str(path), "sha256": digest(path)}


@pytest.fixture
def inputs(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic interface input, not decodable video")
    implementation = (
        Path(__file__).parents[2] / "examples/reconstruction/cpu_fixture.py"
    )
    runtime = tmp_path / "runtime.json"
    write_json(runtime, {"purpose": "synthetic_cpu_fixture", "model": None})
    config = {
        "provider": "command",
        "base_dir": str(tmp_path),
        "argv": [
            "{python}",
            "{input:implementation}",
            "--video",
            "{video}",
            "--out",
            "{out}",
        ],
        "input_files": {"implementation": ref(implementation)},
        "runtime_identity": ref(runtime),
        "algorithm": {
            "name": "synthetic_cpu_fixture",
            "version": "1",
            "implementation_sha256": digest(implementation),
        },
        "environment": {"inherit": [], "secrets": []},
        "resources": {"kind": "cpu", "lock_dir": str(tmp_path / "locks")},
    }
    return video, config


def test_plan_is_read_only_and_idempotent(inputs, tmp_path, monkeypatch):
    video, config = inputs
    monkeypatch.setattr(
        providers, "device_lease", lambda **k: pytest.fail("Planning leased a device")
    )
    monkeypatch.setattr(
        providers, "run_process", lambda *a, **k: pytest.fail("Planning ran a worker")
    )
    before = set(tmp_path.iterdir())
    plan = providers.plan_provider(config, video)
    assert set(tmp_path.iterdir()) == before
    assert providers.plan_provider(plan["config"], video) == plan
    assert all(Path(item["path"]).is_absolute() for item in plan["dependencies"])


def test_command_then_import_are_real_distinct_modes(inputs, tmp_path):
    video, config = inputs
    output = tmp_path / "command_result"
    result = providers.prepare_provider(config, video, output, 20)
    assert result["execution_mode"] == "fresh_command"
    bundle = providers.verify_provider_bundle(
        result["bundle_path"], source_video_sha256=digest(video)
    )
    assert bundle["algorithm"]["name"] == "synthetic_cpu_fixture"
    assert bundle["frames"]["status"] == "unknown"
    assert bundle["coordinates"]["scale_status"] == "unknown"
    assert read_json(output / "command-execution.json")["status"] == "COMPLETE"
    imported = providers.prepare_provider(
        {"provider": "artifact", "manifest": ref(Path(result["manifest_path"]))},
        video,
        tmp_path / "import_result",
        20,
    )
    assert imported["execution_mode"] == "import"
    assert imported["ply_sha256"] == result["ply_sha256"]
    assert not (tmp_path / "import_result" / "command").exists()


def test_completed_command_recovers_missing_bundle_without_rerun(
    inputs, tmp_path, monkeypatch
):
    video, config = inputs
    output = tmp_path / "result"
    result = providers.prepare_provider(config, video, output, 20)
    Path(result["bundle_path"]).unlink()
    monkeypatch.setattr(
        providers,
        "run_process",
        lambda *a, **k: pytest.fail("Reexecuted completed backend"),
    )
    restored = providers.prepare_provider(config, video, output, 20)
    assert Path(restored["bundle_path"]).is_file()
    assert restored["cache_hit"]


def test_completed_backend_recovers_before_manifest_publication(
    inputs, tmp_path, monkeypatch
):
    video, config = inputs
    output = tmp_path / "result"
    real_prepare = providers.prepare_reconstruction
    monkeypatch.setattr(
        providers,
        "prepare_reconstruction",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("before publication")),
    )
    with pytest.raises(RuntimeError, match="before publication"):
        providers.prepare_provider(config, video, output, 20)
    monkeypatch.setattr(providers, "prepare_reconstruction", real_prepare)
    monkeypatch.setattr(
        providers,
        "run_process",
        lambda *a, **k: pytest.fail("Reexecuted completed backend"),
    )
    assert (
        providers.prepare_provider(config, video, output, 20)["execution_mode"]
        == "fresh_command"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_key",
        "unhashed_code",
        "missing_environment",
        "point_cloud",
        "unknown_placeholder",
    ],
)
def test_invalid_contracts_fail_before_output(inputs, tmp_path, mutation):
    video, config = inputs
    if mutation == "unknown_key":
        config["surprise"] = True
    elif mutation == "unhashed_code":
        config["algorithm"]["implementation_sha256"] = "1" * 64
    elif mutation == "missing_environment":
        del config["environment"]
    elif mutation == "point_cloud":
        config["representation"] = "point_cloud"
    else:
        config["argv"].append("{unknown}")
    with pytest.raises(ValueError):
        providers.prepare_provider(config, video, tmp_path / "absent", 20)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "mutation", ["overlap_id", "overlap_hash", "no_frame_evidence", "no_scale_evidence"]
)
def test_frame_and_scale_claims_need_disjoint_evidence(inputs, mutation):
    video, config = inputs
    evidence = config["runtime_identity"]
    config["frames"] = {
        "status": "declared",
        "training": [{"id": "train", "sha256": "1" * 64}],
        "heldout": [{"id": "heldout", "sha256": "2" * 64}],
        "evidence": evidence,
    }
    if mutation == "overlap_id":
        config["frames"]["heldout"][0]["id"] = "train"
    elif mutation == "overlap_hash":
        config["frames"]["heldout"][0]["sha256"] = "1" * 64
    elif mutation == "no_frame_evidence":
        config["frames"]["evidence"] = None
    else:
        config["coordinates"] = {
            "frame": "z_up",
            "unit": "m",
            "scale_status": "verified",
            "evidence": None,
        }
    with pytest.raises(ValueError):
        providers.plan_provider(config, video)


def test_mutated_dependency_invalidates_bundle_and_frozen_plan(inputs, tmp_path):
    video, config = inputs
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    Path(config["runtime_identity"]["path"]).write_text("changed runtime")
    with pytest.raises(ValueError, match="hash changed"):
        verify_bundle(result["bundle_path"])
    with pytest.raises(ValueError, match="hash changed"):
        providers.prepare_provider(config, video, tmp_path / "result", 20)


def test_changed_nonsecret_environment_fails_after_plan(inputs, monkeypatch):
    video, config = inputs
    config["environment"]["inherit"] = ["PROVIDER_TEST_VERSION"]
    monkeypatch.setenv("PROVIDER_TEST_VERSION", "v1")
    frozen = providers.plan_provider(config, video)
    monkeypatch.setenv("PROVIDER_TEST_VERSION", "v2")
    with pytest.raises(ValueError, match="environment changed"):
        providers.plan_provider(frozen["config"], video)


def test_secret_values_never_enter_plan_or_receipt_and_prevent_reuse(
    inputs, tmp_path, monkeypatch
):
    video, config = inputs
    monkeypatch.setenv("PROVIDER_TEST_SECRET", "private-value-never-write")
    config["environment"]["secrets"] = ["PROVIDER_TEST_SECRET"]
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    for path in (tmp_path / "result").glob("*.json"):
        assert "private-value-never-write" not in path.read_text()
    with pytest.raises(ValueError, match="Secret-dependent"):
        providers.prepare_provider(config, video, tmp_path / "result", 20)
    assert Path(result["bundle_path"]).is_file()


def test_timeout_uses_real_supervision_and_keeps_failure_receipt(inputs, tmp_path):
    video, config = inputs
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n")
    config["input_files"]["implementation"] = ref(script)
    config["algorithm"]["implementation_sha256"] = digest(script)
    config["argv"] = ["{python}", "{input:implementation}"]
    with pytest.raises(ProcessTimeoutError):
        providers.prepare_provider(config, video, tmp_path / "result", 0.3)
    receipt = read_json(tmp_path / "result" / "command-execution.json")
    assert receipt["status"] == "TIMEOUT"
    assert receipt["wall_s"] > 0
    assert not (tmp_path / "result" / "reconstruction-bundle.json").exists()


def test_declared_gaussian_rejects_numeric_nan_even_with_correct_hashes(
    inputs, tmp_path
):
    video, config = inputs
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    bundle_path = Path(result["bundle_path"])
    bundle = read_json(bundle_path)
    artifact = Path(result["ply_path"])
    data = artifact.read_bytes()
    end = data.index(b"end_header\n") + len(b"end_header\n")
    artifact.write_bytes(data[:end] + struct.pack("<f", float("nan")) + data[end + 4 :])
    bundle["artifacts"][0]["sha256"] = digest(artifact)
    write_json(bundle_path, bundle)
    verify_bundle(bundle_path)
    with pytest.raises(ValueError, match="finite"):
        providers.verify_provider_bundle(bundle_path)


def test_aholo_requires_explicit_authorization_without_creating_output(
    inputs, tmp_path
):
    video, _ = inputs
    with pytest.raises(PermissionError):
        providers.prepare_provider(
            {"provider": "aholo"}, video, tmp_path / "absent", 20
        )
    assert not (tmp_path / "absent").exists()


def test_source_hash_is_checked_when_reading_bundle(inputs, tmp_path):
    video, config = inputs
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    with pytest.raises(ValueError, match="another source"):
        verify_bundle(result["bundle_path"], source_video_sha256="f" * 64)


@pytest.mark.parametrize(
    "exception,status",
    [(ProcessCancelledError(0.1), "CANCELLED"), (KeyboardInterrupt(), "INTERRUPTED")],
)
def test_owner_cancellation_and_interrupt_preserve_terminal_status(
    inputs, tmp_path, monkeypatch, exception, status
):
    video, config = inputs

    def interrupted(*args, **kwargs):
        raise exception

    monkeypatch.setattr(providers, "run_process", interrupted)
    with pytest.raises(type(exception)):
        providers.prepare_provider(config, video, tmp_path / "result", 20)
    receipt = read_json(tmp_path / "result" / "command-execution.json")
    assert receipt["status"] == status
    assert receipt["wall_s"] >= 0
    assert receipt["manifest"] is None


def test_provider_and_execution_mode_cannot_contradict(inputs, tmp_path):
    video, config = inputs
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    path = Path(result["bundle_path"])
    bundle = read_json(path)
    bundle["provenance"]["execution_mode"] = "cloud_new"
    write_json(path, bundle)
    with pytest.raises(ValueError, match="contradicts"):
        verify_bundle(path)


def test_declared_frames_and_scale_remain_operator_evidence(inputs, tmp_path):
    video, config = inputs
    config["frames"] = {
        "status": "declared",
        "training": [{"id": "train-1", "sha256": "1" * 64}],
        "heldout": [{"id": "heldout-1", "sha256": "2" * 64}],
        "evidence": config["runtime_identity"],
    }
    config["coordinates"] = {
        "frame": "z_up",
        "unit": "m",
        "scale_status": "verified",
        "evidence": config["runtime_identity"],
    }
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    bundle = verify_bundle(result["bundle_path"])
    assert (
        bundle["declaration_verification"]
        == "operator_evidence_not_independent_certification"
    )
    assert not result["scale_calibrated"]


def test_hash_valid_rgb_point_cloud_is_not_a_gaussian_import(inputs, tmp_path):
    video, config = inputs
    result = providers.prepare_provider(config, video, tmp_path / "result", 20)
    artifact = Path(result["ply_path"])
    payload = (
        b"ply\nformat binary_little_endian 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
        + struct.pack("<3f3B", 0, 0, 0, 255, 0, 0)
    )
    artifact.write_bytes(payload)
    manifest_path = Path(result["manifest_path"])
    manifest = read_json(manifest_path)
    manifest["artifacts"][0].update(sha256=digest(artifact), bytes=len(payload))
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="point cloud"):
        providers.prepare_provider(
            {"provider": "artifact", "manifest": ref(manifest_path)},
            video,
            tmp_path / "rejected",
            20,
        )
    assert not (tmp_path / "rejected" / "reconstruction-bundle.json").exists()
