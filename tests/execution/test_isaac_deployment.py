"""Deployment boundaries: preserve argv, mount scope, and existing services."""

import shlex

import pytest

from simready.execution.isaac_container import container_command, remote_command


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
    assert command[command.index("--name") + 1].startswith("simready-")


def test_remote_argv_cannot_inject_shell_commands():
    args = ["python3", "/srv/a b/launch.py", "--out", "/tmp/$(touch nope); end"]
    command = remote_command("user@rtx-server", args)
    assert shlex.split(command[-1]) == args
    for host in ["-oProxyCommand=anything", "host;command", "a b", "a\nb"]:
        with pytest.raises(ValueError):
            remote_command(host, args)
