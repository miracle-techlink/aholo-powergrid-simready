"""Render measured cup-grasp poses; never invent or constrain object motion."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--rollout", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--observe", action="store_true")
    ap.add_argument("--scene", type=Path)
    ap.add_argument("--width", type=int, choices=[1280, 1920], default=1280)
    ap.add_argument("--subframes", type=int, choices=range(1, 33), default=6)
    args = ap.parse_args()
    if not args.observe and not args.rollout:
        ap.error("--rollout is required for measured replay")
    width, height = args.width, args.width * 9 // 16
    args.out.mkdir(parents=True, exist_ok=False)
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True},
        experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
    )
    status = 2
    try:
        import carb.settings
        import numpy as np
        import omni.replicator.core as rep
        import omni.usd
        from pxr import Gf, UsdGeom, UsdLux, UsdPhysics

        omni.usd.get_context().open_stage(str(args.assets / "task.usda"))
        stage = omni.usd.get_context().get_stage()
        if args.rollout:
            result = json.loads((args.rollout / "result.json").read_text())
            data = np.load(args.rollout / "rollout.npz", allow_pickle=False)
        else:
            bodies = [
                p
                for p in stage.Traverse()
                if p.HasAPI(UsdPhysics.RigidBodyAPI)
                and (
                    str(p.GetPath()).startswith("/World/G2/")
                    or str(p.GetPath()) == "/World/Cup"
                )
            ]
            result = {
                "status": "NOT_EXECUTED",
                "body_names": [p.GetName() for p in bodies],
            }
            poses = []
            cache = UsdGeom.XformCache()
            for p in bodies:
                matrix = cache.GetLocalToWorldTransform(p)
                quat = matrix.ExtractRotationQuat()
                poses.append(
                    [*matrix.ExtractTranslation(), *quat.GetImaginary(), quat.GetReal()]
                )
            data = {
                "poses": np.array([poses]),
                "dt": 0.0,
                "phases": np.array(["OBSERVE"]),
            }
        for p in stage.Traverse():
            if p.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(p).CreateRigidBodyEnabledAttr(False)
            if p.IsA(UsdPhysics.Joint):
                UsdPhysics.Joint(p).CreateJointEnabledAttr(False)
        if args.scene:
            stage.DefinePrim("/World/Background", "Xform").GetReferences().AddReference(
                str(args.scene)
            )
            UsdGeom.Imageable(stage.GetPrimAtPath("/World/Ground")).MakeInvisible()
        from simready.simulation.manipulation_visuals import (
            refine_tables,
            repair_robot_uv_interpolation,
        )

        texture = args.scene.parent / "table_wood.png" if args.scene else None
        contract_path = (
            args.scene.parent / "scene_contract.json" if args.scene else None
        )
        contract = (
            json.loads(contract_path.read_text())
            if contract_path and contract_path.exists()
            else {}
        )
        if contract.get("render_table_proxies") is False:
            for name in ["Table", "TableB"]:
                for suffix in ["", "Stem", "Foot"]:
                    UsdGeom.Imageable(
                        stage.GetPrimAtPath("/World/" + name + suffix)
                    ).MakeInvisible()
        else:
            refine_tables(stage, texture if texture and texture.exists() else None)
        uv_repairs = repair_robot_uv_interpolation(stage)
        ops = []
        parent_inverse = []
        for name in result["body_names"]:
            path = "/World/Cup" if name == "Cup" else "/World/G2/" + name
            prim = stage.GetPrimAtPath(path)
            xf = UsdGeom.Xformable(prim)
            xf.ClearXformOpOrder()
            ops.append(xf.AddTransformOp(opSuffix="measuredReplay"))
            parent_inverse.append(
                UsdGeom.XformCache()
                .GetLocalToWorldTransform(prim.GetParent())
                .GetInverse()
            )
        dome = UsdLux.DomeLight.Define(stage, "/World/Lighting/Dome")
        dome.CreateIntensityAttr(650)
        key = UsdLux.DistantLight.Define(stage, "/World/Lighting/Key")
        key.CreateIntensityAttr(1100)
        key.CreateAngleAttr(15)
        key.AddRotateXYZOp().Set(Gf.Vec3f(-30, 20, -40))
        if args.scene:
            fill = UsdLux.RectLight.Define(stage, "/World/Lighting/Softbox")
            fill.CreateWidthAttr(2.5)
            fill.CreateHeightAttr(2.5)
            fill.CreateIntensityAttr(700.0)
            fill.CreateEnableColorTemperatureAttr(True)
            fill.CreateColorTemperatureAttr(4800.0)
            fill.AddTranslateOp().Set(Gf.Vec3d(0.3, -0.3, 2.65))
        cam = UsdGeom.Camera.Define(stage, "/World/Camera")
        cam.CreateFocalLengthAttr(28)
        cam.CreateHorizontalApertureAttr(36)
        cam.CreateVerticalApertureAttr(20.25)
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100))
        camop = cam.AddTransformOp()
        settings = carb.settings.get_settings()
        settings.set_bool("/app/player/playSimulations", False)
        settings.set_string("/rtx/rendermode", "RealTimePathTracing")
        settings.set_int("/rtx/post/aa/op", 4)
        settings.set_bool("/rtx-transient/dldenoiser/enabled", True)
        settings.set_bool("/rtx/rtpt/gaussian/accumulatedDepth/enabled", True)
        settings.set_bool("/rtx/rtpt/gaussian/accumulatedAlbedo/enabled", True)
        settings.set_int("/rtx/rtpt/gaussian/maxGaussiansToAccumulate", 48)
        rp = rep.create.render_product(str(cam.GetPath()), (width, height))
        # Synchronous readback prevents a cold shader cache from silently
        # advancing all replay poses before the first actual image exists.
        from PIL import Image

        rgb_annotation = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb_annotation.attach([rp])
        (args.out / "frames").mkdir()
        total = len(data["poses"])
        dt = float(data["dt"])
        samples = (
            [0]
            if args.observe
            else (
                [
                    0,
                    round(3.0 / dt),
                    round(6.5 / dt),
                    int(total * 0.62),
                    int(total * 0.82),
                    total - 1,
                ]
                if args.preview
                else list(range(0, total, max(1, round(1.0 / (30.0 * dt)))))
            )
        )
        if args.observe:
            depth_annotation = rep.AnnotatorRegistry.get_annotator(
                "distance_to_image_plane"
            )
            depth_annotation.attach([rp])
        mappings = []
        for num, step in enumerate(samples):
            for op, pinv, pose in zip(
                ops, parent_inverse, data["poses"][step], strict=True
            ):
                t = Gf.Matrix4d().SetRotate(
                    Gf.Quatd(float(pose[6]), Gf.Vec3d(*map(float, pose[3:6])))
                )
                t.SetTranslateOnly(Gf.Vec3d(*map(float, pose[:3])))
                op.Set(t * pinv)
            angle = 0.2 * np.sin(num / max(len(samples) - 1, 1) * np.pi)
            eye = (
                Gf.Vec3d(-0.15 + 0.3 * angle, -2.15, 1.85)
                if args.scene
                else Gf.Vec3d(1.85 + 0.15 * angle, -1.8, 1.85)
            )
            target = Gf.Vec3d(0.30, -0.05, 1.0)
            if args.scene:
                # Smoothly approach the hands after the establishing shot,
                # then follow the measured cup without changing its trajectory.
                seconds = step * dt
                approach = np.clip((seconds - 3.0) / 4.0, 0.0, 1.0)
                approach = approach * approach * (3 - 2 * approach)
                eye = Gf.Vec3d(
                    -0.15 + 0.45 * approach,
                    -2.15 + 0.30 * approach,
                    1.85 - 0.25 * approach,
                )
                target = Gf.Vec3d(0.30 + 0.15 * approach, -0.05 - 0.25 * approach, 1.0)
                cam.GetFocalLengthAttr().Set(23.0 + 8 * approach)
            if args.observe:
                head = data["poses"][step, result["body_names"].index("head_link3"), :3]
                eye = Gf.Vec3d(*map(float, head + np.array([0.13, 0.04, 0.02])))
                target = Gf.Vec3d(0.65, -0.13, 0.85)
                cam.GetFocalLengthAttr().Set(18.0)
            camop.Set(
                Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0, 0, 1)).GetInverse()
            )
            if num == 0:
                ready_start = time.monotonic()
                ready = 0
                while ready < 3:
                    if time.monotonic() - ready_start > 600:
                        raise TimeoutError("RGB capture did not become ready")
                    app.update()
                    rep.orchestrator.step(
                        rt_subframes=args.subframes,
                        delta_time=1 / 30,
                        pause_timeline=True,
                        wait_for_render=True,
                    )
                    rep.orchestrator.wait_until_complete()
                    rgb = np.asarray(rgb_annotation.get_data())
                    if (
                        rgb.shape[:2] == (height, width)
                        and rgb.ndim == 3
                        and rgb[..., :3].std() > 1
                    ):
                        ready += 1
                print("RGB_CAPTURE_READY", time.monotonic() - ready_start, flush=True)
            rep.orchestrator.step(
                rt_subframes=12 if args.preview else args.subframes,
                delta_time=1 / 30,
                pause_timeline=True,
                wait_for_render=True,
            )
            rep.orchestrator.wait_until_complete()
            rgb = np.asarray(rgb_annotation.get_data()).copy()
            if rgb.shape[:2] != (height, width) or rgb.ndim != 3:
                raise RuntimeError(
                    f"Invalid actual RGB at replay frame {num}: {rgb.shape}"
                )
            Image.fromarray(rgb[..., :3]).save(
                args.out / "frames" / f"rgb_{num:04d}.png"
            )
            mappings.append(
                {
                    "frame": num,
                    "physics_step": int(step),
                    "time_s": step * dt,
                    "phase": str(data["phases"][step]),
                }
            )
            print("RENDER", num, step, flush=True)
        rep.orchestrator.wait_until_complete()
        if args.observe:
            from PIL import Image

            from simready.core.artifacts import digest

            for _ in range(3):
                rep.orchestrator.step(
                    rt_subframes=16, delta_time=0.0, pause_timeline=True
                )
            rep.orchestrator.wait_until_complete()
            rgb = np.asarray(rgb_annotation.get_data()).copy()
            depth = (
                np.asarray(depth_annotation.get_data()).copy().reshape(height, width)
            )
            Image.fromarray(rgb[:, :, :3]).save(args.out / "image.png")
            np.save(args.out / "depth.npy", depth)
            matrix = np.array(camop.Get()).T @ np.diag([1.0, -1.0, -1.0, 1.0])
            fx = 18.0 / 36.0 * width
            calibration = {
                "schema_version": 1,
                "camera_convention": "opencv_c2w",
                "image_size": [width, height],
                "intrinsics": [
                    [fx, 0, (width - 1) / 2],
                    [0, fx, (height - 1) / 2],
                    [0, 0, 1],
                ],
                "camera_to_world": matrix.tolist(),
                "image_sha256": digest(args.out / "image.png"),
                "depth_sha256": digest(args.out / "depth.npy"),
                "evidence": "mounted synthetic head RGB-D camera; calibrated renderer, not a real camera",
            }
            (args.out / "calibration.json").write_text(
                json.dumps(calibration, indent=2) + "\n"
            )
        (args.out / "render.json").write_text(
            json.dumps(
                {
                    "mode": "measured physics replay"
                    if args.rollout
                    else "synthetic initial observation",
                    "physics_result": result["status"],
                    "frames": mappings,
                    "appearance_only_uv_repairs": uv_repairs,
                    "background": str(args.scene),
                    "rt_subframes": 12 if args.preview else args.subframes,
                    "scene_contract": contract,
                    "visible_gaussian_prims": [
                        str(p.GetPath())
                        for p in stage.Traverse()
                        if p.GetTypeName() == "ParticleField3DGaussianSplat"
                        and UsdGeom.Imageable(p).ComputeVisibility() != "invisible"
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        status = 0
    finally:
        app.close(exit_code=status)


if __name__ == "__main__":
    main()
