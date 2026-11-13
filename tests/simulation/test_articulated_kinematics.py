"""Analytic tests of joint-frame composition, not robot-specific snapshots."""

import numpy as np
import pytest

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from simready.simulation.articulated_kinematics import UsdJointTree


def make_tree():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    for name in ("root", "link", "tip"):
        UsdGeom.Xform.Define(stage, "/World/" + name)
    hinge = UsdPhysics.RevoluteJoint.Define(stage, "/World/hinge")
    hinge.CreateBody0Rel().SetTargets(["/World/root"])
    hinge.CreateBody1Rel().SetTargets(["/World/link"])
    hinge.CreateAxisAttr("Z")
    hinge.CreateLocalPos0Attr(Gf.Vec3f(1, 0, 0))
    hinge.CreateLocalPos1Attr(Gf.Vec3f(0, -0.5, 0))
    hinge.CreateLowerLimitAttr(-180)
    hinge.CreateUpperLimitAttr(180)
    fixed = UsdPhysics.FixedJoint.Define(stage, "/World/tool")
    fixed.CreateBody0Rel().SetTargets(["/World/link"])
    fixed.CreateBody1Rel().SetTargets(["/World/tip"])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(0, 0.5, 0))
    return stage


def test_child_joint_offset_rotates_with_link():
    stage = make_tree()
    tree = UsdJointTree(stage, "/World/root")
    np.testing.assert_allclose(
        tree.pose("/World/tip", {"hinge": 0})[:3, 3], [1, 1, 0], atol=1e-8
    )
    np.testing.assert_allclose(
        tree.pose("/World/tip", {"hinge": np.pi / 2})[:3, 3], [0, 0, 0], atol=1e-8
    )


def test_unreachable_target_is_rejected():
    stage = make_tree()
    tree = UsdJointTree(stage, "/World/root")
    target = np.eye(4)
    target[:3, 3] = [8, 0, 0]
    with pytest.raises(ValueError, match="IK rejected"):
        tree.solve("/World/tip", target, ["hinge"], [0])


def test_disconnected_body_is_rejected():
    stage = make_tree()
    UsdGeom.Xform.Define(stage, "/World/isolated")
    tree = UsdJointTree(stage, "/World/root")
    with pytest.raises(ValueError, match="rooted tree"):
        tree.pose("/World/isolated", {})


def test_ambiguous_parent_is_rejected():
    stage = make_tree()
    joint = UsdPhysics.FixedJoint.Define(stage, "/World/extra")
    joint.CreateBody0Rel().SetTargets(["/World/root"])
    joint.CreateBody1Rel().SetTargets(["/World/tip"])
    with pytest.raises(ValueError, match="Multiple parents"):
        UsdJointTree(stage, "/World/root")
