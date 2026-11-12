#!/usr/bin/env python3
"""Standard pipeline: 3DGS reconstruction -> USD -> Isaac Sim scene.

Usage:
  python pipeline_3dgs_to_isaac.py --input 3dgs_v1/model.npz --output scene_with_gsplat.usda
  python pipeline_3dgs_to_isaac.py --input 3dgs_v1/model.npz --ply-only  # stop at PLY
  python pipeline_3dgs_to_isaac.py --ply model.ply --output scene.usda  # skip npz step

Stages:
  1. npz -> PLY   (npz_to_ply.py)
  2. PLY -> USD   (usd-convert-gsplat, bundled with Isaac 6.1)
  3. USD -> Isaac Sim stage (compose with SimReady assets + robot)
"""

import os
import sys
import argparse
import subprocess
import shutil


def find_converter():
    value = os.environ.get("SIMREADY_GSPLAT_CONVERTER") or shutil.which(
        "usd-convert-gsplat"
    )
    if not value or not os.path.isfile(value):
        raise FileNotFoundError(
            "Set SIMREADY_GSPLAT_CONVERTER to the installed usd-convert-gsplat executable"
        )
    return value


def run_npz_to_ply(npz_path: str, ply_path: str):
    """Stage 1: npz -> PLY."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "npz_to_ply.py")
    cmd = [sys.executable, script, npz_path, ply_path]
    print(f"[pipeline] stage 1: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"STDERR: {r.stderr}", file=sys.stderr)
        raise RuntimeError(f"npz_to_ply failed: {r.returncode}")
    print(r.stdout)


def run_ply_to_usd(
    ply_path: str,
    usd_path: str,
    up_axis: str = "Z",
    rotate_x: float = 0.0,
    rotate_y: float = 0.0,
    rotate_z: float = 0.0,
):
    """Stage 2: PLY -> USD via usd-convert-gsplat."""
    env = os.environ.copy()
    converter = find_converter()
    cmd = [
        converter,
        "-i",
        ply_path,
        "-o",
        usd_path,
        "--up-axis",
        up_axis,
    ]
    if rotate_x:
        cmd += ["--rotate-x", str(rotate_x)]
    if rotate_y:
        cmd += ["--rotate-y", str(rotate_y)]
    if rotate_z:
        cmd += ["--rotate-z", str(rotate_z)]
    print(f"[pipeline] stage 2: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(f"STDOUT: {r.stdout}", file=sys.stderr)
        print(f"STDERR: {r.stderr}", file=sys.stderr)
        raise RuntimeError(f"usd-convert-gsplat failed: {r.returncode}")
    print(r.stdout)


def run_compose_stage(gsplat_usd: str, output_usd: str, simready_dir: str = None):
    """Stage 3: compose gsplat USD + SimReady assets into final Isaac Sim scene."""
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "compose_isaac_scene.py"
    )
    cmd = [sys.executable, script, gsplat_usd, output_usd]
    if simready_dir:
        cmd += ["--simready", simready_dir]
    print(f"[pipeline] stage 3: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"STDERR: {r.stderr}", file=sys.stderr)
        raise RuntimeError(f"compose_isaac_scene failed: {r.returncode}")
    print(r.stdout)


def main():
    ap = argparse.ArgumentParser(description="3DGS -> USD -> Isaac Sim pipeline")
    ap.add_argument("--input", "-i", help="Input model.npz (or .ply with --ply)")
    ap.add_argument("--ply", help="Input is already PLY, skip stage 1")
    ap.add_argument("--output", "-o", help="Output USD path")
    ap.add_argument("--ply-only", action="store_true", help="Stop after PLY generation")
    ap.add_argument("--usd-only", action="store_true", help="Stop after USD conversion")
    ap.add_argument(
        "--up-axis",
        default="Z",
        choices=["Y", "Z"],
        help="Target up-axis (default Z for Isaac)",
    )
    ap.add_argument("--rotate-x", type=float, default=0.0)
    ap.add_argument("--rotate-y", type=float, default=0.0)
    ap.add_argument("--rotate-z", type=float, default=0.0)
    ap.add_argument(
        "--simready", help="Path to SimReady asset directory for scene composition"
    )
    ap.add_argument(
        "--workdir", default=None, help="Working directory for intermediate files"
    )
    args = ap.parse_args()
    if not args.output and not args.ply_only:
        ap.error("--output required except for --ply-only")

    workdir = args.workdir or os.path.dirname(
        os.path.abspath(args.output or args.input or args.ply or ".")
    )
    os.makedirs(workdir, exist_ok=True)

    # Stage 1: npz -> PLY
    if args.ply:
        ply_path = args.ply
        print(f"[pipeline] skipping stage 1, using PLY: {ply_path}")
    else:
        if not args.input:
            print("ERROR: --input or --ply required", file=sys.stderr)
            sys.exit(1)
        ply_path = os.path.join(
            workdir, os.path.splitext(os.path.basename(args.input))[0] + ".ply"
        )
        run_npz_to_ply(args.input, ply_path)

    if args.ply_only:
        print(f"[pipeline] PLY-only mode. Output: {ply_path}")
        return

    # Stage 2: PLY -> USD
    usd_path = args.output
    run_ply_to_usd(
        ply_path, usd_path, args.up_axis, args.rotate_x, args.rotate_y, args.rotate_z
    )

    if args.usd_only:
        print(f"[pipeline] USD-only mode. Output: {usd_path}")
        return

    # Stage 3: compose Isaac Sim scene
    final_path = os.path.splitext(usd_path)[0] + "_scene.usda"
    run_compose_stage(usd_path, final_path, args.simready)
    print(f"[pipeline] DONE. Final scene: {final_path}")


if __name__ == "__main__":
    main()
