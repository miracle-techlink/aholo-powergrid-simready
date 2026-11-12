#!/usr/bin/env python3
"""Compose a 3DGS USD splat + SimReady collision assets + robot into a final Isaac Sim scene.

Layer structure (bottom -> top):
  1. gsplat_layer.usda   — visual background (ParticleField3DGaussianSplat, no collision)
  2. simready_assets     — collision-enabled USD refs (cabinets, buttons, etc.)
  3. robot_layer         — Lite3+Z1 (optional)
  4. stage.usda          — root stage composing all above
"""

import os
import sys
import argparse
import math


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gsplat_usd", help="3DGS USD file (from usd-convert-gsplat)")
    ap.add_argument("output", help="Output scene USD path")
    ap.add_argument("--simready", help="Path to SimReady asset directory")
    ap.add_argument("--robot", default=None, help="Robot USD path (default: Lite3+Z1)")
    ap.add_argument("--robot-pos", nargs=3, type=float, default=[0, 0, 0.5])
    args = ap.parse_args()

    from pxr import Usd, UsdGeom, Gf, Sdf, UsdPhysics

    stage = Usd.Stage.CreateNew(args.output)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    # --- Layer 1: 3DGS visual background ---
    gsplat_path = os.path.abspath(args.gsplat_usd)
    gsplat_prim = stage.DefinePrim("/World/GaussianSplat", "Xform")
    gsplat_prim.GetReferences().AddReference(gsplat_path)
    print(f"[compose] added 3DGS layer: {gsplat_path}")

    # --- Layer 2: SimReady collision assets ---
    if args.simready and os.path.isdir(args.simready):
        simready_root = os.path.abspath(args.simready)
        # Look for USD files in SimReady directory
        usd_files = []
        for root, dirs, files in os.walk(simready_root):
            for f in files:
                if f.endswith((".usd", ".usda", ".usdc")):
                    usd_files.append(os.path.join(root, f))
        print(f"[compose] found {len(usd_files)} USD files in {simready_root}")
        # Add first few as collision assets (limit for demo)
        for i, usd_file in enumerate(usd_files[:20]):
            name = os.path.splitext(os.path.basename(usd_file))[0]
            prim_path = f"/World/Assets/{name}_{i:03d}"
            prim = stage.DefinePrim(prim_path, "Xform")
            prim.GetReferences().AddReference(usd_file)
            # Add collision API if not present
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI.Apply(prim)
            # Offset to avoid overlap with gsplat origin
            x = (i % 5) * 2.0 - 4.0
            y = (i // 5) * 2.0 - 2.0
            UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(x, y, 0.0))
        print(f"[compose] added {min(len(usd_files), 20)} SimReady assets")

    # --- Layer 3: Robot (Lite3 + Z1) ---
    robot_path = args.robot
    if robot_path is None:
        # Default: use stage_dual_dog.usda which already has Lite3+Z1
        robot_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "stage_dual_dog.usda"
        )
    if os.path.isfile(robot_path):
        robot_prim = stage.DefinePrim("/World/Robot", "Xform")
        robot_prim.GetReferences().AddReference(os.path.abspath(robot_path))
        xf = UsdGeom.Xformable(robot_prim)
        xf.ClearXformOpOrder()
        xf.AddTranslateOp().Set(Gf.Vec3d(*args.robot_pos))
        print(f"[compose] added robot: {robot_path}")

    # --- Physics scene ---
    phys_scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    phys_scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    phys_scene.CreateGravityMagnitudeAttr(9.81)

    # --- Ground plane ---
    ground = stage.DefinePrim("/World/Ground", "Plane")
    UsdGeom.Xformable(ground).AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
    UsdPhysics.CollisionAPI.Apply(ground)

    # --- Dome light ---
    from pxr import UsdLux

    dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
    dome.CreateIntensityAttr(1000.0)

    stage.Save()
    print(f"[compose] saved scene -> {args.output}")
    print(f"[compose] open in Isaac Sim: {args.output}")


if __name__ == "__main__":
    main()
