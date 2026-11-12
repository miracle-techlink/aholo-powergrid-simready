"""Replay a source-bound reviewed scene through capture or physics/audit/capture."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.simulation.asset_bundle import verify_bundle


def run_scene_delivery(config_path, assets, output, python=None, dry_run=False):
    config_path, assets, output = (
        Path(p).resolve() for p in (config_path, assets, output)
    )
    config = read_json(config_path)
    bundle = verify_bundle(assets)
    if config.get("schema_version") != 1 or config["scene"] != bundle["scene"]:
        raise ValueError("Example and bundle scene must match")
    executable = str(python or sys.executable)  # Keep virtualenv symlinks intact.
    render = config["render"]
    if any(
        type(render[k]) is not int or render[k] <= 0
        for k in ("width", "height", "frames", "fps")
    ):
        raise ValueError("Render dimensions, frames and fps must be positive integers")
    if render["width"] % 2 or render["height"] % 2:
        raise ValueError("H.264 yuv420p dimensions must be even")
    common = [executable, "-m", "simready"]
    stages = [
        (
            "observations",
            common
            + [
                "ingestion",
                "extract",
                "--video",
                str(assets / bundle["source_video"]),
                "--out",
                str(output / "observations"),
                "--fps",
                str(config.get("observation_fps", 2)),
            ],
        )
    ]
    if bundle["scene"] == "starbucks":
        command = common + [
            "simulation",
            "cafe-video",
            "--scene",
            str(assets / bundle["scene_usd"]),
            "--robot",
            str(assets / bundle["robot_usd"]),
            "--out",
            str(output / "render"),
            "--tour",
            "--encode",
            "--frames",
            str(render["frames"]),
            "--fps",
            str(render["fps"]),
            "--res",
            str(render["width"]),
            str(render["height"]),
            "--subframes",
            str(render.get("subframes", 32)),
        ]
        if bundle.get("color_layer"):
            command += [
                "--color-fix",
                "--color-layer",
                str(assets / bundle["color_layer"]),
            ]
        stages.append(("render", command))
        video = output / "render/g2_starbucks_4k.mp4"
    else:
        if render["fps"] != 25:
            raise ValueError(
                "Measured substation replay uses the frozen 25 fps sampling"
            )
        stages += [
            (
                "physics",
                common
                + [
                    "simulation",
                    "mission",
                    "--config",
                    str(assets / bundle["mission_config"]),
                    "--out",
                    str(output / "mission"),
                    "--max-seconds",
                    "65",
                ],
            ),
            (
                "audit",
                common
                + ["simulation", "mission-audit", "--run", str(output / "mission")],
            ),
            (
                "render",
                common
                + [
                    "simulation",
                    "mission-video",
                    "--run",
                    str(output / "mission"),
                    "--out",
                    str(output / "render"),
                    "--width",
                    str(render["width"]),
                    "--height",
                    str(render["height"]),
                ],
            ),
        ]
        video = output / "render/navigation_vision_z1_stop.mp4"
    plan = {
        "schema_version": 1,
        "scene": bundle["scene"],
        "mode": "reviewed_scene_reproduction",
        "cloud_submission": False,
        "bundle_sha256": digest(assets / "bundle.json"),
        "config_sha256": digest(config_path),
        "source_video_sha256": bundle["source_video_sha256"],
        "stages": [{"name": n, "argv": c} for n, c in stages],
        "video": str(video),
    }
    if dry_run:
        return plan
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    start = time.monotonic()
    report = {"status": "RUNNING", "stages": [], "new_reconstruction": False}
    from simready.execution.isaac_environment import isaac_environment

    env = isaac_environment()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        for name, command in stages:
            before = time.monotonic()
            with (output / f"{name}.log").open("w") as log:
                code = subprocess.run(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
                ).returncode
            report["stages"].append(
                {"name": name, "wall_s": time.monotonic() - before, "returncode": code}
            )
            write_json(output / "timing.json", report)
            if code:
                raise RuntimeError(
                    f"Stage {name} failed with exit {code}; see {name}.log"
                )
        if not video.is_file() or not video.stat().st_size:
            raise RuntimeError("Capture exited without a nonempty video")
        report.update(
            status="COMPLETE",
            video=str(video),
            video_sha256=digest(video),
            physics_validated=bundle["scene"] == "substation",
        )
    except BaseException as error:
        report.update(status="FAILED", error=str(error))
        raise
    finally:
        report["wall_s"] = time.monotonic() - start
        write_json(output / "timing.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_scene_delivery(
        args.config, args.assets, args.out, args.python, args.dry_run
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
