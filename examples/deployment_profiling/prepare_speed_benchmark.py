"""Freeze an existing verified deployment for runtime-only RSI experiments."""

import argparse
import shutil
import subprocess
from pathlib import Path

from examples.deployment_profiling.runtime_variants import render_variant
from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference


def prepare(
    deployment: Path,
    mission: Path,
    dependency_plan: Path,
    isaac_python: Path,
    output: Path,
) -> Path:
    """Pin source, pixels and physics; build one isolated viewport candidate."""
    deployment, mission, output = (
        deployment.resolve(),
        mission.resolve(),
        output.resolve(),
    )
    output.mkdir(parents=True, exist_ok=False)
    if not read_json(mission / "result.json")["passed"]:
        raise ValueError("Only a successful physics replay can be benchmarked")
    workspace = output / "workspace"
    workspace.mkdir()
    shutil.copytree(
        deployment / "simready_bridge",
        workspace / "simready_bridge",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("artifacts", "reports", "configs"):
        (workspace / name).symlink_to(deployment / name, target_is_directory=True)
    scripts = workspace / "scripts"
    scripts.mkdir()
    original = deployment / "scripts/render_panel_stop_mission.py"
    baseline = scripts / "render_baseline.py"
    shutil.copy2(original, baseline)
    source = original.read_text()
    candidate = scripts / "render_viewport_after_startup.py"
    candidate.write_text(render_variant(source, viewport_after=16))
    compile(candidate.read_text(), str(candidate), "exec")
    repository = Path(__file__).resolve().parents[2]
    verifier = output / "verifier"
    shutil.copytree(
        repository / "src/simready",
        verifier / "simready",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    files = set(mission.glob("*.json")) | {
        mission / "measured_replay.npz",
        mission / "sensors.jsonl",
        mission / "scene.usda",
    }
    plan = read_json(dependency_plan)
    render_task = next(task for task in plan["tasks"] if task["stage"] == "render")
    files.update(Path(path).resolve() for path in render_task["inputs"].values())
    files.update(
        Path(item["path"])
        for item in read_json(mission / "scene_preflight.json")["dependencies"]
    )
    files.update(workspace.rglob("*.py"))
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()

    def variant(identifier, worker, priority, description):
        return {
            "id": identifier,
            "priority": priority,
            "description": description,
            "worker_sha256": digest(worker),
            "argv": [
                str(isaac_python.absolute()),
                str(worker),
                "--run",
                str(mission),
                "--out",
                "{out}",
                "--width",
                "1920",
                "--height",
                "1080",
            ],
        }

    protocol = {
        "schema_version": 1,
        "benchmark_id": "SUBSTATION-RUNTIME-RSI-v1",
        "scope": "render_replay",
        "scene_id": "substation-estimated-cabinets",
        "family": "local-simready-delivery",
        "hardware_identity": gpu,
        "cwd": str(workspace),
        "verifier_root": str(verifier),
        "frozen_inputs": [reference(path) for path in sorted(files)],
        "verifier_files": [reference(path) for path in sorted(verifier.rglob("*.py"))],
        "workload": {
            "width": 1920,
            "height": 1080,
            "frames": 714,
            "fps": 25,
            "tile_grid": [6, 8],
            "ssim_frames": [
                0,
                25,
                125,
                240,
                270,
                300,
                375,
                400,
                500,
                550,
                575,
                650,
                713,
            ],
        },
        "quality_policy": {
            "absolute_max": {
                "mean_mae": 2.0,
                "p95_frame_mae": 4.0,
                "max_frame_mae": 8.0,
                "p99_tile_mae": 8.0,
                "max_tile_mae": 25.0,
                "mean_temporal_error_mae": 2.0,
            },
            "absolute_min_ssim": 0.97,
            "sharpness_ratio": [0.97, 1.03],
            "noise_multiplier": 1.5,
            "noise_floor": {
                "mean_mae": 0.1,
                "p95_frame_mae": 0.25,
                "max_frame_mae": 0.5,
                "p99_tile_mae": 0.5,
                "max_tile_mae": 1.0,
                "mean_temporal_error_mae": 0.1,
            },
            "ssim_noise_floor": 0.002,
        },
        "speed_policy": {
            "repetitions": 2,
            "minimum_gain_fraction": 0.08,
            "max_baseline_ratio": 1.1,
            "max_candidate_tail_ratio": 1.1,
        },
        "required_render_metadata": {
            "native_render": True,
            "new_physics": False,
            "usd_pose_checks_passed": True,
            "source_result_sha256": digest(mission / "result.json"),
            "source_replay_sha256": digest(mission / "measured_replay.npz"),
            "source_scene_sha256": digest(mission / "scene.usda"),
        },
        "baseline": variant("baseline", baseline, 0, "Frozen original renderer"),
        "candidates": [
            variant(
                "disable_background_viewport_after16",
                candidate,
                1,
                "Preserve 16 startup frames, then disable the preview viewport; keep capture camera and quality settings",
            )
        ],
        "provider": {"kind": "rules"},
        "max_rounds": 1,
        "trial_timeout_s": 1200,
        "verification_timeout_s": 600,
        "environment": {
            "OMNI_KIT_ACCEPT_EULA": "YES",
            "LD_PRELOAD": "/lib/aarch64-linux-gnu/libgomp.so.1",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
        },
        "reused_physics": reference(mission / "result.json"),
        "scope_limits": [
            "Existing reconstructed scene and measured physics replay reused exactly; no new cloud reconstruction or physics in this track.",
            "One scene; benchmark-scoped runtime recipe only, no cross-scene active skill.",
            "Shared machine; report control variability and do not claim isolated GPU speedup.",
        ],
    }
    path = output / "protocol.json"
    write_json(path, protocol)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--dependency-plan", type=Path, required=True)
    parser.add_argument("--isaac-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(
        prepare(
            args.deployment,
            args.mission,
            args.dependency_plan,
            args.isaac_python,
            args.out,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
