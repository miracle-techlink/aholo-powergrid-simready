"""Observe one authored USD camera as synchronized RGB and camera-z depth.

Depth is rendered geometry evidence from the supplied (possibly repaired)
scene, never a real sensor measurement or proof of physical metric scale.
Only main() starts Isaac; pinhole and raster validation are CPU-testable.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.geometry.cameras import rigid_inverse
from simready.simulation.gaussian_asset import validate_sim3

DEPTH_DOCUMENTATION = (
    "https://docs.omniverse.nvidia.com/kit/docs/omni_replicator/1.12.16/"
    "source/extensions/omni.replicator.core/docs/API.html"
)


def calibration_to_usd_camera(
    calibration: dict[str, Any], *, focal_length_mm: float = 21.0
) -> dict[str, Any]:
    """Convert zero-skew OpenCV pinhole calibration to USD camera attributes.

    Integer raster coordinates denote pixel centers. Returned USD matrix uses
    row vectors, whereas input c2w uses columns and right/down/forward axes.
    Non-square pixels are refused because RTX may conform aperture aspect ratio.
    Principal-point shifts use signed USD aperture offsets, not camera motion.
    """
    if (
        not isinstance(calibration, dict)
        or type(calibration.get("schema_version")) is not int
        or calibration.get("schema_version") != 1
        or calibration.get("camera_convention") != "opencv_c2w"
        or calibration.get("distortion") != "none"
    ):
        raise ValueError("An undistorted schema-1 opencv_c2w calibration is required")
    size = calibration.get("image_size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(type(value) is not int or not 1 <= value <= 8192 for value in size)
    ):
        raise ValueError("image_size must contain width,height integers in 1..8192")
    width, height = size
    if width * height > 16_000_000:
        raise ValueError("Capture exceeds the 16 megapixel worker limit")
    intrinsics = np.asarray(calibration.get("intrinsics"), dtype=float)
    pose = np.asarray(calibration.get("camera_to_world"), dtype=float)
    if pose.shape != (4, 4):
        raise ValueError("camera_to_world must be a rigid 4x4 matrix")
    rigid_inverse(pose)
    if (
        intrinsics.shape != (3, 3)
        or not np.isfinite(intrinsics).all()
        or not np.allclose(intrinsics[2], [0, 0, 1], atol=1e-9, rtol=0)
        or not np.allclose([intrinsics[0, 1], intrinsics[1, 0]], 0, atol=1e-9, rtol=0)
    ):
        raise ValueError("intrinsics must be a finite zero-skew pinhole matrix")
    fx, fy, cx, cy = (
        intrinsics[0, 0],
        intrinsics[1, 1],
        intrinsics[0, 2],
        intrinsics[1, 2],
    )
    if min(fx, fy) <= 0 or not (0 <= cx <= width - 1 and 0 <= cy <= height - 1):
        raise ValueError(
            "Focal pixels must be positive and principal point inside the image"
        )
    if not np.isclose(fx, fy, rtol=1e-6, atol=1e-8):
        raise ValueError("This calibrated RTX worker requires square pixels (fx=fy)")
    if (
        type(focal_length_mm) not in (int, float)
        or not math.isfinite(focal_length_mm)
        or focal_length_mm <= 0
    ):
        raise ValueError("focal_length_mm must be finite and positive")
    horizontal = focal_length_mm * width / fx
    vertical = focal_length_mm * height / fy
    usd_column = pose @ np.diag([1.0, -1.0, -1.0, 1.0])
    return {
        "width": width,
        "height": height,
        "focal_length_mm": float(focal_length_mm),
        "horizontal_aperture_mm": float(horizontal),
        "vertical_aperture_mm": float(vertical),
        "horizontal_aperture_offset_mm": float(
            ((width - 1) / 2 - cx) * horizontal / width
        ),
        "vertical_aperture_offset_mm": float(
            (cy - (height - 1) / 2) * vertical / height
        ),
        "usd_transform_row_major": usd_column.T.tolist(),
    }


def normalize_camera_z(
    depth: np.ndarray,
    *,
    width: int,
    height: int,
    meters_per_unit: float,
    min_valid_fraction: float = 0.05,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Validate rendered camera-z values and replace invalid/background with zero.

    Replicator documents metric depth. This first worker requires a stage with
    metersPerUnit=1 so numeric world units and declared renderer meters agree;
    it does not certify that the reconstructed geometry has real metric scale.
    Other unit conventions must be validated before extending this boundary.
    """
    if (
        type(meters_per_unit) not in (int, float)
        or not math.isfinite(meters_per_unit)
        or not math.isclose(meters_per_unit, 1.0, rel_tol=0, abs_tol=1e-9)
    ):
        raise ValueError(
            "Unverified depth conversion: this worker requires metersPerUnit=1"
        )
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise ValueError("Depth raster dimensions must be positive integers")
    if not math.isfinite(min_valid_fraction) or not 0 < min_valid_fraction <= 1:
        raise ValueError("min_valid_fraction must be in (0, 1]")
    values = np.asarray(depth)
    if values.shape == (height, width, 1):
        values = values[:, :, 0]
    if values.shape != (height, width) or values.dtype.kind not in "fiu":
        raise ValueError("Depth raster must match the calibrated image size")
    if np.any(values < 0):
        raise ValueError("Negative camera-z depth indicates invalid renderer data")
    valid = np.isfinite(values) & (values > 0) & (values <= np.finfo(np.float32).max)
    fraction = float(valid.mean())
    if fraction < min_valid_fraction:
        raise ValueError(f"Insufficient valid camera-z depth: {fraction:.6f}")
    clean = np.where(valid, values, 0).astype(np.float32)
    return clean, {
        "valid_fraction": fraction,
        "valid_pixel_count": int(valid.sum()),
        "invalid_pixel_count": int(valid.size - valid.sum()),
        "min_world_units": float(clean[valid].min()),
        "max_world_units": float(clean[valid].max()),
        "median_world_units": float(np.median(clean[valid])),
        "invalid_encoding": 0,
        "kind": "camera_z",
        "unit": "world_units",
        "renderer_reported_unit": "meters",
        "stage_meters_per_unit": meters_per_unit,
        "numeric_unit_conversion_factor": 1.0,
        "metric_scale_verified": False,
    }


def _array(data: Any) -> np.ndarray:
    return np.asarray(data["data"] if isinstance(data, dict) else data)


def _capture(
    args: argparse.Namespace, app: Any, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Called only after SimulationApp creation; imports stay inside Isaac."""
    import carb
    import omni.kit.app
    import omni.timeline
    import omni.usd
    from PIL import Image
    from pxr import Gf, UsdGeom, UsdLux, UsdUtils

    started = time.perf_counter()
    omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
        "omni.replicator.core", True
    )
    import omni.replicator.core as rep

    settings = carb.settings.get_settings()
    for key, value in {
        "/app/player/playSimulations": False,
        "/rtx/rendermode": "RealTimePathTracing",
        "/rtx/post/aa/op": 4,
        "/rtx/post/dlss/execMode": 2,
        "/rtx/dldenoiser/enabled": True,
        "/rtx/rtpt/gaussian/accumulatedDepth/enabled": True,
        "/rtx/rtpt/gaussian/accumulatedAlbedo/enabled": True,
        "/rtx/rtpt/gaussian/maxGaussiansToAccumulate": 48,
    }.items():
        settings.set(key, value)
    # Resolve all referenced local layers before producing a seemingly valid
    # image of a scene with missing cabinets or robots.
    layers, _, unresolved = UsdUtils.ComputeAllDependencies(str(args.scene))
    if unresolved:
        raise ValueError(f"Scene dependencies unresolved: {list(unresolved)[:8]}")
    layer_evidence = [
        {"path": layer.realPath, "sha256": digest(layer.realPath)}
        for layer in layers
        if layer.realPath and Path(layer.realPath).is_file()
    ]
    if not omni.usd.get_context().open_stage(str(args.scene)):
        raise ValueError("Isaac could not open the source scene")
    omni.timeline.get_timeline_interface().pause()
    stage = omni.usd.get_context().get_stage()
    # Author observation settings into a session layer: never save source USD.
    stage.SetEditTarget(stage.GetSessionLayer())
    stage_units = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not math.isclose(stage_units, 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError(
            "Depth capture currently requires source stage metersPerUnit=1"
        )
    camera_settings = inputs["camera_settings"]
    camera_path = "/SimReadyDepthObservation/Camera"
    if stage.GetPrimAtPath("/SimReadyDepthObservation"):
        raise ValueError("Scene already uses reserved /SimReadyDepthObservation scope")
    camera = UsdGeom.Camera.Define(stage, camera_path)
    camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
    for key, factory in (
        ("focal_length_mm", camera.CreateFocalLengthAttr),
        ("horizontal_aperture_mm", camera.CreateHorizontalApertureAttr),
        ("vertical_aperture_mm", camera.CreateVerticalApertureAttr),
        ("horizontal_aperture_offset_mm", camera.CreateHorizontalApertureOffsetAttr),
        ("vertical_aperture_offset_mm", camera.CreateVerticalApertureOffsetAttr),
    ):
        factory(camera_settings[key])
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.001, 10000.0))
    camera.AddTransformOp().Set(Gf.Matrix4d(camera_settings["usd_transform_row_major"]))
    # Add a neutral light only if the scene contains no light at all.
    added_light = not any(prim.HasAPI(UsdLux.LightAPI) for prim in stage.Traverse())
    if added_light:
        UsdLux.DomeLight.Define(
            stage, "/SimReadyDepthObservation/Light"
        ).CreateIntensityAttr(300.0)
    width, height = camera_settings["width"], camera_settings["height"]
    product = rep.create.render_product(camera_path, (width, height))
    rgb_annotator = rep.AnnotatorRegistry.get_annotator("rgb")
    depth_annotator = rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
    rgb_annotator.attach([product])
    depth_annotator.attach([product])
    for _ in range(args.warmup_updates):
        app.update()
    last_failure = "No capture attempted"
    for attempt in range(args.capture_attempts):
        captured_attempt = attempt + 1
        rep.orchestrator.step(
            rt_subframes=args.subframes, delta_time=0.0, pause_timeline=True
        )
        rep.orchestrator.wait_until_complete()
        rgb = _array(rgb_annotator.get_data()).copy()
        raw_depth = _array(depth_annotator.get_data()).copy()
        try:
            if (
                rgb.shape not in {(height, width, 3), (height, width, 4)}
                or rgb.dtype != np.uint8
            ):
                raise ValueError("RGB capture is not a matching uint8 raster")
            depth, statistics = normalize_camera_z(
                raw_depth,
                width=width,
                height=height,
                meters_per_unit=stage_units,
            )
            if float(rgb[:, :, :3].std()) < 1.0:
                raise ValueError("RGB capture is nearly uniform; inspect scene/camera")
            break
        except ValueError as error:
            last_failure = str(error)
            for _ in range(4):
                app.update()
    else:
        raise ValueError(f"Synchronized RGB/depth capture failed: {last_failure}")
    rgb_annotator.detach([product])
    depth_annotator.detach([product])
    for path, expected in inputs["frozen"].items():
        if digest(path) != expected:
            raise ValueError(f"Input changed during capture: {path}")
    for row in layer_evidence:
        if digest(row["path"]) != row["sha256"]:
            raise ValueError("A referenced scene layer changed during capture")
    Image.fromarray(rgb[:, :, :3]).save(args.out / "image.png")
    np.save(args.out / "depth.npy", depth, allow_pickle=False)
    valid_mask = (depth > 0).astype(np.uint8) * 255
    Image.fromarray(valid_mask).save(args.out / "depth_valid.png")
    calibration = dict(inputs["calibration"])
    calibration.update(
        {
            "image_sha256": digest(args.out / "image.png"),
            "depth": {
                "path": "depth.npy",
                "sha256": digest(args.out / "depth.npy"),
                "kind": "camera_z",
                "unit": "world_units",
            },
            "calibration_kind": "authored_render_camera_with_synchronized_depth",
            "real_camera_calibrated": False,
            "source_calibration_sha256": inputs["frozen"][args.camera_calibration],
            "source_image_sha256": inputs["calibration"]["image_sha256"],
            "scene_sha256": inputs["frozen"][args.scene],
            "renderer_sha256": inputs["worker_sha256"],
            "evidence": "New synchronized Replicator RGB and distance_to_image_plane from the supplied USD; camera pose/intrinsics reused from the bound source calibration. Repaired scene depth, not real sensor depth.",
            "depth_statistics": statistics,
        }
    )
    write_json(args.out / "calibration.json", calibration)
    return {
        "schema_version": 1,
        "status": "COMPLETE",
        "image": {"path": "image.png", "sha256": calibration["image_sha256"]},
        "depth": calibration["depth"],
        "calibration": {
            "path": "calibration.json",
            "sha256": digest(args.out / "calibration.json"),
        },
        "depth_valid_mask": {
            "path": "depth_valid.png",
            "sha256": digest(args.out / "depth_valid.png"),
        },
        "source_scene": {
            "path": str(args.scene),
            "sha256": inputs["frozen"][args.scene],
        },
        "source_scene_spec_sha256": inputs["frozen"][args.scene_spec],
        "scene_layers": layer_evidence,
        "renderer_sha256": inputs["worker_sha256"],
        "camera_settings": camera_settings,
        "depth_statistics": statistics,
        "capture_attempt": captured_attempt,
        "added_fallback_light": added_light,
        "physics_stepped": False,
        "depth_source": "rendered_repaired_scene_geometry",
        "real_sensor_measurement": False,
        "metric_scale_verified": False,
        "semantic_accuracy_validated": False,
        "depth_documentation": DEPTH_DOCUMENTATION,
        "capture_s": time.perf_counter() - started,
    }


def main(argv: list[str] | None = None) -> int:
    """Isaac-only standalone worker; preserve nonzero failure status on shutdown."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("scene", "scene-spec", "ply", "camera-calibration", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--subframes", type=int, default=8)
    parser.add_argument("--warmup-updates", type=int, default=12)
    parser.add_argument("--capture-attempts", type=int, default=8)
    args = parser.parse_args(argv)
    for name in ("scene", "scene_spec", "ply", "camera_calibration"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.out = args.out.resolve()
    if args.out.exists():
        raise FileExistsError(f"Observation output must be fresh: {args.out}")
    if (
        not 1 <= args.subframes <= 64
        or not 1 <= args.capture_attempts <= 32
        or not 0 <= args.warmup_updates <= 120
    ):
        raise ValueError("Render limits exceeded")
    frozen = {
        getattr(args, name): digest(getattr(args, name))
        for name in ("scene", "scene_spec", "ply", "camera_calibration")
    }
    calibration, spec = read_json(args.camera_calibration), read_json(args.scene_spec)
    camera_settings = calibration_to_usd_camera(calibration)
    transform = validate_sim3(spec["source_to_world"])
    if calibration.get("ply_sha256") != frozen[args.ply]:
        raise ValueError("Calibration PLY hash does not match input")
    if calibration.get("source_to_world_sha256") != object_digest(transform.tolist()):
        raise ValueError("Calibration and scene spec source_to_world do not match")
    if not isinstance(calibration.get("image_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", calibration["image_sha256"]
    ):
        raise ValueError("Source calibration must identify its original image SHA256")
    inputs = {
        "frozen": frozen,
        "calibration": calibration,
        "camera_settings": camera_settings,
        "worker_sha256": digest(__file__),
    }
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(
        args.out / "request.json",
        {
            "schema_version": 1,
            "inputs": {str(path): value for path, value in frozen.items()},
            "camera_settings": camera_settings,
            "renderer_sha256": inputs["worker_sha256"],
        },
    )
    app, exit_code = None, 1
    started = time.perf_counter()
    try:
        from isaacsim import SimulationApp

        app = SimulationApp(
            {"headless": True, "limit_cpu_threads": 4, "fast_shutdown": True},
            experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
        )
        record = _capture(args, app, inputs)
        record["total_s_before_shutdown"] = time.perf_counter() - started
        write_json(args.out / "manifest.json", record)
        print(f"Depth observation complete: {args.out / 'manifest.json'}", flush=True)
        exit_code = 0
    except BaseException as error:
        traceback.print_exc()
        write_json(
            args.out / "failure.json",
            {
                "schema_version": 1,
                "status": "FAILED",
                "error": str(error),
                "elapsed_s": time.perf_counter() - started,
            },
        )
    finally:
        if app is not None:
            # Isaac 6.1 supports exit_code. Fast shutdown otherwise masks failures.
            app.close(exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
