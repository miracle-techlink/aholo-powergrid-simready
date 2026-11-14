"""Replay a source-bound reviewed scene through capture or physics/audit/capture."""

import argparse
import json
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.validation import number
from simready.execution.device_lease import device_lease
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.execution.resource_pool import DEFAULT_LOCK_DIR
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


def verify_scene_delivery(output, config, *, assets=None, timing=None):
    """Check final receipts and their artifact bindings before reporting success."""
    output = Path(output)
    scene = config["scene"]
    if assets is not None:
        assets = Path(assets)
        bundle = verify_bundle(assets)
        plan = read_json(output / "plan.json")
        if (
            plan.get("scene") != scene
            or bundle["scene"] != scene
            or plan.get("bundle_sha256") != digest(assets / "bundle.json")
            or plan.get("source_video_sha256") != bundle["source_video_sha256"]
            or plan.get("config_identity") != object_digest(config)
        ):
            raise ValueError(
                "Delivery plan is not bound to the requested bundle and configuration"
            )
    timing = read_json(output / "timing.json") if timing is None else timing
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
    config_path,
    assets,
    output,
    python=None,
    dry_run=False,
    progress=None,
    *,
    timeout_s=7200,
    cancel_event=None,
    process_environment=None,
    pass_fds=(),
):
    if number(timeout_s, "timeout_s") <= 0:
        raise ValueError("timeout_s must be positive")
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
        "config_identity": object_digest(config),
        "source_video_sha256": bundle["source_video_sha256"],
        "stages": [{"name": n, "argv": c} for n, c in stages],
        "video": str(video),
        "timeout_s": timeout_s,
    }
    if dry_run:
        return plan
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    start = time.monotonic()
    deadline = start + timeout_s
    report = {"status": "RUNNING", "stages": [], "new_reconstruction": False}
    from simready.execution.isaac_environment import isaac_environment

    env = isaac_environment()
    env["PYTHONUNBUFFERED"] = "1"
    env.update(process_environment or {})

    def remaining():
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelledError(time.monotonic() - start)
        value = deadline - time.monotonic()
        if value <= 0:
            raise ProcessTimeoutError(timeout_s, time.monotonic() - start)
        return value

    try:
        for name, command in stages:
            if progress:
                progress(name, "RUNNING")
            before = time.monotonic()
            row = {"name": name, "returncode": None, "status": "RUNNING"}
            try:
                code = run_process(
                    command,
                    cwd=None,
                    env=env,
                    stdout=output / f"{name}.log",
                    stderr=output / f"{name}.stderr.log",
                    timeout=remaining(),
                    cancel_event=cancel_event,
                    pass_fds=pass_fds,
                ).returncode
                row.update(returncode=code, status="FAILED" if code else "COMPLETE")
            except BaseException as error:
                row["status"] = (
                    "TIMEOUT"
                    if isinstance(error, ProcessTimeoutError)
                    else "CANCELLED"
                    if isinstance(error, ProcessCancelledError)
                    else "FAILED"
                    if isinstance(error, Exception)
                    else "INTERRUPTED"
                )
                if progress:
                    progress(name, row["status"])
                raise
            finally:
                row["wall_s"] = time.monotonic() - before
                report["stages"].append(row)
                write_json(output / "timing.json", report)
            if progress:
                progress(name, "FAILED" if code else "COMPLETE")
            if code:
                raise RuntimeError(
                    f"Stage {name} failed with exit {code}; see {name}.log"
                )
        if not video.is_file() or not video.stat().st_size:
            raise RuntimeError("Capture exited without a nonempty video")
        remaining()
        report.update(
            status="COMPLETE",
            video=str(video),
            video_sha256=digest(video),
            physics_validated=bundle["scene"] == "substation",
        )
        if digest(config_path) != plan["config_sha256"]:
            raise ValueError("Scene configuration changed during execution")
        verify_scene_delivery(output, config, assets=assets, timing=report)
        remaining()
    except BaseException as error:
        report.update(
            status="TIMEOUT"
            if isinstance(error, ProcessTimeoutError)
            else "CANCELLED"
            if isinstance(error, ProcessCancelledError)
            else "FAILED"
            if isinstance(error, Exception)
            else "INTERRUPTED",
            error=str(error) or type(error).__name__,
        )
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
    parser.add_argument("--timeout-s", type=float, default=7200)
    parser.add_argument("--resource-kind", choices=("rtx", "cpu"), default="rtx")
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--device-inventory", type=Path)
    parser.add_argument("--lock-dir", type=Path, default=Path(DEFAULT_LOCK_DIR))
    args = parser.parse_args(argv)
    planned = run_scene_delivery(
        args.config, args.assets, args.out, args.python, True, timeout_s=args.timeout_s
    )
    if args.dry_run:
        result = planned
    else:
        # Public host entry owns the device. Container launchers use the internal
        # scene_execution entry instead and retain the lease for the container.
        with device_lease(
            resource_kind=args.resource_kind,
            gpu=args.gpu,
            device_inventory=read_json(args.device_inventory)
            if args.device_inventory
            else None,
            lock_dir=args.lock_dir,
        ) as lease:
            result = run_scene_delivery(
                args.config,
                args.assets,
                args.out,
                args.python,
                timeout_s=args.timeout_s,
                process_environment=lease.environment,
                pass_fds=lease.pass_fds,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
