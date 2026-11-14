"""Controller contracts and failure paths; no physical success is inferred."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from simready.core.artifacts import read_json
from simready.simulation.cup_control import GRIPPER, CupController, controller_spec
from simready.simulation.cup_grasp import run_task
from simready.simulation.cup_recording import CupRecorder
from simready.simulation.cup_runtime import IsaacCupRuntime


@pytest.fixture
def controls():
    names = [f"arm-{i}" for i in range(7)] + list(GRIPPER)
    plan = {
        "initial": dict.fromkeys(names, 0.0),
        "arm_names": names[:7],
        "waypoints": [
            {"name": name, "q": [value] * 7}
            for name, value in [
                ("pregrasp", 0),
                ("grasp", 0.1),
                ("lift", 0.2),
                ("transfer", 0.3),
                ("place", 0.4),
            ]
        ],
        "paths": {},
        "destination": {"cup_bottom_xyz_m": [0, 0, 0.85]},
    }
    return plan, names


def observe(names, *, tilt=0, force=5, support=0):
    palm = np.array([0, 0, 1.1, 0, 0, 0, 1], dtype=float)
    cup = np.r_[[0, 0, 1], Rotation.from_euler("y", tilt, degrees=True).as_quat()]
    return {
        "palm": palm,
        "poses": np.stack([palm, cup]),
        "joints": np.zeros(len(names)),
        "forces": np.array(
            [[force, 0, 0], [-force, 0, 0], [0, 0, 0], [0, 0, support]], float
        ),
        "environment_forces": np.zeros((1, 2, 3)),
    }


class Kinematics:
    def __init__(self):
        self.targets = []

    def solve(self, target, seed):
        self.targets.append(target.copy())
        return np.r_[target[:3, 3], Rotation.from_matrix(target[:3, :3]).as_rotvec(), 0]

    def palm_pose(self, joints):
        target = np.eye(4)
        target[:3, 3] = [0, 0, 1.1]
        return target


def test_default_baseline_keeps_clock_phase_order_and_original_interpolation(controls):
    plan, names = controls
    original = copy.deepcopy(plan)
    controller = CupController(plan, names)
    assert controller.spec["version"] == "baseline-v1"
    assert sum(round(phase[1] / controller.dt) for phase in controller.phases) == 5400
    obs = observe(names)
    kinematics = Kinematics()
    approach = controller.phases[1]
    controller.begin_phase(approach, obs, kinematics)
    for step in range(controller.steps):
        t = (step + 1) / 480
        weight = t**3 * (10 - 15 * t + 6 * t * t)
        target = controller.command(step, obs, kinematics)
        assert target.dtype == np.float32
        np.testing.assert_allclose(target[0, :7], np.float32(0.1 * weight), atol=1e-8)
        np.testing.assert_array_equal(target[0, 7:], 0)
    assert kinematics.targets == []
    assert plan == original


def test_candidate_feedback_direction_rate_and_gripper_bounds(controls):
    plan, names = controls
    controller = CupController(plan, names, version="stabilized-v2", mass_kg=0.1)
    obs = observe(names, tilt=12)
    saved = {key: value.copy() for key, value in obs.items()}
    kinematics = Kinematics()
    controller.begin_phase(controller.phases[3], obs, kinematics)
    previous = np.zeros(7)
    for step in range(controller.steps):
        command = controller.command(step, obs, kinematics)
        assert np.max(np.abs(controller.offset - previous)) <= 0.3 / 240 + 1e-12
        previous = controller.offset.copy()
        assert np.max(np.abs(command[0, 7:])) <= 0.750001
    assert controller.extra_close > 0
    assert 0 < np.degrees(controller.correction.magnitude()) <= 20.000001
    assert Rotation.from_matrix(kinematics.targets[0][:3, :3]).as_rotvec()[1] < 0
    assert all(np.array_equal(obs[key], saved[key]) for key in obs)


def test_large_contact_force_inhibits_extra_closure(controls):
    plan, names = controls
    controller = CupController(plan, names, version="stabilized-v2", mass_kg=0.1)
    obs, kinematics = observe(names, tilt=12, force=30), Kinematics()
    controller.begin_phase(controller.phases[3], obs, kinematics)
    for step in range(48):
        controller.command(step, obs, kinematics)
    assert controller.extra_close == 0


@pytest.mark.parametrize(
    "tilt,support,allowed", [(0, 1, True), (7, 1, False), (0, -1, False), (0, 0, False)]
)
def test_release_requires_continuous_upright_positive_support(
    controls, tilt, support, allowed
):
    plan, names = controls
    controller = CupController(plan, names, version="stabilized-v2", mass_kg=0.1)
    obs, kinematics = observe(names, tilt=tilt, support=support), Kinematics()
    controller.begin_phase(controller.phases[3], obs, kinematics)
    controller.begin_phase(controller.phases[6], obs, kinematics)
    for step in range(40):
        controller.command(step, obs, kinematics)
    if allowed:
        controller.begin_phase(controller.phases[7], obs, kinematics)
    else:
        with pytest.raises(ValueError, match="refused release"):
            controller.begin_phase(controller.phases[7], obs, kinematics)
        assert controller.events[-1]["event"] == "RELEASE_BLOCKED"


def test_recorder_copies_mutable_sensor_buffers_and_command(tmp_path, controls):
    _, names = controls
    obs, command = observe(names), np.zeros((1, len(names)))
    recorder = CupRecorder(tmp_path, 1 / 240)
    recorder.append(obs, command, "LIFT")
    obs["poses"][:] = 99
    command[:] = 99
    result = {}
    recorder.save(result)
    with np.load(tmp_path / "rollout.npz") as recorded:
        assert not np.any(recorded["poses"] == 99)
        assert not np.any(recorded["commands"] == 99)
    assert result["frames"] == 1


@pytest.mark.parametrize(
    "failure", [ValueError("initialization failed"), KeyboardInterrupt("interrupted")]
)
def test_initialization_failure_has_receipt_and_always_closes(tmp_path, failure):
    calls = []

    class FailingRuntime:
        def __init__(self, args, result):
            pass

        def start(self):
            calls.append("start")
            raise failure

        def close_physics(self):
            calls.append("physics")
            return []

        def close_app(self, code):
            calls.append(("app", code))

    args = SimpleNamespace(out=tmp_path, controller="baseline-v1")
    if isinstance(failure, Exception):
        assert run_task(args, runtime_factory=FailingRuntime) == 2
    else:
        with pytest.raises(KeyboardInterrupt):
            run_task(args, runtime_factory=FailingRuntime)
    report = read_json(tmp_path / "result.json")
    assert report["status"] == (
        "FAILED" if isinstance(failure, Exception) else "INTERRUPTED"
    )
    assert report["controller"] == controller_spec("baseline-v1")
    assert calls == ["start", "physics", ("app", 2)]


def test_receipt_io_failure_still_closes_application(tmp_path, monkeypatch):
    from simready.simulation import cup_grasp

    closed = []

    class Runtime:
        def __init__(self, args, result):
            pass

        def start(self):
            raise ValueError("original failure")

        def close_physics(self):
            return []

        def close_app(self, code):
            closed.append(code)

    def disk_failure(*args, **kwargs):
        raise OSError("disk error")

    monkeypatch.setattr(cup_grasp, "write_json", disk_failure)
    assert (
        run_task(
            SimpleNamespace(out=tmp_path, controller="baseline-v1"),
            runtime_factory=Runtime,
        )
        == 2
    )
    assert closed == [2]


def test_partial_physics_cleanup_attempts_both_resources():
    runtime = IsaacCupRuntime(SimpleNamespace(), {})
    calls = []

    def detach():
        calls.append("detach")
        raise OSError("detach failed")

    runtime.simulation = SimpleNamespace(detach_stage=detach)
    runtime.attached = True
    runtime.stage = object()
    runtime.release_memory_stage = lambda stage: calls.append("release")
    errors = runtime.close_physics()
    assert calls == ["detach", "release"]
    assert errors == [{"resource": "stage_attachment", "error": "OSError"}]
    assert runtime.close_physics() == []


def test_cleanup_interrupt_releases_stage_then_propagates():
    runtime = IsaacCupRuntime(SimpleNamespace(), {})
    calls = []

    def detach():
        calls.append("detach")
        raise KeyboardInterrupt("stop cleanup")

    runtime.simulation = SimpleNamespace(detach_stage=detach)
    runtime.attached = True
    runtime.stage = object()
    runtime.release_memory_stage = lambda stage: calls.append("release")
    with pytest.raises(KeyboardInterrupt, match="stop cleanup"):
        runtime.close_physics()
    assert calls == ["detach", "release"]
    assert runtime.close_physics() == []


def test_cleanup_interrupt_closes_app_and_keeps_interrupted_receipt(tmp_path):
    calls = []

    class Runtime:
        def __init__(self, args, result):
            pass

        def start(self):
            raise ValueError("earlier failure")

        def close_physics(self):
            raise KeyboardInterrupt("user interrupted cleanup")

        def close_app(self, code):
            calls.append(code)

    with pytest.raises(KeyboardInterrupt, match="user interrupted cleanup"):
        run_task(
            SimpleNamespace(out=tmp_path, controller="baseline-v1"),
            runtime_factory=Runtime,
        )
    assert calls == [2]
    assert read_json(tmp_path / "result.json")["status"] == "INTERRUPTED"
