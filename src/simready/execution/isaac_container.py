"""Build argv for an owned Isaac Sim container; never modify an existing service."""

import os
import re
import shlex
import subprocess
import uuid
from pathlib import Path

DEFAULT_IMAGE = "simready-isaac:6.1.0"


def validate_paths(repo, assets, output):
    """Resolve aliases and reject output scopes that can mutate input trees."""
    repo, assets, output = (
        Path(p).expanduser().resolve() for p in (repo, assets, output)
    )
    if not repo.is_dir() or not assets.is_dir():
        raise ValueError("Repository and asset directories must exist")
    if output.is_relative_to(assets) or assets.is_relative_to(output):
        raise ValueError("Output and asset directories must not overlap")
    if repo.is_relative_to(output):
        raise ValueError("Output must not equal or contain the repository")
    if any(any(c in str(p) for c in (",", "\n", "\r")) for p in (repo, assets, output)):
        raise ValueError("Worker paths cannot contain commas or newlines")
    return repo, assets, output


def container_command(
    *, repo, assets, output, image=DEFAULT_IMAGE, gpu="0", worker=None
):
    """Return a mount-scoped Docker command; input directories are read-only."""
    paths = validate_paths(repo, assets, output)
    if not paths[2].is_dir():
        raise ValueError("Output directory must exist")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/@-]*", image):
        raise ValueError("Invalid container image")
    if not re.fullmatch(r"(?:[0-9]+|GPU-[a-zA-Z0-9-]+)", gpu):
        raise ValueError("Select one GPU index or UUID")
    name = f"simready-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--init",
        # The upstream healthcheck expects a long-lived app and its default
        # log directory. A one-shot worker reports success via exit/evidence.
        "--no-healthcheck",
        "--name",
        name,
        "--label",
        "org.simready.owned=true",
        "--gpus",
        f"device={gpu}",
        "--shm-size",
        "8g",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--group-add",
        "1234",
        "-e",
        "ACCEPT_EULA=Y",
        "-e",
        "NVIDIA_DRIVER_CAPABILITIES=all",
        "-e",
        "OMNI_KIT_ACCEPT_EULA=YES",
        "-e",
        "HOME=/output/.isaac-home",
        "-e",
        "PYTHONPATH=/workspace/src",
        "--workdir",
        "/workspace",
        "--entrypoint",
        "/isaac-sim/python.sh",
    ]
    for path, target, readonly in zip(
        paths, ("/workspace", "/assets", "/output"), (True, True, False), strict=True
    ):
        spec = f"type=bind,src={path},dst={target}"
        cmd += ["--mount", spec + (",readonly" if readonly else "")]
    for suffix, target in (
        (".isaac-cache", "/isaac-sim/kit/cache"),
        (".isaac-data", "/isaac-sim/kit/data"),
        (".isaac-logs", "/isaac-sim/kit/logs"),
    ):
        cmd += ["--mount", f"type=bind,src={paths[2] / suffix},dst={target}"]
    cmd += [image, *(worker or ["-m", "simready.simulation.environment_check"])]
    return cmd


def remote_command(host, command):
    """SSH receives one quoted argv, with option injection rejected."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*", host):
        raise ValueError("Use a trusted SSH config alias or user@hostname")
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        host,
        shlex.join(command),
    ]


def execute_container(command):
    """On interruption stop only the unique container created by this call."""
    name = command[command.index("--name") + 1]
    try:
        return subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        subprocess.run(["docker", "stop", "--time", "10", name], check=False)
        raise
