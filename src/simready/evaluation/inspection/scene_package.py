"""Independent structural inspection of a newly generated USD scene package.

This checks loadability, finite bounds and physical schema consistency. It does
not infer reconstruction accuracy, scale truth, or robot task success.
"""

import argparse
import math
from pathlib import Path

from simready.core.artifacts import digest, write_json


def inspect_scene(directory):
    from pxr import Usd, UsdGeom, UsdPhysics

    root = Path(directory).resolve()
    stage = Usd.Stage.Open(str(root / "scene.usda"))
    if stage is None:
        raise ValueError("Generated scene cannot be opened")
    if stage.GetCompositionErrors():
        raise ValueError("Generated scene has unresolved USD composition dependencies")
    dependencies = []
    for layer in stage.GetUsedLayers():
        if layer.anonymous:
            continue
        path = Path(layer.realPath).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Generated USD has an external or missing dependency")
        dependencies.append(
            {"path": str(path.relative_to(root)), "sha256": digest(path)}
        )
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
    )
    bounds = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    values = [*bounds.GetMin(), *bounds.GetMax()]
    if bounds.IsEmpty() or not all(math.isfinite(x) for x in values):
        raise ValueError("Scene bounds are empty or nonfinite")
    splats = collisions = joints = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() == "ParticleField3DGaussianSplat":
            splats += 1
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                raise ValueError("Gaussian appearance cannot serve as solid collision")
        collisions += int(prim.HasAPI(UsdPhysics.CollisionAPI))
        if prim.IsA(UsdPhysics.PrismaticJoint):
            joints += 1
            joint = UsdPhysics.PrismaticJoint(prim)
            low, high = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
            if (
                low is None
                or high is None
                or not math.isfinite(low)
                or not math.isfinite(high)
                or low >= high
            ):
                raise ValueError("Invalid prismatic travel limits")
            targets = joint.GetBody1Rel().GetTargets()
            if len(targets) != 1 or not stage.GetPrimAtPath(targets[0]).HasAPI(
                UsdPhysics.RigidBodyAPI
            ):
                raise ValueError("Prismatic joint lacks a real movable rigid body")
    if splats != 1:
        raise ValueError("Expected exactly one source Gaussian appearance layer")
    return {
        "schema_version": 1,
        "passed": True,
        "scope": "USD structure only",
        "dependencies": sorted(dependencies, key=lambda x: x["path"]),
        "gaussian_fields": splats,
        "collision_prims": collisions,
        "prismatic_joints": joints,
        "bounds_world": {"min": list(bounds.GetMin()), "max": list(bounds.GetMax())},
        "reconstruction_fidelity_validated": False,
        "metric_scale_independently_validated": False,
        "robot_task_validated": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-directory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    write_json(args.out, inspect_scene(args.scene_directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
