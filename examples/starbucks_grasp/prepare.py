"""Prepare a source-backed cup and fixed-base G2 manipulation canary.

This first stage intentionally excludes the cafe background: it isolates arm
control/contact failures before composing and recording the repaired scene.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

from simready.simulation.articulated_kinematics import UsdJointTree


def portable_flatten(source, output):
    stage = Usd.Stage.Open(str(source))
    flat = Usd.Stage.Open(stage.Flatten())
    for prim in flat.Traverse():
        for attr in prim.GetAttributes():
            if attr.GetTypeName() != Sdf.ValueTypeNames.Asset:
                continue
            value = attr.Get()
            if not value or not value.path:
                continue
            path = Path(value.resolvedPath or value.path)
            if path.is_file():
                name = hashlib.sha256(path.read_bytes()).hexdigest()[:16] + path.suffix
                dest = output.parent / "textures" / name
                dest.parent.mkdir(exist_ok=True)
                if not dest.exists():
                    shutil.copy2(path, dest)
                attr.Set(Sdf.AssetPath("textures/" + name))
    flat.GetRootLayer().Export(str(output))


def box(stage, path, center, size, color):
    prim = UsdGeom.Cube.Define(stage, path)
    prim.CreateSizeAttr(1)
    prim.AddTranslateOp().Set(Gf.Vec3d(*center))
    prim.AddScaleOp().Set(Gf.Vec3f(*size))
    prim.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdPhysics.CollisionAPI.Apply(prim.GetPrim())
    return prim.GetPrim()


def round_table(stage, path, xy, radius=0.32, top=0.85):
    tabletop = UsdGeom.Cylinder.Define(stage, path)
    tabletop.CreateAxisAttr("Z")
    tabletop.CreateRadiusAttr(radius)
    tabletop.CreateHeightAttr(0.04)
    tabletop.AddTranslateOp().Set(Gf.Vec3d(*xy, top - 0.02))
    tabletop.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.16, 0.065)])
    UsdPhysics.CollisionAPI.Apply(tabletop.GetPrim())
    stem = UsdGeom.Cylinder.Define(stage, path + "Stem")
    stem.CreateAxisAttr("Z")
    stem.CreateRadiusAttr(0.035)
    stem.CreateHeightAttr(top - 0.05)
    stem.AddTranslateOp().Set(Gf.Vec3d(*xy, (top - 0.05) / 2))
    stem.CreateDisplayColorAttr([Gf.Vec3f(0.055, 0.05, 0.045)])
    UsdPhysics.CollisionAPI.Apply(stem.GetPrim())
    foot = UsdGeom.Cylinder.Define(stage, path + "Foot")
    foot.CreateAxisAttr("Z")
    foot.CreateRadiusAttr(0.22)
    foot.CreateHeightAttr(0.025)
    foot.AddTranslateOp().Set(Gf.Vec3d(*xy, 0.0125))
    foot.CreateDisplayColorAttr([Gf.Vec3f(0.055, 0.05, 0.045)])
    UsdPhysics.CollisionAPI.Apply(foot.GetPrim())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", type=Path, required=True)
    ap.add_argument("--cup", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--localization", type=Path)
    ap.add_argument("--layout", type=Path)
    args = ap.parse_args()
    target_xy = [0.55, -0.23]
    perception = None
    layout = (
        json.loads(args.layout.read_text())
        if args.layout
        else {
            "tables": {
                "Table": {"xy": [0.65, -0.13], "radius": 0.32, "top": 0.85},
                "TableB": {"xy": [0.4, -0.94], "radius": 0.32, "top": 0.85},
            },
            "destination": [0.4, -0.7, 0.85],
        }
    )
    destination = layout["destination"]
    if args.localization:
        perception = json.loads(args.localization.read_text())
        if (
            perception.get("status") != "PASS"
            or perception.get("ground_truth_object_pose_read") is not False
        ):
            raise ValueError("Require a passed non-oracle visual localization")
        target_xy = perception["cup_bottom_xyz_m"][:2]
        if len(target_xy) != 2 or not np.isfinite(target_xy).all():
            raise ValueError("Invalid visual target")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    portable_flatten(args.robot, out / "robot.usdc")
    portable_flatten(args.cup, out / "cup.usdc")
    s = Usd.Stage.CreateNew(str(out / "task.usda"))
    UsdGeom.SetStageMetersPerUnit(s, 1)
    UsdGeom.SetStageUpAxis(s, "Z")
    world = UsdGeom.Xform.Define(s, "/World")
    s.SetDefaultPrim(world.GetPrim())
    robot = s.DefinePrim("/World/G2", "Xform")
    robot.GetReferences().AddReference("robot.usdc")
    cup = s.DefinePrim("/World/Cup", "Xform")
    cup.GetReferences().AddReference("cup.usdc", "/World/Object")
    UsdGeom.Xformable(cup).AddTranslateOp().Set(Gf.Vec3d(0.55, -0.23, 0.852))
    UsdPhysics.MassAPI(cup).CreateMassAttr(0.085)
    UsdPhysics.RigidBodyAPI(cup).CreateKinematicEnabledAttr(False)
    # Overrides the unmeasured legacy restitution 0.4; all values remain priors.
    mat = UsdShade.Material.Define(s, "/World/CupContact")
    contact = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
    contact.CreateStaticFrictionAttr(0.8)
    contact.CreateDynamicFrictionAttr(0.6)
    contact.CreateRestitutionAttr(0)
    for prim in Usd.PrimRange(cup):
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat, materialPurpose="physics")
    for name, table in layout["tables"].items():
        round_table(s, "/World/" + name, table["xy"], table["radius"], table["top"])
    box(s, "/World/Ground", (0, 0, -0.05), (5, 5, 0.1), (0.3, 0.3, 0.3))
    k = UsdJointTree(s, "/World/G2/base_link")
    names = [f"idx6{i}_arm_r_joint{i}" for i in range(1, 8)]
    body = "/World/G2/gripper_r_base_link"
    pose = np.eye(4)
    pose[:3, :3] = [[0, 1, 0], [1, 0, 0], [0, 0, -1]]
    q = np.zeros(7)
    waypoints = []
    for label, xyz in [
        ("pregrasp", [*target_xy, 1.30]),
        ("grasp", [*target_xy, 1.115]),
        ("lift", [*target_xy, 1.265]),
        ("transfer", [*destination[:2], 1.265]),
        ("place", [*destination[:2], destination[2] + 0.265]),
    ]:
        pose[:3, 3] = xyz
        for attempt in range(9):
            try:
                q = k.solve(
                    body,
                    pose,
                    names,
                    q
                    if attempt == 0
                    else np.random.default_rng(attempt).uniform(-1, 1, 7),
                )
                break
            except ValueError:
                if attempt == 8:
                    raise
        waypoints.append(
            {"name": label, "q": q.tolist(), "gripper_base_xyz": pose[:3, 3].tolist()}
        )
    initial = dict(zip(names, waypoints[0]["q"], strict=True))
    # Sample Cartesian transfers before execution; sequential IK preserves the
    # tool orientation while solving the redundant arm. These are planning
    # targets; measured poses are recorded separately by the physics worker.
    paths = {}
    for start, end in [
        ("pregrasp", "grasp"),
        ("grasp", "lift"),
        ("lift", "transfer"),
        ("transfer", "place"),
        ("place", "transfer"),
    ]:
        a = next(w for w in waypoints if w["name"] == start)
        b = next(w for w in waypoints if w["name"] == end)
        qq = np.array(a["q"])
        samples = []
        for t in np.linspace(0, 1, 81):
            pose[:3, 3] = (1 - t) * np.array(a["gripper_base_xyz"]) + t * np.array(
                b["gripper_base_xyz"]
            )
            qq = k.solve(body, pose, names, qq)
            samples.append(qq.tolist())
        paths[start + "_" + end] = samples
        b["q"] = samples[-1]
    # Park the unused arm with the same measured joint frames.
    left_names = [f"idx2{i}_arm_l_joint{i}" for i in range(1, 8)]
    pose[:3, 3] = [0.12, 0.40, 1.10]
    for attempt in range(12):
        try:
            left_q = k.solve(
                "/World/G2/gripper_l_base_link",
                pose,
                left_names,
                np.random.default_rng(attempt).uniform(-1, 1, 7),
            )
            initial.update(dict(zip(left_names, left_q, strict=True)))
            break
        except ValueError:
            if attempt == 11:
                raise
    for prim in list(Usd.PrimRange(robot)):
        # Explicit multi-joint drives replace legacy mimic declarations with
        # mismatched axes. The coupling/sign correction is saved in the plan.
        schemas = prim.GetMetadata("apiSchemas")
        for schema in schemas.GetAppliedItems() if schemas else []:
            if schema.startswith("PhysxMimicJointAPI:"):
                prim.RemoveAppliedSchema(schema)
        if prim.IsA(UsdPhysics.RevoluteJoint):
            j = UsdPhysics.RevoluteJoint(prim)
            name = prim.GetName()
            if "body_joint" in name:
                j.CreateLowerLimitAttr(0)
                j.CreateUpperLimitAttr(0)
            drive = UsdPhysics.DriveAPI.Apply(prim, "angular")
            drive.CreateTypeAttr("force")
            drive.CreateStiffnessAttr(800 if "arm_" in name else 100)
            drive.CreateDampingAttr(40 if "arm_" in name else 5)
            drive.CreateMaxForceAttr(
                80 if "arm_" in name else (0.6 if "gripper_" in name else 10)
            )
            drive.CreateTargetPositionAttr(float(np.rad2deg(initial.get(name, 0))))
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            # Start in a kinematically consistent configuration, then let PhysX
            # exclusively evolve poses. No object attachment is authored.
            body_pose = k.pose(str(prim.GetPath()), initial)
            parent = np.array(
                UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
            ).T
            local = np.linalg.inv(parent) @ body_pose
            xf = UsdGeom.Xformable(prim)
            xf.ClearXformOpOrder()
            xf.AddTransformOp(opSuffix="initial").Set(Gf.Matrix4d(local.T.tolist()))
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(True)
            if "gripper_r_" in str(prim.GetPath()):
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                    mat, materialPurpose="physics"
                )
    s.GetRootLayer().Save()
    plan = {
        "schema_version": 1,
        "status": "CONTACT_CANARY_PENDING",
        "robot_root": "/World/G2",
        "arm_names": names,
        "waypoints": waypoints,
        "paths": paths,
        "initial": initial,
        "cup": {
            "path": "/World/Cup",
            "bottom_z_m": 0.85,
            "mass_kg": 0.085,
            "height_m": 0.105,
            "parameter_source": "unmeasured legacy geometry scale and chosen contact prior",
            "real_world_validated": False,
        },
        "destination": {
            "table": "/World/TableB",
            "cup_bottom_xyz_m": destination,
        },
        "gripper_coupling": "left inner/outer negative; right inner/outer and both support joints positive; original limits preserved",
        "fixture": "root fixed; five torso joints locked at zero for arm-only canary",
        "scope": "fixed-base G2, reconstructed cup, synthetic support table; no visual localization yet",
        "sources": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [args.robot, args.cup]
        },
    }
    if perception:
        plan["visual_localization"] = perception
        plan["visual_localization_sha256"] = hashlib.sha256(
            args.localization.read_bytes()
        ).hexdigest()
        plan["scope"] = (
            "fixed-base G2; RGB-D localized known reconstructed cup; known destination table"
        )
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    if args.layout:
        shutil.copy2(args.layout, out / "task_layout.json")
    print(json.dumps({"out": str(out), "waypoints": waypoints}, indent=2))


if __name__ == "__main__":
    main()
