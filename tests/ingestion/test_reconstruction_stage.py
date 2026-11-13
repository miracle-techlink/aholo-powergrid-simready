"""Offline, hash-bound reconstruction and paid-action boundaries."""

import shutil
import sys
import time

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.ingestion import reconstruction_stage as stage


@pytest.fixture
def video(tmp_path):
    source = tmp_path / "source video.mp4"
    source.write_bytes(b"test input video")
    return source


@pytest.fixture(autouse=True)
def no_cloud(monkeypatch):
    monkeypatch.delenv("AHOLO_API_KEY", raising=False)
    monkeypatch.delenv("SIMREADY_JOB_DEADLINE_UNIX_S", raising=False)
    monkeypatch.setattr(
        stage,
        "supervise_process",
        lambda *a, **k: pytest.fail("Unexpected cloud worker"),
    )


def artifact_manifest(directory, video, payload=b"ply\nformat ascii 1.0\nend_header\n"):
    directory.mkdir(parents=True)
    ply = directory / "reconstruction.ply"
    ply.write_bytes(payload)
    path = directory / "manifest.json"
    write_json(
        path,
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "source_video_sha256": digest(video),
            "artifacts": [
                {
                    "kind": "ply",
                    "path": ply.name,
                    "sha256": digest(ply),
                    "bytes": ply.stat().st_size,
                }
            ],
        },
    )
    return path


def provider_job(out, video, *, complete=True):
    out.mkdir(parents=True, exist_ok=True)
    write_json(
        out / "receipt.json", {"world_id": "known-job", "video_sha256": digest(video)}
    )
    if complete:
        (out / "splats.ply").write_bytes(b"provider PLY fixture")
        write_json(
            out / "status.json", {"world_id": "known-job", "status": "SUCCEEDED"}
        )
        write_json(
            out / "assets.json",
            {
                "world_id": "known-job",
                "assets": [
                    {
                        "path": "splats.ply",
                        "sha256": digest(out / "splats.ply"),
                        "bytes": (out / "splats.ply").stat().st_size,
                    }
                ],
            },
        )


def test_new_job_requires_permission_before_output_creation(tmp_path, video):
    out = tmp_path / "new job"
    with pytest.raises(PermissionError, match="--submit-reconstruction"):
        stage.prepare_reconstruction(video, out)
    assert not out.exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "10"])
def test_invalid_deadlines_do_not_prepare_job(tmp_path, video, timeout):
    out = tmp_path / "job"
    with pytest.raises(ValueError, match="timeout"):
        stage.prepare_reconstruction(video, out, submit=True, timeout=timeout)
    assert not out.exists()


def test_submit_requires_boolean_not_truthy_value(tmp_path, video):
    with pytest.raises(ValueError, match="boolean"):
        stage.prepare_reconstruction(video, tmp_path / "job", submit="yes")


def test_import_and_cache_are_offline_read_only_and_relocatable(
    tmp_path, video, monkeypatch
):
    source = artifact_manifest(tmp_path / "artifacts", video)
    before = {item.name: item.read_bytes() for item in source.parent.iterdir()}
    monkeypatch.setitem(sys.modules, "requests", None)
    out = tmp_path / "job"
    result = stage.prepare_reconstruction(video, out, artifact_manifest=source)
    assert result["status"] == "COMPLETE"
    assert (
        result["cache_hit"] and not result["new_submission"] and not result["resumed"]
    )
    assert result["ply_sha256"] == digest(out / "splats.ply")
    assert result["source_video_sha256"] == digest(video)
    assert result["provenance"] == "user_supplied_artifact_manifest"
    assert result["geometry_validated"] is False and result["scale_calibrated"] is False
    assert {item.name: item.read_bytes() for item in source.parent.iterdir()} == before
    assert not (out / "submit_intent.json").exists()
    original_manifest = (out / "reconstruction.json").read_bytes()
    moved = tmp_path / "relocated"
    shutil.move(out, moved)
    cached = stage.prepare_reconstruction(video, moved)
    assert cached["ply_path"] == str(moved / "splats.ply")
    assert cached["manifest_path"] == str(moved / "reconstruction.json")
    assert (moved / "reconstruction.json").read_bytes() == original_manifest
    assert (
        stage.prepare_reconstruction(
            video, tmp_path / "second job", artifact_manifest=cached["manifest_path"]
        )["ply_sha256"]
        == cached["ply_sha256"]
    )


@pytest.mark.parametrize(
    "tamper",
    ["video", "artifact", "missing_ply", "duplicate", "escape", "absolute", "symlink"],
)
def test_invalid_import_fails_before_output_changes(tmp_path, video, tamper):
    source = artifact_manifest(tmp_path / "artifacts", video)
    manifest = read_json(source)
    record = manifest["artifacts"][0]
    if tamper == "video":
        video.write_bytes(b"different video")
    elif tamper == "artifact":
        (source.parent / record["path"]).write_bytes(b"changed")
    elif tamper == "missing_ply":
        record["kind"] = "spz"
    elif tamper == "duplicate":
        manifest["artifacts"].append(record.copy())
    elif tamper == "escape":
        record["path"] = "../source video.mp4"
    elif tamper == "absolute":
        record["path"] = str(source.parent / record["path"])
    else:
        (source.parent / record["path"]).unlink()
        (source.parent / record["path"]).symlink_to(video)
    write_json(source, manifest)
    out = tmp_path / "job"
    with pytest.raises(ValueError):
        stage.prepare_reconstruction(video, out, artifact_manifest=source, submit=True)
    assert not out.exists()


def test_cache_tamper_never_falls_back_to_new_submission(tmp_path, video):
    source = artifact_manifest(tmp_path / "artifacts", video)
    out = tmp_path / "job"
    stage.prepare_reconstruction(video, out, artifact_manifest=source)
    (out / "splats.ply").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash changed"):
        stage.prepare_reconstruction(video, out, submit=True)
    assert not (out / "submit_intent.json").exists()


def test_explicit_import_cannot_silently_select_different_cache(tmp_path, video):
    first = artifact_manifest(tmp_path / "first", video)
    other = artifact_manifest(tmp_path / "other", video, b"other PLY")
    out = tmp_path / "job"
    stage.prepare_reconstruction(video, out, artifact_manifest=first)
    with pytest.raises(ValueError, match="differs from the cached"):
        stage.prepare_reconstruction(video, out, artifact_manifest=other)


def test_output_cannot_overwrite_input_or_follow_state_symlink(tmp_path, video):
    source = artifact_manifest(tmp_path / "artifacts", video)
    with pytest.raises(ValueError, match="contain the source video"):
        stage.prepare_reconstruction(video, tmp_path, artifact_manifest=source)
    out = tmp_path / "job"
    out.mkdir()
    (out / "reconstruction.json").symlink_to(source)
    with pytest.raises(ValueError, match="symlinked"):
        stage.prepare_reconstruction(video, out)
    assert read_json(source)["status"] == "COMPLETE"


def test_import_refuses_overwrite_and_unknown_cloud_intent(tmp_path, video):
    source = artifact_manifest(tmp_path / "artifacts", video)
    out = tmp_path / "job"
    out.mkdir()
    target = out / "splats.ply"
    target.write_bytes(b"existing data")
    with pytest.raises(ValueError, match="overwrite"):
        stage.prepare_reconstruction(video, out, artifact_manifest=source)
    assert target.read_bytes() == b"existing data"
    write_json(out / "submit_intent.json", {"video_sha256": digest(video)})
    with pytest.raises(ValueError, match="fresh output"):
        stage.prepare_reconstruction(video, out, artifact_manifest=source)


def test_unknown_submission_blocks_even_explicit_submit(tmp_path, video):
    out = tmp_path / "job"
    write_json(out / "submit_intent.json", {"video_sha256": digest(video)})
    with pytest.raises(RuntimeError, match="outcome unknown"):
        stage.prepare_reconstruction(video, out, submit=True)
    assert not (out / "reconstruction.lock").exists()


def test_existing_provider_artifacts_become_offline_manifest(tmp_path, video):
    out = tmp_path / "job"
    provider_job(out, video)
    before = {item.name: item.read_bytes() for item in out.iterdir()}
    result = stage.prepare_reconstruction(video, out)
    assert result["cache_hit"] and result["resumed"] and not result["new_submission"]
    assert result["provenance"] == "cloud_job_receipt"
    assert result["world_id"] == "known-job"
    for name, contents in before.items():
        assert (out / name).read_bytes() == contents


@pytest.mark.parametrize("resume", [False, True])
def test_supervised_worker_argv_deadline_and_completion(
    tmp_path, video, monkeypatch, resume
):
    out = tmp_path / "job with spaces"
    if resume:
        provider_job(out, video, complete=False)
    monkeypatch.setenv("SIMREADY_JOB_DEADLINE_UNIX_S", str(time.time() + 30))
    monkeypatch.setenv("AHOLO_API_KEY", "test-only-key")
    seen = []

    def worker(argv, **kwargs):
        seen.append((argv, kwargs))
        assert argv[:3] == [
            sys.executable,
            "-m",
            "simready.ingestion.aholo_reconstruction",
        ]
        assert argv[argv.index("--video") + 1] == str(video)
        assert argv[argv.index("--out") + 1] == str(out)
        assert ("--submit" in argv) is not resume
        assert 0 < kwargs["timeout"] <= 30
        assert kwargs["directory"].is_relative_to(out)
        provider_job(out, video)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(stage, "supervise_process", worker)
    result = stage.prepare_reconstruction(video, out, submit=not resume, timeout=300)
    assert result["new_submission"] is not resume
    assert result["resumed"] is resume
    assert result["cache_hit"] is False
    assert result["ply_sha256"] == digest(out / "splats.ply")
    assert stage.prepare_reconstruction(video, out)["cache_hit"] is True
    assert len(seen) == 1


@pytest.mark.parametrize(
    "status,error", [("TIMEOUT", TimeoutError), ("FAILED", RuntimeError)]
)
def test_failed_worker_never_publishes_complete_receipt(
    tmp_path, video, monkeypatch, status, error
):
    out = tmp_path / "job"
    monkeypatch.setenv("AHOLO_API_KEY", "test-only-key")

    def worker(*args, **kwargs):
        provider_job(out, video, complete=False)
        return {"status": status}

    monkeypatch.setattr(stage, "supervise_process", worker)
    with pytest.raises(error):
        stage.prepare_reconstruction(video, out, submit=True)
    assert (out / "receipt.json").exists()
    assert not (out / "reconstruction.json").exists()


@pytest.mark.parametrize(
    "tamper", ["receipt_video", "status_job", "asset_job", "artifact", "video"]
)
def test_successful_exit_does_not_override_provenance_or_hash_mismatch(
    tmp_path, video, monkeypatch, tamper
):
    out = tmp_path / "job"
    monkeypatch.setenv("AHOLO_API_KEY", "test-only-key")

    def worker(*args, **kwargs):
        provider_job(out, video)
        if tamper == "artifact":
            (out / "splats.ply").write_bytes(b"changed")
        elif tamper == "video":
            video.write_bytes(b"changed video")
        else:
            filename, field = {
                "receipt_video": ("receipt.json", "video_sha256"),
                "status_job": ("status.json", "world_id"),
                "asset_job": ("assets.json", "world_id"),
            }[tamper]
            data = read_json(out / filename)
            data[field] = "different"
            write_json(out / filename, data)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(stage, "supervise_process", worker)
    with pytest.raises(ValueError):
        stage.prepare_reconstruction(video, out, submit=True)
    assert not (out / "reconstruction.json").exists()


def test_expired_deadline_keeps_receipt_and_does_not_start_worker(
    tmp_path, video, monkeypatch
):
    out = tmp_path / "job"
    provider_job(out, video, complete=False)
    monkeypatch.setenv("SIMREADY_JOB_DEADLINE_UNIX_S", str(time.time() - 1))
    monkeypatch.setenv("AHOLO_API_KEY", "test-only-key")
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        stage.prepare_reconstruction(video, out)
    assert (out / "receipt.json").exists()


def test_resume_receipt_for_different_video_blocks_before_worker(tmp_path, video):
    out = tmp_path / "job"
    provider_job(out, video, complete=False)
    video.write_bytes(b"different video")
    with pytest.raises(ValueError, match="another video"):
        stage.prepare_reconstruction(video, out, submit=True)
    assert not (out / "reconstruction.lock").exists()


def test_missing_credentials_fails_before_cloud_or_submission_intent(tmp_path, video):
    out = tmp_path / "job"
    with pytest.raises(ValueError, match="requires AHOLO_API_KEY"):
        stage.prepare_reconstruction(video, out, submit=True)
    assert not (out / "submit_intent.json").exists()
    assert not list(out.glob("supervisor_*"))
