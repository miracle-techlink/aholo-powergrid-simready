#!/usr/bin/env python3
"""Render measured robot/contact poses from a completed stop mission."""

import argparse
import json
import subprocess
import traceback
from pathlib import Path

from simready.core.artifacts import digest, write_json
from simready.simulation.preflight import simulation_lease

MODULE_DIR = Path(__file__).resolve().parent


def run(a, apps):
    from isaaclab.app import AppLauncher

    app = AppLauncher(
        headless=True,
        enable_cameras=True,
        limit_cpu_threads=4,
        experience="isaacsim.exp.base.kit",
    ).app
    apps.append(app)
    import signal

    import carb
    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from PIL import Image, ImageDraw, ImageFont
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    from scipy.spatial.transform import Rotation

    stop = {"requested": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(requested=True))
    result = json.loads((a.run / "result.json").read_text())
    assert result["passed"], "Render accepted mission only"
    data = np.load(a.run / "measured_replay.npz")
    rows = [json.loads(x) for x in (a.run / "sensors.jsonl").read_text().splitlines()]
    # Disable physics on an isolated replay copy before Kit loads it. Leaving
    # rigid bodies enabled lets the renderer retain simulation transforms.
    from simready.simulation.asset_bundle import copy_layer_with_resolved_paths

    copy_layer_with_resolved_paths(a.run / "scene.usda", a.out / "visual_replay.usda")
    visual = Usd.Stage.Open(str(a.out / "visual_replay.usda"))
    if a.environment_override:
        env = visual.GetPrimAtPath("/World/PanelEnvironment")
        env.GetReferences().ClearReferences()
        env.GetReferences().AddReference(
            str(a.environment_override.resolve()), "/World"
        )
    for path in [str(p.GetPath()) for p in visual.Traverse()]:
        prim = visual.GetPrimAtPath(path)
        if not prim:
            continue
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
        if prim.IsA(UsdPhysics.Joint):
            UsdPhysics.Joint(prim).CreateJointEnabledAttr(False)
        if prim.IsA(UsdPhysics.Scene):
            prim.SetActive(False)
    visual.GetRootLayer().Save()
    omni.usd.get_context().open_stage(str(a.out / "visual_replay.usda"))
    for _ in range(12):
        app.update()
    stage = omni.usd.get_context().get_stage()
    omni.timeline.get_timeline_interface().pause()
    if a.hide_robot:
        assert a.view in ["left", "right"], (
            "Hide robot only for explicitly separate cabinet inspection views"
        )
        for path in ["/World/Lite3", "/World/FrankaArm"]:
            UsdGeom.Imageable(stage.GetPrimAtPath(path)).MakeInvisible()
    settings = carb.settings.get_settings()
    settings.set("/app/player/playSimulations", False)
    settings.set("/rtx/rendermode", "RealTimePathTracing")
    settings.set("/rtx/post/aa/op", 4)
    settings.set("/rtx/dlss/frameGeneration", False)
    settings.set("/rtx/post/colorcorr/enabled", False)
    paths = [str(p) for p in data["body_paths"]] + [str(data["cap_path"])]
    ops = []
    for path in paths:
        x = UsdGeom.Xformable(stage.GetPrimAtPath(path))
        x.ClearXformOpOrder()
        x.SetResetXformStack(True)
        ops.append(x.AddTransformOp(opSuffix="taskReplay"))
    camera = UsdGeom.Camera.Define(stage, "/World/TaskReviewCamera")
    camera.CreateFocalLengthAttr(21.0)
    camera.CreateHorizontalApertureAttr(36.0)
    camera.CreateVerticalApertureAttr(36.0 * a.height / a.width)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, 40))
    camera_op = camera.AddTransformOp()
    product = rep.create.render_product(str(camera.GetPath()), (a.width, a.height))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([product])
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", max(16, a.width // 68)
    )
    hud_scale = a.width / 1280.0
    video = a.out / "navigation_vision_z1_stop.mp4"
    writer = (
        None
        if a.frames
        else subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{a.width}x{a.height}",
                "-r",
                "25",
                "-i",
                "-",
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "4",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(video),
            ],
            stdin=subprocess.PIPE,
        )
    )
    torso = paths.index("/World/Lite3/TORSO")
    records = []
    peak_frame = int(
        np.argmax([rows[int(step)]["target_contact_N"] for step in data["steps"]])
    )
    selected = (
        list(map(int, a.frames.split(",")))
        if a.frames
        else list(range(len(data["steps"])))
    )
    try:
        for ordinal, i in enumerate(selected):
            step = data["steps"][i]
            if stop["requested"] or (a.out / "STOP_REQUESTED").exists():
                raise RuntimeError("Review render stopped by request")
            row = rows[int(step)]
            poses = np.concatenate([data["body_poses"][i], data["cap_poses"][i][None]])
            for op, pose in zip(ops, poses, strict=False):
                matrix = np.eye(4)
                matrix[:3, :3] = Rotation.from_quat(pose[3:]).as_matrix()
                matrix[:3, 3] = pose[:3]
                op.Set(Gf.Matrix4d(matrix.T.tolist()))
            if a.frames or ordinal == 0 or i % 100 == 0:
                for path, pose in zip(paths, poses, strict=False):
                    actual = np.array(
                        UsdGeom.Xformable(
                            stage.GetPrimAtPath(path)
                        ).ComputeLocalToWorldTransform(0)
                    ).T
                    assert np.linalg.norm(actual[:3, 3] - pose[:3]) < 1e-6, (
                        path,
                        actual[:3, 3],
                        pose[:3],
                    )
            root = poses[torso, :3]
            target = np.array(row["target_surface_from_rgbd"] or [3.94, -0.58, 0.79])
            if a.view in ["right", "left"]:
                camera.GetFocalLengthAttr().Set(12.0)
                eye = (
                    np.array([3.25, 0.17, 0.65])
                    if a.view == "right"
                    else np.array([3.13, -0.19, 0.64])
                )
                look = (
                    np.array([3.25, -0.595, 0.65])
                    if a.view == "right"
                    else np.array([3.13, 0.525, 0.64])
                )
            elif row["phase"] in ["PRESS", "WITHDRAW"]:
                camera.GetFocalLengthAttr().Set(35.0)
                eye = target + np.array([-0.28, 0.20, 0.20])
                look = target + np.array([0.0, 0.018, 0.0])
            else:
                camera.GetFocalLengthAttr().Set(21.0)
                eye = root + np.array([-0.95, 0.22, 1.04])
                look = root + np.array([0.12, -0.08, 0.14])
            camera_op.Set(
                Gf.Matrix4d()
                .SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*look), Gf.Vec3d(0, 0, 1))
                .GetInverse()
            )
            stage.GetPrimAtPath("/World/TaskState/RightFarSimulatedLoad").GetAttribute(
                "task:state"
            ).Set(row["load_state"])
            if a.frames or ordinal == 0:
                for _ in range(12):
                    app.update()
            rep.orchestrator.step(
                rt_subframes=16 if ordinal == 0 or a.frames else 2,
                delta_time=0.0,
                pause_timeline=True,
            )
            pixels = np.asarray(rgb.get_data())[..., :3]
            for _ in range(6):
                if pixels.shape == (a.height, a.width, 3):
                    break
                for _ in range(6):
                    app.update()
                rep.orchestrator.step(
                    rt_subframes=12, delta_time=0.0, pause_timeline=True
                )
                pixels = np.asarray(rgb.get_data())[..., :3]
            assert pixels.shape == (a.height, a.width, 3)
            im = Image.fromarray(pixels)
            draw = ImageDraw.Draw(im)
            compliant = "tool_compression_m" in row
            draw.rectangle(
                (0, 0, a.width, round((88 if compliant else 62) * hud_scale)),
                fill=(12, 18, 26),
            )
            title = (
                f"ESTIMATED CABINET ASSET REVIEW | {a.view.upper()} | ROBOT HIDDEN"
                if a.hide_robot
                else f"MEASURED PHYSICS REPLAY | {row['t']:.2f}s | {row['phase']} | SIM LOAD: {row['load_state']}"
            )
            draw.text(
                (round(12 * hud_scale), round(6 * hud_scale)),
                title,
                font=font,
                fill="white",
            )
            draw.text(
                (round(12 * hud_scale), round(33 * hud_scale)),
                f"Z1 contact {row['target_contact_N']:.2f} N | button {row['target_depth_m'] * 1000:.2f} mm | known-map + RGB-D",
                font=font,
                fill=(175, 215, 240),
            )
            if compliant:
                draw.text(
                    (round(12 * hud_scale), round(59 * hud_scale)),
                    f"Spring compression {row['tool_compression_m'] * 1000:.2f} mm | ESTIMATED CABINET COMPLETION",
                    font=font,
                    fill=(240, 210, 150),
                )
            elif a.environment_override:
                draw.text(
                    (round(12 * hud_scale), round(92 * hud_scale)),
                    "ESTIMATED APPEARANCE PREVIEW | NO NEW PHYSICS",
                    font=font,
                    fill=(255, 190, 100),
                    stroke_width=1,
                    stroke_fill=(0, 0, 0),
                )
            if writer:
                writer.stdin.write(np.asarray(im).tobytes())
            if (
                a.frames
                or i % 100 == 0
                or i == peak_frame
                or row["target_contact_N"] > 0.2
                and not any(x.get("contact") for x in records)
                or i == len(data["steps"]) - 1
            ):
                name = f"frame_{i:04d}.png"
                im.save(a.out / name)
                records.append(
                    dict(
                        frame=i,
                        t=row["t"],
                        phase=row["phase"],
                        contact=row["target_contact_N"] > 0.2,
                        path=name,
                    )
                )
            if i % 50 == 0:
                print(
                    json.dumps(
                        dict(frame=i, total=len(data["steps"]), phase=row["phase"])
                    ),
                    flush=True,
                )
    finally:
        if writer:
            writer.stdin.close()
            writer.wait(timeout=45)
    if writer:
        assert writer.returncode == 0
    write_json(
        a.out / "render.json",
        dict(
            native_render=True,
            new_physics=False,
            source_result_sha256=digest(a.run / "result.json"),
            source_replay_sha256=digest(a.run / "measured_replay.npz"),
            renderer_sha256=digest(__file__),
            video_sha256=digest(video) if writer else None,
            frames=len(selected),
            fps=25,
            width=a.width,
            height=a.height,
            screenshots=records,
            peak_contact_frame=peak_frame,
            usd_pose_checks_passed=True,
            real_electrical_function_verified=False,
            whole_scene_certified=False,
            environment_override=str(a.environment_override.resolve())
            if a.environment_override
            else None,
            source_scene_sha256=digest(a.run / "scene.usda"),
            view=a.view,
            robot_hidden_for_asset_inspection=a.hide_robot,
            estimated_appearance=bool(
                a.environment_override or "tool_compression_m" in rows[-1]
            ),
        ),
    )
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--frames")
    p.add_argument(
        "--environment-override",
        type=Path,
        help="Inspection only: render estimated appearance on an old measured replay",
    )
    p.add_argument("--view", choices=["follow", "right", "left"], default="follow")
    p.add_argument("--hide-robot", action="store_true")
    a = p.parse_args()
    a.run = a.run.resolve()
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=False)
    apps = []
    code = 2
    try:
        with simulation_lease():
            code = run(a, apps)
    except BaseException:
        traceback.print_exc()
    finally:
        if apps:
            apps[-1].close(wait_for_replicator=False, exit_code=code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
