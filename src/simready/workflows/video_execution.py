"""Run scene preparation, independent structure inspection, and native survey."""

import argparse
import shutil
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.core.validation import number
from simready.evaluation.physical_receipt import verify_physical_receipt
from simready.execution.process_scope import parent_cancellation, parent_cancelled
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.workflows.physical_estimation import verify_compilation


def execute(inputs, output, *, timeout_s=7200, cancel_event=None):
    if number(timeout_s, "timeout_s") <= 0:
        raise ValueError("timeout_s must be positive")
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    for name in (
        "pipeline-status.json",
        "delivery.json",
        "structure-audit.json",
        "physical-audit.json",
        "scene",
        "render",
        "physical",
    ):
        if (output / name).exists() or (output / name).is_symlink():
            raise FileExistsError(f"Previous worker evidence exists: {name}")
    output.mkdir(parents=True, exist_ok=True)
    started, timing = time.monotonic(), []
    deadline = started + timeout_s
    state = {"schema_version": 1, "status": "RUNNING", "stage": "inputs"}
    parent = None

    def check_budget():
        elapsed = time.monotonic() - started
        if (cancel_event is not None and cancel_event.is_set()) or parent_cancelled(
            parent
        ):
            raise ProcessCancelledError(elapsed)
        if time.monotonic() >= deadline:
            raise ProcessTimeoutError(deadline - started, elapsed)

    def step(name, module, argv):
        state["stage"] = name
        write_json(output / "pipeline-status.json", state)
        start = time.monotonic()
        print(f"[{name}] RUNNING; log: {output / (name + '.log')}", flush=True)
        record = {"name": name, "returncode": None}
        try:
            check_budget()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessTimeoutError(timeout_s, time.monotonic() - started)
            code = run_process(
                [sys.executable, "-m", module, *map(str, argv)],
                cwd=None,
                timeout=remaining,
                cancel_event=cancel_event,
                stdout=output / (name + ".log"),
                stderr=output / (name + ".stderr.log"),
            ).returncode
            record["returncode"] = code
        finally:
            record["wall_s"] = time.monotonic() - start
            timing.append(record)
        if code:
            raise RuntimeError(f"{name} failed with exit {code}; inspect its log")
        print(f"[{name}] COMPLETE ({timing[-1]['wall_s']:.2f}s)", flush=True)

    try:
        parent = parent_cancellation()
        check_budget()
        source = read_json(inputs / "input.json")
        if "worker_timeout_s" in source:
            budget = number(source["worker_timeout_s"], "worker_timeout_s")
            if budget <= 0:
                raise ValueError("worker_timeout_s must be positive")
            deadline = min(deadline, started + budget)
        if (
            digest(inputs / "splats.ply") != source["ply_sha256"]
            or digest(inputs / "scene-spec.json") != source["spec_sha256"]
        ):
            raise ValueError("Worker inputs changed")
        physical_mode = "NOT_RUN"
        physical_limits = []
        if "physical_compilation" in source:
            binding = verify_compilation(
                inputs / "physical",
                read_json(inputs / "scene-spec.json"),
                source["source_video_sha256"],
                source["ply_sha256"],
            )
            if binding != source["physical_compilation"]:
                raise ValueError("Worker physical compilation receipt changed")
            shutil.copytree(inputs / "physical", output / "physical")
            verify_compilation(
                output / "physical",
                read_json(inputs / "scene-spec.json"),
                source["source_video_sha256"],
                source["ply_sha256"],
            )
            compiled = read_json(output / "physical/compilation.json")
            physical_mode = compiled["execution_mode"]
            physical_limits = compiled["limitations"]
        if not source["prepare_only"]:
            step("environment", "simready.simulation.environment_check", [])
        step(
            "preparation",
            "simready.assets.compilation.scene_preparation",
            [
                "--ply",
                inputs / "splats.ply",
                "--output",
                output / "scene",
                "--spec",
                inputs / "scene-spec.json",
            ],
        )
        step(
            "structure_audit",
            "simready.evaluation.scene_package",
            [
                "--scene-directory",
                output / "scene",
                "--out",
                output / "structure-audit.json",
            ],
        )
        preparation = read_json(output / "scene/preparation.json")
        audit = read_json(output / "structure-audit.json")
        if audit.get("passed") is not True:
            raise ValueError("Independent structural inspection did not pass")
        physical_status = "NOT_EVALUATED"
        if read_json(inputs / "scene-spec.json").get("objects"):
            step(
                "physical_audit",
                "simready.evaluation.physical_assets",
                [
                    "--scene-directory",
                    output / "scene",
                    "--scene-spec",
                    inputs / "scene-spec.json",
                    "--out",
                    output / "physical-audit.json",
                ],
            )
            verify_physical_receipt(
                read_json(output / "physical-audit.json"),
                directory=output / "scene",
                scene_spec=inputs / "scene-spec.json",
            )
            physical_status = "PASS"
        if not source["prepare_only"]:
            step(
                "render",
                "simready.simulation.scene_video",
                [
                    "--scene",
                    output / "scene/scene.usda",
                    "--preparation",
                    output / "scene/preparation.json",
                    "--spec",
                    inputs / "scene-spec.json",
                    "--out",
                    output / "render",
                ],
            )
            render = read_json(output / "render/render.json")
            if (
                render["video_sha256"] != digest(output / "render/scene_survey.mp4")
                or render["scene_sha256"] != digest(output / "scene/scene.usda")
                or render["spec_sha256"] != source["spec_sha256"]
            ):
                raise ValueError("Render receipt is not bound to current inputs")
        paths = [
            *sorted((output / "scene").rglob("*")),
            output / "structure-audit.json",
        ]
        if physical_mode != "NOT_RUN":
            paths.extend(sorted((output / "physical").rglob("*")))
        if physical_status == "PASS":
            paths.append(output / "physical-audit.json")
        if not source["prepare_only"]:
            paths.extend(
                [
                    output / "render/render.json",
                    output / "render/camera.json",
                    output / "render/scene_survey.mp4",
                ]
            )
            paths.extend(sorted((output / "render/frames").glob("capture_*.png")))
        artifacts = [
            {
                "path": str(path.relative_to(output)),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
            for path in paths
            if path.is_file()
        ]
        if (
            digest(inputs / "splats.ply") != source["ply_sha256"]
            or digest(inputs / "scene-spec.json") != source["spec_sha256"]
        ):
            raise ValueError("Worker inputs changed during execution")
        result = {
            "schema_version": 1,
            "status": "COMPLETE",
            "delivery_level": "SCENE_EXPORTED"
            if source["prepare_only"]
            else "SCENE_SURVEY_READY",
            "source_video_sha256": source["source_video_sha256"],
            "reconstruction_sha256": source["ply_sha256"],
            "scene_spec_sha256": source["spec_sha256"],
            "scene": "scene/scene.usda",
            "video": None if source["prepare_only"] else "render/scene_survey.mp4",
            "metric_scale_verified": preparation["metric_scale_verified"],
            "metric_scale_independently_validated": False,
            "physics_configured": preparation.get("physics_configured", False),
            "physical_asset_validation": physical_status,
            "physical_estimation": physical_mode,
            "robot_task_validated": False,
            "limitations": preparation["limitations"]
            + physical_limits
            + [
                "Structural audit does not measure reconstruction fidelity or whole-scene asset completeness",
                "No robot motion or contact task executed by the new-video survey",
            ],
            "artifacts": artifacts,
            "stages": timing,
        }
        check_budget()
        write_json(output / "delivery.json", result)
        state.update(status="COMPLETE", stage="summary")
        return 0
    except (ProcessTimeoutError, ProcessCancelledError) as error:
        state.update(
            status="TIMEOUT" if isinstance(error, ProcessTimeoutError) else "CANCELLED",
            error=str(error),
        )
        return 2
    except Exception as error:
        state.update(status="FAILED", error=str(error))
        return 2
    except BaseException as error:
        state.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        state.update(wall_s=time.monotonic() - started, stages=timing)
        write_json(output / "pipeline-status.json", state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=7200)
    args = parser.parse_args(argv)
    return execute(args.inputs, args.out, timeout_s=args.timeout_s)


if __name__ == "__main__":
    raise SystemExit(main())
