"""Capability-based robot port shared by visual and physical scene adapters.

The port binds an existing controller/articulation; it does not synthesize a
locomotion policy, infer a TCP, or certify a newly imported robot. Native tensor
commands remain inside the trusted controller process, not an external RPC API.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RobotDescription:
    robot_id: str
    mode: str
    components: tuple[str, ...]
    capabilities: tuple[str, ...]
    joint_names: tuple[str, ...] = ()
    joint_units: tuple[str, ...] = ()
    position_unit: str = "m"
    angle_unit: str = "rad"
    quaternion_order: str = "xyzw"
    root_frame: str = "world"
    observation_source: str = "physics"
    joint_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    passive_joints: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.robot_id or self.mode not in ("kinematic_demo", "dynamic_physics"):
            raise ValueError("Robot ID and explicit execution mode are required")
        for names in (self.components, self.capabilities, self.joint_names):
            if any(not isinstance(n, str) or not n for n in names):
                raise ValueError("Robot components, capabilities and joints need names")
            if len(set(names)) != len(names):
                raise ValueError("Duplicate robot component, capability or joint name")
        if len(self.joint_units) != len(self.joint_names) or any(
            u not in ("m", "rad") for u in self.joint_units
        ):
            raise ValueError("Every bound joint needs an explicit meter/radian unit")
        if self.position_unit != "m" or self.angle_unit != "rad":
            raise ValueError("The robot port uses meters and radians")
        if self.quaternion_order != "xyzw":
            raise ValueError("The robot port returns xyzw quaternions")
        if (self.root_frame, self.observation_source) != (
            ("world", "physics")
            if self.mode == "dynamic_physics"
            else ("parent", "usd_authored")
        ):
            raise ValueError("Declare the execution mode's pose frame and state source")
        if self.mode == "dynamic_physics" and "root_pose" in self.capabilities:
            raise ValueError("Physical task commands cannot overwrite root poses")


@dataclass(frozen=True)
class RootPoseTarget:
    """Parent-frame translation and rotation about parent +Z, for USD demos only."""

    position_m: tuple[float, float, float]
    yaw_rad: float

    def __post_init__(self):
        if len(self.position_m) != 3 or not all(
            math.isfinite(v) for v in (*self.position_m, self.yaw_rad)
        ):
            raise ValueError("Finite XYZ and yaw required")


@dataclass(frozen=True)
class JointDriveTarget:
    """One full articulation batch; position is rad/m, effort is N*m/N.

    Names must follow describe()['joint_names']; arrays have shape (1, joints).
    The trusted controller owns finite-value, actuator-limit and collision checks.
    This port only inspects metadata, so dispatch never synchronizes GPU values.
    """

    joint_names: tuple[str, ...]
    position: Any
    effort: Any


class RobotInterface:
    """Read backend state and dispatch only explicitly bound capabilities."""

    def __init__(
        self,
        description: RobotDescription,
        observe: Callable[[], Mapping],
        handlers: Mapping[str, Callable],
    ):
        if set(handlers) != set(description.capabilities):
            raise ValueError("Every advertised capability must have exactly one driver")
        if not callable(observe) or any(not callable(h) for h in handlers.values()):
            raise ValueError("Observation and command drivers must be callable")
        self.description = description
        self._observe = observe
        self._handlers = dict(handlers)

    def describe(self) -> dict:
        description = asdict(self.description)
        description["joint_groups"] = dict(self.description.joint_groups)
        return {
            "schema_version": 1,
            **description,
            "batch_size": 1,
            "joint_velocity_units": tuple(
                f"{unit}/s" for unit in self.description.joint_units
            ),
            "joint_effort_units": tuple(
                "N*m" if unit == "rad" else "N" for unit in self.description.joint_units
            ),
        }

    def observe(self) -> Mapping:
        """Read current backend buffers, never command targets.

        Physics drivers require the caller to step/update the articulation first.
        Returned native tensors may alias backend storage; callers must not mutate
        them. USD drivers report authored transforms, not physics measurements.
        """
        return self._observe()

    def joint_indices(self, names: Sequence[str]) -> list[int]:
        if len(set(names)) != len(names):
            raise ValueError("Repeated joint in a requested group")
        actual = self.description.joint_names
        if any(name not in actual for name in names):
            raise ValueError("Requested joint is absent from the bound robot")
        return [actual.index(name) for name in names]

    def command(self, capability: str, target: Any) -> None:
        if capability not in self._handlers:
            raise ValueError(
                f"{self.description.robot_id} does not support {capability}"
            )
        self._handlers[capability](target)


def articulation_interface(
    robot,
    *,
    robot_id,
    components,
    groups,
    joint_units,
    quaternion_order,
    passive_joints=(),
):
    """Bind one initialized Isaac articulation: base, arm, or composed rig.

    groups plus passive_joints must cover every DOF exactly once. Passive joints
    still occupy channels in the full drive arrays (e.g. a spring's rest target);
    their actuator setup and target values remain the driver's responsibility.
    The caller declares the backend quaternion order; observations use xyzw.
    Navigation, IK, TCP and mounting calibration remain robot-driver concerns.
    """
    actual = tuple(robot.joint_names)
    if not actual or getattr(robot, "num_instances", 1) != 1:
        raise ValueError("Joint drive requires exactly one articulated robot")
    groups = {name: tuple(names) for name, names in groups.items()}
    if any(
        not isinstance(name, str) or not name or not names
        for name, names in groups.items()
    ):
        raise ValueError("Controller groups require nonempty names and joint lists")
    passive_joints = tuple(passive_joints)
    expected = [name for names in groups.values() for name in names] + list(
        passive_joints
    )
    if len(set(expected)) != len(expected) or set(expected) != set(actual):
        raise ValueError(
            "Controller groups and passive joints must exactly cover the articulation"
        )
    if set(joint_units) != set(actual) or quaternion_order not in ("xyzw", "wxyz"):
        raise ValueError("Declare every joint unit and the backend quaternion order")
    description = RobotDescription(
        robot_id,
        "dynamic_physics",
        tuple(components),
        ("joint_drive",),
        actual,
        tuple(joint_units[n] for n in actual),
        joint_groups=tuple(groups.items()),
        passive_joints=passive_joints,
    )

    def drive(target):
        if (
            not isinstance(target, JointDriveTarget)
            or tuple(target.joint_names) != actual
        ):
            raise ValueError("Command joint order differs from the bound articulation")
        shapes = [
            tuple(getattr(value, "shape", ()))
            for value in (target.position, target.effort)
        ]
        if shapes != [(1, len(actual)), (1, len(actual))]:
            raise ValueError("Joint drive expects one batch with the bound joint count")
        # Limits and policy outputs are checked by the bound task controller.
        # Preserve backend tensors and the original write order, without host copies.
        robot.set_joint_position_target(target.position)
        robot.set_joint_effort_target(target.effort)
        robot.write_data_to_sim()

    def observe():
        quaternion = robot.data.root_quat_w
        if quaternion_order == "wxyz":
            quaternion = getattr(quaternion, "torch", quaternion)[..., [1, 2, 3, 0]]
        return {
            "joint_names": actual,
            "joint_position": robot.data.joint_pos,
            "joint_velocity": robot.data.joint_vel,
            "root_position": robot.data.root_pos_w,
            "root_quaternion": quaternion,
            "quaternion_order": "xyzw",
            "root_frame": "world",
            "observation_source": "physics",
        }

    return RobotInterface(description, observe, {"joint_drive": drive})


def kinematic_usd_interface(position_op, yaw_op, *, robot_id="g2"):
    """Bind translate/rotateZ on a meter, Z-up USD stage, in the prim's parent frame.

    This deliberately narrow adapter reports authored local state. It neither
    observes physical motion nor commands navigation, joints or a full 6D pose.
    """
    from pxr import Gf, UsdGeom

    prim = position_op.GetAttr().GetPrim()
    stage = prim.GetStage()
    if (
        yaw_op.GetAttr().GetPrim() != prim
        or position_op.GetOpType() != UsdGeom.XformOp.TypeTranslate
        or yaw_op.GetOpType() != UsdGeom.XformOp.TypeRotateZ
        or position_op.IsInverseOp()
        or yaw_op.IsInverseOp()
        or UsdGeom.Xformable(prim).GetOrderedXformOps() != [position_op, yaw_op]
        or UsdGeom.Xformable(prim).GetResetXformStack()
    ):
        raise ValueError(
            "USD robot requires only parent-frame translate then rotateZ ops"
        )
    if (
        UsdGeom.GetStageMetersPerUnit(stage) != 1.0
        or UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z
    ):
        raise ValueError("USD robot requires a meter, Z-up stage")
    vector_type = {
        UsdGeom.XformOp.PrecisionDouble: Gf.Vec3d,
        UsdGeom.XformOp.PrecisionFloat: Gf.Vec3f,
        UsdGeom.XformOp.PrecisionHalf: Gf.Vec3h,
    }[position_op.GetPrecision()]

    def drive(target):
        if not isinstance(target, RootPoseTarget):
            raise ValueError("Expected a metric root pose with yaw in radians")
        position_op.Set(vector_type(*target.position_m))
        yaw_op.Set(math.degrees(target.yaw_rad))

    def observe():
        yaw = math.radians(yaw_op.Get() or 0)
        return {
            "joint_names": (),
            "root_position": (tuple(position_op.Get() or (0, 0, 0)),),
            "root_quaternion": ((0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),),
            "quaternion_order": "xyzw",
            "root_frame": "parent",
            "observation_source": "usd_authored",
        }

    description = RobotDescription(
        robot_id,
        "kinematic_demo",
        ("mobile_base",),
        ("root_pose",),
        root_frame="parent",
        observation_source="usd_authored",
    )
    return RobotInterface(description, observe, {"root_pose": drive})
