"""Validate and render a prepared original-scene Starbucks task bundle."""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from simready.execution.isaac_launcher import launch_worker, seed_runtime_cache


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=["grasp", "tour"], required=True)
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model-python", default=sys.executable)
    ap.add_argument("--image", default="simready-isaac:6.1.0")
    ap.add_argument("--gpu", default="0")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--width", type=int, choices=[1280, 1920], default=1920)
    ap.add_argument("--render-subframes", type=int, choices=range(1, 33), default=6)
    ap.add_argument("--patrol-x-range", nargs=2, type=float)
    ap.add_argument("--follow-camera", action="store_true")
    ap.add_argument(
        "--synchronous-capture", action=argparse.BooleanOptionalAction, default=True
    )
    ap.add_argument(
        "--camera-focal-length",
        type=float,
        help="Patrol lens in mm; defaults to the historical 18 mm camera",
    )
    ap.add_argument(
        "--dome-intensity",
        type=float,
        help="Explicit patrol lighting, independent of camera mode",
    )
    ap.add_argument("--physics-hz", type=int, choices=[240, 480], default=480)
    ap.add_argument(
        "--runtime-seed",
        type=Path,
        help="Optional completed worker directory from which to copy shader caches",
    )
    args = ap.parse_args()
    if any(
        value is not None and (not 0 < value < float("inf"))
        for value in [args.camera_focal_length, args.dome_intensity]
    ):
        ap.error("Camera focal length and dome intensity must be finite and positive")
    repo = Path(__file__).resolve().parents[2]
    args.assets = args.assets.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    artifacts = args.out / "artifacts"
    artifacts.mkdir()
    started = time.perf_counter()
    report = {
        "status": "RUNNING",
        "case": args.case,
        "scope": "prepared original-scene assets through static/task validation and optional video; excludes source reconstruction",
        "stages": [],
    }

    def save():
        report["wall_s"] = time.perf_counter() - started
        (args.out / "timing.json").write_text(json.dumps(report, indent=2) + "\n")

    def command(argv):
        subprocess.run(list(map(str, argv)), cwd=repo, check=True)

    def worker(module, options, assets=None):
        code = launch_worker(
            ["-m", module, *options],
            repo=repo,
            assets=assets or args.assets,
            output=artifacts,
            image=args.image,
            gpu=args.gpu,
        )
        if code:
            raise RuntimeError(f"{module} exited with {code}")

    def step(name, operation):
        row = {"stage": name, "status": "RUNNING"}
        report["stages"].append(row)
        tick = time.perf_counter()
        try:
            operation()
            row["status"] = "PASS"
        except BaseException:
            row["status"] = "FAILED"
            raise
        finally:
            row["wall_s"] = time.perf_counter() - tick
            save()

    try:
        step(
            "seed_isolated_shader_cache",
            lambda: seed_runtime_cache(args.runtime_seed, artifacts),
        )
        step(
            "actual_scene_support_probe",
            lambda: worker(
                "simready.simulation.scene_probe",
                ["--assets", "/assets", "--out", "/output/probe"],
            ),
        )
        audit_assets = args.assets
        audit_options = []
        if args.case == "grasp":
            step(
                "observation_planning_physics_task",
                lambda: command(
                    [
                        sys.executable,
                        repo / "examples/starbucks_grasp/run.py",
                        "--assets",
                        args.assets,
                        "--scene",
                        "scene/scene.usda",
                        "--require-original-video",
                        "--out",
                        args.out / "task",
                        "--model-python",
                        args.model_python,
                        "--image",
                        args.image,
                        "--gpu",
                        args.gpu,
                        "--physics-hz",
                        str(args.physics_hz),
                        *(
                            ["--runtime-seed", str(args.runtime_seed.resolve())]
                            if args.runtime_seed
                            else []
                        ),
                    ]
                ),
            )
            audit_assets = args.out / "task/planned"
            audit_options = ["--task", args.out / "task/artifacts/audit.json"]
        else:
            step(
                "patrol_clearance_plan",
                lambda: command(
                    [
                        sys.executable,
                        repo / "examples/starbucks_simready/check_route.py",
                        "--assets",
                        args.assets,
                        "--trajectory",
                        args.assets / "source_trajectory.json",
                        "--profile",
                        args.assets / "height_profile.json",
                        "--out",
                        artifacts / "route",
                        *(
                            ["--x-range", *map(str, args.patrol_x_range)]
                            if args.patrol_x_range
                            else []
                        ),
                        *(["--follow-camera"] if args.follow_camera else []),
                    ]
                ),
            )
            audit_options = ["--route", artifacts / "route/route_check.json"]
        step(
            "independent_scene_audit",
            lambda: command(
                [
                    sys.executable,
                    "-m",
                    "simready.evaluation.scene_readiness",
                    "--assets",
                    audit_assets,
                    "--probe",
                    artifacts / "probe/probe.json",
                    *audit_options,
                    "--out",
                    artifacts / "readiness.json",
                ]
            ),
        )
        if args.render:
            if args.case == "grasp":
                shutil.copytree(
                    args.out / "task/artifacts/physics", artifacts / "physics"
                )
                step(
                    "measured_grasp_replay",
                    lambda: worker(
                        "simready.simulation.grasp_video",
                        [
                            "--assets",
                            "/assets",
                            "--scene",
                            "/assets/scene/scene.usda",
                            "--rollout",
                            "/output/physics",
                            "--out",
                            "/output/video",
                            "--width",
                            str(args.width),
                            "--subframes",
                            str(args.render_subframes),
                        ],
                        assets=audit_assets,
                    ),
                )
                video = args.out / "G2_cup_transfer_simready.mp4"
                step(
                    "encode",
                    lambda: command(
                        [
                            "ffmpeg",
                            "-nostdin",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-framerate",
                            "30",
                            "-i",
                            artifacts / "video/frames/rgb_%04d.png",
                            "-c:v",
                            "libx264",
                            "-crf",
                            "18",
                            "-preset",
                            "fast",
                            "-pix_fmt",
                            "yuv420p",
                            "-movflags",
                            "+faststart",
                            video,
                        ]
                    ),
                )
            else:
                step(
                    "authored_patrol_render",
                    lambda: worker(
                        "simready.simulation.cafe_video",
                        [
                            "--tour",
                            "--synchronous-capture"
                            if args.synchronous_capture
                            else "--stream-capture",
                            "--scene",
                            "/assets/scene/scene.usda",
                            "--robot",
                            "/assets/robot_nav.usdc",
                            "--replay-trajectory",
                            "/output/route/trajectory.json",
                            "--res",
                            str(args.width),
                            str(args.width * 9 // 16),
                            "--subframes",
                            str(args.render_subframes),
                            "--out",
                            "/output/video",
                            "--encode",
                            "--camera-focal-length",
                            str(
                                args.camera_focal_length
                                if args.camera_focal_length is not None
                                else (16 if args.follow_camera else 18)
                            ),
                            "--dome-intensity",
                            str(
                                args.dome_intensity
                                if args.dome_intensity is not None
                                else (650 if args.follow_camera else 200)
                            ),
                        ],
                    ),
                )
                video = args.out / "G2_30s_patrol_simready.mp4"
                shutil.copy2(artifacts / "video/g2_starbucks_4k.mp4", video)
            step(
                "decode_complete_video",
                lambda: command(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        video,
                        "-f",
                        "null",
                        "-",
                    ]
                ),
            )
        report["status"] = "PASS"
    except BaseException as error:
        report.update(status="FAILED", error=str(error))
        raise
    finally:
        save()
    print(
        json.dumps(
            {
                "status": report["status"],
                "wall_s": report["wall_s"],
                "out": str(args.out),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
