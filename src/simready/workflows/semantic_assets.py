"""Source-bound semantic masks → static object proxies → USD → independent audit."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, read_json, write_json
from simready.simulation.scene_preparation import prepare_scene
from simready.simulation.semantic_projection import build_semantic_spec


def _worker(command: list[str], log: Path, timeout_s: float) -> int:
    with log.open("w") as stream:
        process = subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_s,
        )
    return process.returncode


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
) -> dict[str, Any]:
    """Run bounded candidate generation and record per-stage wall time.

    Exactly one of image or segmentation_manifest is required. The latter is
    explicit evidence reuse, never reported as new model inference. A separate
    interpreter reopens the authored USD and checks it against the external
    generated scene spec. This does not certify semantic or real physics accuracy.
    Output must be fresh; failures retain logs and an unambiguous FAILED receipt.
    """
    started = time.perf_counter()
    if type(worker_timeout_s) not in (int, float) or not 1 <= worker_timeout_s <= 7200:
        raise ValueError("worker_timeout_s must be within 1..7200")
    if (image is None) == (segmentation_manifest is None):
        raise ValueError("Provide exactly one image or segmentation_manifest")
    if segmentation_backend not in {"segformer", "grounded"}:
        raise ValueError("segmentation_backend must be segformer or grounded")
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
    package = Path(__file__).resolve().parents[1]
    implementation = {
        relative: digest(package / relative)
        for relative in (
            "workflows/semantic_assets.py",
            "simulation/semantic_projection.py",
            "simulation/depth_projection.py",
            "simulation/category_priors.py",
            "simulation/scene_preparation.py",
            "simulation/physical_spec.py",
            "simulation/physical_authoring.py",
            "evaluation/physical_assets.py",
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
        "implementation_sha256": implementation,
    }
    stage = "segmentation"
    try:
        tick = time.perf_counter()
        if image is not None:
            command = [
                str(segmentation_python or sys.executable),
                "-m",
                "simready.ingestion.grounded_segmentation"
                if segmentation_backend == "grounded"
                else "simready.ingestion.semantic_segmentation",
                "--image",
                str(image),
                "--output-dir",
                str(out / "segmentation"),
                "--device",
                device,
                "--min-area-px",
                str(min_area_px),
            ]
            for key, value in (("--model-id", model_id), ("--revision", revision)):
                if value is not None:
                    command.extend([key, value])
            if local_files_only:
                command.append("--local-files-only")
            if _worker(command, out / "logs" / "segmentation.log", worker_timeout_s):
                raise RuntimeError(
                    "Segmentation worker failed; see logs/segmentation.log"
                )
            segmentation_manifest = out / "segmentation" / "manifest.json"
        else:
            segmentation_manifest = Path(segmentation_manifest).resolve(strict=True)
        steps.append(
            {
                "stage": stage,
                "wall_s": time.perf_counter() - tick,
                "mode": "new_inference" if image else "reuse_manifest",
            }
        )
        stage, tick = "projection_and_priors", time.perf_counter()
        projection = build_semantic_spec(
            ply,
            segmentation_manifest,
            calibration,
            out / "projection",
            scene_spec=scene_spec,
            priors_file=priors_file,
            min_points=min_points,
            min_score=min_score,
        )
        steps.append({"stage": stage, "wall_s": time.perf_counter() - tick})
        generated_spec = out / "projection" / "scene_spec.json"
        stage, tick = "usd_authoring", time.perf_counter()
        prepare_scene(ply, out / "scene", generated_spec)
        steps.append({"stage": stage, "wall_s": time.perf_counter() - tick})
        stage, tick = "independent_usd_audit", time.perf_counter()
        code = _worker(
            [
                sys.executable,
                "-m",
                "simready.evaluation.physical_assets",
                "--scene-directory",
                str(out / "scene"),
                "--scene-spec",
                str(generated_spec),
                "--out",
                str(out / "physical_audit.json"),
            ],
            out / "logs" / "physical_audit.log",
            worker_timeout_s,
        )
        steps.append(
            {"stage": stage, "wall_s": time.perf_counter() - tick, "exit_code": code}
        )
        if code not in (0, 1):
            raise RuntimeError(
                "Independent USD audit failed to run; see logs/physical_audit.log"
            )
        audit = read_json(out / "physical_audit.json")
        if any(
            digest(package / name) != value for name, value in implementation.items()
        ):
            raise RuntimeError("Pipeline implementation changed during execution")
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
    except Exception as error:
        failed_step = {
            "stage": stage,
            "wall_s": time.perf_counter() - tick,
            "status": "FAILED",
        }
        if steps and steps[-1]["stage"] == stage:
            steps[-1] = failed_step
        else:
            steps.append(failed_step)
        result.update(
            status="FAILED",
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


def main(argv: list[str] | None = None) -> int:
    """CLI exit 0 means candidate ready; 2 means review/audit blocked, not release."""
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
    args = vars(parser.parse_args(argv))
    args["output_dir"] = args.pop("out")
    try:
        result = run_semantic_assets(**args)
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        RuntimeError,
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
