"""Synthetic measured cup trajectories exercise the independent CPU audit."""

import numpy as np
import pytest

from simready.core.artifacts import digest, write_json
from simready.evaluation.cup_transfer import audit

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402


@pytest.fixture
def transfer(tmp_path):
    assets, rollout = tmp_path / "assets", tmp_path / "rollout"
    assets.mkdir()
    rollout.mkdir()
    stage = Usd.Stage.CreateNew(str(assets / "task.usda"))
    UsdGeom.Xform.Define(stage, "/World")
    cup = UsdGeom.Xform.Define(stage, "/World/Cup").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(cup)
    UsdPhysics.MassAPI.Apply(cup).CreateMassAttr(0.085)
    mesh = UsdGeom.Mesh.Define(stage, "/World/Cup/collision")
    mesh.CreatePointsAttr(
        [(-0.02, -0.02, 0), (0.02, -0.02, 0), (0.02, 0.02, 0.1), (-0.02, 0.02, 0.1)]
    )
    table = UsdGeom.Cylinder.Define(stage, "/World/TableB")
    table.CreateHeightAttr(1)
    table.CreateRadiusAttr(0.5)
    table.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.5))
    stage.GetRootLayer().Save()
    for name in ("robot.usdc", "cup.usdc"):
        dependency = Usd.Stage.CreateNew(str(assets / name))
        dependency.GetRootLayer().Save()
    write_json(assets / "plan.json", {"destination": {"cup_bottom_xyz_m": [0, 0, 0]}})
    phases = np.repeat(
        [
            "SETTLE",
            "APPROACH",
            "CLOSE",
            "LIFT",
            "HOLD",
            "TRANSFER",
            "PLACE",
            "RELEASE",
            "RETREAT",
            "VERIFY",
        ],
        [2, 2, 2, 2, 480, 2, 2, 2, 2, 2],
    )
    count = len(phases)
    poses = np.zeros((count, 2, 7))
    poses[:, :, 6] = 1
    holding = (phases == "HOLD") | (phases == "TRANSFER")
    poses[holding, -1, 2] = 0.2
    forces = np.zeros((count, 4, 3))
    forces[holding, :2, 2] = 0.3
    forces[phases == "VERIFY", 3, 2] = 0.85
    arrays = {
        "poses": poses,
        "phases": phases,
        "forces": forces,
        "dt": np.array(1 / 240),
        "joints": np.zeros((count, 2)),
        "commands": np.zeros((count, 2)),
        "environment_forces": np.zeros((count, 1, 2, 3)),
    }
    np.savez(rollout / "rollout.npz", **arrays)
    result = {
        "frames": count,
        "dt_s": 1 / 240,
        "physics_hz": 240,
        "body_names": ["robot", "Cup"],
        "joint_names": ["a", "b"],
        "contact_filters": [
            "/World/G2/gripper_r_left_support_link",
            "/World/G2/gripper_r_right_support_link",
            "/World/Table",
            "/World/TableB",
        ],
        "environment_filters": ["/World/Table", "/World/TableB"],
        "asset_sha256": {
            name: digest(assets / name)
            for name in ("task.usda", "plan.json", "robot.usdc", "cup.usdc")
        },
        "rollout_sha256": digest(rollout / "rollout.npz"),
    }
    write_json(rollout / "result.json", result)
    return assets, rollout, arrays, result


def test_complete_synthetic_trajectory_retains_original_acceptance_thresholds(transfer):
    assets, rollout, _, _ = transfer
    report = audit(assets, rollout)
    assert report["status"] == "PASS", report
    assert report["result_sha256"] == digest(rollout / "result.json")
    assert report["metrics"]["placement_xy_error_m"] == 0


def test_incomplete_asset_hash_inventory_cannot_pass(transfer):
    assets, rollout, _, result = transfer
    del result["asset_sha256"]["cup.usdc"]
    write_json(rollout / "result.json", result)
    report = audit(assets, rollout)
    assert report["status"] == "FAILED"
    assert not report["checks"]["evidence_hashes_match"]


def test_return_to_previous_phase_is_not_a_complete_sequence(transfer):
    assets, rollout, arrays, result = transfer
    # All unique phase labels still appear in the normal order, but the state
    # machine returns from APPROACH to SETTLE before CLOSE.
    arrays["phases"][3] = "SETTLE"
    np.savez(rollout / "rollout.npz", **arrays)
    result["rollout_sha256"] = digest(rollout / "rollout.npz")
    write_json(rollout / "result.json", result)
    report = audit(assets, rollout)
    assert not report["checks"]["complete_phase_sequence"]
    assert report["status"] == "FAILED"


@pytest.mark.parametrize(
    "corruption", ["clock", "frames", "forces", "sensor_order", "nonfinite"]
)
def test_sampling_or_sensor_mismatch_is_rejected_before_scoring(transfer, corruption):
    assets, rollout, arrays, result = transfer
    if corruption == "clock":
        arrays["dt"] = np.array(float("inf"))
    elif corruption == "frames":
        result["frames"] += 1
    elif corruption == "forces":
        arrays["forces"] = arrays["forces"][:-1]
    elif corruption == "sensor_order":
        result["contact_filters"].reverse()
    else:
        arrays["commands"][0, 0] = float("nan")
    np.savez(rollout / "rollout.npz", **arrays)
    result["rollout_sha256"] = digest(rollout / "rollout.npz")
    write_json(rollout / "result.json", result)
    with pytest.raises(ValueError, match="Rollout"):
        audit(assets, rollout)


def test_escaping_asset_reference_is_rejected(transfer, tmp_path):
    assets, rollout, _, result = transfer
    external = tmp_path / "external"
    external.write_text("other run")
    result["asset_sha256"]["../external"] = digest(external)
    write_json(rollout / "result.json", result)
    assert not audit(assets, rollout)["checks"]["evidence_hashes_match"]
