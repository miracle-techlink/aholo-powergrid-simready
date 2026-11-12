#!/usr/bin/env python3
"""Test render-loop variants to find the fastest capture path.

Variants:
  A_baseline   — orchestrator.step + kit.update (current fast_render.py)
  B_kitonly    — pure kit.update() x2 + get_data (skip orchestrator)
  C_kitonly_tuned — B + denoiser off + DLSS off + AA off
Also: measure stage open from .usda vs .usdc
"""


def main():
    import os, sys, time, json, argparse

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    os.environ.setdefault("LD_PRELOAD", "/lib/aarch64-linux-gnu/libgomp.so.1")

    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", default="/tmp/scene_test.usda")
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--out", default="/tmp/variant_result.json")
    args, _ = parser.parse_known_args()

    from isaaclab.app import AppLauncher

    t0 = time.time()
    launcher = AppLauncher(
        headless=True, offscreen_render=True, experience="isaacsim.exp.base.kit"
    )
    simulation_app = launcher.app
    p0 = time.time() - t0

    import omni.usd, omni.kit.commands, omni.kit.app
    import omni.replicator.core as rep
    import numpy as np
    from pxr import Usd, UsdGeom, Gf
    import carb

    kit = omni.kit.app.get_app()
    ctx = omni.usd.get_context()

    results = {"P0_app_start": p0}

    # --- stage open timing (usda) ---
    t = time.time()
    ctx.open_stage(os.path.abspath(args.scene))
    for _ in range(30):
        kit.update()
    results["P1_stage_open_usda"] = time.time() - t

    stage = ctx.get_stage()
    W, H = 640, 360

    cam = stage.DefinePrim("/World/RenderCam", "Camera")
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    t_op = xf.AddTranslateOp()
    q_op = xf.AddOrientOp()
    rp = rep.create.render_product("/World/RenderCam", resolution=(W, H))
    rgb_a = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb_a.attach([rp])

    def move_cam(i):
        t_op.Set(Gf.Vec3d(8 * np.cos(i * 0.05), 8 * np.sin(i * 0.05), 1.5))
        q_op.Set(Gf.Quatf(0.7071, 0.7071, 0, 0))

    def bench(name, frame_fn, n):
        move_cam(0)
        frame_fn()  # warmup
        t = time.time()
        ok = 0
        for i in range(n):
            move_cam(i + 1)
            arr = frame_fn()
            if arr is not None and arr.mean() > 5:
                ok += 1
        el = time.time() - t
        results[name] = {
            "fps": round(n / el, 2),
            "ms": round(el / n * 1000, 1),
            "valid": f"{ok}/{n}",
        }
        print(name, results[name], flush=True)

    # --- A: orchestrator.step + update (baseline) ---
    def variant_a():
        rep.orchestrator.step(rt_subframes=1)
        kit.update()
        d = rgb_a.get_data()
        return np.ascontiguousarray(d[..., :3], dtype=np.uint8)

    bench("A_orchestrator", variant_a, args.frames)

    # --- B: pure kit.update x2 ---
    def variant_b():
        kit.update()
        kit.update()
        d = rgb_a.get_data()
        return np.ascontiguousarray(d[..., :3], dtype=np.uint8)

    bench("B_kitonly_x2", variant_b, args.frames)

    # --- B1: pure kit.update x1 ---
    def variant_b1():
        kit.update()
        d = rgb_a.get_data()
        return np.ascontiguousarray(d[..., :3], dtype=np.uint8)

    bench("B_kitonly_x1", variant_b1, args.frames)

    # --- C: tuned settings + kitonly ---
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/post/dlss/enabled", value=False
    )
    omni.kit.commands.execute("ChangeSetting", path="rtx/post/aa/op", value=0)  # off
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/post/denoiser/enabled", value=False
    )
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/indirectLight/enabled", value=False
    )
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/transient/ecolorbuffer/historylength", value=0
    )
    for _ in range(5):
        kit.update()
    bench("C_tuned_kitonly", variant_b1, args.frames)

    # --- D: tuned + orchestrator ---
    bench("D_tuned_orchestrator", variant_a, args.frames)

    # --- E: usdc stage open ---
    try:
        from pxr import UsdUtils, Sdf

        usdc_path = args.scene.replace(".usda", ".usdc")
        t = time.time()
        stage_flatten = Usd.Stage.Open(args.scene)
        stage_flatten.Export(usdc_path)
        results["usdc_export_s"] = round(time.time() - t, 2)
        ctx.close_stage()
        t = time.time()
        ctx.open_stage(os.path.abspath(usdc_path))
        for _ in range(30):
            kit.update()
        results["P1_stage_open_usdc"] = round(time.time() - t, 2)
        bench("E_usdc_tuned", variant_b1, args.frames)
    except Exception as e:
        results["usdc_error"] = str(e)

    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(json.dumps(results, indent=1))
    simulation_app.close()


if __name__ == "__main__":
    main()
