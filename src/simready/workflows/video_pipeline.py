"""Resumable new-video construction with explicit reconstruction and scale evidence."""

import argparse
import fcntl
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.execution.isaac_launcher import default_repository, launch_worker


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
    for name, value in (
        ("observation_fps", args.observation_fps),
        ("timeout", args.reconstruction_timeout),
    ):
        if not math.isfinite(value) or value <= 0:
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
        "observation_fps": args.observation_fps,
        "prepare_only": args.prepare_only,
        "stages": ["observations", "reconstruction", "worker_inputs", "scene_delivery"],
        "delivery_scope": "Source-bound scene and survey; no automatic robot task or whole-scene certification",
    }
    plan["identity"] = object_digest(plan)
    return plan


def checked_artifact(root, item):
    if not isinstance(item.get("path"), str) or Path(item["path"]).is_absolute():
        raise ValueError("Artifact paths must be relative to their run")
    if type(item.get("bytes")) is not int or item["bytes"] <= 0:
        raise ValueError("Stage artifacts must be nonempty files")
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Stage artifact escaped its run or is missing")
    if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
        raise ValueError(f"Stage artifact changed: {item['path']}")
    return path


def artifact_record(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Stage outputs must be files inside the run")
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


class VideoStages:
    """Each completed stage is reusable only while all recorded artifacts match."""

    def __init__(self, root, identity):
        self.root, self.identity, self.history = root, identity, []

    def run(self, name, action):
        receipt = self.root / "stages" / f"{name}.json"
        if receipt.exists():
            old = read_json(receipt)
            if old["identity"] != self.identity:
                raise ValueError("Stage identity differs from the current run plan")
            if not isinstance(old.get("artifacts"), list) or not old["artifacts"]:
                raise ValueError("Resume stage requires nonempty artifact evidence")
            for item in old["artifacts"]:
                checked_artifact(self.root, item)
            if old.get("name") != name or not isinstance(old.get("result"), dict):
                raise ValueError("Resume stage name or result is invalid")
            self.history.append({"name": name, "reused": True, "wall_s": 0.0})
            print(f"[{name}] REUSED (hash verified)", flush=True)
            return old["result"]
        print(f"[{name}] RUNNING", flush=True)
        started = time.monotonic()
        result, paths = action()
        record = {
            "identity": self.identity,
            "name": name,
            "wall_s": time.monotonic() - started,
            "result": result,
            "artifacts": [artifact_record(self.root, path) for path in paths],
        }
        if not record["artifacts"]:
            raise ValueError("A successful stage must provide verifiable artifacts")
        write_json(receipt, record)
        self.history.append({"name": name, "reused": False, "wall_s": record["wall_s"]})
        print(f"[{name}] COMPLETE ({record['wall_s']:.2f}s)", flush=True)
        return result


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


def verify_delivery(target, evidence, source, render_config):
    expected_level = (
        "SCENE_EXPORTED" if source["prepare_only"] else "SCENE_SURVEY_READY"
    )
    if (
        evidence.get("schema_version") != 1
        or evidence.get("status") != "COMPLETE"
        or evidence.get("delivery_level") != expected_level
        or evidence.get("scene") != "scene/scene.usda"
        or evidence.get("robot_task_validated") is not False
        or evidence.get("metric_scale_independently_validated") is not False
    ):
        raise ValueError(
            "Worker delivery contract is incomplete or has unsupported claims"
        )
    for field, source_field in (
        ("source_video_sha256", "source_video_sha256"),
        ("reconstruction_sha256", "ply_sha256"),
        ("scene_spec_sha256", "spec_sha256"),
    ):
        if evidence.get(field) != source[source_field]:
            raise ValueError("Scene delivery is bound to different input artifacts")
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Delivery requires nonempty artifact evidence")
    paths = set()
    for item in artifacts:
        if item["path"] in paths:
            raise ValueError("Repeated delivery artifact")
        paths.add(item["path"])
        checked_artifact(target, item)
    required = {"scene/scene.usda", "scene/preparation.json", "structure-audit.json"}
    if not source["prepare_only"]:
        required |= {
            "render/render.json",
            "render/camera.json",
            "render/scene_survey.mp4",
        }
    if not required.issubset(paths):
        raise ValueError(
            "Delivery is missing required scene, audit or render artifacts"
        )
    preparation = read_json(target / "scene/preparation.json")
    if preparation["source_sha256"] != source["ply_sha256"]:
        raise ValueError("Prepared scene is bound to another reconstruction")
    if read_json(target / "structure-audit.json").get("passed") is not True:
        raise ValueError("Generated scene has no passing structure audit")
    if source["prepare_only"]:
        if evidence.get("video") is not None:
            raise ValueError("Prepare-only run cannot claim a rendered video")
        return
    if evidence.get("video") != "render/scene_survey.mp4":
        raise ValueError("Unexpected or missing survey video")
    receipt = read_json(target / "render/render.json")
    if (
        receipt.get("status") != "COMPLETE"
        or receipt["video_sha256"] != digest(target / evidence["video"])
        or receipt["scene_sha256"] != digest(target / evidence["scene"])
        or receipt["spec_sha256"] != source["spec_sha256"]
        or any(
            receipt[key] != render_config[key]
            for key in ("width", "height", "fps", "frames")
        )
    ):
        raise ValueError("Survey receipt does not match requested inputs or video")


def execute_video(args, plan):
    from simready.ingestion.reconstruction_stage import prepare_reconstruction
    from simready.ingestion.video_frames import extract_frames

    root, video = Path(plan["out"]), Path(plan["video"])
    if root.exists() and not args.resume:
        raise FileExistsError(
            "Run exists; use --resume with identical inputs or choose a new --out"
        )
    if args.resume and not (root / "run-plan.json").is_file():
        raise ValueError("Resume requires an existing run-plan.json")
    if (
        not args.reconstruction_manifest
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
                    "Resume input/config changed; create a new run and reuse its reconstruction manifest"
                )
        else:
            write_json(root / "run-plan.json", plan)
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

        def inputs(reconstruction_result):
            target = fresh_attempt(root, "worker_inputs")
            target.mkdir()
            shutil.copy2(reconstruction_result["ply_path"], target / "splats.ply")
            if digest(target / "splats.ply") != reconstruction_result["ply_sha256"]:
                raise ValueError("Reconstruction changed while copying worker inputs")
            write_json(target / "scene-spec.json", plan["spec"])
            write_json(
                target / "input.json",
                {
                    "source_video_sha256": plan["source_video_sha256"],
                    "ply_sha256": reconstruction_result["ply_sha256"],
                    "spec_sha256": digest(target / "scene-spec.json"),
                    "prepare_only": args.prepare_only,
                },
            )
            return {"path": str(target)}, list(target.iterdir())

        def delivery(input_result):
            target = fresh_attempt(root, "execution")
            assets = Path(input_result["path"])
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
            )
            status_path = target / "pipeline-status.json"
            status = read_json(status_path) if status_path.is_file() else {}
            if code or status.get("status") != "COMPLETE":
                raise RuntimeError(
                    f"Scene worker failed at {status.get('stage', 'launch')}; inspect {target}"
                )
            evidence = read_json(target / "delivery.json")
            source = read_json(assets / "input.json")
            verify_delivery(target, evidence, source, plan["spec"]["render"])
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
                result = stages.run(name, action)
                if name == "reconstruction":
                    recon = result
            state["stage"] = "worker_inputs"
            prepared = stages.run("worker_inputs", lambda: inputs(recon))
            state["stage"] = "scene_delivery"
            result = stages.run("scene_delivery", lambda: delivery(prepared))
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
                limitations=result["limitations"],
            )
            return state, 0
        except Exception as error:
            state.update(
                status="FAILED",
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
