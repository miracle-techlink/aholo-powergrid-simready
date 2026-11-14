"""Real CPU configuration, stage stopping, upgrade invalidation and old evidence."""

import shutil
from pathlib import Path

import pytest

from examples.pipeline.create_fixture import create_fixture
from simready.cli import main
from simready.core.artifacts import digest, read_json, write_json
from simready.workflows import video_pipeline
from simready.workflows.pipeline_spec import load_pipeline


@pytest.fixture
def configured(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for real observation extraction")
    path = create_fixture(tmp_path / "inputs")
    # Individual contract tests can coexist with other agents' source edits.
    monkeypatch.setattr(
        video_pipeline, "implementation_identity", lambda: "test-engine"
    )
    return path, tmp_path / "run"


def command(path, output, *flags):
    return ["run", "--pipeline", str(path), "--out", str(output), *flags]


def test_partial_resume_and_downstream_only_upgrade(configured):
    path, output = configured
    assert main(command(path, output, "--until", "reconstruction")) == 0
    partial = read_json(output / "summary.json")
    assert partial["status"] == "STAGE_READY" and partial["task_validated"] is False
    assert not (output / "execution").exists()
    assert main(command(path, output, "--resume")) == 0
    summary = read_json(output / "summary.json")
    assert summary["physical_asset_validation"] == "PASS"
    assert summary["stages"][0]["reused"] and summary["stages"][1]["reused"]
    old_receipt = read_json(output / "stages/scene_delivery.json")
    canonical = output / old_receipt["receipt"]
    old_bytes = canonical.read_bytes()
    spec = path.parent / "scene-spec.json"
    value = read_json(spec)
    value["objects"][0]["physics"]["mass_kg"] = 0.2
    write_json(spec, value)
    assert main(command(path, output, "--resume")) == 3
    assert main(command(path, output, "--upgrade")) == 0
    changed = read_json(output / "summary.json")
    flags = {row["name"]: row["reused"] for row in changed["stages"]}
    assert flags == {
        "observations": True,
        "reconstruction": True,
        "worker_inputs": False,
        "scene_delivery": False,
    }
    assert canonical.read_bytes() == old_bytes
    assert len(list((output / "plans").glob("*.json"))) == 2
    assert main(command(path, output, "--resume")) == 0
    assert all(row["reused"] for row in read_json(output / "summary.json")["stages"])


@pytest.mark.parametrize(
    "change", ["unknown_stage", "unimplemented", "bad_fps", "missing_input"]
)
def test_config_rejects_before_creating_run(configured, change):
    path, output = configured
    value = read_json(path)
    if change == "unknown_stage":
        value["stages"]["mystery"] = {}
    elif change == "unimplemented":
        value["stages"]["assets"]["provider"] = "trellis2"
    elif change == "bad_fps":
        value["stages"]["observations"]["fps"] = 0
    else:
        value["input"]["video"] = "missing.mp4"
    write_json(path, value)
    assert main(command(path, output, "--dry-run")) == 3
    assert not output.exists()


def test_dry_run_does_not_execute_and_has_independent_stage_identities(
    configured, monkeypatch
):
    path, output = configured
    monkeypatch.setattr(
        video_pipeline, "execute_video", lambda *args: pytest.fail("dry-run executed")
    )
    assert main(command(path, output, "--dry-run")) == 0
    assert not output.exists()
    args = load_pipeline(path, output)
    first = video_pipeline.make_plan(args)
    args.observation_fps = 3
    second = video_pipeline.make_plan(args)
    for stage in first["stages"]:
        assert (
            first["stage_contracts"]["identities"][stage]
            == second["stage_contracts"]["identities"][stage]
        ) is (stage != "observations")


def test_changed_stage_artifact_blocks_reuse_without_overwriting_receipt(configured):
    path, output = configured
    assert main(command(path, output, "--until", "reconstruction")) == 0
    index = read_json(output / "stages/reconstruction.json")
    receipt = output / index["receipt"]
    before = digest(receipt)
    Path(index["result"]["ply_path"]).write_bytes(b"changed output")
    assert main(command(path, output, "--resume")) == 2
    assert digest(receipt) == before
    assert not (output / "execution").exists()


def command_provider(path, secret=None):
    from simready.core.evidence import reference

    script = (
        Path(__file__).parents[2] / "examples/reconstruction_backends/cpu_fixture.py"
    )
    runtime = path.parent / "command-environment.json"
    write_json(runtime, {"purpose": "synthetic CPU contract"})
    return {
        "provider": "command",
        "base_dir": str(path.parent),
        "argv": [
            "{python}",
            "{input:implementation}",
            "--video",
            "{video}",
            "--out",
            "{out}",
        ],
        "input_files": {"implementation": reference(script)},
        "runtime_identity": reference(runtime),
        "algorithm": {
            "name": "synthetic_cpu_fixture",
            "version": "1",
            "implementation_sha256": digest(script),
        },
        "environment": {"inherit": [], "secrets": [secret] if secret else []},
        "resources": {"kind": "cpu"},
    }


def test_secret_command_cannot_reuse_outer_stage_receipts(configured, monkeypatch):
    path, output = configured
    monkeypatch.setenv("PIPELINE_SYNTHETIC_SECRET", "fixture-one")
    value = read_json(path)
    value["stages"]["reconstruction"] = command_provider(
        path, "PIPELINE_SYNTHETIC_SECRET"
    )
    write_json(path, value)
    assert main(command(path, output, "--until", "reconstruction")) == 0
    first = (output / "run-plan.json").read_bytes()
    assert b"fixture-one" not in first
    monkeypatch.setenv("PIPELINE_SYNTHETIC_SECRET", "fixture-two")
    for flag in ["--resume", "--upgrade"]:
        assert main(command(path, output, flag, "--until", "reconstruction")) == 3
        assert (output / "run-plan.json").read_bytes() == first


def test_command_completion_recovers_before_outer_receipt_publication(
    configured, monkeypatch
):
    from simready.ingestion import reconstruction_providers

    path, output = configured
    value = read_json(path)
    value["stages"]["reconstruction"] = command_provider(path)
    write_json(path, value)
    real = reconstruction_providers.prepare_provider

    def interrupted(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("interrupted before outer receipt publication")

    monkeypatch.setattr(reconstruction_providers, "prepare_provider", interrupted)
    assert main(command(path, output, "--until", "reconstruction")) == 2
    monkeypatch.setattr(reconstruction_providers, "prepare_provider", real)
    monkeypatch.setattr(
        reconstruction_providers,
        "_command",
        lambda *a: pytest.fail("Completed command reran"),
    )
    assert main(command(path, output, "--resume", "--until", "reconstruction")) == 0
    assert read_json(output / "summary.json")["status"] == "STAGE_READY"


def test_source_change_before_publication_cannot_poison_stage_cache(
    configured, monkeypatch
):
    from simready.ingestion import video_frames

    path, output = configured
    args = load_pipeline(path, output, until="observations")
    plan = video_pipeline.make_plan(args)
    extract = video_frames.extract_frames

    def changing_source(*a, **kw):
        result = extract(*a, **kw)
        monkeypatch.setattr(
            video_pipeline, "implementation_identity", lambda: "changed-code"
        )
        return result

    monkeypatch.setattr(video_frames, "extract_frames", changing_source)
    result, code = video_pipeline.execute_video(args, plan)
    assert code == 2 and result["status"] == "FAILED"
    assert not (output / "stages/observations.json").exists()


def test_external_worker_environment_requires_frozen_manifest(configured):
    from simready.core.evidence import reference

    path, output = configured
    value = read_json(path)
    value["execution"]["backend"] = "docker"
    write_json(path, value)
    assert main(command(path, output, "--dry-run")) == 3
    environment = path.parent / "isaac-environment.json"
    write_json(
        environment,
        {"image": "example@sha256:" + "a" * 64, "status": "operator_declared"},
    )
    value["execution"]["environment_manifest"] = reference(environment)
    write_json(path, value)
    assert main(command(path, output, "--dry-run")) == 0
    assert not output.exists()
