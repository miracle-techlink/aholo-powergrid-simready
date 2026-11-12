"""Capture RGB view + world-space pointcloud (subsampled) per pose."""


def main():
    import os, json, argparse

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="views_pc")
    parser.add_argument("--n", type=int, default=600)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--sub", type=int, default=60000)
    parser.add_argument("--subframes", type=int, default=12)
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
    import omni.physx
    from omni.physx import get_physx_scene_query_interface as physx_sqi
    import carb

    def floor_hit(x, y):
        hit = physx_sqi().raycast_closest(
            carb.Float3(x, y, 6.0), carb.Float3(0, 0, -1), 12.0
        )
        if isinstance(hit, dict):
            p = hit.get("position")
            return p[2] if p is not None else None
        return hit.position.z if hit else None

    os.makedirs(args.out, exist_ok=True)
    W, H = 960, 540
    cam = stage.DefinePrim("/World/WalkCam", "Camera")
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    t_op = xf.AddTranslateOp()
    q_op = xf.AddOrientOp()
    rp = rep.create.render_product("/World/WalkCam", resolution=(W, H))
    rgb_a = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb_a.attach([rp])
    pc_a = rep.AnnotatorRegistry.get_annotator("pointcloud")
    pc_a.attach([rp])
    from simready_bridge.geometry import usd_look_at_quaternion as lookat_quat

    rng = np.random.default_rng(args.seed)
    poses = []
    n = 0
    cx = (mn[0] + mx[0]) / 2
    cy = (mn[1] + mx[1]) / 2
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
                    fy_ = float(rng.choice([-1.0, 1.0]))
                    fx_ = rng.uniform(-0.35, 0.35)
                else:
                    fy_ = rng.uniform(-1, 1)
                    fx_ = rng.uniform(-1, 1)
                fwd = np.array([fx_, fy_, rng.uniform(-0.06, 0.08)])
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
        rep.orchestrator.step(rt_subframes=args.subframes)
        kit.update()
        data = rgb_a.get_data()
        arr = np.ascontiguousarray(data[..., :3], dtype=np.uint8)
        if arr.mean() < 8 or arr.std() < 15:
            continue
        pd = pc_a.get_data()
        pts = np.asarray(pd["data"], dtype=np.float32)
        rgbs = np.asarray(pd["pointRgb"])[..., :3]
        if len(pts) != len(rgbs) or len(pts) < 500:
            continue
        good = np.isfinite(pts).all(-1) & (rgbs.astype(np.int32).sum(-1) > 15)
        pts = pts[good]
        rgbs = rgbs[good]
        if len(pts) < 500:
            continue
        k = min(args.sub, len(pts))
        sel = rng.choice(len(pts), k, replace=False)
        np.savez_compressed(
            os.path.join(args.out, f"pc_{n:04d}.npz"),
            pts=pts[sel].astype(np.float32),
            rgb=rgbs[sel].astype(np.uint8),
        )
        Image.fromarray(arr).save(os.path.join(args.out, f"view_{n:04d}.png"))
        poses.append(
            {
                "eye": list(map(float, eye)),
                "target": [float(target[0]), float(target[1]), float(target[2])],
            }
        )
        n += 1
        if n % 20 == 0:
            print(f"{n} saved", flush=True)
    json.dump(poses, open(os.path.join(args.out, "poses.json"), "w"))
    print("DONE", n)
    simulation_app.close()


if __name__ == "__main__":
    main()
