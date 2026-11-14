"""Build argv for an owned Isaac Sim container; never modify an existing service."""

import os
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path

from simready.core.validation import number
from simready.execution.processes.local import ProcessCancelledError, run_process

DEFAULT_IMAGE = "simready-isaac-core:6.1.0"
OWNERSHIP_LABEL = "org.simready.launch"


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
    if gpu is not None and not re.fullmatch(r"(?:[0-9]+|GPU-[a-zA-Z0-9-]+)", gpu):
        raise ValueError("Select one GPU index or UUID")
    name = f"simready-{uuid.uuid4().hex[:12]}"
    ownership = uuid.uuid4().hex
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
        "--label",
        f"{OWNERSHIP_LABEL}={ownership}",
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
    if gpu is not None:
        cmd += ["--gpus", f"device={gpu}"]
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


def _owned_identity(command):
    """A name alone cannot authorize removal after a name-collision failure."""
    if command[:2] != ["docker", "run"]:
        raise ValueError("Expected one owned Docker run command")
    # Parse only the options emitted by container_command, stopping at IMAGE.
    # Worker arguments after IMAGE may legitimately include --name/--label.
    flags = {"--rm", "--init", "--no-healthcheck"}
    values = {
        "--name",
        "--label",
        "--gpus",
        "--shm-size",
        "--user",
        "--group-add",
        "-e",
        "--workdir",
        "--entrypoint",
        "--mount",
    }
    names, labels = [], []
    index = 2
    while index < len(command) and command[index].startswith("-"):
        option = command[index]
        if option in flags:
            index += 1
            continue
        if option not in values or index + 1 >= len(command):
            raise ValueError("Unsupported or incomplete owned Docker option")
        if option == "--name":
            names.append(command[index + 1])
        elif option == "--label":
            labels.append(command[index + 1])
        index += 2
    if index >= len(command) or len(names) != 1:
        raise ValueError("Expected an image and one owned container name")
    name = names[0]
    tokens = [
        label.removeprefix(OWNERSHIP_LABEL + "=")
        for label in labels
        if label.startswith(OWNERSHIP_LABEL + "=")
    ]
    if (
        not re.fullmatch(r"simready-[a-f0-9]{12}", name)
        or "org.simready.owned=true" not in labels
        or len(tokens) != 1
        or not re.fullmatch(r"[a-f0-9]{32}", tokens[0])
    ):
        raise ValueError("Missing unique container ownership identity")
    return name, tokens[0]


def _cleanup_owned_container(name, ownership, *, timeout_s, prefix, report):
    """Bound inspect+remove together; never replace the original worker error."""
    deadline = time.monotonic() + timeout_s
    report.update(status="UNKNOWN", timeout_s=timeout_s)
    try:
        inspect_out = Path(str(prefix) + ".inspect.stdout.log")
        inspect_err = Path(str(prefix) + ".inspect.stderr.log")
        report.update(inspect_stdout=str(inspect_out), inspect_stderr=str(inspect_err))
        result = run_process(
            [
                "docker",
                "inspect",
                "--format",
                '{{.Id}} {{index .Config.Labels "' + OWNERSHIP_LABEL + '"}}',
                name,
            ],
            cwd=None,
            timeout=max(0.000001, deadline - time.monotonic()),
            stdout=inspect_out,
            stderr=inspect_err,
            inherit_cancellation=False,
        )
        report["inspect_returncode"] = result.returncode
        if result.returncode != 0:
            # Failed inspection also covers daemon failure and creation races;
            # it cannot prove that the remote container has been cancelled.
            report["reason"] = "Container absence/removal could not be confirmed"
            return
        identity = inspect_out.read_text().strip().split()
        if (
            len(identity) != 2
            or not re.fullmatch(r"[a-f0-9]{64}", identity[0])
            or identity[1] != ownership
        ):
            report.update(status="OWNERSHIP_MISMATCH", reason="No removal attempted")
            return
        report["container_id"] = identity[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            report.update(status="TIMEOUT")
            return
        remove_out = Path(str(prefix) + ".remove.stdout.log")
        remove_err = Path(str(prefix) + ".remove.stderr.log")
        report.update(remove_stdout=str(remove_out), remove_stderr=str(remove_err))
        result = run_process(
            ["docker", "rm", "--force", identity[0]],
            cwd=None,
            timeout=remaining,
            stdout=remove_out,
            stderr=remove_err,
            inherit_cancellation=False,
        )
        report.update(
            remove_returncode=result.returncode,
            status="REMOVED" if result.returncode == 0 else "FAILED",
        )
    except BaseException as error:
        # This runs while preserving another exception/exit status. A second
        # interrupt, missing docker client or cleanup timeout must not mask it.
        report.update(status="FAILED", error=type(error).__name__)


def execute_container(
    command,
    *,
    timeout_s=7200,
    cleanup_timeout_s=15,
    cancel_event=None,
    stdout=None,
    stderr=None,
    details=None,
):
    """Run a bounded client and clean up only its token-verified container ID.

    The cleanup allowance is additional to timeout_s. Failed inspection is
    UNKNOWN: a daemon may still be creating a container after its client died.
    """
    for label, value in (
        ("timeout_s", timeout_s),
        ("cleanup_timeout_s", cleanup_timeout_s),
    ):
        if number(value, label) <= 0:
            raise ValueError(f"{label} must be positive")
    name, ownership = _owned_identity(command)
    if cleanup_timeout_s > 15:
        raise ValueError(
            "cleanup_timeout_s must be <=15 to fit nested cancellation grace"
        )
    details = details if details is not None else {}
    cleanup = details.setdefault("cleanup", {})
    details.update(name=name, ownership=ownership)
    if cancel_event is not None and cancel_event.is_set():
        cleanup["status"] = "NOT_STARTED"
        raise ProcessCancelledError(0.0)
    if stdout is None or stderr is None:
        directory = Path(tempfile.mkdtemp(prefix="simready-container-"))
        stdout = stdout or directory / "stdout.log"
        stderr = stderr or directory / "stderr.log"
    details.update(stdout=str(stdout), stderr=str(stderr))
    needs_cleanup = True
    try:
        result = run_process(
            command,
            cwd=None,
            timeout=timeout_s,
            stdout=stdout,
            stderr=stderr,
            cancel_event=cancel_event,
        )
        if result.returncode == 0:
            needs_cleanup = False
            cleanup["status"] = "NOT_REQUIRED_ON_SUCCESS"
        return result.returncode
    except FileNotFoundError:
        needs_cleanup = False
        cleanup["status"] = "NOT_STARTED"
        raise
    finally:
        if needs_cleanup:
            _cleanup_owned_container(
                name,
                ownership,
                timeout_s=cleanup_timeout_s,
                prefix=Path(str(stdout) + ".cleanup"),
                report=cleanup,
            )
