"""Resumable new-video construction with explicit reconstruction and scale evidence."""

import argparse
import fcntl
import json
import math
import os
import shutil
import sys
import time
from functools import partial
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.execution.isaac_launcher import default_repository, launch_worker
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.workflows.physical_estimation import (
    estimation_plan,
    produce_estimation,
    verify_compilation,
    verify_estimation,
)
from simready.workflows.stage_ledger import VideoStages as VideoStages
from simready.workflows.stage_ledger import failure_status as _failure_status
from simready.workflows.video_contracts import (
    verify_delivery as verify_delivery,
)
from simready.workflows.video_contracts import (
    verify_delivery_result,
    verify_observations_result,
    verify_reconstruction_result,
    verify_worker_inputs,
)


class StageCompletedError(Exception):
    """Requested intermediate stage completed; no scene-delivery claim."""


def implementation_identity():
    package = Path(__file__).resolve().parents[1]
    return object_digest(
        {
            str(path.relative_to(package)): digest(path)
            for path in sorted(package.rglob("*.py"))
        }
    )


def validate_render(value):
    render = {"width": 1280, "height": 720, "frames": 120, "fps": 24, "subframes": 8}
    if not isinstance(value, dict) or set(value) - set(render):
        raise ValueError("Unknown render options")
    render.update(value)
    for key, limit in (
        ("width", 7680),
        ("height", 4320),
        ("frames", 10000),
        ("fps", 120),
        ("subframes", 256),
    ):
        if type(render[key]) is not int or not 1 <= render[key] <= limit:
            raise ValueError(f"Invalid render {key}; allowed range 1..{limit}")
    if render["width"] % 2 or render["height"] % 2:
        raise ValueError("Video dimensions must be even")
    return render


def make_plan(args):
    video = args.video.expanduser().resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError("A nonempty source video is required")
    output = args.out.expanduser().absolute()
    if output.is_symlink():
        raise ValueError("Run directory must not be a symlink")
    output = output.resolve()
    repo = Path(args.repo or default_repository()).expanduser().resolve()
    if output == repo or repo.is_relative_to(output) or video.is_relative_to(output):
        raise ValueError("Output cannot contain source video or repository")
    if args.backend == "docker" and not (repo / "src/simready/cli.py").is_file():
        raise ValueError("Docker requires a source checkout via --repo")
    if any(c in str(p) for p in (repo, output) for c in (",", "\n", "\r")):
        raise ValueError("Worker paths cannot contain commas or newlines")
    spec = read_json(args.scene_spec) if args.scene_spec else {"schema_version": 1}
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise ValueError("Scene specification requires schema_version=1")
    spec["render"] = validate_render(spec.get("render", {}))
    # Scene asset files must be explicitly packaged by a supported spec field.
    if args.scene_spec and output in args.scene_spec.expanduser().resolve().parents:
        raise ValueError("Scene specification must live outside the run directory")
    worker_timeout = getattr(args, "worker_timeout", 7200.0)
    for name, value in (
        ("observation_fps", args.observation_fps),
        ("timeout", args.reconstruction_timeout),
        ("worker_timeout", worker_timeout),
    ):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    manifest = args.reconstruction_manifest
    reconstruction = None
    if manifest:
        manifest = manifest.expanduser().resolve()
        if manifest.is_relative_to(output):
            raise ValueError("Reuse a reconstruction manifest from a separate run")
        reconstruction = {"path": str(manifest), "sha256": digest(manifest)}
    plan = {
        "schema_version": 1,
        "mode": "video_to_scene",
        "implementation_sha256": implementation_identity(),
        "video": str(video),
        "source_video_sha256": digest(video),
        "out": str(output),
        "repo": str(repo),
        "backend": args.backend,
        "python": args.python,
        "image": args.image,
        "gpu": args.gpu,
        "spec": spec,
        "reconstruction_manifest": reconstruction,
        "reconstruction_timeout_s": args.reconstruction_timeout,
        "worker_timeout_s": worker_timeout,
        "observation_fps": args.observation_fps,
        "prepare_only": args.prepare_only,
        "physical_estimation": estimation_plan(
            getattr(args, "physical_candidate", None),
            getattr(args, "physical_estimation_request", None),
            output,
            getattr(args, "physical_timeout", 300),
        ),
        "stages": ["observations", "reconstruction", "worker_inputs", "scene_delivery"],
        "delivery_scope": "Source-bound scene and survey; no automatic robot task or whole-scene certification",
    }
    if plan["physical_estimation"] is not None:
        plan["stages"].insert(2, "physical_estimation")
    configuration = getattr(args, "pipeline_configuration", None)
    if configuration is not None:
        from simready.ingestion.reconstruction_providers import plan_provider
        from simready.workflows.pipeline_spec import (
            stage_recipes,
            worker_source_identity,
        )

        provider = plan_provider(configuration["reconstruction"], video)
        plan["pipeline"] = {**configuration, "reconstruction": provider}
        plan["pipeline"]["worker_source"] = worker_source_identity(repo)
        for ref in provider["dependencies"]:
            if Path(ref["path"]).is_relative_to(output):
                raise ValueError(
                    "Reconstruction provider input must live outside the run"
                )
        if configuration.get("decomposition"):
            for ref in configuration["decomposition"]["dependencies"]:
                if Path(ref["path"]).is_relative_to(output):
                    raise ValueError("Decomposition inputs must live outside the run")
            plan["stages"].insert(2, "decomposition")
        plan["stage_contracts"] = stage_recipes(plan)
        if getattr(args, "until", None) and args.until not in plan["stages"]:
            raise ValueError("Requested stage is not enabled in this pipeline")
    plan["identity"] = object_digest(plan)
    return plan


def fresh_attempt(root, name):
    root = root.resolve()
    parent = root / name
    if parent.is_symlink() or not parent.resolve().is_relative_to(root):
        raise ValueError("Attempt parent symlink escapes the run directory")
    parent.mkdir(exist_ok=True)
    for index in range(1, 10001):
        candidate = parent / f"attempt-{index:04d}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise RuntimeError("Too many attempts; choose another run directory")


def execute_video(args, plan):
    from simready.ingestion.reconstruction_stage import prepare_reconstruction
    from simready.ingestion.video_frames import extract_frames

    root, video = Path(plan["out"]), Path(plan["video"])
    upgrade = getattr(args, "upgrade", False)
    normalized = "pipeline" in plan
    if normalized and (args.resume or upgrade):
        provider_config = plan["pipeline"]["reconstruction"]["config"]
        if provider_config.get("environment_identity", {}).get("secret_names"):
            raise ValueError(
                "Secret-dependent reconstruction requires a fresh --out; resume/upgrade reuse is disabled"
            )
    if upgrade and not normalized:
        raise ValueError("Upgrade requires a versioned --pipeline configuration")
    if root.exists() and not (args.resume or upgrade):
        raise FileExistsError(
            "Run exists; use --resume with identical inputs or choose a new --out"
        )
    if (args.resume or upgrade) and not (root / "run-plan.json").is_file():
        raise ValueError("Resume requires an existing run-plan.json")
    if (
        not normalized
        and not args.reconstruction_manifest
        and not args.submit_reconstruction
        and not any(
            (root / "reconstruction" / name).is_file()
            for name in ("receipt.json", "reconstruction.json")
        )
    ):
        raise ValueError(
            "New video requires --submit-reconstruction to upload and start Aholo, "
            "or --reconstruction-manifest to reuse a source-bound local result"
        )
    if (args.backend == "docker" or not args.prepare_only) and not args.accept_eula:
        raise ValueError("Isaac execution requires --accept-eula")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.resume:
            if read_json(root / "run-plan.json") != plan:
                raise ValueError(
                    "Resume input/config changed; use --upgrade for a versioned pipeline or a new output"
                )
        elif upgrade:
            previous = read_json(root / "run-plan.json")
            if "pipeline" not in previous:
                raise ValueError(
                    "Legacy runs retain strict resume; use a fresh directory for the normalized pipeline"
                )
            write_json(root / "plans" / (previous["identity"] + ".json"), previous)
            write_json(root / "plans" / (plan["identity"] + ".json"), plan)
            write_json(root / "run-plan.json", plan)
        else:
            write_json(root / "run-plan.json", plan)
            if normalized:
                write_json(root / "plans" / (plan["identity"] + ".json"), plan)
        stages = VideoStages(root, plan["identity"])
        started = time.monotonic()
        state = {
            "schema_version": 1,
            "mode": "video_to_scene",
            "status": "RUNNING",
            "source_video_sha256": plan["source_video_sha256"],
            "task_validated": False,
            "metric_scale_independently_validated": False,
            "scale_provenance_is_user_supplied": True,
        }

        def run_stage(name, action, validate=None):
            def verify_stage(result, artifacts):
                if implementation_identity() != plan["implementation_sha256"]:
                    raise ValueError("Runtime source changed before stage publication")
                if normalized and name == "scene_delivery":
                    from simready.workflows.pipeline_spec import verify_worker_identity

                    verify_worker_identity(plan["pipeline"], plan["repo"])
                if validate is not None:
                    validate(result, artifacts)

            options = (
                {"stage_identity": plan["stage_contracts"]["identities"][name]}
                if normalized
                else {}
            )
            result = stages.run(name, action, verify_stage, **options)
            if getattr(args, "until", None) == name and name != plan["stages"][-1]:
                state.update(
                    status="STAGE_READY",
                    stage=name,
                    stage_result=result,
                    delivery_level="INTERMEDIATE",
                    completed_pipeline=False,
                )
                raise StageCompletedError()
            return result

        def observation():
            target = fresh_attempt(root, "observations")
            manifest = extract_frames(
                video, target, fps=args.observation_fps, min_laplacian=0, dedup_bits=0
            )
            return {
                "manifest": str(target / "manifest.json"),
                "frames": len(manifest["frames"]),
            }, [
                target / "manifest.json",
                *[target / row["path"] for row in manifest["frames"]],
            ]

        def reconstruction():
            if normalized:
                from simready.core.evidence import resolve_ref
                from simready.ingestion.reconstruction_providers import prepare_provider

                provider = plan["pipeline"]["reconstruction"]
                for ref in provider["dependencies"]:
                    resolve_ref(ref, Path.cwd())
                if provider["config"]["provider"] in {"aholo", "command"}:
                    target = (
                        root
                        / "reconstruction"
                        / plan["stage_contracts"]["identities"]["reconstruction"]
                    )
                else:
                    target = fresh_attempt(root, "reconstruction")
                result = prepare_provider(
                    provider["config"],
                    video,
                    target,
                    args.reconstruction_timeout,
                    authorize_cloud=args.submit_reconstruction,
                )
                paths = [
                    p for p in target.rglob("*") if p.is_file() and p.stat().st_size
                ]
                return result, paths
            result = prepare_reconstruction(
                video,
                root / "reconstruction",
                artifact_manifest=args.reconstruction_manifest,
                submit=args.submit_reconstruction,
                timeout=args.reconstruction_timeout,
            )
            if result["source_video_sha256"] != plan["source_video_sha256"]:
                raise ValueError("Reconstruction belongs to another source video")
            return result, [Path(result["manifest_path"]), Path(result["ply_path"])]

        def validate_reconstruction(result, artifacts):
            # The legacy result contract permits additive provider metadata.
            legacy = {
                key: value
                for key, value in result.items()
                if key not in {"bundle_path", "execution_mode"}
            }
            verify_reconstruction_result(
                legacy,
                artifacts,
                root=root,
                source_video_sha256=plan["source_video_sha256"],
            )
            if normalized:
                from simready.ingestion.reconstruction_providers import (
                    verify_provider_bundle,
                )
                from simready.workflows.stage_ledger import checked_artifact

                recorded = {checked_artifact(root, row) for row in artifacts}
                bundle = Path(result["bundle_path"])
                if bundle not in recorded:
                    raise ValueError("Provider bundle missing from stage artifacts")
                verify_provider_bundle(
                    bundle, source_video_sha256=plan["source_video_sha256"]
                )

        def inputs(reconstruction_result, compiled_spec, physical_directory=None):
            target = fresh_attempt(root, "worker_inputs")
            target.mkdir()
            shutil.copy2(reconstruction_result["ply_path"], target / "splats.ply")
            if digest(target / "splats.ply") != reconstruction_result["ply_sha256"]:
                raise ValueError("Reconstruction changed while copying worker inputs")
            write_json(target / "scene-spec.json", compiled_spec)
            physical_binding = {}
            if physical_directory is not None:
                shutil.copytree(physical_directory, target / "physical")
                physical_binding["physical_compilation"] = verify_compilation(
                    target / "physical",
                    compiled_spec,
                    plan["source_video_sha256"],
                    reconstruction_result["ply_sha256"],
                )
            write_json(
                target / "input.json",
                {
                    "source_video_sha256": plan["source_video_sha256"],
                    "ply_sha256": reconstruction_result["ply_sha256"],
                    "spec_sha256": digest(target / "scene-spec.json"),
                    "prepare_only": args.prepare_only,
                    "worker_timeout_s": plan["worker_timeout_s"],
                    **physical_binding,
                },
            )
            return {"path": str(target)}, [p for p in target.rglob("*") if p.is_file()]

        def delivery(input_result):
            if normalized:
                from simready.workflows.pipeline_spec import verify_worker_identity

                verify_worker_identity(plan["pipeline"], plan["repo"])
            target = fresh_attempt(root, "execution")
            assets = Path(input_result["path"])
            input_hashes = {
                name: digest(assets / name)
                for name in ("input.json", "scene-spec.json", "splats.ply")
            }
            worker_assets, worker_out = (
                ("/assets", "/output")
                if args.backend == "docker"
                else (str(assets), str(target))
            )
            code = launch_worker(
                [
                    "-m",
                    "simready.workflows.video_execution",
                    "--inputs",
                    worker_assets,
                    "--out",
                    worker_out,
                ],
                repo=plan["repo"],
                assets=assets,
                output=target,
                backend=args.backend,
                python=args.python,
                image=args.image,
                gpu=args.gpu,
                timeout_s=plan["worker_timeout_s"],
                resource_kind="cpu" if args.prepare_only else "rtx",
            )
            status_path = target / "pipeline-status.json"
            status = read_json(status_path) if status_path.is_file() else {}
            if status.get("status") == "TIMEOUT":
                raise ProcessTimeoutError(
                    plan["worker_timeout_s"], status.get("wall_s", 0)
                )
            if status.get("status") == "CANCELLED":
                raise ProcessCancelledError(status.get("wall_s", 0))
            if status.get("status") == "INTERRUPTED":
                raise KeyboardInterrupt("Scene worker was interrupted")
            if code or status.get("status") != "COMPLETE":
                raise RuntimeError(
                    f"Scene worker failed at {status.get('stage', 'launch')}; inspect {target}"
                )
            evidence = read_json(target / "delivery.json")
            if any(
                digest(assets / name) != expected
                for name, expected in input_hashes.items()
            ):
                raise ValueError("Worker changed its frozen inputs during execution")
            return {"execution": str(target), **evidence}, [
                status_path,
                target / "delivery.json",
                *[target / item["path"] for item in evidence["artifacts"]],
            ]

        try:
            for name, action in (
                ("observations", observation),
                ("reconstruction", reconstruction),
            ):
                state["stage"] = name
                write_json(root / "summary.json", state)
                result = run_stage(
                    name,
                    action,
                    validate_reconstruction
                    if name == "reconstruction"
                    else partial(
                        verify_observations_result,
                        root=root,
                        source_video_sha256=plan["source_video_sha256"],
                        fps=plan["observation_fps"],
                    ),
                )
                if name == "reconstruction":
                    recon = result
            compiled_spec, physical_directory = plan["spec"], None
            if normalized and plan["pipeline"].get("decomposition"):
                from simready.workflows.semantic_stage import (
                    produce_decomposition,
                    verify_decomposition,
                )

                state["stage"] = "decomposition"
                write_json(root / "summary.json", state)
                semantic_plan = plan["pipeline"]["decomposition"]
                semantic = run_stage(
                    "decomposition",
                    lambda: produce_decomposition(
                        semantic_plan,
                        recon,
                        plan["spec"],
                        fresh_attempt(root, "decomposition"),
                    ),
                    partial(
                        verify_decomposition,
                        root=root,
                        plan=semantic_plan,
                        reconstruction=recon,
                        spec=plan["spec"],
                    ),
                )
                compiled_spec = read_json(semantic["scene_spec"])
                semantic_binding = read_json(
                    Path(semantic["directory"]) / "binding.json"
                )
                state["decomposition"] = {
                    "binding": str(Path(semantic["directory"]) / "binding.json"),
                    "semantic_accuracy": "NOT_EVALUATED",
                    "status": semantic_binding["status"],
                    "release_authorized": False,
                }
            if plan.get("physical_estimation") is not None:
                state["stage"] = "physical_estimation"
                write_json(root / "summary.json", state)
                physical = run_stage(
                    "physical_estimation",
                    lambda: produce_estimation(
                        plan["physical_estimation"],
                        compiled_spec,
                        plan["source_video_sha256"],
                        recon["ply_sha256"],
                        fresh_attempt(root, "physical_estimation"),
                    ),
                    partial(
                        verify_estimation,
                        root=root,
                        spec=compiled_spec,
                        video_sha256=plan["source_video_sha256"],
                        ply_sha256=recon["ply_sha256"],
                    ),
                )
                physical_directory = Path(physical["directory"])
                compiled_spec = read_json(physical_directory / "compiled-spec.json")
            state["stage"] = "worker_inputs"
            write_json(root / "summary.json", state)
            prepared = run_stage(
                "worker_inputs",
                lambda: inputs(recon, compiled_spec, physical_directory),
                partial(
                    verify_worker_inputs,
                    root=root,
                    source_video_sha256=plan["source_video_sha256"],
                    ply_sha256=recon["ply_sha256"],
                    scene_spec=compiled_spec,
                    prepare_only=plan["prepare_only"],
                    worker_timeout_s=plan["worker_timeout_s"],
                ),
            )
            state["stage"] = "scene_delivery"
            write_json(root / "summary.json", state)
            result = run_stage(
                "scene_delivery",
                lambda: delivery(prepared),
                partial(
                    verify_delivery_result,
                    root=root,
                    inputs=Path(prepared["path"]),
                    render_config=plan["spec"]["render"],
                ),
            )
            if implementation_identity() != plan["implementation_sha256"]:
                raise ValueError(
                    "Runtime source changed during execution; results are not accepted"
                )
            state.update(
                status="COMPLETE",
                stage="summary",
                delivery_level=result["delivery_level"],
                scene=str(Path(result["execution"]) / result["scene"]),
                video=str(Path(result["execution"]) / result["video"])
                if result.get("video")
                else None,
                reconstruction_new_submission=recon["new_submission"],
                metric_scale_verified=result["metric_scale_verified"],
                physical_asset_validation=result["physical_asset_validation"],
                limitations=result["limitations"],
                physical_estimation=result.get("physical_estimation", "NOT_RUN"),
            )
            return state, 0
        except StageCompletedError:
            if implementation_identity() != plan["implementation_sha256"]:
                state.update(
                    status="FAILED", error="Runtime source changed during execution"
                )
                return state, 2
            return state, 0
        except Exception as error:
            state.update(
                status=_failure_status(error),
                error=str(error),
                resume="Repeat the identical command with --resume; completed stages are hash-checked",
            )
            return state, 2
        except BaseException as error:
            state.update(status="INTERRUPTED", error=type(error).__name__)
            raise
        finally:
            state["invocation_wall_s"] = time.monotonic() - started
            state["stages"] = stages.history
            write_json(root / "summary.json", state)
            write_json(root / "history" / f"{time.time_ns()}.json", state)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="simready run --video", description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--scene-spec",
        type=Path,
        help="Coordinate/scale, explicit objects and survey camera JSON",
    )
    physical = parser.add_mutually_exclusive_group()
    physical.add_argument(
        "--physical-candidate",
        type=Path,
        help="Import an evidence-bound object mass candidate",
    )
    physical.add_argument(
        "--physical-estimation-request",
        type=Path,
        help="Run a declared NeRF2Physics estimate before compiling scene physics",
    )
    parser.add_argument(
        "--physical-timeout",
        type=float,
        default=300,
        help="CPU physical-estimation stage execution limit in seconds",
    )
    parser.add_argument(
        "--reconstruction-manifest",
        type=Path,
        help="Reuse a source-bound local reconstruction without network",
    )
    parser.add_argument(
        "--submit-reconstruction",
        action="store_true",
        help="Explicitly upload this video and submit a new Aholo job if no receipt exists",
    )
    parser.add_argument("--reconstruction-timeout", type=float, default=7200)
    parser.add_argument(
        "--worker-timeout",
        type=float,
        default=7200.0,
        help="Maximum seconds for the local or Docker scene worker; recorded in the run plan",
    )
    parser.add_argument("--observation-fps", type=float, default=2)
    parser.add_argument("--backend", choices=("docker", "local"), default="docker")
    parser.add_argument(
        "--python", default=os.environ.get("ISAAC_PYTHON", sys.executable)
    )
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--image", default="simready-isaac:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Export and inspect USD without rendering or robot execution",
    )
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only plan; no files, process, cloud or GPU",
    )
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args)
        if args.dry_run:
            result, code = (
                {
                    "status": "PLANNED",
                    **plan,
                    "new_cloud_submission_authorized": args.submit_reconstruction,
                },
                0,
            )
        else:
            result, code = execute_video(args, plan)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = {"status": "INVALID", "error": str(error)}, 3
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
