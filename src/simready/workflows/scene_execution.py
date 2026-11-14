"""Internal worker for a launcher-owned Isaac environment and device lease.

Use the public scene pipeline or launcher for scheduling guarantees. Direct SDK
or internal-module invocation is trusted worker code, not a resource sandbox.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from simready.core.artifacts import read_json, write_json
from simready.core.validation import number
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.workflows.scene_delivery import run_scene_delivery, verify_scene_delivery


def execute_scene(
    assets,
    output,
    *,
    timeout_s=7200,
    cancel_event=None,
    process_environment=None,
    pass_fds=(),
    config_path=None,
):
    if number(timeout_s, "timeout_s") <= 0:
        raise ValueError("timeout_s must be positive")
    output = Path(output).resolve()
    for name in (
        "pipeline-status.json",
        "plan.json",
        "environment.json",
        "environment.stderr.log",
        "delivery",
    ):
        path = output / name
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Refusing to overwrite existing execution: {path}")
    if config_path is not None:
        source = Path(config_path).resolve(strict=True)
        if (output / "scene.json").exists() or (output / "scene.json").is_symlink():
            raise FileExistsError("Refusing to overwrite existing scene configuration")
        read_json(source)
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output / "scene.json")
    config = read_json(output / "scene.json")
    start = time.monotonic()
    deadline = start + timeout_s
    state = {
        "schema_version": 1,
        "status": "RUNNING",
        "stage": "bundle_verification",
        "mode": "reviewed_scene_reproduction",
        "cloud_submission": False,
        "new_reconstruction": False,
        "timeout_s": timeout_s,
        "stages": [],
    }
    stage_started = {}

    def progress(stage, status):
        if status == "RUNNING":
            stage_started[stage] = time.monotonic()
        elapsed = time.monotonic() - stage_started.get(stage, time.monotonic())
        if status != "RUNNING":
            row = {"name": stage, "status": status, "wall_s": elapsed}
            if state["stages"] and state["stages"][-1]["name"] == stage:
                state["stages"][-1] = row
            else:
                state["stages"].append(row)
        log = output / ("environment.json" if stage == "environment" else "plan.json")
        if stage in ("observations", "physics", "audit", "render"):
            log = output / "delivery" / f"{stage}.log"
        state.update(stage=stage, stage_status=status)
        state.update(stage_wall_s=elapsed, stage_log=str(log))
        write_json(output / "pipeline-status.json", state)
        print(f"[{stage}] {status} ({elapsed:.1f}s); log: {log}", flush=True)

    def remaining():
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelledError(time.monotonic() - start)
        value = deadline - time.monotonic()
        if value <= 0:
            raise ProcessTimeoutError(timeout_s, time.monotonic() - start)
        return value

    try:
        progress("bundle_verification", "RUNNING")
        plan = run_scene_delivery(
            output / "scene.json",
            assets,
            output / "delivery",
            dry_run=True,
            timeout_s=remaining(),
        )
        write_json(output / "plan.json", plan)
        progress("bundle_verification", "COMPLETE")
        progress("environment", "RUNNING")
        command = [sys.executable, "-m", "simready.simulation.environment_check"]
        if config["scene"] == "substation":
            command.append("--require-lab")
        code = run_process(
            command,
            cwd=None,
            env=process_environment,
            stdout=output / "environment.json",
            stderr=output / "environment.stderr.log",
            timeout=remaining(),
            cancel_event=cancel_event,
            pass_fds=pass_fds,
        ).returncode
        if code:
            raise RuntimeError(
                "Isaac environment check failed; inspect environment.json and environment.stderr.log"
            )
        environment = read_json(output / "environment.json")
        if (
            environment.get("passed") is not True
            or environment.get("ffmpeg") is not True
        ):
            raise RuntimeError(
                "Isaac environment is incomplete; ffmpeg and required SDK modules must be available"
            )
        progress("environment", "COMPLETE")
        run_scene_delivery(
            output / "scene.json",
            assets,
            output / "delivery",
            progress=progress,
            timeout_s=remaining(),
            cancel_event=cancel_event,
            process_environment=process_environment,
            pass_fds=pass_fds,
        )
        progress("delivery_verification", "RUNNING")
        remaining()
        evidence = verify_scene_delivery(output / "delivery", config, assets=assets)
        remaining()
        progress("delivery_verification", "COMPLETE")
        state.update(
            status="COMPLETE",
            stage="delivery_verification",
            stage_status="COMPLETE",
            **evidence,
        )
        return 0
    except Exception as error:
        status = (
            "TIMEOUT"
            if isinstance(error, ProcessTimeoutError)
            else "CANCELLED"
            if isinstance(error, ProcessCancelledError)
            else "FAILED"
        )
        progress(state["stage"], status)
        state.update(status=status, error=str(error))
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return 2
    except BaseException as error:
        progress(state["stage"], "INTERRUPTED")
        state.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        state["wall_s"] = time.monotonic() - start
        write_json(output / "pipeline-status.json", state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=7200)
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional source configuration; output scene.json must not already exist",
    )
    args = parser.parse_args(argv)
    return execute_scene(
        args.assets, args.out, timeout_s=args.timeout_s, config_path=args.config
    )


if __name__ == "__main__":
    raise SystemExit(main())
