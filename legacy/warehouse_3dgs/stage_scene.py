"""Dual-dog staging: Go2(+Z1 arm) at rack picking crate, Lite3 (with backpack) waiting to carry."""


def main():
    import os

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(
        headless=True, offscreen_render=True, experience="isaacsim.exp.base.kit"
    )
    app = launcher.app
    import omni.usd, omni.kit.app, omni.kit.commands
    import omni.replicator.core as rep
    import numpy as np
    from PIL import Image
    from pxr import Usd, UsdGeom, Sdf, Gf

    CDN = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2/Isaac/Environments/Simple_Warehouse"
    ctx = omni.usd.get_context()
    ctx.open_stage(f"{CDN}/warehouse.usd")
    kit = omni.kit.app.get_app()
    omni.kit.commands.execute(
        "ChangeSetting", path="rtx/post/tonemap/exposure", value=1.4
    )
    for _ in range(30):
        kit.update()
    stage = ctx.get_stage()
    ROOT = os.path.dirname(os.path.abspath(__file__))
    GO2 = f"{ROOT}/../go2_model/urdf/go2_description_local.urdf"
    Z1 = f"{ROOT}/../z1_arm/xacro/z1_local.urdf"
    LITE3 = f"{ROOT}/../lite3_model/Lite3/usd/Lite3.usd"

    # ---- import robots (URDF importer converts & saves usd next to urdf) ----
    from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig

    def conv(urdf):
        cfg = URDFImporterConfig()
        cfg.urdf_path = urdf
        cfg.merge_fixed_joints = False
        cfg.fix_base = True
        cfg.make_default_prim = True
        cfg.create_physics_scene = False
        return URDFImporter(cfg).import_urdf()

    go2_usd = conv(GO2)
    for _ in range(10):
        kit.update()
    z1_usd = conv(Z1)
    for _ in range(10):
        kit.update()
    print("go2:", go2_usd, "z1:", z1_usd)

    # ---- assemble scene ----
    SPOT_PICK = (2.0, -6.0, 0.0)  # Go2 beside rack
    SPOT_CARRY = (4.5, -6.0, 0.0)  # Lite3 waiting
    omni.kit.commands.execute(
        "CreatePrimCommand", prim_type="Scope", prim_path="/World/Demo"
    )

    def add_ref(path, url):
        prim = stage.DefinePrim(path, "Xform")
        prim.GetReferences().AddReference(url)
        return prim

    go2 = add_ref(
        "/World/Demo/Go2", go2_usd if isinstance(go2_usd, str) else str(go2_usd)
    )
    z1 = add_ref("/World/Demo/Z1", z1_usd if isinstance(z1_usd, str) else str(z1_usd))
    lite3 = add_ref("/World/Demo/Lite3", LITE3)
    for _ in range(10):
        kit.update()

    def place(prim, xyz, yaw_deg=0.0, scale=1.0):
        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        t = xf.AddTranslateOp()
        t.Set(Gf.Vec3d(*xyz))
        r = xf.AddRotateZOp()
        r.Set(yaw_deg)
        s = xf.AddScaleOp()
        s.Set(Gf.Vec3d(scale, scale, scale))

    place(go2, SPOT_PICK, yaw_deg=90)  # face rack (+? rack at -y side)
    place(lite3, SPOT_CARRY, yaw_deg=90, scale=1.0)
    # Z1 arm on Go2 back: go2 torso height ~0.55*? lite3/go2 ~0.35m back height; mount at back center
    place(z1, (SPOT_PICK[0] + 0.0, SPOT_PICK[1], 0.38), yaw_deg=90)
    for _ in range(10):
        kit.update()

    # rack + crates at pick station
    rack = add_ref("/World/Demo/Rack", f"{CDN}/Props/SM_ShelfRack_A.usd")
    place(rack, (2.0, -8.0, 0.0), 0.0)
    for i, (x, y, z, sc) in enumerate(
        [(1.6, -8.0, 0.95, 0.6), (2.4, -8.0, 0.95, 0.6), (2.0, -8.0, 1.75, 0.6)]
    ):
        c = add_ref(f"/World/Demo/Crate{i}", f"{CDN}/Props/SM_Crate_A_01.usd")
        place(c, (x, y, z), 0.0, sc)
    # floating crate in Z1 gripper pose (mid hand-off)
    hand = add_ref("/World/Demo/CrateHand", f"{CDN}/Props/SM_Crate_A_01.usd")
    place(hand, (2.5, -6.9, 0.85), 20.0, 0.6)
    # backpack platform + crate on Lite3
    bp = stage.DefinePrim("/World/Demo/Lite3Backpack", "Cube")
    UsdGeom.Cube(bp).GetSizeAttr().Set(0.30)
    place(bp, (SPOT_CARRY[0], SPOT_CARRY[1], 0.42), 90.0)
    crt = add_ref("/World/Demo/CrateOnLite3", f"{CDN}/Props/SM_Crate_A_01.usd")
    place(crt, (SPOT_CARRY[0], SPOT_CARRY[1], 0.62), 0.0, 0.6)
    for _ in range(15):
        kit.update()

    # cinematic camera
    cam = stage.DefinePrim("/World/CineCam", "Camera")
    xf = UsdGeom.Xformable(cam)
    xf.ClearXformOpOrder()
    t = xf.AddTranslateOp()
    t.Set(Gf.Vec3d(3.2, -4.2, 1.1))
    q = xf.AddOrientOp()
    from simready_bridge.geometry import usd_look_at_quaternion as lookat

    w, x_, y_, z_ = lookat((3.2, -4.2, 1.1), (2.3, -6.8, 0.5))
    q.Set(Gf.Quatf(w, x_, y_, z_))
    rp = rep.create.render_product("/World/CineCam", resolution=(1280, 720))
    rgb_a = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb_a.attach([rp])
    kit.update()
    kit.update()
    rep.orchestrator.step(rt_subframes=48)
    kit.update()
    data = rgb_a.get_data()
    arr = np.ascontiguousarray(data[..., :3], dtype=np.uint8)
    Image.fromarray(arr).save(os.path.join(ROOT, "stage_dual_dog.png"))
    stage.Export(os.path.join(ROOT, "stage_dual_dog.usda"))
    print("SAVED stage_dual_dog.png")
    app.close()


if __name__ == "__main__":
    main()
