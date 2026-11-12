"""CPU contract regressions; no Isaac, CUDA, policy or simulator is started."""

from types import SimpleNamespace

import numpy as np
import pytest

from simready.simulation.robot_interface import (
    JointDriveTarget,
    RobotDescription,
    RobotInterface,
    RootPoseTarget,
    articulation_interface,
    kinematic_usd_interface,
)


class MetadataOnlyTensor:
    """Fail if dispatch tries to read values or transfer anything to the host."""

    def __init__(self, shape):
        self.shape = shape

    def __array__(self, *args, **kwargs):
        raise AssertionError("Unexpected value conversion")

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected tensor access: {name}")


class ArticulationStub:
    def __init__(self, names=("joint2", "knee", "spring", "joint1")):
        self.joint_names = names
        self.num_instances = 1
        self.calls = []
        self.data = SimpleNamespace(
            joint_pos=np.zeros((1, len(names))),
            joint_vel=np.zeros((1, len(names))),
            root_pos_w=np.array([[1.0, 2.0, 3.0]]),
            root_quat_w=np.array([[0.0, 0.0, 0.0, 1.0]]),
        )

    def set_joint_position_target(self, value):
        self.calls.append(("position", value))

    def set_joint_effort_target(self, value):
        self.calls.append(("effort", value))

    def write_data_to_sim(self):
        self.calls.append(("write",))


def bind(robot, **overrides):
    config = dict(
        robot_id="composed",
        components=("mobile_base", "arm", "passive_tool"),
        groups={"base": ("knee",), "arm": ("joint1", "joint2")},
        passive_joints=("spring",),
        joint_units={"joint1": "rad", "joint2": "rad", "knee": "rad", "spring": "m"},
        quaternion_order="xyzw",
    )
    return articulation_interface(robot, **(config | overrides))


def test_group_indices_and_units_follow_actual_articulation_order():
    port = bind(ArticulationStub())
    assert port.joint_indices(("joint1", "joint2")) == [3, 0]
    info = port.describe()
    assert info["joint_names"] == ("joint2", "knee", "spring", "joint1")
    assert info["joint_units"] == ("rad", "rad", "m", "rad")
    assert info["joint_velocity_units"] == ("rad/s", "rad/s", "m/s", "rad/s")
    assert info["joint_effort_units"] == ("N*m", "N*m", "N", "N*m")
    assert info["joint_groups"] == {"base": ("knee",), "arm": ("joint1", "joint2")}
    assert info["passive_joints"] == ("spring",)
    assert info["mode"] == "dynamic_physics"
    assert info["capabilities"] == ("joint_drive",)
    assert info["observation_source"] == "physics"
    assert info["root_frame"] == "world"
    assert info["quaternion_order"] == "xyzw"


@pytest.mark.parametrize("requested", [("absent",), ("joint1", "joint1")])
def test_invalid_requested_group_is_rejected(requested):
    with pytest.raises(ValueError):
        bind(ArticulationStub()).joint_indices(requested)


@pytest.mark.parametrize(
    "overrides",
    [
        {"groups": {"arm": ("joint1", "joint2")}},
        {"groups": {"base": ("knee", "joint1"), "arm": ("joint1", "joint2")}},
        {"passive_joints": ("spring", "missing")},
        {"groups": {"": ("knee",), "arm": ("joint1", "joint2")}},
        {"groups": {"empty": (), "base": ("knee",), "arm": ("joint1", "joint2")}},
        {"joint_units": {"joint1": "rad"}},
        {
            "joint_units": {
                "joint1": "deg",
                "joint2": "rad",
                "knee": "rad",
                "spring": "m",
            }
        },
        {"quaternion_order": "guess"},
    ],
)
def test_invalid_binding_fails_before_any_write(overrides):
    robot = ArticulationStub()
    with pytest.raises(ValueError):
        bind(robot, **overrides)
    assert robot.calls == []


def test_duplicate_backend_names_and_multi_robot_batches_are_rejected():
    robot = ArticulationStub(("joint2", "knee", "spring", "joint1", "joint1"))
    with pytest.raises(ValueError, match="Duplicate"):
        bind(robot)
    robot = ArticulationStub()
    robot.num_instances = 2
    with pytest.raises(ValueError, match="exactly one"):
        bind(robot)


def test_command_preserves_native_tensors_and_write_order_without_host_reads():
    robot = ArticulationStub()
    port = bind(robot)
    position, effort = MetadataOnlyTensor((1, 4)), MetadataOnlyTensor((1, 4))
    port.command("joint_drive", JointDriveTarget(robot.joint_names, position, effort))
    assert robot.calls == [("position", position), ("effort", effort), ("write",)]


@pytest.mark.parametrize(
    "target",
    [
        RootPoseTarget((0, 0, 0), 0),
        JointDriveTarget(
            ("joint1", "joint2", "knee", "spring"), np.zeros((1, 4)), np.zeros((1, 4))
        ),
        JointDriveTarget(
            ("joint2", "knee", "spring", "joint1"), np.zeros(4), np.zeros((1, 4))
        ),
        JointDriveTarget(
            ("joint2", "knee", "spring", "joint1"), np.zeros((1, 4)), np.zeros((2, 4))
        ),
        JointDriveTarget(
            ("joint2", "knee", "spring", "joint1"), np.zeros((1, 3)), np.zeros((1, 4))
        ),
    ],
)
def test_invalid_joint_command_is_atomic_before_backend_writes(target):
    robot = ArticulationStub()
    with pytest.raises(ValueError):
        bind(robot).command("joint_drive", target)
    assert robot.calls == []


@pytest.mark.parametrize("capability", ["root_pose", "navigate", "tcp_pose"])
def test_unbound_capabilities_are_rejected(capability):
    robot = ArticulationStub()
    with pytest.raises(ValueError, match="does not support"):
        bind(robot).command(capability, RootPoseTarget((0, 0, 0), 0))
    assert robot.calls == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_observation_reads_current_backend_measurements_and_converts_wxyz(wrapped):
    robot = ArticulationStub()
    quaternion = np.array([[0.5, -0.5, 0.5, -0.5]])
    robot.data.root_quat_w = (
        SimpleNamespace(torch=quaternion) if wrapped else quaternion
    )
    port = bind(robot, quaternion_order="wxyz")
    port.command(
        "joint_drive",
        JointDriveTarget(robot.joint_names, np.ones((1, 4)), np.zeros((1, 4))),
    )
    robot.data.joint_pos = np.array([[0.1, 0.2, 0.3, 0.4]])
    robot.data.joint_vel = np.array([[0.5, 0.6, 0.7, 0.8]])
    state = port.observe()
    assert state["joint_position"] is robot.data.joint_pos
    assert state["joint_velocity"] is robot.data.joint_vel
    assert state["root_position"] is robot.data.root_pos_w
    np.testing.assert_array_equal(state["root_quaternion"], [[-0.5, 0.5, -0.5, 0.5]])
    np.testing.assert_array_equal(quaternion, [[0.5, -0.5, 0.5, -0.5]])
    robot.data.joint_pos = np.full((1, 4), 0.9)
    assert port.observe()["joint_position"] is robot.data.joint_pos


def test_xyzw_observation_keeps_native_backend_quaternion():
    robot = ArticulationStub()
    assert bind(robot).observe()["root_quaternion"] is robot.data.root_quat_w


@pytest.mark.parametrize(
    "capabilities,handlers",
    [
        (("joint_drive",), {}),
        ((), {"joint_drive": lambda _: None}),
        (("joint_drive",), {"joint_drive": None}),
    ],
)
def test_advertised_capabilities_need_exactly_matching_callable_drivers(
    capabilities, handlers
):
    description = RobotDescription("test", "dynamic_physics", ("arm",), capabilities)
    with pytest.raises(ValueError):
        RobotInterface(description, lambda: {}, handlers)


def test_description_rejects_physical_root_pose_and_duplicate_capabilities():
    with pytest.raises(ValueError, match="overwrite"):
        RobotDescription("test", "dynamic_physics", ("arm",), ("root_pose",))
    with pytest.raises(ValueError, match="Duplicate"):
        RobotDescription(
            "test", "dynamic_physics", ("arm",), ("joint_drive", "joint_drive")
        )


@pytest.mark.parametrize(
    "position,yaw", [((1, 2), 0), ((1, 2, float("nan")), 0), ((1, 2, 3), float("inf"))]
)
def test_root_pose_requires_finite_metric_xyz_and_radian_yaw(position, yaw):
    with pytest.raises(ValueError):
        RootPoseTarget(position, yaw)


@pytest.fixture
def usd_rig():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    parent = UsdGeom.Xform.Define(stage, "/World")
    parent.AddTranslateOp().Set((10, 0, 0))
    rig = UsdGeom.Xform.Define(stage, "/World/Robot")
    return stage, rig, rig.AddTranslateOp(), rig.AddRotateZOp()


def test_g2_usd_command_and_observation_use_parent_frame_and_read_authored_state(
    usd_rig,
):
    _, _, position, yaw = usd_rig
    port = kinematic_usd_interface(position, yaw)
    info = port.describe()
    assert info["mode"] == "kinematic_demo"
    assert info["capabilities"] == ("root_pose",)
    assert info["root_frame"] == "parent"
    assert info["observation_source"] == "usd_authored"
    port.command("root_pose", RootPoseTarget((1, 2, 3), np.pi / 2))
    assert tuple(position.Get()) == (1, 2, 3)
    assert yaw.Get() == pytest.approx(90)
    # An external USD edit is visible; this is neither cached command nor world pose.
    position.Set((4, 5, 6))
    yaw.Set(180)
    state = port.observe()
    assert state["root_position"] == ((4, 5, 6),)
    np.testing.assert_allclose(state["root_quaternion"], [[0, 0, 1, 0]], atol=1e-7)
    assert state["observation_source"] == "usd_authored"
    for capability in ("joint_drive", "navigate", "tcp_pose"):
        with pytest.raises(ValueError, match="does not support"):
            port.command(capability, None)
    with pytest.raises(ValueError, match="metric root pose"):
        port.command("root_pose", {"position": [0, 0, 0]})
    assert tuple(position.Get()) == (4, 5, 6)


@pytest.mark.parametrize(
    "change",
    [
        "centimeters",
        "y_up",
        "extra_scale",
        "reordered_ops",
        "reset_stack",
        "different_prim",
        "wrong_axis",
    ],
)
def test_usd_adapter_rejects_ambiguous_frames_or_units(usd_rig, change):
    from pxr import UsdGeom

    stage, rig, position, yaw = usd_rig
    if change == "centimeters":
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    elif change == "y_up":
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    elif change == "extra_scale":
        rig.AddScaleOp()
    elif change == "reordered_ops":
        rig.SetXformOpOrder([yaw, position])
    elif change == "reset_stack":
        rig.SetResetXformStack(True)
    elif change == "different_prim":
        yaw = UsdGeom.Xform.Define(stage, "/Other").AddRotateZOp()
    elif change == "wrong_axis":
        yaw = rig.AddRotateXOp()
    with pytest.raises(ValueError, match="USD robot requires"):
        kinematic_usd_interface(position, yaw)


def test_z1_and_composed_examples_bind_shuffled_backend_joint_order():
    from examples.robots.bind_articulation import bind_lite3_z1, bind_z1

    z1 = ArticulationStub(("joint2", "joint1"))
    port = bind_z1(z1, arm_joints=("joint1", "joint2"), quaternion_order="xyzw")
    assert port.joint_indices(("joint1", "joint2")) == [1, 0]
    assert port.describe()["components"] == ("arm",)
    robot = ArticulationStub()
    port = bind_lite3_z1(
        robot,
        leg_joints=("knee",),
        arm_joints=("joint1", "joint2"),
        passive_joint_units={"spring": "m"},
        quaternion_order="xyzw",
    )
    assert port.joint_indices(("joint1", "joint2")) == [3, 0]
    assert port.describe()["passive_joints"] == ("spring",)
