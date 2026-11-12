"""Replay a source-bound reviewed scene through capture or physics/audit/capture."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.simulation.asset_bundle import verify_bundle


def validate_scene_config(config, scene):
    if config.get("schema_version") != 1 or config["scene"] != scene:
        raise ValueError("Example and bundle scene must match")
    render = config["render"]
    if any(
        type(render[k]) is not int or render[k] <= 0
        for k in ("width", "height", "frames", "fps")
    ):
        raise ValueError("Render dimensions, frames and fps must be positive integers")
    if render["width"] % 2 or render["height"] % 2:
        raise ValueError("H.264 yuv420p dimensions must be even")
    if scene == "substation" and render["fps"] != 25:
        raise ValueError("Measured substation replay uses the frozen 25 fps sampling")


def verify_scene_delivery(output, config):
    """Check final receipts and their artifact bindings before reporting success."""
    output = Path(output)
    scene = config["scene"]
    timing = read_json(output / "timing.json")
    expected = (
        ["observations", "render"]
        if scene == "starbucks"
        else ["observations", "physics", "audit", "render"]
    )
    if (
        timing.get("status") != "COMPLETE"
        or [item["name"] for item in timing["stages"]] != expected
        or any(item["returncode"] != 0 for item in timing["stages"])
    ):
        raise ValueError("Delivery stages did not all complete")
    name = (
        "g2_starbucks_4k.mp4"
        if scene == "starbucks"
        else "navigation_vision_z1_stop.mp4"
    )
    video = output / "render" / name
    if (
        not video.is_file()
        or not video.stat().st_size
        or digest(video) != timing["video_sha256"]
    ):
        raise ValueError("Delivered video is missing, empty or changed")
    render = config["render"]
    if scene == "starbucks":
        receipt = read_json(output / "render/delivery.json")
        if (
            receipt["frames"] != render["frames"]
            or receipt["fps"] != render["fps"]
            or receipt["resolution"] != [render["width"], render["height"]]
        ):
            raise ValueError("Cafe capture does not match the requested delivery")
    else:
        mission = output / "mission"
        audit = read_json(mission / "audit.json")
        receipt = read_json(output / "render/render.json")
        if (
            audit.get("passed") is not True
            or not audit.get("checks")
            or not all(value is True for value in audit["checks"].values())
            or read_json(mission / "result.json").get("passed") is not True
        ):
            raise ValueError("Physical mission has no passing independent audit")
        for field, path in (
            ("result_sha256", "result.json"),
            ("sensor_sha256", "sensors.jsonl"),
            ("replay_sha256", "measured_replay.npz"),
        ):
            if audit[field] != digest(mission / path):
                raise ValueError("Physical audit is not bound to current evidence")
        if (
            receipt["source_result_sha256"] != audit["result_sha256"]
            or receipt["source_replay_sha256"] != audit["replay_sha256"]
            or receipt["video_sha256"] != timing["video_sha256"]
            or receipt["width"] != render["width"]
            or receipt["height"] != render["height"]
            or receipt["fps"] != 25
            or receipt["frames"] <= 0
        ):
            raise ValueError("Physical replay receipt does not match audited evidence")
    return {
        "video": str(video),
        "video_sha256": timing["video_sha256"],
        "physics_validated": scene == "substation",
    }


def run_scene_delivery(
    config_path, assets, output, python=None, dry_run=False, progress=None
):
    config_path, assets, output = (
        Path(p).resolve() for p in (config_path, assets, output)
    )
    config = read_json(config_path)
    bundle = verify_bundle(assets)
    validate_scene_config(config, bundle["scene"])
    executable = str(python or sys.executable)  # Keep virtualenv symlinks intact.
    render = config["render"]
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
            if progress:
                progress(name, "RUNNING")
            before = time.monotonic()
            with (output / f"{name}.log").open("w") as log:
                code = subprocess.run(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
                ).returncode
            report["stages"].append(
                {"name": name, "wall_s": time.monotonic() - before, "returncode": code}
            )
            write_json(output / "timing.json", report)
            if progress:
                progress(name, "FAILED" if code else "COMPLETE")
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
