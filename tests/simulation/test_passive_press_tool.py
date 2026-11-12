import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdPhysics  # noqa: E402

from simready.simulation.arm_planner import ArmScenePlanner
from simready.simulation.kinematics import Chain
from simready.simulation.press_tool import build_spring_plunger

if not os.environ.get("SIMREADY_BRIDGE_TEST_ASSETS"):
    pytest.skip(
        "Set SIMREADY_BRIDGE_TEST_ASSETS for the historical USD fixture",
        allow_module_level=True,
    )
ROOT = Path(os.environ["SIMREADY_BRIDGE_TEST_ASSETS"])


def test_passive_plunger_has_spring_mass_and_conservative_travel_envelope():
    stage = Usd.Stage.Open(
        str(ROOT / "reports/panel_stop_task_20261111/mission_v5/scene.usda")
    )
    stage.SetEditTarget(stage.GetSessionLayer())
    flange = "/World/FrankaArm/Geometry/world/link00/link01/link02/link03/link04/link05/link06"
    for name in ["TaskPressTip", "TaskPressShaft"]:
        stage.RemovePrim(flange + "/" + name)
    cfg = json.loads(
        (ROOT / "configs/panel_stop_estimated_spring_tool_20261111.json").read_text()
    )["tool"]
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
    chain = Chain.from_usd(ROOT / "artifacts/runtime_v1/lite3_z1.usda")
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
