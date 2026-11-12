"""Render multi-view RGB of Isaac Simple_Warehouse (CDN) with one moving USD camera.
Poses exact -> no COLMAP. Outputs view_XXXX.png + poses.json (eye/target per view)."""


def main():
    import os, sys, json, argparse

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="views")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--res", type=int, default=720)
    parser.add_argument("--seed", type=int, default=0)
    args, _ = parser.parse_known_args()

    from isaaclab.app import AppLauncher

    launcher = AppLauncher(
        headless=True, offscreen_render=True, experience="isaacsim.exp.base.kit"
    )
    simulation_app = launcher.app

    import omni.usd, omni.kit.commands, omni.kit.app
    import omni.replicator.core as rep
    import numpy as np
    from PIL import Image
    from pxr import Usd, UsdGeom, Gf

    CDN = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2/Isaac/Environments/Simple_Warehouse/warehouse.usd"
    ctx = omni.usd.get_context()
    ctx.open_stage(CDN)
    kit = omni.kit.app.get_app()
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/renderMode", value="PathTracing"
    )
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/post/tonemap/exposure", value=1.6
    )
    for _ in range(30):
        kit.update()

    stage = ctx.get_stage()
    bbox = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True
    )
    r = bbox.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    mn = np.array(r.GetMin())
    mx = np.array(r.GetMax())
    print("bbox", mn.round(1), mx.round(1), flush=True)

    import omni.physx
    from omni.physx import get_physx_scene_query_interface as physx_sqi

    def floor_hit(x, y):
        hit = physx_sqi().raycast_closest(
            carb.Float3(x, y, 6.0), carb.Float3(0, 0, -1), 12.0
        )
        if isinstance(hit, dict):
            p = hit.get("position")
            return p[2] if p is not None else None
        return hit.position.z if hit else None

    import carb

    os.makedirs(args.out, exist_ok=True)
    # one camera + one render product
    cam_path = "/World/WalkCam"
    cam = stage.DefinePrim(cam_path, "Camera")
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    t_op = xf.AddTranslateOp()
    q_op = xf.AddOrientOp()
    W = round(args.res * 16 / 9)
    rp = rep.create.render_product(cam_path, resolution=(W, args.res))
    rgb_a = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb_a.attach([rp])
    dep_a = rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
    dep_a.attach([rp])

    from simready.geometry.cameras import usd_look_at_quaternion as lookat_quat

    rng = np.random.default_rng(args.seed)
    poses = []
    cx = (mn[0] + mx[0]) / 2
    cy = (mn[1] + mx[1]) / 2
    lanes = np.linspace(mn[1] + 1.5, mx[1] - 1.5, max(3, args.n // 60))
    for i in range(args.n):
        ok = False
        for _try in range(12):
            if i < args.n * 0.75:
                y = float(rng.uniform(mn[1] + 1.5, mx[1] - 1.5))
                x = float(rng.uniform(mn[0] + 1.0, mx[0] - 1.0))
                z = floor_hit(x, y)
                if z is None or z > 0.4:
                    continue
                eye = (x, y, z + 1.2)
                if rng.random() < 0.8:
                    fy = float(rng.choice([-1.0, 1.0]))
                    fx = rng.uniform(-0.35, 0.35)
                else:
                    fy = rng.uniform(-1, 1)
                    fx = rng.uniform(-1, 1)
                fwd = np.array([fx, fy, rng.uniform(-0.06, 0.08)])
                fwd /= np.linalg.norm(fwd)
                target = np.array(eye) + fwd * 5.0
            else:
                th = rng.uniform(0, 2 * np.pi)
                rad = (mx[0] - mn[0]) * 0.42
                eye = (
                    cx + rad * np.cos(th),
                    cy + rad * np.sin(th) * 0.6,
                    float(rng.uniform(1.5, 4.0)),
                )
                target = (cx, cy, 1.0)
            ok = True
            break
        if not ok:
            continue
        t_op.Set(Gf.Vec3d(*eye))
        w, xq, yq, zq = lookat_quat(eye, target)
        q_op.Set(Gf.Quatf(w, xq, yq, zq))
        kit.update()
        kit.update()
        rep.orchestrator.step(rt_subframes=24)
        kit.update()
        data = rgb_a.get_data()
        arr = np.ascontiguousarray(data[..., :3], dtype=np.uint8)
        if arr.mean() < 8 or arr.std() < 15:
            continue  # black or empty view
        Image.fromarray(arr).save(os.path.join(args.out, f"view_{i:04d}.png"))
        dv = dep_a.get_data()
        np.save(
            os.path.join(args.out, f"depth_{i:04d}.npy"),
            np.asarray(dv, dtype=np.float32),
        )
        poses.append(
            {
                "eye": list(map(float, eye)),
                "target": [float(target[0]), float(target[1]), float(target[2])],
            }
        )
        if i % 20 == 0:
            print(f"{i}/{args.n}", flush=True)
    json.dump(poses, open(os.path.join(args.out, "poses.json"), "w"))
    print("DONE", len(poses))
    simulation_app.close()


if __name__ == "__main__":
    main()
