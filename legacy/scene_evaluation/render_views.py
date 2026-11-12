#!/usr/bin/env python3
"""Render requested static captured-GS views; camera requests are NOT verified GT.

Run with Isaac venv Python, DISPLAY=:1, LD_PRELOAD=libgomp.so.1. Camera JSON:
{"resolution":[720,1280],"K":[[840,0,360],[0,840,640],[0,0,1]],
 "views":[{"id":"yaw0","eye":[0,0,1.1],"target":[1,0,1.1]}]}
Each view may instead provide camera_to_world_cv (column-vector OpenCV x-right,
y-down, z-forward). A static pose is held for repeated captures before export.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import traceback

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(name, "1")
os.environ.setdefault("PYTORCH_JIT", "0")
os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")

import numpy as np

from legacy.scene_evaluation.render_common import (
    sha,
    digest,
    save,
    prepare_camera,
    check_cpu_quota,
)

DEFAULT_SCENE = Path(
    "/home/nvidia/Desktop/星巴克重建资产_20261108/02_星巴克实拍重建/scene.usda"
)
LOCK = Path("/tmp/g2_starbucks_tour.gpu.lock")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--cameras", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only-id")
    parser.add_argument("--subframes", type=int, default=2)
    parser.add_argument("--settle-captures", type=int, default=3)
    parser.add_argument("--depth", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    args.scene, args.cameras, args.out = (
        args.scene.resolve(),
        args.cameras.resolve(),
        args.out.resolve(),
    )
    document = json.loads(args.cameras.read_text())
    camera_evidence = document.get("camera_evidence")
    if (
        camera_evidence
        and sha(Path(camera_evidence["path"])) != camera_evidence["sha256"]
    ):
        raise ValueError("Camera evidence bytes do not match the bound SHA256")
    views = [
        prepare_camera(v, document)
        for v in document["views"]
        if not args.only_id or v["id"] == args.only_id
    ]
    for view in views:
        evidence = view.get("camera_evidence")
        if evidence and sha(Path(evidence["path"])) != evidence["sha256"]:
            raise ValueError(f"Changed camera evidence for {view['id']}")
    if not views or len({v["id"] for v in views}) != len(views):
        raise ValueError("Views must be nonempty with unique IDs")
    if any("/" in v["id"] or ".." in v["id"] for v in views):
        raise ValueError("View IDs must be simple filenames")
    if args.settle_captures < 2 or args.subframes < 1:
        raise ValueError("At least2 static settle captures and1 subframe required")
    if args.validate_only:
        print(json.dumps({"camera_requests": views, "verified": False}))
        return
    if args.out.exists():
        raise FileExistsError("Use a fresh render output directory")
    quota = check_cpu_quota()
    lock = LOCK.open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    args.out.mkdir(parents=True)
    save(
        args.out / "request.json",
        {
            "scene": str(args.scene),
            "camera_file": str(args.cameras),
            "camera_file_sha256": sha(args.cameras),
            "views": views,
            "cpu_quota_chain": quota,
            "status": "starting",
            "camera_match_verified": False,
        },
    )
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(
        headless=True, offscreen_render=True, experience="isaacsim.exp.base.kit"
    )
    app = launcher.app
    try:
        import carb
        import omni.usd
        import omni.kit.app
        from pxr import UsdGeom, UsdLux, Gf, Sdf

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "omni.replicator.core", True
        )
        import omni.replicator.core as rep
        from omni.kit.viewport.window import get_viewport_window_instances

        for window in get_viewport_window_instances(None):
            window.viewport_api.updates_enabled = False
        stage = omni.usd.get_context().get_stage()
        stage.GetRootLayer().subLayerPaths = [str(args.scene)]
        stage.SetMetadata("upAxis", "Z")
        stage.SetMetadata("metersPerUnit", 1.0)
        assets = []
        todo, seen = [args.scene], set()
        while todo:
            path = todo.pop().resolve()
            if str(path) in seen:
                continue
            seen.add(str(path))
            assets.append({"path": str(path), "sha256": sha(path)})
            layer = Sdf.Layer.FindOrOpen(str(path))
            for ref in layer.GetExternalReferences():
                if not ref:
                    continue
                if "://" in ref or "[" in ref:
                    raise ValueError("Only local unpackaged scene layers permitted")
                todo.append(path.parent / ref)
        candidate_hash = digest(sorted(assets, key=lambda a: (a["path"], a["sha256"])))
        settings = carb.settings.get_settings()
        settings_values = {
            "/rtx/rendermode": "RealTimePathTracing",
            "/rtx/post/aa/op": 4,
            "/rtx/post/dlss/execMode": 2,
            "/rtx/dldenoiser/enabled": True,
            "/rtx/dldenoiser/responsiveDenoising": True,
            "/rtx/rtpt/gaussian/accumulatedDepth/enabled": True,
            "/rtx/rtpt/gaussian/accumulatedAlbedo/enabled": True,
            "/rtx/rtpt/gaussian/maxGaussiansToAccumulate": 48,
        }
        for key, value in settings_values.items():
            settings.set(key, value)
        camera = UsdGeom.Camera.Define(stage, "/World/BenchCamera")
        op = UsdGeom.Xformable(camera).AddTransformOp()
        sun = UsdLux.DistantLight.Define(stage, "/World/BenchSun")
        sun.CreateIntensityAttr(1800.0)
        sun.CreateAngleAttr(12.0)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-45, 20, 30))
        UsdLux.DomeLight.Define(stage, "/World/BenchFill").CreateIntensityAttr(200.0)
        if len({tuple(v["resolution"]) for v in views}) != 1:
            raise ValueError("Use separate runs for different resolutions")
        rp = rep.create.render_product(
            str(camera.GetPath()), tuple(views[0]["resolution"])
        )
        writer = rep.WriterRegistry.get("BasicWriter")
        writer.initialize(output_dir=str(args.out / "raw_frames"), rgb=True)
        writer.attach([rp])
        depth = (
            rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
            if args.depth
            else None
        )
        if depth:
            depth.attach([rp])
        results = []
        for index, view in enumerate(views):
            started = time.monotonic()
            camera_evidence = view.get("camera_evidence")
            w, h = view["resolution"]
            k = np.asarray(view["K"])
            focal = 20.0
            horizontal, vertical = focal * w / k[0, 0], focal * h / k[1, 1]
            camera.CreateFocalLengthAttr(focal)
            camera.CreateHorizontalApertureAttr(horizontal)
            camera.CreateVerticalApertureAttr(vertical)
            camera.CreateHorizontalApertureOffsetAttr(
                (w / 2 - k[0, 2]) * horizontal / w
            )
            camera.CreateVerticalApertureOffsetAttr((k[1, 2] - h / 2) * vertical / h)
            camera.CreateClippingRangeAttr(Gf.Vec2f(*view["near_far"]))
            cv = np.asarray(view["camera_to_world_cv"])
            usd_column = cv @ np.diag([1.0, -1.0, -1.0, 1.0])
            op.Set(Gf.Matrix4d(usd_column.T.tolist()))
            for _ in range(args.settle_captures):
                rep.orchestrator.step(
                    rt_subframes=args.subframes,
                    pause_timeline=True,
                    delta_time=1 / 30,
                    wait_for_render=True,
                )
            rep.orchestrator.wait_until_complete()
            raw_index = (index + 1) * args.settle_captures - 1
            raw = args.out / "raw_frames" / f"rgb_{raw_index:04d}.png"
            image = args.out / (view["id"] + ".png")
            os.link(raw, image)
            view_path = args.out / (view["id"] + ".camera.json")
            save(view_path, view)
            depth_record = None
            if depth:
                z = np.asarray(depth.get_data(), dtype=np.float32)
                zpath = args.out / (view["id"] + ".depth.npy")
                np.save(zpath, z)
                valid = np.isfinite(z) & (z > 0) & (z < view["near_far"][1])
                depth_record = {
                    "path": str(zpath),
                    "sha256": sha(zpath),
                    "shape": list(z.shape),
                    "valid_fraction": float(valid.mean()),
                    "kind": "distance_to_image_plane",
                    "units": "meters",
                    "geometry_verified": False,
                    "warning": "Renderer Gaussian depth is a localization proxy, not solid-surface or collision GT.",
                }
            result = {
                "view_id": view["id"],
                "render": str(image),
                "render_sha256": sha(image),
                "candidate_digest": candidate_hash,
                "assets": assets,
                "camera_evidence_sha256": camera_evidence["sha256"]
                if camera_evidence
                else sha(args.cameras),
                "camera_evidence": camera_evidence,
                "camera_request": str(args.cameras),
                "camera_request_sha256": sha(args.cameras),
                "resolved_camera": str(view_path),
                "resolved_camera_sha256": sha(view_path),
                "camera_match_verified": False,
                "camera_status": "unverified_request",
                "renderer_script_sha256": sha(Path(__file__)),
                "render_settings": settings_values,
                "raw_capture_index": raw_index,
                "static_settle_captures": args.settle_captures,
                "readback_note": "One-frame readback lag; chosen sample follows at least2 renders of unchanged requested pose.",
                "depth": depth_record,
                "seconds": time.monotonic() - started,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            save(args.out / (view["id"] + ".render_provenance.json"), result)
            results.append(result)
            save(
                args.out / "render_manifest.json",
                {"views": results, "camera_match_verified": False},
            )
            print("VIEW_READY", view["id"], str(image), flush=True)
        writer.detach()
        if depth:
            depth.detach([rp])
        stage.GetRootLayer().Export(str(args.out / "render_stage.usda"))
    except BaseException as error:
        # SimulationApp.close may terminate with exit0; retain explicit failure evidence first.
        save(
            args.out / "render_failure.json",
            {
                "error": repr(error),
                "traceback": traceback.format_exc(),
                "raw_files": [str(p) for p in (args.out / "raw_frames").glob("*")],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        print("RENDER_FAILED", traceback.format_exc(), flush=True)
        raise
    finally:
        app.close()
        lock.close()


if __name__ == "__main__":
    main()
