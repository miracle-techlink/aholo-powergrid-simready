"""One entry for source-preserving repair of the two registered cafe cases."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=["grasp", "tour"], required=True)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--sfm-root", required=True, type=Path)
    ap.add_argument("--source-trajectory", type=Path)
    ap.add_argument("--model-python", default=sys.executable)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()
    if args.case == "tour" and not args.source_trajectory:
        ap.error("--case tour requires the original --source-trajectory")
    args.out.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent

    def command(values):
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(here.parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
        )
        subprocess.run(list(map(str, values)), check=True, env=env)

    command(
        [
            sys.executable,
            here / "build.py",
            "--case",
            args.case,
            "--input",
            args.input,
            "--sfm-root",
            args.sfm_root,
            "--out",
            args.out / "base",
        ]
    )
    masks = args.out / "person_masks"
    for name in [
        "frame_001001",
        "frame_001106",
        "frame_001129",
        "frame_001166",
        "frame_001241",
    ]:
        command(
            [
                args.model_python,
                "-m",
                "simready.ingestion.grounded_segmentation",
                "--image",
                args.sfm_root / "full_sfm/images" / (name + ".png"),
                "--categories",
                "person",
                "--local-files-only",
                "--min-area-px",
                "1024",
                "--output-dir",
                masks / name,
            ]
        )
    assets = args.out / "assets"
    command(
        [
            sys.executable,
            here / "clean_transients.py",
            "--assets",
            args.out / "base",
            "--masks",
            masks,
            "--sfm-root",
            args.sfm_root,
            "--out",
            assets,
        ]
    )
    if args.case == "tour":
        bundle = json.loads((args.input / "bundle.json").read_text())
        command(
            [
                sys.executable,
                here / "prepare_nav_pose.py",
                "--robot",
                args.input / bundle["robot_usd"],
                "--out",
                args.out / "nav_pose",
            ]
        )
        for name in ["robot_nav.usdc", "height_profile.json", "pose.json"]:
            shutil.copy2(args.out / "nav_pose" / name, assets / name)
        shutil.copy2(args.source_trajectory, assets / "source_trajectory.json")
    if args.run:
        command(
            [
                sys.executable,
                here / "run.py",
                "--case",
                args.case,
                "--assets",
                assets,
                "--out",
                args.out / "delivery",
                "--model-python",
                args.model_python,
                *(["--render"] if args.render else []),
            ]
        )
    print(json.dumps({"assets": str(assets), "status": "PREPARED"}), flush=True)


if __name__ == "__main__":
    main()
