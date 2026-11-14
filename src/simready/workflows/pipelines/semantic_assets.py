"""Source-bound semantic masks → static object proxies → USD → independent audit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

from simready.assets.association.semantic_projection import build_semantic_spec
from simready.assets.compilation.scene_preparation import prepare_scene
from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import ensure_unchanged, reference, resolve_ref
from simready.core.validation import number
from simready.evaluation.inspection.physical_receipt import verify_physical_receipt
from simready.execution.processes.local import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.execution.resources.resource_pool import DEFAULT_LOCK_DIR
from simready.execution.workers.device_lease import device_lease


def _worker(
    command: list[str],
    log: Path,
    timeout_s: float,
    *,
    gpu=None,
    device_inventory=None,
    lock_dir=DEFAULT_LOCK_DIR,
    cancel_event: Event | None = None,
) -> int:
    if number(timeout_s, "timeout_s") <= 0:
        raise ValueError("timeout_s must be positive")
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError(0.0)
    if gpu is not None:
        started = time.monotonic()
        with device_lease(
            resource_kind="cuda",
            gpu=gpu,
            device_inventory=device_inventory,
            lock_dir=lock_dir,
        ) as lease:
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                raise ProcessTimeoutError(timeout_s, time.monotonic() - started)
            write_json(
                log.with_suffix(".lease.json"),
                {
                    "resource_kind": "cuda",
                    "gpu_uuid": lease.gpu["uuid"],
                    "lock_directory": str(lease.pool.directory),
                },
            )
            return run_process(
                command,
                cwd=None,
                stdout=log,
                stderr=log.with_suffix(".stderr.log"),
                timeout=remaining,
                env={**os.environ, **lease.environment},
                pass_fds=lease.pass_fds,
                cancel_event=cancel_event,
            ).returncode
    return run_process(
        command,
        cwd=None,
        stdout=log,
        stderr=log.with_suffix(".stderr.log"),
        timeout=timeout_s,
        cancel_event=cancel_event,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
    ).returncode


def run_semantic_assets(
    *,
    ply: str | Path,
    calibration: str | Path,
    output_dir: str | Path,
    scene_spec: str | Path | None = None,
    segmentation_manifest: str | Path | None = None,
    image: str | Path | None = None,
    segmentation_python: str | Path | None = None,
    segmentation_backend: str = "grounded",
    model_id: str | None = None,
    revision: str | None = None,
    device: str = "cpu",
    local_files_only: bool = False,
    min_area_px: int = 256,
    min_points: int = 16,
    min_score: float = 0.25,
    priors_file: str | Path | None = None,
    worker_timeout_s: float = 1200.0,
    timeout_s: float = 3600.0,
    cancel_event: Event | None = None,
    device_inventory: dict | None = None,
    lock_dir: str | Path = DEFAULT_LOCK_DIR,
) -> dict[str, Any]:
    """Run bounded candidate generation and record per-stage wall time.

    Exactly one of image or segmentation_manifest is required. The latter is
    explicit evidence reuse, never reported as new model inference. A separate
    interpreter reopens the authored USD and checks it against the external
    generated scene spec. This does not certify semantic or real physics accuracy.
    Output must be fresh; failures retain logs and their terminal status. Model
    inference, CPU projection, USD authoring and inspection run in owned process
    groups with the remaining total budget and inherited cancellation scope.
    """
    started = time.perf_counter()
    if number(timeout_s, "timeout_s") <= 0:
        raise ValueError("timeout_s must be positive")
    deadline = started + timeout_s
    if type(worker_timeout_s) not in (int, float) or not 1 <= worker_timeout_s <= 7200:
        raise ValueError("worker_timeout_s must be within 1..7200")
    if (image is None) == (segmentation_manifest is None):
        raise ValueError("Provide exactly one image or segmentation_manifest")
    if segmentation_backend not in {"segformer", "grounded"}:
        raise ValueError("segmentation_backend must be segformer or grounded")
    if not isinstance(device, str) or not re.fullmatch(r"cpu|cuda(?::\d+)?", device):
        raise ValueError("device must be cpu, cuda or cuda:N")
    if segmentation_backend == "grounded" and (
        model_id is not None or revision is not None
    ):
        raise ValueError(
            "Grounded backend uses its pinned detector/SAM pair; model-id/revision apply to SegFormer"
        )
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Workflow output already exists: {out}")
    ply, calibration = (
        Path(ply).resolve(strict=True),
        Path(calibration).resolve(strict=True),
    )
    if image is not None:
        image = Path(image).resolve(strict=True)
        if read_json(calibration).get("image_sha256") != digest(image):
            raise ValueError("Calibration image hash mismatch before inference")
    elif any(value is not None for value in (model_id, revision, segmentation_python)):
        raise ValueError(
            "Model options cannot be used with reused segmentation evidence"
        )
    out.mkdir(parents=True, exist_ok=False)
    (out / "logs").mkdir()
    steps = []
    package = Path(__file__).resolve().parents[2]
    implementation = {
        relative: digest(package / relative)
        for relative in (
            "workflows/pipelines/semantic_assets.py",
            "assets/association/semantic_projection.py",
            "geometry/depth_projection.py",
            "geometry/transforms_validation.py",
            "assets/appearance/gaussian_asset.py",
            "assets/physics/category_priors.py",
            "assets/compilation/scene_preparation.py",
            "assets/compilation/spec_validation.py",
            "assets/compilation/collision_geometry.py",
            "assets/compilation/usd_layers.py",
            "assets/physics/physical_spec.py",
            "assets/physics/physical_authoring.py",
            "evaluation/inspection/physical_assets.py",
            "evaluation/inspection/comparison.py",
            "evaluation/inspection/expectations.py",
            "evaluation/inspection/physical_checks.py",
            "evaluation/inspection/physical_receipt.py",
            "execution/processes/local.py",
            "execution/processes/cancellation.py",
            "execution/workers/device_lease.py",
            "execution/resources/gpu_capability.py",
            "execution/resources/resource_pool.py",
        )
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "RUNNING",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "release_authorized": False,
        "llm_api_calls": 0,
        "llm_tokens": 0,
        "token_scope": "This deterministic workflow only; development agent tokens are not counted.",
        "worker_timeout_s": worker_timeout_s,
        "timeout_s": timeout_s,
        "implementation_sha256": implementation,
    }
    stage = "segmentation"

    def remaining():
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelledError(time.perf_counter() - started)
        value = deadline - time.perf_counter()
        if value <= 0:
            raise ProcessTimeoutError(timeout_s, time.perf_counter() - started)
        return min(worker_timeout_s, value)

    def cpu_stage(kind, arguments):
        command = [
            sys.executable,
            "-m",
            "simready.workflows.pipelines.semantic_assets",
            "internal-worker",
            kind,
            *map(str, arguments),
        ]
        code = _worker(
            command,
            out / "logs" / f"{stage}.log",
            remaining(),
            **({"cancel_event": cancel_event} if cancel_event is not None else {}),
        )
        if code:
            raise RuntimeError(
                f"{stage} worker failed with exit {code}; see logs/{stage}.log"
            )

    try:
        tick = time.perf_counter()
        frozen = [
            reference(path)
            for path in (ply, calibration, scene_spec, priors_file, image)
            if path is not None
        ]
        if image is not None:
            command = [
                str(segmentation_python or sys.executable),
                "-m",
                "simready.ingestion.segmentation.grounded"
                if segmentation_backend == "grounded"
                else "simready.ingestion.segmentation.semantic",
                "--image",
                str(image),
                "--output-dir",
                str(out / "segmentation"),
                "--device",
                "cuda:0" if device.startswith("cuda") else device,
                "--min-area-px",
                str(min_area_px),
            ]
            for key, value in (("--model-id", model_id), ("--revision", revision)):
                if value is not None:
                    command.extend([key, value])
            if local_files_only:
                command.append("--local-files-only")
            options = {"cancel_event": cancel_event} if cancel_event is not None else {}
            if device.startswith("cuda"):
                options.update(
                    gpu=device.split(":")[-1] if ":" in device else "0",
                    device_inventory=device_inventory,
                    lock_dir=lock_dir,
                )
            if _worker(
                command, out / "logs" / "segmentation.log", remaining(), **options
            ):
                raise RuntimeError(
                    "Segmentation worker failed; see logs/segmentation.log"
                )
            segmentation_manifest = out / "segmentation" / "manifest.json"
        else:
            segmentation_manifest = Path(segmentation_manifest).resolve(strict=True)
        frozen.append(reference(segmentation_manifest))
        manifest = read_json(segmentation_manifest)
        for item in [
            manifest["source_image"],
            *[row["mask"] for row in manifest["instances"]],
        ]:
            path = resolve_ref(item, Path(segmentation_manifest).parent)
            frozen.append({"path": str(path), "sha256": item["sha256"]})
        camera = read_json(calibration)
        if "depth" in camera:
            path = resolve_ref(camera["depth"], calibration.parent)
            frozen.append({"path": str(path), "sha256": camera["depth"]["sha256"]})
        ensure_unchanged(frozen)
        steps.append(
            {
                "stage": stage,
                "wall_s": time.perf_counter() - tick,
                "mode": "new_inference" if image else "reuse_manifest",
            }
        )
        stage, tick = "projection_and_priors", time.perf_counter()
        arguments = [
            "--ply",
            ply,
            "--segmentation-manifest",
            segmentation_manifest,
            "--calibration",
            calibration,
            "--out",
            out / "projection",
            "--min-points",
            min_points,
            "--min-score",
            min_score,
        ]
        for flag, value in (
            ("--scene-spec", scene_spec),
            ("--priors-file", priors_file),
        ):
            if value is not None:
                arguments.extend([flag, value])
        cpu_stage("projection", arguments)
        projection = read_json(out / "projection/projection.json")
        steps.append({"stage": stage, "wall_s": time.perf_counter() - tick})
        generated_spec = out / "projection" / "scene_spec.json"
        spec_sha256 = digest(generated_spec)
        ensure_unchanged(frozen)
        stage, tick = "usd_authoring", time.perf_counter()
        cpu_stage(
            "authoring",
            ["--ply", ply, "--out", out / "scene", "--scene-spec", generated_spec],
        )
        steps.append({"stage": stage, "wall_s": time.perf_counter() - tick})
        ensure_unchanged(frozen)
        stage, tick = "independent_usd_audit", time.perf_counter()
        code = _worker(
            [
                sys.executable,
                "-m",
                "simready.evaluation.inspection.physical_assets",
                "--scene-directory",
                str(out / "scene"),
                "--scene-spec",
                str(generated_spec),
                "--out",
                str(out / "physical_audit.json"),
            ],
            out / "logs" / "physical_audit.log",
            remaining(),
            **({"cancel_event": cancel_event} if cancel_event is not None else {}),
        )
        steps.append(
            {"stage": stage, "wall_s": time.perf_counter() - tick, "exit_code": code}
        )
        if code not in (0, 1):
            raise RuntimeError(
                "Independent USD audit failed to run; see logs/physical_audit.log"
            )
        audit = read_json(out / "physical_audit.json")
        remaining()
        ensure_unchanged(frozen)
        if digest(generated_spec) != spec_sha256:
            raise ValueError(
                "Generated scene specification changed during authoring/audit"
            )
        if code == 0:
            verify_physical_receipt(
                audit, directory=out / "scene", scene_spec=generated_spec
            )
        if any(
            digest(package / name) != value for name, value in implementation.items()
        ):
            raise RuntimeError("Pipeline implementation changed during execution")
        remaining()
        result.update(
            status="AUDIT_FAILED" if code else projection["status"],
            semantic_manifest={
                "path": str(segmentation_manifest),
                "sha256": digest(segmentation_manifest),
            },
            assets_generated=len(projection["accepted"]),
            regions_skipped=len(projection["skipped"]),
            review_reasons=projection["review_reasons"],
            physical_audit_status=audit["status"],
            semantic_accuracy="NOT_EVALUATED",
            real_world_calibration="NOT_EVALUATED",
            dynamics_response="NOT_EVALUATED",
            appearance_source="source_ply",
            repaired_observation_scene_composed=False,
            files={
                "scene": "scene/scene.usda",
                "catalog": "scene/asset_catalog.json",
                "scene_spec": "projection/scene_spec.json",
                "projection": "projection/projection.json",
                "physical_audit": "physical_audit.json",
            },
        )
    except BaseException as error:
        failed_step = {
            "stage": stage,
            "wall_s": time.perf_counter() - tick,
            "status": "TIMEOUT"
            if isinstance(error, ProcessTimeoutError)
            else "CANCELLED"
            if isinstance(error, ProcessCancelledError)
            else "FAILED"
            if isinstance(error, Exception)
            else "INTERRUPTED",
        }
        if steps and steps[-1]["stage"] == stage:
            steps[-1] = failed_step
        else:
            steps.append(failed_step)
        result.update(
            status=failed_step["status"],
            failed_stage=stage,
            error=f"{type(error).__name__}: {error}",
        )
        raise
    finally:
        result["wall_s"] = time.perf_counter() - started
        result["orchestration_overhead_s"] = result["wall_s"] - sum(
            row["wall_s"] for row in steps
        )
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_json(out / "workflow.json", result)
    return result


def _internal_worker(argv):
    """Private CPU stage dispatch; the public workflow owns budgets and receipts."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="stage", required=True)
    projection = subparsers.add_parser("projection")
    projection.add_argument("--ply", type=Path, required=True)
    projection.add_argument("--segmentation-manifest", type=Path, required=True)
    projection.add_argument("--calibration", type=Path, required=True)
    projection.add_argument("--out", type=Path, required=True)
    projection.add_argument("--scene-spec", type=Path)
    projection.add_argument("--priors-file", type=Path)
    projection.add_argument("--min-points", type=int, default=16)
    projection.add_argument("--min-score", type=float, default=0.25)
    authoring = subparsers.add_parser("authoring")
    authoring.add_argument("--ply", type=Path, required=True)
    authoring.add_argument("--out", type=Path, required=True)
    authoring.add_argument("--scene-spec", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.stage == "projection":
        build_semantic_spec(
            args.ply,
            args.segmentation_manifest,
            args.calibration,
            args.out,
            scene_spec=args.scene_spec,
            priors_file=args.priors_file,
            min_points=args.min_points,
            min_score=args.min_score,
        )
    else:
        prepare_scene(args.ply, args.out, args.scene_spec)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI exit 0 means candidate ready; 2 means review/audit blocked, not release."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "internal-worker":
        return _internal_worker(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ply", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--segmentation-manifest", type=Path)
    parser.add_argument("--scene-spec", type=Path)
    parser.add_argument("--priors-file", type=Path)
    parser.add_argument("--segmentation-python", type=Path)
    parser.add_argument(
        "--segmentation-backend", choices=["segformer", "grounded"], default="grounded"
    )
    parser.add_argument("--model-id")
    parser.add_argument("--revision")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--min-area-px", type=int, default=256)
    parser.add_argument("--min-points", type=int, default=16)
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--worker-timeout-s", type=float, default=1200.0)
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    parser.add_argument("--device-inventory", type=Path)
    parser.add_argument("--lock-dir", type=Path, default=Path(DEFAULT_LOCK_DIR))
    args = vars(parser.parse_args(argv))
    args["output_dir"] = args.pop("out")
    try:
        if args["device_inventory"] is not None:
            args["device_inventory"] = read_json(args["device_inventory"])
        result = run_semantic_assets(**args)
    except (ProcessTimeoutError, ProcessCancelledError) as error:
        print(
            json.dumps(
                {
                    "status": "TIMEOUT"
                    if isinstance(error, ProcessTimeoutError)
                    else "CANCELLED",
                    "error": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 2
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as error:
        print(
            json.dumps({"status": "INVALID", "error": str(error)}, ensure_ascii=False)
        )
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "CANDIDATE_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
