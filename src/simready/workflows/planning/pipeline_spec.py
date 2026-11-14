"""Versioned pipeline configuration: declarations only, no worker or GPU imports."""

from __future__ import annotations

import argparse
import copy
import importlib.metadata
import sys
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json
from simready.core.evidence import reference, resolve_ref
from simready.core.validation import number
from simready.workflows.contracts.validation import fields, file_path


def runtime_fingerprint(python):
    """Host identity is hashed; remote/image environment remains a declaration."""
    path = Path(python).expanduser().absolute()
    if not path.is_file():
        raise ValueError("Execution python must identify an existing executable")
    packages = sorted(
        (d.metadata.get("Name", ""), d.version)
        for d in importlib.metadata.distributions()
    )
    return {
        "host_python": {"path": str(path), "sha256": digest(path)},
        "host_packages_sha256": object_digest(packages),
    }


def worker_source_identity(repo):
    package = Path(repo) / "src/simready"
    if not (package / "cli.py").is_file():
        package = Path(__file__).resolve().parents[2]
    return {
        "root": str(package.resolve()),
        "files": {
            str(p.relative_to(package)): digest(p)
            for p in sorted(package.rglob("*.py"))
        },
    }


def verify_worker_identity(pipeline, repo):
    if worker_source_identity(repo) != pipeline["worker_source"]:
        raise ValueError("Actual worker checkout changed")
    for key in ("worker_python", "worker_environment_manifest"):
        if pipeline.get(key):
            resolve_ref(pipeline[key], Path.cwd())


def load_pipeline(path: Path, output: Path, **flags):
    """Translate a strict configuration to the existing canonical video workflow."""
    path = path.expanduser().resolve()
    raw = read_json(path)
    fields(raw, {"schema_version", "input", "stages", "execution"}, "pipeline")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("Expected pipeline schema_version=1")
    source = fields(raw.get("input"), {"video"}, "input")
    video = file_path(source.get("video"), path.parent)
    stages = fields(
        raw.get("stages"),
        {
            "observations",
            "reconstruction",
            "decomposition",
            "assets",
            "physics",
            "delivery",
        },
        "stages",
    )
    observations = fields(
        stages.get("observations", {}), {"provider", "fps"}, "observations"
    )
    if observations.get("provider", "video_frames") != "video_frames":
        raise ValueError("Unregistered observation provider")
    fps = observations.get("fps", 2)
    if number(fps, "fps") <= 0:
        raise ValueError("fps must be positive")
    assets = fields(stages.get("assets", {}), {"provider", "scene_spec"}, "assets")
    if assets.get("provider", "scene_spec") != "scene_spec":
        raise ValueError(
            "Asset completion provider is not implemented; use an explicit scene_spec"
        )
    spec = (
        file_path(assets["scene_spec"], path.parent) if "scene_spec" in assets else None
    )
    physics = fields(
        stages.get("physics", {}),
        {"provider", "request", "candidate", "timeout_s"},
        "physics",
    )
    request = candidate = None
    if physics:
        kind = physics.get("provider")
        if (
            kind == "nerf2physics"
            and "request" in physics
            and "candidate" not in physics
        ):
            request = file_path(physics["request"], path.parent)
        elif (
            kind == "candidate" and "candidate" in physics and "request" not in physics
        ):
            candidate = file_path(physics["candidate"], path.parent)
        else:
            raise ValueError(
                "Physics provider requires exactly its request or candidate input"
            )
    delivery = fields(stages.get("delivery", {}), {"provider", "mode"}, "delivery")
    if delivery.get("provider", "usd_scene") != "usd_scene" or delivery.get(
        "mode", "prepare"
    ) not in {"prepare", "survey"}:
        raise ValueError("Unsupported delivery provider or mode")
    execution = fields(
        raw.get("execution", {}),
        {
            "backend",
            "python",
            "image",
            "gpu",
            "repo",
            "worker_timeout_s",
            "reconstruction_timeout_s",
            "runtime_identity",
            "environment_manifest",
        },
        "execution",
    )
    backend = execution.get("backend", "local")
    if backend not in {"local", "docker"}:
        raise ValueError("Pipeline backend must be local or docker")
    python = execution.get("python", sys.executable)
    if not isinstance(python, str):
        raise ValueError("python must be a path")
    if not Path(python).is_absolute():
        python = str((path.parent / python).absolute())
    runtime_name = execution.get("runtime_identity", "local-host")
    if not isinstance(runtime_name, str) or not runtime_name.strip():
        raise ValueError("runtime_identity must be nonempty")
    reconstruction = copy.deepcopy(stages.get("reconstruction"))
    if not isinstance(reconstruction, dict):
        raise ValueError("Pipeline requires a reconstruction provider")
    reconstruction.setdefault("base_dir", str(path.parent))
    if not Path(reconstruction["base_dir"]).is_absolute():
        reconstruction["base_dir"] = str(
            (path.parent / reconstruction["base_dir"]).resolve()
        )
    decomposition = None
    if "decomposition" in stages:
        from simready.workflows.stages.semantic_stage import plan_decomposition

        decomposition = plan_decomposition(stages["decomposition"], path.parent, video)
    namespace = argparse.Namespace(
        video=video,
        out=output,
        scene_spec=spec,
        physical_candidate=candidate,
        physical_estimation_request=request,
        physical_timeout=physics.get("timeout_s", 300),
        reconstruction_manifest=None,
        submit_reconstruction=flags.get("submit_reconstruction", False),
        reconstruction_timeout=execution.get("reconstruction_timeout_s", 7200),
        worker_timeout=execution.get("worker_timeout_s", 7200),
        observation_fps=fps,
        backend=backend,
        python=python,
        repo=Path(execution["repo"]).expanduser() if execution.get("repo") else None,
        image=execution.get("image", "simready-isaac-core:6.1.0"),
        gpu=execution.get("gpu", "0"),
        prepare_only=delivery.get("mode", "prepare") == "prepare",
        accept_eula=flags.get("accept_eula", False),
        resume=flags.get("resume", False),
        upgrade=flags.get("upgrade", False),
        until=flags.get("until"),
        dry_run=flags.get("dry_run", False),
        pipeline_configuration={
            "schema_version": 1,
            "reconstruction": reconstruction,
            "decomposition": decomposition,
            "runtime_identity": runtime_name,
            "host": runtime_fingerprint(sys.executable),
        },
    )
    if namespace.repo and not namespace.repo.is_absolute():
        namespace.repo = (path.parent / namespace.repo).resolve()
    if namespace.backend == "local":
        namespace.pipeline_configuration["worker_python"] = runtime_fingerprint(python)[
            "host_python"
        ]
    if "environment_manifest" in execution:
        manifest = resolve_ref(execution["environment_manifest"], path.parent)
        if manifest.is_relative_to(output.resolve()):
            raise ValueError("Worker environment manifest must live outside the run")
        namespace.pipeline_configuration["worker_environment_manifest"] = reference(
            manifest
        )
    elif (
        backend == "docker"
        or Path(python).absolute() != Path(sys.executable).absolute()
    ):
        raise ValueError(
            "External worker environment requires a pinned environment_manifest file reference"
        )
    if path.is_relative_to(output.resolve()):
        raise ValueError(
            "Pipeline configuration must live outside its output directory"
        )
    return namespace


def implementation_files(names):
    package = Path(__file__).resolve().parents[2]
    paths = set()
    for name in names:
        target = package / name
        if target.is_dir():
            paths.update(target.rglob("*.py"))
        elif target.is_file():
            paths.add(target)
        else:
            raise ValueError(f"Missing stage implementation: {name}")
    return {str(p.relative_to(package)): digest(p) for p in sorted(paths)}


def stage_recipes(plan):
    """Conservative implementation closures with explicit data dependencies."""
    from simready.workflows.contracts.stage_ledger import stage_identity

    pipeline = plan["pipeline"]
    common = [
        "core",
        "workflows/contracts",
        "workflows/planning/pipeline_spec.py",
        "workflows/pipelines/video_pipeline.py",
    ]
    implementations = {
        "observations": ["ingestion/video_frames.py", "execution"],
        "reconstruction": [
            "ingestion/reconstruction/providers.py",
            "assets/appearance",
            "geometry",
            "ingestion/reconstruction/stage.py",
            "ingestion/reconstruction/aholo_client.py",
            "ingestion/reconstruction/aholo_reconstruction.py",
            "execution",
        ],
        "decomposition": [
            "workflows/stages/semantic_stage.py",
            "assets/association",
            "assets/physics",
            "assets/appearance",
            "geometry",
            "execution",
        ],
        "physical_estimation": [
            "workflows/stages/physical_estimation.py",
            "ingestion/nerf2physics.py",
            "assets/physics",
            "geometry",
            "execution",
        ],
        "worker_inputs": [
            "workflows/planning/video_contracts.py",
            "workflows/stages/physical_estimation.py",
            "assets/physics/physical_candidate.py",
            "assets/physics/physical_spec.py",
        ],
        "scene_delivery": [
            "workflows/stages/video_execution.py",
            "workflows/planning/video_contracts.py",
            "workflows/stages/physical_estimation.py",
            "assets",
            "simulation",
            "geometry",
            "evaluation",
            "execution",
        ],
    }
    configs = {
        "observations": {"fps": plan["observation_fps"]},
        "reconstruction": pipeline["reconstruction"],
        "decomposition": pipeline.get("decomposition"),
        "physical_estimation": plan["physical_estimation"],
        "worker_inputs": {
            "spec": plan["spec"],
            "prepare_only": plan["prepare_only"],
            "timeout_s": plan["worker_timeout_s"],
        },
        "scene_delivery": {
            k: plan[k]
            for k in (
                "backend",
                "python",
                "image",
                "gpu",
                "worker_timeout_s",
                "prepare_only",
                "repo",
            )
        },
    }
    dependencies = {
        "observations": [],
        "reconstruction": [],
        "decomposition": ["reconstruction"],
        "physical_estimation": [
            "decomposition" if pipeline.get("decomposition") else "reconstruction"
        ],
        "worker_inputs": [
            "physical_estimation"
            if plan["physical_estimation"]
            else (
                "decomposition" if pipeline.get("decomposition") else "reconstruction"
            )
        ],
        "scene_delivery": ["worker_inputs"],
    }
    recipes, identities = {}, {}
    for name in plan["stages"]:
        config = copy.deepcopy(configs[name])
        if name in {"decomposition", "physical_estimation"}:
            config = {"options": config, "base_spec": plan["spec"]}
        recipe = {
            "provider": name,
            "version": "1.0",
            "config": config,
            "inputs": {"source_video": plan["source_video_sha256"]},
            "runtime": {
                "host": pipeline["host"],
                "declaration": pipeline["runtime_identity"],
            },
            "implementation": implementation_files(common + implementations[name]),
            "dependencies": {key: identities[key] for key in dependencies[name]},
        }
        if name == "scene_delivery":
            recipe["runtime"]["worker_python"] = pipeline.get("worker_python")
            recipe["runtime"]["worker_source"] = pipeline["worker_source"]
            recipe["runtime"]["environment_manifest"] = pipeline.get(
                "worker_environment_manifest"
            )
        recipes[name] = recipe
        identities[name] = stage_identity(recipe)
    return {"recipes": recipes, "identities": identities}
