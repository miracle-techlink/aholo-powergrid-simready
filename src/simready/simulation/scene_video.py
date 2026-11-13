"""Native survey of a generated scene with an explicit or bounds-derived camera."""

import argparse
import json
import math
import os
import subprocess
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json


def camera_samples(bounds, camera, frames):
    """Return world-frame eye/target pairs; no claims about free-space navigation."""
    import numpy as np

    lower, upper = (np.asarray(bounds[key], dtype=float) for key in ("min", "max"))
    if (
        lower.shape != (3,)
        or upper.shape != (3,)
        or not np.isfinite([lower, upper]).all()
        or (upper <= lower).any()
    ):
        raise ValueError("Need finite positive scene bounds for a camera")
    if not isinstance(camera, dict) or set(camera) - {"eye", "target", "orbit_degrees"}:
        raise ValueError("Unknown camera field")
    if ("eye" in camera) != ("target" in camera):
        raise ValueError("Explicit camera requires both eye and target")
    center = (lower + upper) / 2
    extent = float(np.max(upper - lower))
    eye = np.asarray(
        camera.get("eye", center + np.array([1.0, -1.0, 0.55]) * extent), dtype=float
    )
    target = np.asarray(camera.get("target", center), dtype=float)
    degrees = camera.get("orbit_degrees", 60.0)
    if any(x.shape != (3,) or not np.isfinite(x).all() for x in (eye, target)):
        raise ValueError("Camera eye and target must be finite triples")
    if (
        type(degrees) not in (int, float)
        or not math.isfinite(degrees)
        or abs(degrees) > 360
    ):
        raise ValueError("Camera sweep must be within 360 degrees")
    delta = eye - target
    if np.linalg.norm(delta[:2]) < max(1e-6, extent * 1e-6):
        raise ValueError("Camera direction must not be parallel to world up")
    rows = []
    for frame in range(frames):
        theta = math.radians(degrees * frame / max(1, frames - 1))
        c, s = math.cos(theta), math.sin(theta)
        rotated = np.array(
            [c * delta[0] - s * delta[1], s * delta[0] + c * delta[1], delta[2]]
        )
        rows.append(
            {
                "frame": frame,
                "eye": (target + rotated).tolist(),
                "target": target.tolist(),
            }
        )
    return rows


def render_scene(scene, preparation, spec, output):
    from PIL import Image

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = read_json(spec)
    render = config["render"]
    width, height, count, fps = (
        render[key] for key in ("width", "height", "frames", "fps")
    )
    poses = camera_samples(
        read_json(preparation)["bounds_world"], config.get("camera", {}), count
    )
    write_json(
        output / "camera.json",
        {
            "schema_version": 1,
            "source": "explicit scene spec"
            if config.get("camera")
            else "bounds-derived diagnostic view",
            "poses": poses,
            "camera_clearance_validated": False,
        },
    )
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True},
        experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
    )
    try:
        import carb
        import omni.kit.app
        import omni.usd
        from pxr import Gf, UsdGeom, UsdLux

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "omni.replicator.core", True
        )
        import omni.replicator.core as rep

        settings = carb.settings.get_settings()
        for key, value in {
            "/rtx/rendermode": "RealTimePathTracing",
            "/rtx/post/aa/op": 4,
            "/rtx/post/dlss/execMode": 2,
            "/rtx/dldenoiser/enabled": True,
            "/rtx/rtpt/gaussian/accumulatedDepth/enabled": True,
            "/rtx/rtpt/gaussian/accumulatedAlbedo/enabled": True,
            "/rtx/rtpt/gaussian/maxGaussiansToAccumulate": 48,
        }.items():
            settings.set(key, value)
        stage = omni.usd.get_context().get_stage()
        stage.GetRootLayer().subLayerPaths = [str(Path(scene).resolve())]
        stage.SetMetadata("upAxis", "Z")
        stage.SetMetadata("metersPerUnit", 1.0)
        camera = UsdGeom.Camera.Define(stage, "/Survey/Camera")
        camera.CreateFocalLengthAttr(20.0)
        camera.CreateHorizontalApertureAttr(36.0)
        camera.CreateVerticalApertureAttr(36.0 * height / width)
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, 100000.0))
        transform = camera.AddTransformOp()
        UsdLux.DomeLight.Define(stage, "/Survey/Light").CreateIntensityAttr(300.0)
        product = rep.create.render_product(str(camera.GetPath()), (width, height))
        writer = rep.WriterRegistry.get("BasicWriter")
        writer.initialize(output_dir=str(output / "frames"), rgb=True)
        writer.attach([product])
        for row in poses:
            transform.Set(
                Gf.Matrix4d()
                .SetLookAt(
                    Gf.Vec3d(*row["eye"]), Gf.Vec3d(*row["target"]), Gf.Vec3d(0, 0, 1)
                )
                .GetInverse()
            )
            rep.orchestrator.step(
                rt_subframes=render["subframes"], delta_time=0.0, pause_timeline=True
            )
            rep.orchestrator.wait_until_complete()
        writer.detach()
        rep.orchestrator.wait_until_complete()
    finally:
        app.close()
    images = sorted((output / "frames").glob("rgb_*.png"))
    if len(images) != count:
        raise ValueError(f"Expected {count} captures, got {len(images)}")
    ledger = []
    # Contiguous names decouple encoder timing from Replicator's writer numbering.
    import numpy as np

    for index, path in enumerate(images):
        with Image.open(path) as frame:
            if frame.size != (width, height):
                raise ValueError("Unexpected captured resolution")
            pixels = np.asarray(frame.convert("RGB"), dtype=np.float32)
            ledger.append(
                {
                    "frame": index,
                    "path": str(path.relative_to(output)),
                    "sha256": digest(path),
                    "mean": float(pixels.mean()),
                    "std": float(pixels.std()),
                }
            )
        path.rename(output / "frames" / f"capture_{index:06d}.png")
        ledger[-1]["path"] = f"frames/capture_{index:06d}.png"
    if all(row["std"] < 1 for row in ledger):
        raise ValueError(
            "All views are nearly uniform; inspect camera and reconstruction"
        )
    video = output / "scene_survey.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(output / "frames/capture_%06d.png"),
            "-frames:v",
            str(count),
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(video),
        ],
        check=True,
    )
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,nb_frames,avg_frame_rate",
                "-of",
                "json",
                str(video),
            ],
            text=True,
        )
    )["streams"][0]
    numerator, denominator = map(int, probe["avg_frame_rate"].split("/"))
    if (probe["width"], probe["height"], int(probe["nb_frames"])) != (
        width,
        height,
        count,
    ) or numerator / denominator != fps:
        raise ValueError("Encoded movie does not match requested timing/resolution")
    write_json(
        output / "render.json",
        {
            "schema_version": 1,
            "status": "COMPLETE",
            "video": video.name,
            "video_sha256": digest(video),
            "scene_sha256": digest(scene),
            "spec_sha256": digest(spec),
            "frames": count,
            "fps": fps,
            "width": width,
            "height": height,
            "source_consistency_validated": False,
            "poses_sha256": digest(output / "camera.json"),
            "captures": ledger,
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    render_scene(args.scene, args.preparation, args.spec, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
