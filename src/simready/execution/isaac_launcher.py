"""Launch a packaged scene workflow locally, in Docker, or on an SSH server."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path


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


def launch_worker(
    worker,
    *,
    repo,
    assets,
    output,
    backend="docker",
    python=None,
    image="simready-isaac:6.1.0",
    gpu="0",
):
    """Execute one argv in an isolated output scope and preserve its receipt."""
    from simready.execution.isaac_container import (
        container_command,
        execute_container,
        validate_paths,
    )

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
            repo=repo, assets=assets, output=output, image=image, gpu=gpu, worker=worker
        )
    else:
        command = [str(python or sys.executable), *worker]
    print(shlex.join(command), flush=True)
    receipt_path, receipt = reserve_receipt(output)
    record = {
        "backend": backend,
        "argv": command,
        "returncode": None,
        "status": "RUNNING",
    }
    receipt.write(json.dumps(record, indent=2) + "\n")
    receipt.flush()
    print(f"Launch receipt: {receipt_path}", flush=True)
    try:
        code = (
            execute_container(command)
            if backend == "docker"
            else subprocess.run(
                command, env=worker_environment(repo, output), check=False
            ).returncode
        )
        record.update(returncode=code, status="COMPLETE" if code == 0 else "FAILED")
        return code
    except Exception as error:
        record.update(status="FAILED", error=type(error).__name__)
        raise
    except BaseException as error:
        record.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        receipt.seek(0)
        receipt.write(json.dumps(record, indent=2) + "\n")
        receipt.truncate()
        receipt.close()


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
        "--config",
        default="examples/scenes/substation.json",
        help="Path relative to repository",
    )
    parser.add_argument(
        "--python", default=sys.executable, help="Isaac Python for local mode"
    )
    parser.add_argument("--image", default="simready-isaac:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument(
        "--ssh-host",
        help="SSH config alias; repo/assets must already be on this server",
    )
    parser.add_argument(
        "--accept-eula",
        action="store_true",
        help="Confirm acceptance of NVIDIA Isaac Sim EULA",
    )
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument(
        "--require-lab",
        action="store_true",
        help="Require the physics worker dependencies in doctor mode",
    )
    parser.add_argument(
        "--launch-smoke",
        action="store_true",
        help="Start Kit for 10 updates in doctor mode",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from simready.execution.isaac_container import (
        container_command,
        remote_command,
        validate_paths,
    )

    if Path(args.config).is_absolute() or ".." in Path(args.config).parts:
        parser.error("--config must stay inside the repository")
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
            "--config",
            args.config,
            "--image",
            args.image,
            "--gpu",
            args.gpu,
        ]
        for flag in ("accept_eula", "doctor", "launch_smoke", "require_lab"):
            if getattr(args, flag):
                command.append("--" + flag.replace("_", "-"))
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
            else [
                "-m",
                "simready.workflows.scene_delivery",
                "--config",
                str(
                    Path("/workspace" if args.backend == "docker" else args.repo)
                    / args.config
                ),
                "--assets",
                "/assets" if args.backend == "docker" else str(args.assets),
                "--out",
                "/output/delivery"
                if args.backend == "docker"
                else str(args.out / "delivery"),
            ]
        )
        if args.doctor and args.require_lab:
            worker += ["--require-lab"]
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
        )
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(command, env=worker_environment(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
