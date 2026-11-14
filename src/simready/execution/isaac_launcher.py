"""Launch an explicit Isaac worker locally, in Docker, or on an SSH server."""

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from simready.core.artifacts import read_json
from simready.core.validation import number
from simready.execution.device_lease import device_lease
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.execution.resource_pool import DEFAULT_LOCK_DIR


def package_source_root():
    """Directory containing simready, also valid for a non-editable wheel install."""
    return Path(__file__).resolve().parents[2]


def default_repository():
    candidate = package_source_root().parent
    if (candidate / "src/simready/cli.py").is_file():
        return candidate
    return Path.cwd()


def worker_environment(repo=None, source_output=None):
    """Expose project code without importing another interpreter's binary wheels.

    Wheel installs need a package-only path: host site-packages may contain
    NumPy/SciPy binaries for a different Python ABI than the Isaac interpreter.
    A caller-owned output directory can hold that isolated source link.
    """
    from simready.execution.isaac_environment import isaac_environment

    env = isaac_environment()
    source = Path(repo) / "src" if repo is not None else None
    package_root = package_source_root()
    if source is None or not (source / "simready/cli.py").is_file():
        source = None
        if (
            package_root.name == "src"
            and (package_root.parent / "pyproject.toml").is_file()
        ):
            source = package_root
        elif source_output is not None:
            source = Path(source_output) / ".python-source"
            if source.is_symlink():
                raise ValueError("Worker source directory must not be a symlink")
            source.mkdir(exist_ok=True)
            link = source / "simready"
            package = (package_root / "simready").resolve()
            if link.exists() or link.is_symlink():
                if not link.is_symlink() or link.resolve() != package:
                    raise ValueError(
                        "Worker source link conflicts with an existing path"
                    )
            else:
                link.symlink_to(package, target_is_directory=True)
    if source is not None:
        env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    return env


def reserve_receipt(output):
    """Reserve a fresh receipt; a failed retry cannot overwrite prior evidence."""
    path = output / "launch.json"
    while True:
        try:
            return path, path.open("x")
        except FileExistsError:
            path = output / f"launch-{uuid.uuid4().hex}.json"


def seed_runtime_cache(source, output):
    """Stage complete private copies before publishing; never preserve symlinks."""
    if source is None:
        return
    source, output = (Path(path).expanduser().absolute() for path in (source, output))
    for root in (source, output):
        _check_cache_links(root, recursive=False)
    source, output = source.resolve(), output.resolve()
    if source == output:
        return
    relatives = (".isaac-cache", ".isaac-home/.cache")
    origins = [source / relative for relative in relatives]
    targets = [output / relative for relative in relatives]
    if any(
        output.is_relative_to(origin)
        or any(
            target.is_relative_to(origin) or origin.is_relative_to(target)
            for target in targets
        )
        for origin in origins
    ):
        raise ValueError("Source and destination runtime caches must not overlap")
    pairs = []
    for relative in relatives:
        origin, target = source / relative, output / relative
        _check_cache_links(origin)
        _check_cache_links(target)
        if not origin.is_dir():
            continue
        if target.exists() and not target.is_dir():
            raise ValueError("Runtime cache destination must be a directory")
        pairs.append((origin, target))
    if not pairs:
        return
    output.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".cache-seed-", dir=output))
    preserve_backup = False
    try:
        staged = []
        for index, (origin, target) in enumerate(pairs):
            stage = Path(scratch) / f"new-{index}"
            if target.exists():
                shutil.copytree(target, stage, symlinks=True)
            shutil.copytree(origin, stage, dirs_exist_ok=True, symlinks=True)
            # A source symlink introduced during copy stays a link in staging
            # and is rejected, rather than being dereferenced into shared state.
            _check_cache_links(stage)
            staged.append((stage, target))
        published = []
        try:
            for index, (stage, target) in enumerate(staged):
                _check_cache_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = Path(scratch) / f"old-{index}" if target.exists() else None
                if backup is not None:
                    target.rename(backup)
                published.append((target, backup))
                stage.rename(target)
        except BaseException as error:
            for target, backup in reversed(published):
                try:
                    if target.exists():
                        shutil.rmtree(target)
                    if backup is not None:
                        backup.rename(target)
                except OSError as rollback_error:
                    preserve_backup = True
                    if hasattr(error, "add_note"):
                        error.add_note(
                            f"Cache rollback failed; backups kept at {scratch}: {rollback_error}"
                        )
            raise
    finally:
        if not preserve_backup:
            original_error = sys.exc_info()[1]
            try:
                shutil.rmtree(scratch)
            except OSError as cleanup_error:
                if original_error is None:
                    raise
                if hasattr(original_error, "add_note"):
                    original_error.add_note(
                        f"Cache staging cleanup failed at {scratch}: {cleanup_error}"
                    )


def _check_cache_links(root, *, recursive=True):
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("Runtime cache paths must not traverse symlinks")
    if (
        recursive
        and root.is_dir()
        and any(path.is_symlink() for path in root.rglob("*"))
    ):
        raise ValueError("Runtime cache trees must not contain symlinks")


def _validate_timeouts(timeout_s, cleanup_timeout_s):
    for label, value in (
        ("timeout_s", timeout_s),
        ("cleanup_timeout_s", cleanup_timeout_s),
    ):
        if number(value, label) <= 0:
            raise ValueError(f"{label} must be positive")
    if cleanup_timeout_s > 15:
        raise ValueError(
            "cleanup_timeout_s must be <=15 to fit the 20-second nested cancellation grace"
        )


def _execute_launch(
    command,
    *,
    output,
    backend,
    timeout_s,
    cleanup_timeout_s,
    cancel_event=None,
    environment=None,
    device_request=None,
):
    """Persist every terminal state; successful launch is not task acceptance."""
    from simready.execution.isaac_container import execute_container

    _validate_timeouts(timeout_s, cleanup_timeout_s)
    output.mkdir(parents=True, exist_ok=True)
    receipt_path, receipt = reserve_receipt(output)
    stdout = receipt_path.with_suffix(".stdout.log")
    stderr = receipt_path.with_suffix(".stderr.log")
    record = {
        "backend": backend,
        "argv": command,
        "returncode": None,
        "status": "RUNNING",
        "timeout_s": timeout_s,
        "cleanup_timeout_s": cleanup_timeout_s,
        "nested_cancellation_grace_s": 20,
        "stdout": str(stdout),
        "stderr": str(stderr),
        "process_cleanup_scope": "owned process group and cooperating nested owners; unmanaged detached sessions excluded",
    }
    if backend == "ssh":
        record["remote_status"] = "UNKNOWN"
        record["remote_cancellation_confirmed"] = False
    if backend == "docker":
        record["container"] = {}
    receipt.write(json.dumps(record, indent=2) + "\n")
    receipt.flush()
    print(shlex.join(command), flush=True)
    print(
        f"Launch receipt: {receipt_path}\nstdout: {stdout}\nstderr: {stderr}",
        flush=True,
    )
    started = time.monotonic()
    try:
        with (
            device_lease(**device_request)
            if device_request is not None
            else nullcontext(None) as lease
        ):
            if lease is not None:
                record["device_lease"] = {
                    "resource_kind": lease.resources["kind"],
                    "gpu_uuid": lease.gpu["uuid"] if lease.gpu else None,
                    "lock_directory": str(lease.pool.directory),
                }
            if backend == "docker":
                command = list(command)
                if lease.gpu is not None:
                    command[command.index("--gpus") + 1] = f"device={lease.gpu['uuid']}"
                record["argv"] = command
                try:
                    code = execute_container(
                        command,
                        timeout_s=timeout_s,
                        cleanup_timeout_s=cleanup_timeout_s,
                        cancel_event=cancel_event,
                        stdout=stdout,
                        stderr=stderr,
                        details=record["container"],
                    )
                finally:
                    cleanup = record["container"].get("cleanup", {})
                    if lease.gpu is not None and cleanup.get("status") not in {
                        "NOT_STARTED",
                        "NOT_REQUIRED_ON_SUCCESS",
                        "REMOVED",
                    }:
                        lease.quarantine(record["container"])
                        record["device_lease"]["quarantined"] = True
            else:
                env = environment() if environment is not None else None
                if lease is not None:
                    env = {**(os.environ if env is None else env), **lease.environment}
                code = run_process(
                    command,
                    cwd=None,
                    timeout=timeout_s,
                    stdout=stdout,
                    stderr=stderr,
                    env=env,
                    pass_fds=lease.pass_fds if lease is not None else (),
                    cancel_event=cancel_event,
                ).returncode
            if lease is not None:
                record["device_lease"]["quarantined"] = lease.quarantined
                if lease.quarantined and code == 0:
                    raise RuntimeError("GPU lease was quarantined by a nested worker")
        record.update(returncode=code, status="COMPLETE" if code == 0 else "FAILED")
        if backend == "ssh" and code == 0:
            record["remote_status"] = "COMMAND_EXITED_ZERO"
        return code
    except ProcessTimeoutError as error:
        record.update(status="TIMEOUT", error=type(error).__name__)
        raise
    except ProcessCancelledError as error:
        record.update(status="CANCELLED", error=type(error).__name__)
        raise
    except Exception as error:
        record.update(status="FAILED", error=type(error).__name__)
        raise
    except BaseException as error:
        record.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        record["seconds"] = time.monotonic() - started
        receipt.seek(0)
        receipt.write(json.dumps(record, indent=2) + "\n")
        receipt.truncate()
        receipt.close()


def launch_worker(
    worker,
    *,
    repo,
    assets,
    output,
    backend="docker",
    python=None,
    image="simready-isaac-core:6.1.0",
    gpu="0",
    timeout_s=7200,
    cleanup_timeout_s=15,
    cancel_event=None,
    resource_kind="rtx",
    device_inventory=None,
    lock_dir=DEFAULT_LOCK_DIR,
):
    """Run with a finite deadline plus a bounded Docker cleanup allowance.

    stdout/stderr are isolated per receipt, rather than inherited from the
    terminal. Cancellation only owns the worker process group and, for Docker,
    the verified container. Cooperating nested owners propagate cancellation;
    unmanaged descendants that create new sessions are excluded.
    """
    from simready.execution.isaac_container import (
        container_command,
        validate_paths,
    )

    _validate_timeouts(timeout_s, cleanup_timeout_s)
    repo, assets, output = validate_paths(repo, assets, output)
    if backend not in ("local", "docker"):
        raise ValueError("Worker backend must be local or docker")
    output.mkdir(parents=True, exist_ok=True)
    if backend == "docker":
        for subdir in (".isaac-home", ".isaac-cache", ".isaac-data", ".isaac-logs"):
            path = output / subdir
            if path.is_symlink():
                raise ValueError("Isaac runtime directories must not be symlinks")
            path.mkdir(exist_ok=True)
        command = container_command(
            repo=repo,
            assets=assets,
            output=output,
            image=image,
            gpu=None if resource_kind == "cpu" else gpu,
            worker=worker,
        )
    else:
        command = [str(python or sys.executable), *worker]
    return _execute_launch(
        command,
        output=output,
        backend=backend,
        timeout_s=timeout_s,
        cleanup_timeout_s=cleanup_timeout_s,
        cancel_event=cancel_event,
        environment=lambda: worker_environment(repo, output),
        device_request=dict(
            resource_kind=resource_kind,
            gpu=gpu,
            device_inventory=device_inventory,
            lock_dir=lock_dir,
            container=backend == "docker",
        ),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=["local", "docker", "ssh"], default="docker"
    )
    parser.add_argument("--repo", type=Path, default=default_repository())
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Parent output directory; workflow writes delivery/",
    )
    parser.add_argument(
        "--python", default=sys.executable, help="Isaac Python for local mode"
    )
    parser.add_argument("--image", default="simready-isaac-core:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument(
        "--resource-kind", choices=["cpu", "cuda", "rtx"], default="rtx"
    )
    parser.add_argument(
        "--device-inventory",
        type=Path,
        help="Reviewed physical UUID inventory; otherwise discover locally",
    )
    parser.add_argument("--lock-dir", type=Path, default=Path(DEFAULT_LOCK_DIR))
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=7200,
        help="Finite worker/SSH client deadline in seconds",
    )
    parser.add_argument(
        "--cleanup-timeout-s",
        type=float,
        default=15,
        help="Additional bound for owned Docker cleanup",
    )
    parser.add_argument(
        "--ssh-host",
        help="SSH config alias; repo/assets must already be on this server",
    )
    parser.add_argument(
        "--ssh-local-output",
        type=Path,
        help="Local SSH receipt/log directory (default: runs/ssh-launch-<UUID>)",
    )
    parser.add_argument(
        "--accept-eula",
        action="store_true",
        help="Confirm acceptance of NVIDIA Isaac Sim EULA",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument(
        "--worker",
        nargs=argparse.REMAINDER,
        help="Explicit Python argv, placed last; e.g. -m simready.simulation.physical_probe ...",
    )
    parser.add_argument(
        "--launch-smoke",
        action="store_true",
        help="Start Kit for 10 updates in doctor mode",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.worker is not None and not args.worker:
        parser.error("--worker requires nonempty Python argv")
    if args.launch_smoke and not args.doctor:
        parser.error("--launch-smoke requires --doctor")
    try:
        _validate_timeouts(args.timeout_s, args.cleanup_timeout_s)
    except ValueError as error:
        parser.error(str(error))
    from simready.execution.isaac_container import (
        container_command,
        remote_command,
        validate_paths,
    )

    if args.backend == "ssh":
        if not args.ssh_host:
            parser.error("SSH mode requires --ssh-host")
        # Paths here are remote paths: do not resolve them against this machine.
        command = [
            str(args.repo / ".venv/bin/python"),
            "-m",
            "simready.execution.isaac_launcher",
            "--backend",
            "docker",
            "--repo",
            str(args.repo),
            "--assets",
            str(args.assets),
            "--out",
            str(args.out),
            "--image",
            args.image,
            "--gpu",
            args.gpu,
            "--resource-kind",
            args.resource_kind,
            "--lock-dir",
            str(args.lock_dir),
            "--timeout-s",
            str(args.timeout_s),
            "--cleanup-timeout-s",
            str(args.cleanup_timeout_s),
        ]
        if args.device_inventory:
            command += ["--device-inventory", str(args.device_inventory)]
        for flag in ("accept_eula", "doctor", "launch_smoke"):
            if getattr(args, flag):
                command.append("--" + flag.replace("_", "-"))
        if args.worker is not None:
            command += ["--worker", *args.worker]
        command = remote_command(args.ssh_host, command)
    else:
        if not args.dry_run and not args.accept_eula:
            parser.error(
                "Pass --accept-eula after accepting the NVIDIA Isaac Sim license"
            )
        try:
            args.repo, args.assets, args.out = validate_paths(
                args.repo, args.assets, args.out
            )
        except ValueError as error:
            parser.error(str(error))
        delivery = args.out / "delivery"
        if not args.doctor and (delivery.exists() or delivery.is_symlink()):
            parser.error(
                "Output already contains delivery; select a fresh run directory"
            )
        if not args.dry_run:
            args.out.mkdir(parents=True, exist_ok=True)
            for subdir in (".isaac-home", ".isaac-cache", ".isaac-data", ".isaac-logs"):
                (args.out / subdir).mkdir(exist_ok=True)
        if not args.out.is_dir():
            parser.error("Create the output parent before a Docker dry run")
        worker = (
            ["-m", "simready.simulation.environment_check"]
            if args.doctor
            else args.worker
        )
        if args.doctor and args.launch_smoke:
            worker += ["--launch"]
        if args.backend == "docker":
            command = container_command(
                repo=args.repo,
                assets=args.assets,
                output=args.out,
                image=args.image,
                gpu=args.gpu,
                worker=worker,
            )
        else:
            command = [args.python, *worker]
    if args.backend != "ssh":
        if args.dry_run:
            print(shlex.join(command), flush=True)
            return 0
        return launch_worker(
            worker,
            repo=args.repo,
            assets=args.assets,
            output=args.out,
            backend=args.backend,
            python=args.python,
            image=args.image,
            gpu=args.gpu,
            timeout_s=args.timeout_s,
            cleanup_timeout_s=args.cleanup_timeout_s,
            resource_kind=args.resource_kind,
            device_inventory=read_json(args.device_inventory)
            if args.device_inventory
            else None,
            lock_dir=args.lock_dir,
        )
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    local_output = (
        args.ssh_local_output or Path("runs") / f"ssh-launch-{uuid.uuid4().hex}"
    )
    return _execute_launch(
        command,
        output=local_output.expanduser().resolve(),
        backend="ssh",
        timeout_s=args.timeout_s,
        cleanup_timeout_s=args.cleanup_timeout_s,
        environment=worker_environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
