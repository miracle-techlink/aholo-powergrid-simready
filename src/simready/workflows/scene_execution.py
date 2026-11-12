"""Execute preflight and a reviewed scene inside the selected Isaac environment."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from simready.core.artifacts import read_json, write_json
from simready.workflows.scene_delivery import run_scene_delivery, verify_scene_delivery


def execute_scene(assets, output):
    output = Path(output).resolve()
    config = read_json(output / "scene.json")
    start = time.monotonic()
    state = {
        "schema_version": 1,
        "status": "RUNNING",
        "stage": "bundle_verification",
        "mode": "reviewed_scene_reproduction",
        "cloud_submission": False,
        "new_reconstruction": False,
    }
    stage_started = {}

    def progress(stage, status):
        if status == "RUNNING":
            stage_started[stage] = time.monotonic()
        elapsed = time.monotonic() - stage_started.get(stage, time.monotonic())
        log = output / ("environment.json" if stage == "environment" else "plan.json")
        if stage in ("observations", "physics", "audit", "render"):
            log = output / "delivery" / f"{stage}.log"
        state.update(stage=stage, stage_status=status)
        state.update(stage_wall_s=elapsed, stage_log=str(log))
        write_json(output / "pipeline-status.json", state)
        print(f"[{stage}] {status} ({elapsed:.1f}s); log: {log}", flush=True)

    try:
        progress("bundle_verification", "RUNNING")
        plan = run_scene_delivery(
            output / "scene.json", assets, output / "delivery", dry_run=True
        )
        write_json(output / "plan.json", plan)
        progress("bundle_verification", "COMPLETE")
        progress("environment", "RUNNING")
        command = [sys.executable, "-m", "simready.simulation.environment_check"]
        if config["scene"] == "substation":
            command.append("--require-lab")
        with (
            (output / "environment.json").open("x") as log,
            (output / "environment.stderr.log").open("x") as errors,
        ):
            code = subprocess.run(
                command, stdout=log, stderr=errors, check=False
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
            output / "scene.json", assets, output / "delivery", progress=progress
        )
        progress("delivery_verification", "RUNNING")
        evidence = verify_scene_delivery(output / "delivery", config)
        progress("delivery_verification", "COMPLETE")
        state.update(
            status="COMPLETE",
            stage="delivery_verification",
            stage_status="COMPLETE",
            **evidence,
        )
        return 0
    except Exception as error:
        state.update(status="FAILED", error=str(error))
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return 2
    except BaseException as error:
        state.update(status="INTERRUPTED", error=type(error).__name__)
        raise
    finally:
        state["wall_s"] = time.monotonic() - start
        write_json(output / "pipeline-status.json", state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute_scene(args.assets, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
