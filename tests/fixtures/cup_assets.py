"""Shared synthetic inputs; no test collection or real device initialization."""

import json

from tests.paths import REPOSITORY_ROOT

PROTOCOL = json.loads(
    (REPOSITORY_ROOT / "examples/starbucks_grasp/robustness.json").read_text()
)


def fixture_assets(tmp_path, explicit_inertia):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    assets = tmp_path / "source"
    assets.mkdir()
    stage = Usd.Stage.CreateNew(str(assets / "task.usda"))
    UsdGeom.Xform.Define(stage, "/World")
    cup = UsdGeom.Xform.Define(stage, "/World/Cup").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(cup)
    mass = UsdPhysics.MassAPI.Apply(cup)
    mass.CreateMassAttr(0.085)
    if explicit_inertia:
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.001, 0.002, 0.002))
    for path in [
        "/World/Cup/collision",
        "/World/G2/gripper_r_inner/shape",
        "/World/Table",
        "/World/TableB",
    ]:
        prim = UsdGeom.Cube.Define(stage, path).GetPrim()
        UsdPhysics.CollisionAPI.Apply(prim)
    stage.GetRootLayer().Save()
    for name in ("robot.usdc", "cup.usdc"):
        dependency = Usd.Stage.CreateNew(str(assets / name))
        dependency.GetRootLayer().Save()
    (assets / "plan.json").write_text('{"nominal_control": "unchanged"}')
    return assets
