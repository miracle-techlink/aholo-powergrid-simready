"""Run observation -> known-cup localization -> planning -> contact audit -> video.

Inputs are prepared, portable G2/cup USD assets and an optional local scene.
This entry point does not reconstruct an arbitrary new video.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from simready.execution.isaac_launcher import launch_worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", type=Path, help="USD path relative to assets")
    parser.add_argument("--model-python", default=sys.executable)
    parser.add_argument("--image", default="simready-isaac:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--require-original-video", action="store_true")
    parser.add_argument("--width", type=int, choices=[1280, 1920], default=1920)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    args.assets = args.assets.resolve()
    args.out = args.out.resolve()
    if args.scene and (args.scene.is_absolute() or ".." in args.scene.parts):
        parser.error("--scene must stay inside --assets")
    if args.require_original_video:
        if not args.scene:
            parser.error("--require-original-video requires --scene")
        contract_file = (args.assets / args.scene).parent / "scene_contract.json"
        contract = json.loads(contract_file.read_text())
        source_usd = contract_file.parent / "original_video_splats.usdc"
        if (
            contract.get("appearance") != "original_video_reconstruction"
            or contract.get("render_table_proxies") is not False
            or contract.get("visual_gaussians_removed") != 0
            or hashlib.sha256(source_usd.read_bytes()).hexdigest()
            != contract.get("source_usd_sha256")
        ):
            raise ValueError("Original reconstruction scene contract failed")
    args.out.mkdir(parents=True, exist_ok=False)
    artifacts = args.out / "artifacts"
    artifacts.mkdir()
    planned = args.out / "planned"
    started = time.perf_counter()
    rows = []
    report = {
        "status": "RUNNING",
        "scope": "prepared local task corner; fixed-base G2 two-table cup transfer",
        "stages": rows,
        "original_video_scene_required": args.require_original_video,
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")

    def stage(name, function):
        before = time.perf_counter()
        row = {"stage": name, "status": "RUNNING"}
        rows.append(row)
        try:
            value = function()
            if isinstance(value, int) and value:
                raise RuntimeError(f"{name} exited with {value}")
            row["status"] = "PASS"
        except BaseException:
            row["status"] = "FAILED"
            raise
        finally:
            row["wall_s"] = time.perf_counter() - before
            report["wall_s"] = time.perf_counter() - started
            (args.out / "timing.json").write_text(json.dumps(report, indent=2) + "\n")

    def command(argv):
        subprocess.run(list(map(str, argv)), cwd=repo, env=env, check=True)

    def isaac(module, assets, options):
        return launch_worker(
            ["-u", "-m", module, "--assets", "/assets", *options],
            repo=repo,
            assets=assets,
            output=artifacts,
            image=args.image,
            gpu=args.gpu,
        )

    try:
        scene_options = (
            ["--scene", "/assets/" + args.scene.as_posix()] if args.scene else []
        )
        stage(
            "head_rgbd_observation",
            lambda: isaac(
                "simready.simulation.grasp_video",
                args.assets,
                ["--observe", "--out", "/output/observation", *scene_options],
            ),
        )
        stage(
            "depth_proposal",
            lambda: command(
                [
                    sys.executable,
                    "-m",
                    "simready.ingestion.cup_localization",
                    "propose",
                    "--observation",
                    artifacts / "observation",
                    "--out",
                    artifacts / "roi",
                ]
            ),
        )
        stage(
            "grounding_dino_sam",
            lambda: command(
                [
                    args.model_python,
                    "-m",
                    "simready.ingestion.grounded_segmentation",
                    "--image",
                    artifacts / "roi/image.png",
                    "--output-dir",
                    artifacts / "segmentation",
                    "--categories",
                    "coffee_cup",
                    "--local-files-only",
                    "--min-area-px",
                    "64",
                ]
            ),
        )
        stage(
            "model_registration",
            lambda: command(
                [
                    sys.executable,
                    "-m",
                    "simready.ingestion.cup_localization",
                    "fit",
                    "--observation",
                    artifacts / "roi",
                    "--segmentation",
                    artifacts / "segmentation",
                    "--model",
                    args.assets / "cup.usdc",
                    "--out",
                    artifacts / "localization",
                ]
            ),
        )
        stage(
            "cartesian_planning",
            lambda: command(
                [
                    sys.executable,
                    repo / "examples/starbucks_grasp/prepare.py",
                    "--robot",
                    args.assets / "robot.usdc",
                    "--cup",
                    args.assets / "cup.usdc",
                    "--out",
                    planned,
                    "--localization",
                    artifacts / "localization/localization.json",
                    *(
                        ["--layout", args.assets / "task_layout.json"]
                        if (args.assets / "task_layout.json").exists()
                        else []
                    ),
                ]
            ),
        )
        if args.scene:
            shutil.copytree((args.assets / args.scene).parent, planned / "scene")
        final_scene = (
            ["--scene", "/assets/scene/" + args.scene.name] if args.scene else []
        )
        stage(
            "physx_execution",
            lambda: isaac(
                "simready.simulation.cup_grasp",
                planned,
                ["--out", "/output/physics", *final_scene],
            ),
        )
        stage(
            "independent_audit",
            lambda: command(
                [
                    sys.executable,
                    "-m",
                    "simready.evaluation.cup_transfer",
                    "--assets",
                    planned,
                    "--rollout",
                    artifacts / "physics",
                    "--out",
                    artifacts / "audit.json",
                ]
            ),
        )
        if args.render:
            stage(
                "measured_replay_render",
                lambda: isaac(
                    "simready.simulation.grasp_video",
                    planned,
                    [
                        "--rollout",
                        "/output/physics",
                        "--out",
                        "/output/video",
                        "--width",
                        str(args.width),
                        *final_scene,
                    ],
                ),
            )
            stage(
                "encode_video",
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
                        args.out / "G2_two_table_transfer.mp4",
                    ]
                ),
            )
        report["status"] = "PASS"
    except BaseException as error:
        report.update(status="FAILED", error=str(error))
        raise
    finally:
        report["wall_s"] = time.perf_counter() - started
        report["llm_usage"] = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "scope": "executed pipeline only; excludes development conversation",
        }
        (args.out / "timing.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
