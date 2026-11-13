"""Run scene preparation, independent structure inspection, and native survey."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json


def execute(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    for name in ("pipeline-status.json", "delivery.json", "scene", "render"):
        if (output / name).exists() or (output / name).is_symlink():
            raise FileExistsError(f"Previous worker evidence exists: {name}")
    output.mkdir(parents=True, exist_ok=True)
    started, timing = time.monotonic(), []
    state = {"schema_version": 1, "status": "RUNNING", "stage": "inputs"}

    def step(name, module, argv):
        state["stage"] = name
        write_json(output / "pipeline-status.json", state)
        start = time.monotonic()
        print(f"[{name}] RUNNING; log: {output / (name + '.log')}", flush=True)
        with (output / (name + ".log")).open("x") as log:
            code = subprocess.run(
                [sys.executable, "-m", module, *map(str, argv)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode
        timing.append(
            {"name": name, "wall_s": time.monotonic() - start, "returncode": code}
        )
        if code:
            raise RuntimeError(f"{name} failed with exit {code}; inspect its log")
        print(f"[{name}] COMPLETE ({timing[-1]['wall_s']:.2f}s)", flush=True)

    try:
        source = read_json(inputs / "input.json")
        if (
            digest(inputs / "splats.ply") != source["ply_sha256"]
            or digest(inputs / "scene-spec.json") != source["spec_sha256"]
        ):
            raise ValueError("Worker inputs changed")
        if not source["prepare_only"]:
            step("environment", "simready.simulation.environment_check", [])
        step(
            "preparation",
            "simready.simulation.scene_preparation",
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
            "robot_task_validated": False,
            "limitations": preparation["limitations"]
            + [
                "Structural audit does not measure reconstruction fidelity or whole-scene asset completeness",
                "No robot motion or contact task executed by the new-video survey",
            ],
            "artifacts": artifacts,
            "stages": timing,
        }
        write_json(output / "delivery.json", result)
        state.update(status="COMPLETE", stage="summary")
        return 0
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
    args = parser.parse_args(argv)
    return execute(args.inputs, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
