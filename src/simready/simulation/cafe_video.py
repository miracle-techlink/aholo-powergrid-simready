#!/usr/bin/env python3
"""Render a Starbucks scene survey; subsequent tour uses the same stage/camera.

All output lives in a new run directory. Source reconstructions are read-only.
"""

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("PYTORCH_JIT", "0")
os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")


def atomic_json(path, payload):
    """Keep the previous complete frame ledger available across interruptions."""
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(payload, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def patrol_pose(time_s, duration_s=30.0):
    """Reviewed aisle route: return along the same clear lane, turn at its ends."""
    t = max(0.0, min(30.0, time_s * 30.0 / duration_s))

    def ease(u):
        u = max(0.0, min(1.0, u))
        return u * u * u * (10 + u * (-15 + 6 * u))

    if t < 1:
        return 3.5, -1.6, 180.0
    if t < 13:
        return 3.5 - 4.8 * ease((t - 1) / 12), -1.6, 180.0
    if t < 16:
        return -1.3, -1.6, 180 + 180 * ease((t - 13) / 3)
    if t < 28:
        return -1.3 + 4.8 * ease((t - 16) / 12), -1.6, 360.0
    return 3.5, -1.6, 360 + 180 * ease((t - 28) / 2)


def route_pose(frame, frames, fps, route):
    """Pure route evaluation shared by capture and resume-state reconstruction."""
    if route == "island":
        a = 2 * math.pi * frame / max(1, frames - 1)
        x, y = 0.7 + 2.25 * math.cos(a), -0.3 + 1.35 * math.sin(a)
        dx, dy = -2.25 * math.sin(a), 1.35 * math.cos(a)
        return x, y, math.degrees(math.atan2(dy, dx))
    return patrol_pose(frame / fps, frames / fps)


def advance_wheel_kinematics(wheel, loc, x, y, yaw):
    """Accumulate exactly the same wheel phase across uninterrupted/resumed runs."""
    ang = math.radians(yaw)
    c, s = math.cos(ang), math.sin(ang)
    wx, wy = x + c * loc[0] - s * loc[1], y + s * loc[0] + c * loc[1]
    if wheel["prev"] is not None:
        vx, vy = wx - wheel["prev"][0], wy - wheel["prev"][1]
        ds = math.hypot(vx, vy)
        wheel["distance"] += ds
        if ds > 1e-7:
            wheel["steer"] = math.degrees(math.atan2(-s * vx + c * vy, c * vx + s * vy))
    wheel["prev"] = (wx, wy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", action="store_true")
    ap.add_argument("--tour", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--color-fix", action="store_true")
    ap.add_argument(
        "--renderer",
        default="RealTimePathTracing",
        choices=["RealTimePathTracing", "PathTracing"],
    )
    ap.add_argument("--route", choices=["island", "aisle"], default="aisle")
    ap.add_argument("--patrol-min-x", type=float, default=-1.3)
    ap.add_argument("--patrol-max-x", type=float, default=3.5)
    ap.add_argument("--patrol-y", type=float, default=-1.6)
    ap.add_argument("--encode", action="store_true")
    ap.add_argument(
        "--stream-capture",
        action="store_true",
        help="Legacy BasicWriter capture with one-frame compensation; prefer synchronous capture for delivery",
    )
    ap.add_argument(
        "--synchronous-capture",
        action="store_true",
        help="Copy each completed RGB annotation before advancing the next pose",
    )
    ap.add_argument("--quality", default="high", choices=["high", "gen"])
    ap.add_argument(
        "--scene",
        type=Path,
        required=True,
        help="Explicit composed reconstruction candidate; source remains read-only",
    )
    ap.add_argument(
        "--settings-json",
        type=Path,
        help="Explicit RTX overrides for the trained scene color contract",
    )
    ap.add_argument(
        "--camera-json",
        type=Path,
        help="Fixed calibrated camera: resolution, K, camera_to_world_cv; preserves photographed viewpoint",
    )
    ap.add_argument(
        "--replay-trajectory",
        type=Path,
        help="Replay recorded robot and camera poses exactly, preserving original frame timing",
    )
    ap.add_argument("--encode-threads", type=int, default=2)
    ap.add_argument("--sun-intensity", type=float, default=1800.0)
    ap.add_argument("--dome-intensity", type=float, default=200.0)
    ap.add_argument("--camera-focal-length", type=float, default=18.0)
    ap.add_argument("--res", nargs=2, type=int, default=[1280, 720])
    ap.add_argument("--subframes", type=int, default=32)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--robot", type=Path, help="Prepared kinematic G2 USD pose")
    ap.add_argument(
        "--color-layer", type=Path, help="Optional SH correction override USD"
    )
    args = ap.parse_args()
    if args.synchronous_capture and (args.stream_capture or not args.tour):
        ap.error("--synchronous-capture requires --tour and excludes --stream-capture")
    if args.survey:
        ap.error("Use separate runs with explicit --scene for each reconstruction")
    if args.tour and not args.robot:
        ap.error("--tour requires --robot")
    if args.color_fix and not args.color_layer:
        ap.error("--color-fix requires --color-layer")
    fixed_camera = (
        json.loads(args.camera_json.read_text()) if args.camera_json else None
    )
    replay = None
    if args.replay_trajectory:
        if fixed_camera:
            ap.error("--camera-json and --replay-trajectory are mutually exclusive")
        replay_rows = json.loads(args.replay_trajectory.read_text())
        replay = {row["frame"]: row for row in replay_rows}
        if len(replay) != len(replay_rows) or any(
            i not in replay for i in range(args.frames)
        ):
            ap.error("Replay must contain each requested control frame exactly once")
        if any(
            abs(replay[i]["time_s"] - i / args.fps) > 1e-8 for i in range(args.frames)
        ):
            ap.error("Replay timing does not match requested fps")
    if fixed_camera:
        fixed_pose = np.asarray(fixed_camera["camera_to_world_cv"], dtype=float)
        fixed_k = np.asarray(fixed_camera["K"], dtype=float)
        if (
            fixed_camera["resolution"] != args.res
            or fixed_pose.shape != (4, 4)
            or fixed_k.shape != (3, 3)
            or not np.isfinite(fixed_pose).all()
            or not np.isfinite(fixed_k).all()
            or not np.allclose(fixed_pose[3], [0, 0, 0, 1])
            or not np.allclose(
                fixed_pose[:3, :3].T @ fixed_pose[:3, :3], np.eye(3), atol=1e-6
            )
            or not np.isclose(np.linalg.det(fixed_pose[:3, :3]), 1)
            or fixed_k[0, 0] <= 0
            or fixed_k[1, 1] <= 0
        ):
            ap.error("Invalid fixed camera or resolution mismatch")
    overrides = json.loads(args.settings_json.read_text()) if args.settings_json else {}
    if not isinstance(overrides, dict) or any(
        not key.startswith(("/rtx/", "/rtx-transient/")) for key in overrides
    ):
        ap.error("--settings-json must contain /rtx/ or /rtx-transient/ setting paths")
    if args.encode_threads < 1:
        ap.error("--encode-threads must be positive")
    if args.patrol_min_x >= args.patrol_max_x:
        ap.error("Patrol minimum must be below maximum")

    def configured_route(frame):
        if replay is not None:
            row = replay[frame]
            return row["robot_position"][0], row["robot_position"][1], row["yaw"]
        x, y, yaw = route_pose(frame, args.frames, args.fps, args.route)
        if args.route == "aisle":
            x = args.patrol_min_x + (x + 1.3) / 4.8 * (
                args.patrol_max_x - args.patrol_min_x
            )
            y = args.patrol_y
        return x, y, yaw

    args.out = args.out.resolve()
    if args.stream_capture and not args.tour:
        ap.error("--stream-capture requires --tour")
    if not 0 <= args.offset < args.frames:
        ap.error("--offset must be in [0, --frames)")
    if args.limit is not None and args.limit <= 0:
        ap.error("--limit must be positive")
    if args.preview and args.offset:
        ap.error("--preview cannot be combined with --offset")
    args.out.mkdir(parents=True, exist_ok=True)
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True},
        experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
    )
    import carb
    import omni.kit.app
    import omni.usd
    from pxr import Gf, UsdGeom, UsdLux

    em = omni.kit.app.get_app().get_extension_manager()
    em.set_extension_enabled_immediate("omni.replicator.core", True)
    import omni.replicator.core as rep

    if args.stream_capture or args.synchronous_capture:
        from omni.kit.viewport.window import get_viewport_window_instances

        for window in get_viewport_window_instances(None):
            window.viewport_api.updates_enabled = False
    stage = omni.usd.get_context().get_stage()
    stage.SetMetadata("upAxis", "Z")
    stage.SetMetadata("metersPerUnit", 1.0)
    settings = carb.settings.get_settings()
    # Editor guides can leak into a fresh Replicator render product.
    # They are not scene geometry and must not appear in delivered footage.
    settings.set("/app/viewport/grid/enabled", False)
    settings.set("/rtx/post/dlss/execMode", 2)
    settings.set("/rtx/post/aa/op", 4)  # DLAA: native output resolution, no upscale.
    settings.set("/rtx/dldenoiser/enabled", True)
    settings.set("/rtx/dldenoiser/responsiveDenoising", True)
    settings.set("/rtx/rtpt/gaussian/accumulatedDepth/enabled", True)
    settings.set("/rtx/rtpt/gaussian/accumulatedAlbedo/enabled", True)
    settings.set("/rtx/rtpt/gaussian/maxGaussiansToAccumulate", 48)
    settings.set("/rtx/rendermode", args.renderer)
    settings.set("/rtx/pathtracing/spp", 4)
    settings.set("/rtx/pathtracing/totalSpp", 256)
    settings.set("/rtx/pathtracing/optixDenoiser/enabled", True)
    settings.set("/rtx/rtpt/denoiser/enabled", True)
    for key, value in overrides.items():
        settings.set(key, value)
    print("TOUR_SETUP renderer", args.renderer, flush=True)
    cam = UsdGeom.Camera.Define(stage, "/World/TourCamera")
    cam.CreateFocalLengthAttr(args.camera_focal_length)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.03, 100.0))
    cam_op = UsdGeom.Xformable(cam).AddTransformOp()

    def look(eye, target):
        view = Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1)
        )
        cam_op.Set(view.GetInverse())

    sun = UsdLux.DistantLight.Define(stage, "/World/TourSun")
    sun.CreateIntensityAttr(args.sun_intensity)
    sun.CreateAngleAttr(12.0)
    UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-45, 20, 30))
    dome = UsdLux.DomeLight.Define(stage, "/World/TourFill")
    dome.CreateIntensityAttr(args.dome_intensity)
    print("TOUR_SETUP render_product", args.res, flush=True)
    rp = rep.create.render_product(str(cam.GetPath()), tuple(args.res))
    writer = None
    capture_dir = args.out / ("raw_frames" if args.stream_capture else "frames")
    if any(capture_dir.glob("rgb_*.png")):
        raise FileExistsError(f"Refusing to overwrite captured frames in {capture_dir}")
    if args.synchronous_capture:
        rgb_annotation = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb_annotation.attach([rp])
        capture_dir.mkdir(parents=True, exist_ok=True)
    else:
        writer = rep.WriterRegistry.get("BasicWriter")
        writer.initialize(output_dir=str(capture_dir), rgb=True)
        writer.attach([rp])
    manifest = []
    warmup_count = 0
    for quality in ["high", "gen"] if args.survey else [args.quality]:
        asset = args.scene.resolve()
        stage.GetRootLayer().subLayerPaths = [str(asset)]
        if quality == "high" and args.color_fix:
            stage.GetRootLayer().subLayerPaths.insert(
                0, str(args.color_layer.resolve())
            )
        floor = -0.55 if quality == "high" else -1.24
        print("TOUR_SETUP scene", quality, flush=True)
        if args.tour:
            rig = UsdGeom.Xform.Define(stage, "/World/TourRig")
            rig_t = rig.AddTranslateOp()
            rig_r = rig.AddRotateZOp()
            from simready.simulation.robot_interface import (
                RootPoseTarget,
                kinematic_usd_interface,
            )

            robot_port = kinematic_usd_interface(rig_t, rig_r, robot_id="g2")
            atomic_json(args.out / "robot_interface.json", robot_port.describe())
            robot = stage.DefinePrim("/World/TourRig/G2", "Xform")
            robot.GetReferences().AddReference(str(args.robot.resolve()))
            from simready.simulation.manipulation_visuals import (
                repair_robot_uv_interpolation,
            )

            atomic_json(
                args.out / "robot_uv_repairs.json",
                repair_robot_uv_interpolation(stage, "/World/TourRig/G2"),
            )
            cam.CreateFocalLengthAttr(args.camera_focal_length)
            cam.CreateHorizontalApertureAttr(36.0)
            cam.CreateVerticalApertureAttr(20.25)
            if fixed_camera:
                w, h = args.res
                focal = 20.0
                horizontal, vertical = (
                    focal * w / fixed_k[0, 0],
                    focal * h / fixed_k[1, 1],
                )
                cam.CreateFocalLengthAttr(focal)
                cam.CreateHorizontalApertureAttr(horizontal)
                cam.CreateVerticalApertureAttr(vertical)
                cam.CreateHorizontalApertureOffsetAttr(
                    (w / 2 - fixed_k[0, 2]) * horizontal / w
                )
                cam.CreateVerticalApertureOffsetAttr(
                    (fixed_k[1, 2] - h / 2) * vertical / h
                )
                cam_op.Set(
                    Gf.Matrix4d(
                        (fixed_pose @ np.diag([1.0, -1.0, -1.0, 1.0])).T.tolist()
                    )
                )
            print("TOUR_SETUP robot loaded", flush=True)
            wheels = []
            for p in robot.GetChildren():
                if p.GetName().startswith("chassis_") and p.GetName().endswith("link2"):
                    xf = UsdGeom.Xformable(p)
                    base = xf.GetLocalTransformation()
                    xf.ClearXformOpOrder()
                    op = xf.AddTransformOp(opSuffix="tourWheel")
                    name = str(p.GetPath())
                    steer_prim = stage.GetPrimAtPath(name[:-1] + "1")
                    steer_xf = UsdGeom.Xformable(steer_prim)
                    steer_base = steer_xf.GetLocalTransformation()
                    steer_xf.ClearXformOpOrder()
                    steer_op = steer_xf.AddTransformOp(opSuffix="tourSteer")
                    wheels.append(
                        dict(
                            op=op,
                            base=base,
                            steer_op=steer_op,
                            steer_base=steer_base,
                            distance=0.0,
                            prev=None,
                            steer=0.0,
                        )
                    )
            samples = (
                [round(i * (args.frames - 1) / 8) for i in range(9)]
                if args.preview
                else range(
                    args.offset,
                    min(args.frames, args.offset + args.limit)
                    if args.limit
                    else args.frames,
                )
            )
            if args.offset:
                # Rebuild wheel travel/steering on CPU without replaying GPU frames.
                # Rendering a short overlap after this warms DLAA history separately.
                for history_frame in range(args.offset):
                    hx, hy, hyaw = configured_route(history_frame)
                    for wheel in wheels:
                        advance_wheel_kinematics(
                            wheel, wheel["base"].ExtractTranslation(), hx, hy, hyaw
                        )
                (args.out / "resume_state.json").write_text(
                    json.dumps(
                        dict(
                            offset=args.offset,
                            cpu_replayed_control_frames=args.offset,
                            wheel_states=[
                                dict(
                                    distance=w["distance"],
                                    steer=w["steer"],
                                    prev=w["prev"],
                                )
                                for w in wheels
                            ],
                            temporal_history="Not checkpointed; render overlap before splice for DLAA warmup",
                        ),
                        indent=2,
                    )
                )
                print(
                    "TOUR_RESUME_STATE",
                    args.offset,
                    "prior control frames reconstructed on CPU",
                    flush=True,
                )
            t0 = time.monotonic()
            distance = 0.0
            prev = None
            for frame in samples:
                x, y, yaw = configured_route(frame)
                z = (
                    -(0.535 + 0.0225 * x - 0.0026 * y) / 0.9997
                    if quality == "high"
                    else floor
                )
                robot_z = (
                    replay[frame]["robot_position"][2]
                    if replay is not None
                    else z + 0.01
                )
                robot_port.command(
                    "root_pose", RootPoseTarget((x, y, robot_z), math.radians(yaw))
                )
                if prev is not None:
                    distance += math.hypot(x - prev[0], y - prev[1])
                prev = (x, y)
                for wheel in wheels:
                    base = wheel["base"]
                    loc = base.ExtractTranslation()
                    advance_wheel_kinematics(wheel, loc, x, y, yaw)
                    steer = Gf.Matrix4d().SetRotate(
                        Gf.Rotation(Gf.Vec3d(0, 0, 1), wheel["steer"])
                    )
                    spin = Gf.Matrix4d().SetRotate(
                        Gf.Rotation(
                            Gf.Vec3d(0, 1, 0), math.degrees(wheel["distance"] / 0.07)
                        )
                    )
                    wheel["op"].Set(spin * steer * base)
                    wheel["steer_op"].Set(steer * wheel["steer_base"])
                eye = (0.7, -0.3, floor + 2.4)
                target = (x, y, floor + 1.05)
                if replay is not None:
                    eye, target = replay[frame]["eye"], replay[frame]["target"]
                if fixed_camera:
                    eye = fixed_pose[:3, 3].tolist()
                    target = (fixed_pose[:3, 3] + fixed_pose[:3, 2]).tolist()
                else:
                    look(eye, target)
                if (args.stream_capture or args.synchronous_capture) and not manifest:
                    # Asset loading is asynchronous: step() may return without
                    # producing RGB while USD/Hydra is still initializing.
                    # Keep the first exact pose fixed until real captures exist.
                    from PIL import Image, ImageStat

                    ready_start = time.monotonic()
                    for _ in range(5):
                        app.update()
                    while omni.usd.get_context().get_stage_loading_status()[2] > 0:
                        if time.monotonic() - ready_start > 600:
                            raise TimeoutError("USD assets did not finish loading")
                        app.update()
                    valid_warm_captures = 0
                    previous_count = len(list(capture_dir.glob("rgb_*.png")))
                    while valid_warm_captures < 3:
                        # A cold RTX shader cache can take several minutes for
                        # the first image. Keep the same pose and still require
                        # three real, nonconstant captures before advancing.
                        if time.monotonic() - ready_start > 600:
                            raise TimeoutError("Actual RGB capture never became ready")
                        rep.orchestrator.step(
                            rt_subframes=args.subframes,
                            pause_timeline=True,
                            delta_time=1.0 / args.fps,
                            wait_for_render=True,
                        )
                        rep.orchestrator.wait_until_complete()
                        if args.synchronous_capture:
                            rgb = np.asarray(rgb_annotation.get_data()).copy()
                            if (
                                rgb.ndim == 3
                                and rgb.shape[:2] == tuple(args.res[::-1])
                                and float(rgb[..., :3].std()) > 1
                            ):
                                valid_warm_captures += 1
                            else:
                                app.update()
                            continue
                        warm_files = sorted(capture_dir.glob("rgb_*.png"))
                        if len(warm_files) > previous_count:
                            with Image.open(warm_files[-1]) as im:
                                im.load()
                                if (
                                    im.size != tuple(args.res)
                                    or sum(ImageStat.Stat(im.convert("RGB")).stddev) < 1
                                ):
                                    raise RuntimeError(
                                        "Warmup RGB is invalid or constant"
                                    )
                            valid_warm_captures += 1
                        else:
                            app.update()
                        previous_count = len(warm_files)
                    warmup_count = (
                        valid_warm_captures
                        if args.synchronous_capture
                        else len(warm_files)
                    )
                    atomic_json(
                        args.out / "capture_warmup.json",
                        dict(
                            actual_discarded_rgb=warmup_count,
                            fixed_control_frame=frame,
                            robot_position=[x, y, robot_z],
                            eye=eye,
                            target=target,
                            stage_loading_status=list(
                                omni.usd.get_context().get_stage_loading_status()
                            ),
                            elapsed_seconds=time.monotonic() - ready_start,
                        ),
                    )
                    print(
                        "RGB_CAPTURE_READY",
                        warmup_count,
                        "at fixed control",
                        frame,
                        flush=True,
                    )
                t = time.monotonic()
                rep.orchestrator.step(
                    rt_subframes=args.subframes,
                    pause_timeline=True,
                    delta_time=1.0 / args.fps if args.stream_capture else 0.0,
                    wait_for_render=True,
                )
                rep.orchestrator.wait_until_complete()
                if args.synchronous_capture:
                    rgb = np.asarray(rgb_annotation.get_data()).copy()
                    if rgb.ndim != 3 or rgb.shape[:2] != tuple(args.res[::-1]):
                        raise RuntimeError(
                            "RGB annotation dimensions do not match the render product"
                        )
                    Image.fromarray(rgb[..., :3]).save(
                        capture_dir / f"rgb_{len(manifest):04d}.png"
                    )
                if args.stream_capture:
                    actual_count = len(list(capture_dir.glob("rgb_*.png")))
                    expected_count = warmup_count + len(manifest) + 1
                    if actual_count != expected_count:
                        raise RuntimeError(
                            f"Control {frame}: expected {expected_count} actual RGBs, got {actual_count}"
                        )
                rec = dict(
                    frame=frame,
                    time_s=frame / args.fps,
                    robot_position=[x, y, robot_z],
                    yaw=yaw,
                    eye=eye,
                    target=target,
                    seconds=time.monotonic() - t,
                )
                if args.stream_capture:
                    rec["raw_capture_index"] = warmup_count + len(manifest) + 1
                manifest.append(rec)
                atomic_json(args.out / "trajectory.json", manifest)
                print("TOUR_FRAME", json.dumps(rec), flush=True)
            print("TOUR_DONE", time.monotonic() - t0, flush=True)
            stage.GetRootLayer().Export(str(args.out / "tour_final_stage.usda"))
            break
        eye = (0.0, 0.0, floor + 1.65)
        for angle in (0, 90, 180, 270):
            a = math.radians(angle)
            target = (3 * math.cos(a), 3 * math.sin(a), floor + 1.3)
            look(eye, target)
            t = time.monotonic()
            rep.orchestrator.step(
                rt_subframes=args.subframes, pause_timeline=True, delta_time=0.0
            )
            rep.orchestrator.wait_until_complete()
            rec = dict(
                quality=quality,
                angle=angle,
                eye=eye,
                target=target,
                seconds=time.monotonic() - t,
            )
            manifest.append(rec)
            (args.out / "survey.json").write_text(json.dumps(manifest, indent=2))
            print("TOUR_FRAME", json.dumps(rec), flush=True)
    if args.stream_capture:
        # Kit's measured readback sequence is [pose0,pose0,pose1,...].
        # Capture the unchanged final pose once more, then retain raw[1:N+1].
        # Every delivered frame is genuinely rendered; no interpolation/duplication.
        rep.orchestrator.step(
            rt_subframes=args.subframes,
            pause_timeline=True,
            delta_time=1.0 / args.fps,
            wait_for_render=True,
        )
        rep.orchestrator.wait_until_complete()
    if writer is not None:
        writer.detach()
    if args.synchronous_capture:
        rgb_annotation.detach([rp])
        atomic_json(
            args.out / "capture_mapping.json",
            dict(
                mode="synchronous_annotation_host_copy",
                latency_frames=0,
                delivered_frames=len(manifest),
                discarded_warmup_frames=warmup_count,
                mapping=[
                    dict(frame=i, control_frame=row["frame"], raw=f"rgb_{i:04d}.png")
                    for i, row in enumerate(manifest)
                ],
            ),
        )
    atomic_json(
        args.out / "render_settings.json",
        dict(
            requested_overrides=overrides,
            fixed_camera=fixed_camera,
            replay_trajectory=str(args.replay_trajectory.resolve())
            if args.replay_trajectory
            else None,
            authored_lighting=dict(
                sun_intensity=args.sun_intensity, dome_intensity=args.dome_intensity
            ),
            camera_focal_length_mm=cam.GetFocalLengthAttr().Get(),
            effective={
                key: settings.get(key)
                for key in set(overrides)
                | {
                    "/rtx/post/aa/op",
                    "/rtx/rendermode",
                    "/rtx/post/tonemap/op",
                    "/rtx/background/source/type",
                    "/rtx/background/source/color",
                }
            },
        ),
    )
    if args.stream_capture:
        raw = sorted(capture_dir.glob("rgb_*.png"))
        if len(raw) != warmup_count + len(manifest) + 1:
            raise RuntimeError(
                f"Readback count mismatch: {len(raw)} raw vs {warmup_count + len(manifest) + 1} expected"
            )
        frames_dir = args.out / "frames"
        frames_dir.mkdir(exist_ok=True)
        delivered_raw = raw[warmup_count + 1 :]
        for i, path in enumerate(delivered_raw):
            os.link(path, frames_dir / f"rgb_{i:04d}.png")
        (args.out / "capture_mapping.json").write_text(
            json.dumps(
                dict(
                    latency_frames=1,
                    discarded_initialization_frame=str(raw[0]),
                    discarded_warmup_frames=warmup_count,
                    delivered_frames=len(manifest),
                    raw_frames=len(raw),
                    mapping=[
                        dict(frame=i, control_frame=manifest[i]["frame"], raw=path.name)
                        for i, path in enumerate(delivered_raw)
                    ],
                ),
                indent=2,
            )
        )
        print("CAPTURE_ALIGNED", len(manifest), "frames", flush=True)
    if args.encode:
        import subprocess

        frames = sorted((args.out / "frames").glob("rgb_*.png"))
        if len(frames) != len(manifest):
            raise RuntimeError(
                f"Frame count mismatch: {len(frames)} vs {len(manifest)}"
            )
        video = args.out / "g2_starbucks_4k.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-framerate",
                str(args.fps),
                "-i",
                str(args.out / "frames/rgb_%04d.png"),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "14",
                "-threads",
                str(args.encode_threads),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(video),
            ],
            check=True,
        )
        (args.out / "delivery.json").write_text(
            json.dumps(
                dict(
                    video=str(video),
                    resolution=args.res,
                    fps=args.fps,
                    frames=len(frames),
                    duration_s=len(frames) / args.fps,
                    source=str(asset),
                    native_resolution=True,
                    antialiasing="DLAA",
                    motion="Authored kinematic wheeled patrol, not autonomous navigation",
                    source_limitations="Capture reconstruction contains ghosting and unobserved surfaces",
                ),
                indent=2,
            )
        )
        print("VIDEO_READY", str(video), flush=True)
    app.close()


if __name__ == "__main__":
    main()
