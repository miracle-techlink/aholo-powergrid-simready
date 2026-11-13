"""Tuck the original G2 demo's arms using native joint frames and limits."""

import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--robot", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = Usd.Stage.Open(str(args.robot))
    root = source.GetDefaultPrim()
    root_path = str(root.GetPath())
    angles = {
        "idx21_arm_l_joint1": 90,
        "idx61_arm_r_joint1": -90,
        "idx22_arm_l_joint2": -90,
        "idx62_arm_r_joint2": -90,
    }

    def frame(position, quaternion):
        result = Gf.Matrix4d().SetRotate(Gf.Quatd(quaternion))
        result.SetTranslateOnly(Gf.Vec3d(position))
        return result

    joints = []
    for p in source.Traverse():
        if not p.IsA(UsdPhysics.Joint):
            continue
        j = UsdPhysics.Joint(p)
        a, b = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
        if not a or not b:
            continue
        angle = angles.get(p.GetName(), 0)
        axis = Gf.Vec3d(0, 0, 1)
        if p.IsA(UsdPhysics.RevoluteJoint):
            rev = UsdPhysics.RevoluteJoint(p)
            low, high = rev.GetLowerLimitAttr().Get(), rev.GetUpperLimitAttr().Get()
            if not low <= angle <= high:
                raise ValueError(
                    f"Joint limit violation: {p.GetName()} {angle} {low} {high}"
                )
            axis = Gf.Vec3d(
                *{"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[
                    rev.GetAxisAttr().Get()
                ]
            )
        joints.append(
            (
                str(a[0]),
                str(b[0]),
                frame(j.GetLocalPos0Attr().Get(), j.GetLocalRot0Attr().Get()),
                frame(j.GetLocalPos1Attr().Get(), j.GetLocalRot1Attr().Get()),
                angle,
                axis,
            )
        )
    base = root_path + "/arm_base_link"
    poses = {
        base: UsdGeom.Xformable(
            source.GetPrimAtPath(base)
        ).ComputeLocalToWorldTransform(0)
    }
    for _ in range(40):
        for a, b, local0, local1, angle, axis in joints:
            if a in poses and b not in poses:
                poses[b] = (
                    local1.GetInverse()
                    * Gf.Matrix4d().SetRotate(Gf.Rotation(axis, angle))
                    * local0
                    * poses[a]
                )
    for path, pose in poses.items():
        xf = UsdGeom.Xformable(source.GetPrimAtPath(path))
        xf.ClearXformOpOrder()
        xf.AddTransformOp(opSuffix="navigationPose").Set(pose)
    # Export a new, self-contained USD. The source stage is not saved.
    output = args.out / "robot_nav.usdc"
    source.Flatten().Export(str(output))
    vertices = []
    for prim in source.Traverse():
        if prim.IsA(UsdGeom.Mesh) and "/visuals/" in str(prim.GetPath()):
            pts = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get())
            t = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim))
            vertices.append(pts @ t[:3, :3] + t[3, :3])
    points = np.concatenate(vertices)
    bins = np.floor(points[:, 2] / 0.025).astype(int)
    profile = {
        str(int(b)): [
            points[bins == b, :2].min(0).tolist(),
            points[bins == b, :2].max(0).tolist(),
        ]
        for b in np.unique(bins)
    }
    (args.out / "height_profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    (args.out / "pose.json").write_text(
        json.dumps(
            {
                "scope": "kinematic demo pose; native joint frames and limits, no articulated dynamics claim",
                "joint_degrees": angles,
                "bounds": [points.min(0).tolist(), points.max(0).tolist()],
                "self_collision_validated": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "bounds": [points.min(0).tolist(), points.max(0).tolist()],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
