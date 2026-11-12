"""Launch a packaged scene workflow locally, in Docker, or on an SSH server."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=["local", "docker", "ssh"], default="docker"
    )
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
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
        "--launch-smoke",
        action="store_true",
        help="Start Kit for 10 updates in doctor mode",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from simready.execution.isaac_container import (
        container_command,
        execute_container,
        remote_command,
    )

    if Path(args.config).is_absolute() or ".." in Path(args.config).parts:
        parser.error("--config must stay inside the repository")
    if args.backend == "ssh":
        if not args.ssh_host:
            parser.error("SSH mode requires --ssh-host")
        # Paths here are remote paths: do not resolve them against this machine.
        command = [
            str(args.repo / ".venv/bin/python"),
            str(args.repo / "scripts/launch_isaac.py"),
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
        for flag in ("accept_eula", "doctor", "launch_smoke"):
            if getattr(args, flag):
                command.append("--" + flag.replace("_", "-"))
        command = remote_command(args.ssh_host, command)
    else:
        if not args.dry_run and not args.accept_eula:
            parser.error(
                "Pass --accept-eula after accepting the NVIDIA Isaac Sim license"
            )
        args.repo = args.repo.resolve()
        args.assets = args.assets.resolve()
        args.out = args.out.resolve()
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
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    if args.backend == "docker":
        code = execute_container(command)
    else:
        from simready.execution.isaac_environment import isaac_environment

        env = isaac_environment()
        env["PYTHONPATH"] = (
            str(args.repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        )
        env["OMNI_KIT_ACCEPT_EULA"] = "YES"
        code = subprocess.run(command, env=env, check=False).returncode
    if args.backend != "ssh":
        (args.out / "launch.json").write_text(
            json.dumps(
                {"backend": args.backend, "argv": command, "returncode": code}, indent=2
            )
            + "\n"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
