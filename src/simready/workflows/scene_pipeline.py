"""One command for reviewed scene reproduction; no paid cloud or raw-video reconstruction."""

import argparse
import copy
import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.isaac_container import remote_command, validate_paths
from simready.execution.isaac_launcher import (
    default_repository,
    launch_worker,
    worker_environment,
)
from simready.simulation.asset_bundle import verify_bundle
from simready.workflows.scene_delivery import (
    validate_scene_config,
    verify_scene_delivery,
)

SCENE_PRESETS = {
    "starbucks": {
        "schema_version": 1,
        "scene": "starbucks",
        "observation_fps": 2,
        "render": {
            "width": 3840,
            "height": 2160,
            "frames": 900,
            "fps": 30,
            "subframes": 32,
        },
        "scope": "Reviewed cafe reconstruction with authored G2 motion; no autonomous-navigation claim",
    },
    "substation": {
        "schema_version": 1,
        "scene": "substation",
        "observation_fps": 2,
        "render": {"width": 1920, "height": 1080, "frames": 714, "fps": 25},
        "scope": "Known-map Lite3 navigation, RGB-D target localization, Z1 physical press and independent contact audit; replay duration follows measured physics",
    },
}


def new_run_name(scene):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{scene}-{timestamp}-{uuid.uuid4().hex[:10]}"


def covered_stages(scene, export=False):
    return (
        (["bundle_export"] if export else [])
        + ["bundle_verification", "environment", "observations"]
        + (["physics", "audit"] if scene == "substation" else [])
        + ["render", "delivery_verification", "summary"]
    )


def remote_plan(args):
    if not args.ssh_host or not args.repo:
        raise ValueError("SSH requires --ssh-host and --repo for the remote checkout")
    repo = Path(args.repo)
    if not repo.is_absolute() or ".." in repo.parts:
        raise ValueError("SSH --repo must be an absolute remote path without '..'")

    def remote_path(value):
        path = Path(value)
        return path if path.is_absolute() else repo / path

    output = (
        remote_path(args.out) if args.out else repo / "runs" / new_run_name(args.scene)
    )
    command = [
        str(repo / ".venv/bin/python"),
        "-m",
        "simready",
        "run",
        "--scene",
        args.scene,
        "--backend",
        "docker",
        "--repo",
        str(repo),
        "--out",
        str(output),
        "--image",
        args.image,
        "--gpu",
        args.gpu,
    ]
    if args.bundle_spec:
        command += ["--bundle-spec", str(remote_path(args.bundle_spec))]
    else:
        command += ["--assets", str(remote_path(args.assets or f"data/{args.scene}"))]
    for flag in ("config", "bundle_python"):
        value = getattr(args, flag)
        if value:
            command += ["--" + flag.replace("_", "-"), str(remote_path(value))]
    if args.accept_eula:
        command.append("--accept-eula")
    return {
        "schema_version": 1,
        "scene": args.scene,
        "mode": "reviewed_scene_reproduction",
        "backend": "ssh",
        "remote_backend": "docker",
        "cloud_submission": False,
        "new_reconstruction": False,
        "covered_stages": covered_stages(args.scene, bool(args.bundle_spec)),
        "output": str(output),
        "argv": remote_command(args.ssh_host, command),
        "validation": "Remote bundle, environment and outputs are checked on execution; no local path resolution or transfer",
    }


def prepare_plan(args):
    """Read-only planning: no export, process, GPU, network or output creation."""
    if args.backend == "ssh":
        return remote_plan(args), None
    repo = Path(args.repo or default_repository()).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError("Repository directory does not exist")
    if args.backend == "docker" and not (repo / "src/simready/cli.py").is_file():
        raise ValueError(
            "Docker requires --repo pointing to a source checkout containing src/simready"
        )
    raw_output = (
        Path(args.out).expanduser()
        if args.out
        else Path("runs") / new_run_name(args.scene)
    )
    if raw_output.exists() or raw_output.is_symlink():
        raise FileExistsError(
            f"Output already exists; select a fresh run directory: {raw_output}"
        )
    output = raw_output.resolve()
    if repo.is_relative_to(output):
        raise ValueError("Output must not equal or contain the repository")
    if any(c in str(output) for c in (",", "\n", "\r")):
        raise ValueError("Worker paths cannot contain commas or newlines")
    config = (
        read_json(Path(args.config).expanduser().resolve())
        if args.config
        else copy.deepcopy(SCENE_PRESETS[args.scene])
    )
    validate_scene_config(config, args.scene)
    assets = Path(args.assets or f"data/{args.scene}").expanduser().resolve()
    bundle_spec = None
    if args.bundle_spec:
        bundle_spec = Path(args.bundle_spec).expanduser().resolve()
        spec = read_json(bundle_spec)
        if spec.get("scene") != args.scene:
            raise ValueError("Bundle specification and requested scene must match")
        keys = (
            ["source_video", "scene_usd", "robot_usd"]
            if args.scene == "starbucks"
            else ["source_video", "mission_config"]
        )
        for key in keys:
            path = (bundle_spec.parent / spec[key]).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Missing prepared source {key}: {path}")
            if path.is_relative_to(output):
                raise ValueError("Output must not contain prepared input files")
        assets = output / "bundle"
    else:
        if not assets.is_dir():
            raise FileNotFoundError(
                f"Prepared asset bundle missing: {assets}. Use --assets or --bundle-spec; raw-video reconstruction is not performed."
            )
        validate_paths(repo, assets, output)
        if verify_bundle(assets)["scene"] != args.scene:
            raise ValueError("Asset bundle and requested scene must match")
    execution = output / "execution"
    target_assets = "/assets" if args.backend == "docker" else str(assets)
    target_output = "/output" if args.backend == "docker" else str(execution)
    worker = [
        "-m",
        "simready.workflows.scene_execution",
        "--assets",
        target_assets,
        "--out",
        target_output,
    ]
    plan = {
        "schema_version": 1,
        "scene": args.scene,
        "mode": "reviewed_scene_reproduction",
        "backend": args.backend,
        "cloud_submission": False,
        "new_reconstruction": False,
        "scope": config["scope"]
        if "scope" in config
        else SCENE_PRESETS[args.scene]["scope"],
        "covered_stages": covered_stages(args.scene, bool(bundle_spec)),
        "repo": str(repo),
        "assets": str(assets),
        "bundle_spec": str(bundle_spec) if bundle_spec else None,
        "output": str(output),
        "execution_output": str(execution),
        "python": args.python,
        "bundle_python": args.bundle_python or args.python,
        "image": args.image if args.backend == "docker" else None,
        "gpu": args.gpu if args.backend == "docker" else None,
        "worker_argv": worker,
        "config": config,
        "validation": {
            "bundle": "export_pending" if bundle_spec else "verified",
            "environment": "checked_before_observations",
        },
    }
    return plan, config


def run_pipeline(args):
    plan, config = prepare_plan(args)
    if args.dry_run:
        return {"status": "PLANNED", **plan}, 0
    if not args.accept_eula:
        raise ValueError(
            "Pass --accept-eula after accepting the NVIDIA Isaac Sim license"
        )
    if args.backend == "ssh":
        print(
            json.dumps({"status": "REMOTE_RUNNING", **plan}, ensure_ascii=False),
            flush=True,
        )
        code = subprocess.run(plan["argv"], check=False).returncode
        # The remote command owns the detailed summary. An SSH failure does not
        # establish whether a disconnected remote process has already stopped.
        return {
            "status": "REMOTE_COMPLETE" if code == 0 else "REMOTE_FAILED",
            "returncode": code,
            **plan,
        }, code
    output, assets = Path(plan["output"]), Path(plan["assets"])
    output.mkdir(parents=True, exist_ok=False)
    print(f"Run output: {output}", flush=True)
    write_json(output / "run-plan.json", plan)
    state = {
        "schema_version": 1,
        "status": "RUNNING",
        "scene": args.scene,
        "mode": plan["mode"],
        "backend": args.backend,
        "covered_stages": plan["covered_stages"],
        "cloud_submission": False,
        "new_reconstruction": False,
        "output": str(output),
        "stage": "preparation",
    }
    start = time.monotonic()
    write_json(output / "summary.json", state)
    try:
        if plan["bundle_spec"]:
            state["stage"] = "bundle_export"
            write_json(output / "summary.json", state)
            command = [
                plan["bundle_python"],
                "-m",
                "simready",
                "simulation",
                "export-bundle",
                "--spec",
                plan["bundle_spec"],
                "--out",
                str(assets),
            ]
            export_started = time.monotonic()
            print(
                f"[bundle_export] RUNNING; log: {output / 'bundle-export.log'}",
                flush=True,
            )
            with (output / "bundle-export.log").open("x") as log:
                code = subprocess.run(
                    command,
                    env=worker_environment(plan["repo"], output),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                ).returncode
            if code:
                raise RuntimeError(
                    "Bundle export failed; inspect bundle-export.log. --bundle-python must provide OpenUSD/pxr."
                )
            print(
                f"[bundle_export] COMPLETE ({time.monotonic() - export_started:.1f}s)",
                flush=True,
            )
        state["stage"] = "bundle_verification"
        if verify_bundle(assets)["scene"] != args.scene:
            raise ValueError("Exported bundle and requested scene differ")
        state["bundle_sha256"] = digest(assets / "bundle.json")
        execution = Path(plan["execution_output"])
        execution.mkdir()
        write_json(execution / "scene.json", config)
        state["stage"] = "launch"
        write_json(output / "summary.json", state)
        code = launch_worker(
            plan["worker_argv"],
            repo=plan["repo"],
            assets=assets,
            output=execution,
            backend=args.backend,
            python=args.python,
            image=args.image,
            gpu=args.gpu,
        )
        worker_state = (
            read_json(execution / "pipeline-status.json")
            if (execution / "pipeline-status.json").is_file()
            else None
        )
        if code or not worker_state or worker_state.get("status") != "COMPLETE":
            if worker_state:
                state["stage"] = worker_state.get("stage", "launch")
            reason = (
                worker_state.get("error", "Worker did not complete")
                if worker_state
                else "Worker produced no completion receipt"
            )
            raise RuntimeError(f"{reason} (exit {code}); inspect {execution}")
        state["stage"] = "delivery_verification"
        evidence = verify_scene_delivery(execution / "delivery", config)
        state.update(status="COMPLETE", stage="summary", **evidence)
        return state, 0
    except Exception as error:
        state.update(status="FAILED", error=str(error))
        return state, 2
    except BaseException as error:
        state.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        state["wall_s"] = time.monotonic() - start
        write_json(output / "summary.json", state)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="simready run", description=__doc__)
    parser.add_argument("--scene", choices=SCENE_PRESETS, required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument(
        "--assets", type=Path, help="Prepared bundle; defaults to data/{scene}"
    )
    inputs.add_argument(
        "--bundle-spec",
        type=Path,
        help="Export a specification of existing reviewed assets before running",
    )
    parser.add_argument(
        "--backend", choices=("local", "docker", "ssh"), default="docker"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Fresh run root; defaults to runs/{scene}-UTC-unique-id",
    )
    parser.add_argument(
        "--repo", type=Path, help="Source checkout; required explicitly for SSH"
    )
    parser.add_argument(
        "--config", type=Path, help="Optional scene/render JSON override"
    )
    parser.add_argument(
        "--python",
        default=os.environ.get("ISAAC_PYTHON", sys.executable),
        help="Local Isaac Python; defaults to ISAAC_PYTHON or current Python",
    )
    parser.add_argument(
        "--bundle-python",
        help="OpenUSD/pxr Python for local CPU bundle export; defaults to --python",
    )
    parser.add_argument("--image", default="simready-isaac:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument(
        "--ssh-host",
        help="Remote SSH alias; inputs and checkout must already exist there",
    )
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Offline read-only plan; no export, environment launch, GPU or cloud",
    )
    args = parser.parse_args(argv)
    try:
        result, code = run_pipeline(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = (
            {
                "status": "INVALID",
                "error": str(error),
                "mode": "reviewed_scene_reproduction",
                "cloud_submission": False,
            },
            3,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
