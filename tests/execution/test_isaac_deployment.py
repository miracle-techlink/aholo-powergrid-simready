"""Deployment boundaries: preserve argv, mount scope, and existing services."""

import json
import shlex
import sys

import pytest

from scripts.launch_isaac import main
from simready.execution.isaac_container import (
    container_command,
    remote_command,
    validate_paths,
)


def test_mounts_preserve_spaces_and_only_output_is_writable(tmp_path):
    paths = [
        tmp_path / name for name in ("repo source", "scene assets", "result output")
    ]
    for path in paths:
        path.mkdir()
    command = container_command(repo=paths[0], assets=paths[1], output=paths[2])
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert f"type=bind,src={paths[0]},dst=/workspace,readonly" in mounts
    assert f"type=bind,src={paths[1]},dst=/assets,readonly" in mounts
    assert f"type=bind,src={paths[2]},dst=/output" in mounts
    assert not any("docker.sock" in x for x in command)
    assert "--privileged" not in command
    assert "--no-healthcheck" in command
    assert command[command.index("--name") + 1].startswith("simready-")


def test_remote_argv_cannot_inject_shell_commands():
    args = ["python3", "/srv/a b/launch.py", "--out", "/tmp/$(touch nope); end"]
    command = remote_command("user@rtx-server", args)
    assert shlex.split(command[-1]) == args
    for host in ["-oProxyCommand=anything", "host;command", "a b", "a\nb"]:
        with pytest.raises(ValueError):
            remote_command(host, args)


@pytest.mark.parametrize(
    "relation",
    [
        "equal_assets",
        "inside_assets",
        "above_assets",
        "equal_repo",
        "above_repo",
        "asset_symlink",
    ],
)
def test_output_cannot_alias_or_overlap_inputs(tmp_path, relation):
    repo, assets = tmp_path / "repo", tmp_path / "inputs" / "assets"
    repo.mkdir()
    assets.mkdir(parents=True)
    outputs = {
        "equal_assets": assets,
        "inside_assets": assets / "new-output",
        "above_assets": assets.parent,
        "equal_repo": repo,
        "above_repo": tmp_path,
        "asset_symlink": tmp_path / "alias",
    }
    if relation == "asset_symlink":
        outputs[relation].symlink_to(assets, target_is_directory=True)
    with pytest.raises(ValueError):
        validate_paths(repo, assets, outputs[relation])
    assert not (assets / "new-output").exists()


def test_repository_run_subdirectory_is_supported(tmp_path):
    repo, assets = tmp_path / "repo", tmp_path / "assets"
    repo.mkdir()
    assets.mkdir()
    output = repo / "runs" / "new"
    assert validate_paths(repo, assets, output) == (repo, assets, output)
    assert not output.exists()


@pytest.mark.parametrize("backend", ["docker", "local"])
def test_launcher_rejects_overlap_before_creating_directories(
    tmp_path, monkeypatch, backend
):
    repo, assets = tmp_path / "repo", tmp_path / "assets"
    repo.mkdir()
    assets.mkdir()
    output = assets / "new-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launch_isaac.py",
            "--backend",
            backend,
            "--repo",
            str(repo),
            "--assets",
            str(assets),
            "--out",
            str(output),
            "--accept-eula",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not output.exists()


def test_launcher_preserves_existing_delivery_before_start(tmp_path, monkeypatch):
    repo, assets, output = (tmp_path / name for name in ("repo", "assets", "out"))
    for path in (repo, assets, output):
        path.mkdir()
    (output / "delivery").mkdir()
    original = '{"returncode": 0}\n'
    (output / "launch.json").write_text(original)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launch_isaac.py",
            "--repo",
            str(repo),
            "--assets",
            str(assets),
            "--out",
            str(output),
            "--accept-eula",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert (output / "launch.json").read_text() == original
    assert sorted(p.name for p in output.iterdir()) == ["delivery", "launch.json"]


def test_failed_retry_creates_new_receipt_without_overwriting_success(
    tmp_path, monkeypatch
):
    repo, assets, output = (tmp_path / name for name in ("repo", "assets", "out"))
    for path in (repo, assets, output):
        path.mkdir()
    original = '{"returncode": 0, "status": "COMPLETE"}\n'
    (output / "launch.json").write_text(original)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launch_isaac.py",
            "--repo",
            str(repo),
            "--assets",
            str(assets),
            "--out",
            str(output),
            "--doctor",
            "--resource-kind",
            "cpu",
            "--accept-eula",
        ],
    )
    monkeypatch.setattr(
        "simready.execution.isaac_container.execute_container",
        lambda command, **kwargs: 2,
    )
    assert main() == 2
    assert (output / "launch.json").read_text() == original
    retries = list(output.glob("launch-*.json"))
    assert len(retries) == 1
    report = json.loads(retries[0].read_text())
    assert report["returncode"] == 2 and report["status"] == "FAILED"


@pytest.mark.parametrize(
    "error_type,status",
    [(FileNotFoundError, "FAILED"), (KeyboardInterrupt, "INTERRUPTED")],
)
def test_launch_errors_are_distinguished_from_interruptions(
    tmp_path, monkeypatch, error_type, status
):
    from simready.execution.isaac_launcher import launch_worker

    repo, assets, output = (tmp_path / name for name in ("repo", "assets", "out"))
    repo.mkdir()
    assets.mkdir()

    def fail(*args, **kwargs):
        raise error_type("fixture launch failure")

    monkeypatch.setattr("simready.execution.isaac_launcher.run_process", fail)
    with pytest.raises(error_type):
        launch_worker(
            ["-c", "pass"],
            repo=repo,
            assets=assets,
            output=output,
            backend="local",
            resource_kind="cpu",
        )
    receipt = json.loads((output / "launch.json").read_text())
    assert receipt["status"] == status
    assert receipt["error"] == error_type.__name__
    assert receipt["returncode"] is None


def test_cache_seed_is_private_and_does_not_copy_runtime_config(tmp_path):
    from simready.execution.isaac_launcher import seed_runtime_cache

    source, target = tmp_path / "completed", tmp_path / "new"
    cache = source / ".isaac-cache"
    cache.mkdir(parents=True)
    (cache / "shader").write_bytes(b"compiled")
    (source / "launch.json").write_text("old receipt")
    seed_runtime_cache(source, target)
    (target / ".isaac-cache/shader").write_bytes(b"changed")
    assert (cache / "shader").read_bytes() == b"compiled"
    assert not (target / "launch.json").exists()


def test_cache_seed_rejects_symlink_destination(tmp_path):
    from simready.execution.isaac_launcher import seed_runtime_cache

    source, target, unrelated = (
        tmp_path / x for x in ("source", "target", "unrelated")
    )
    (source / ".isaac-cache").mkdir(parents=True)
    target.mkdir()
    unrelated.mkdir()
    (target / ".isaac-cache").symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        seed_runtime_cache(source, target)
