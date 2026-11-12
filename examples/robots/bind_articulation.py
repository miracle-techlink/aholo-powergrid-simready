"""Bindings for an already initialized robot; no simulator or assets are loaded.

Joint lists come from the robot's reviewed runtime contract. Quaternion order
comes from the installed backend. Neither is inferred from a robot model name.
"""

from simready.simulation.robot_interface import articulation_interface


def bind_z1(robot, *, arm_joints, quaternion_order):
    """Standalone revolute Z1 arm, without additional tool DOFs."""
    return articulation_interface(
        robot,
        robot_id="z1",
        components=("arm",),
        groups={"arm": arm_joints},
        joint_units={name: "rad" for name in arm_joints},
        quaternion_order=quaternion_order,
    )


def bind_lite3_z1(
    robot,
    *,
    leg_joints,
    arm_joints,
    quaternion_order,
    passive_joint_units=None,
):
    """One composed articulation, optionally with a passive spring/tool DOF."""
    passive_joint_units = passive_joint_units or {}
    return articulation_interface(
        robot,
        robot_id="lite3_z1",
        components=("mobile_base", "arm")
        + (("passive_tool",) if passive_joint_units else ()),
        groups={"base": leg_joints, "arm": arm_joints},
        passive_joints=tuple(passive_joint_units),
        joint_units={
            **{name: "rad" for name in (*leg_joints, *arm_joints)},
            **passive_joint_units,
        },
        quaternion_order=quaternion_order,
    )
