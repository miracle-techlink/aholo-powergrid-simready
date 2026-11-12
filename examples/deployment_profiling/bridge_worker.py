"""Adapt a trusted external scene worker to isolated task output directories."""

import argparse
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["physics", "audit", "render"], required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--mission-result", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    command = [args.python, str(args.script.resolve())]
    if args.kind == "physics":
        if args.config is None:
            parser.error("Physics requires --config")
        command += [
            "--config",
            str(args.config.resolve()),
            "--out",
            str(args.out / "mission"),
            "--max-seconds",
            "65",
        ]
    else:
        if args.mission_result is None:
            parser.error("Audit/render requires --mission-result")
        mission = args.mission_result.resolve().parent
        if args.kind == "audit":
            # The old audit writes inside its input directory. Copy the input
            # first so verification never mutates an upstream task artifact.
            isolated = args.out / "mission"
            shutil.copytree(mission, isolated)
            command += ["--run", str(isolated)]
        else:
            command += [
                "--run",
                str(mission),
                "--out",
                str(args.out / "render"),
                "--width",
                "1920",
                "--height",
                "1080",
            ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
