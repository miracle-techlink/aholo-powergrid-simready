"""Spring-tool USD and collision envelopes without historical scene downloads."""

import numpy as np
import pytest

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402

from simready.simulation.arm_planner import ArmScenePlanner
from simready.simulation.kinematics import Chain
from simready.simulation.press_tool import build_spring_plunger


def articulated_fixture(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    parent = "/World/FrankaArm/Geometry/world/link00"
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, parent).GetPrim())
    for index in range(1, 7):
        child = parent + f"/link{index:02d}"
        body = UsdGeom.Xform.Define(stage, child).GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(body)
        joint = UsdPhysics.RevoluteJoint.Define(
            stage, f"/World/FrankaArm/Physics/joint{index}"
        )
        joint.CreateBody0Rel().SetTargets([parent])
        joint.CreateBody1Rel().SetTargets([child])
        joint.CreateAxisAttr("Z")
        joint.CreateLocalPos0Attr(Gf.Vec3f(0))
        joint.CreateLocalPos1Attr(Gf.Vec3f(0))
        joint.CreateLocalRot0Attr(Gf.Quatf(1))
        joint.CreateLocalRot1Attr(Gf.Quatf(1))
        joint.CreateLowerLimitAttr(-180)
        joint.CreateUpperLimitAttr(180)
        parent = child
    flange = stage.GetPrimAtPath(parent)
    mass = UsdPhysics.MassAPI.Apply(flange)
    mass.CreateMassAttr(0.2)
    mass.CreateCenterOfMassAttr(Gf.Vec3f(0))
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.001, 0.002, 0.003))
    mass.CreatePrincipalAxesAttr(Gf.Quatf(1))
    rig_path = tmp_path / "synthetic_arm.usda"
    stage.GetRootLayer().Export(str(rig_path))
    return stage, parent, Chain.from_usd(rig_path)


def test_passive_plunger_has_spring_mass_and_conservative_travel_envelope(tmp_path):
    stage, flange, chain = articulated_fixture(tmp_path)
    cfg = {
        "flange_offset_m": [0.100, 0, 0],
        "tip_radius_m": 0.004,
        "travel_m": 0.004,
        "housing_mass_kg": 0.045,
        "plunger_mass_kg": 0.012,
        "spring_N_m": 800,
        "damping_N_s_m": 1.2,
    }
    old_mass = float(
        UsdPhysics.MassAPI(stage.GetPrimAtPath(flange)).GetMassAttr().Get()
    )
    info = build_spring_plunger(stage, flange, cfg)
    assert np.isclose(
        float(UsdPhysics.MassAPI(stage.GetPrimAtPath(flange)).GetMassAttr().Get()),
        old_mass + 0.045,
    )
    body = stage.GetPrimAtPath(info["contact_body"])
    assert not UsdPhysics.RigidBodyAPI(body).GetKinematicEnabledAttr().Get()
    joint = UsdPhysics.PrismaticJoint(
        stage.GetPrimAtPath("/World/FrankaArm/TaskPressCompliance")
    )
    assert list(map(str, joint.GetBody0Rel().GetTargets())) == [flange]
    assert (
        np.isclose(joint.GetLowerLimitAttr().Get(), -0.004)
        and joint.GetUpperLimitAttr().Get() == 0
    )
    assert (
        UsdPhysics.DriveAPI(joint.GetPrim(), "linear").GetStiffnessAttr().Get() == 800
    )
    planner = ArmScenePlanner(
        stage,
        chain,
        "/World/PanelEnvironment/InteractiveAssets/Controls/right_far_red/Cap",
    )
    tip = next(
        s for s in planner.shapes if s["path"] == info["contact_body"] + "/TaskPressTip"
    )
    assert tip["body"] == "link06" and tip["physical_owner"] == "TaskPressPlunger"
    np.testing.assert_allclose(
        tip["points"][:, 0].min(), 0.100 - 0.004 - 0.004, atol=1e-8
    )
    np.testing.assert_allclose(tip["points"][:, 0].max(), 0.100 + 0.004, atol=1e-8)
