"""Independent geometric/contact audit of a measured two-table cup transfer.

Does not import the planner or simulator worker, and does not grant full-scene,
visual-localization, autonomous-navigation or real-material validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def audit(assets, rollout):
    from pxr import Usd, UsdGeom, UsdPhysics

    assets, rollout = Path(assets), Path(rollout)
    result = json.loads((rollout / "result.json").read_text())
    plan = json.loads((assets / "plan.json").read_text())
    from simready.core.artifacts import digest

    data = np.load(rollout / "rollout.npz", allow_pickle=False)
    pose = data["poses"][:, -1]
    rotation = Rotation.from_quat(pose[:, 3:])
    upright = np.clip(rotation.as_matrix()[:, 2, 2], -1, 1)
    tilt = np.degrees(np.arccos(upright))
    phases = data["phases"]
    dt = float(data["dt"])
    f = np.linalg.norm(data["forces"], axis=-1)
    hold = phases == "HOLD"
    transfer = phases == "TRANSFER"
    verify = phases == "VERIFY"
    stage = Usd.Stage.Open(str(assets / "task.usda"))
    cup = stage.GetPrimAtPath("/World/Cup")
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Cup/collision"))
    points = np.array(mesh.GetPointsAttr().Get())
    local = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())).T
    root = np.array(UsdGeom.XformCache().GetLocalToWorldTransform(cup)).T
    local = np.linalg.inv(root) @ local
    points = points @ local[:3, :3].T + local[:3, 3]
    world = np.einsum("nij,pj->npi", rotation.as_matrix(), points) + pose[
        :, :3, None
    ].transpose(0, 2, 1)
    bottom = world[:, :, 2].min(1)
    table_b = stage.GetPrimAtPath("/World/TableB")
    bbox = (
        UsdGeom.BBoxCache(0, ["default"])
        .ComputeWorldBound(table_b)
        .ComputeAlignedRange()
    )
    top = float(bbox.GetMax()[2])
    center = (np.array(bbox.GetMin()) + np.array(bbox.GetMax())) / 2
    support = plan["destination"]["cup_bottom_xyz_m"]
    mass = float(UsdPhysics.MassAPI(cup).GetMassAttr().Get())
    attachment = []
    for p in stage.Traverse():
        if p.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(p)
            targets = (
                joint.GetBody0Rel().GetTargets() + joint.GetBody1Rel().GetTargets()
            )
            if any(str(t).startswith("/World/Cup") for t in targets):
                attachment.append(str(p.GetPath()))
    checks = {
        "evidence_hashes_match": bool(result.get("asset_sha256"))
        and all(
            digest(assets / name) == value
            for name, value in result.get("asset_sha256", {}).items()
        )
        and digest(rollout / "rollout.npz") == result.get("rollout_sha256"),
        "complete_phase_sequence": list(dict.fromkeys(phases))
        == [
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
        "finite_measured_arrays": all(
            np.isfinite(data[k]).all()
            for k in ["poses", "joints", "forces", "environment_forces"]
        ),
        "minimum_2s_hold": bool(np.count_nonzero(hold) * dt >= 2 - 1e-8),
        "lifted_clear_of_table": bool(bottom[hold].min() - top > 0.1),
        "bilateral_hold_and_transfer": bool(
            np.mean(np.all(f[hold | transfer, :2] > 0.05, axis=1)) > 0.95
        ),
        "upright_during_transfer": bool(tilt[hold | transfer].max() < 15),
        "destination_center_error_under_15mm": bool(
            np.linalg.norm(pose[-1, :2] - np.array(support[:2])) < 0.015
        ),
        "destination_surface_contact": bool(
            np.max(np.abs(bottom[verify] - top)) < 0.003
        ),
        "upright_after_release": bool(tilt[verify].max() < 5),
        "released_from_gripper": bool(f[verify, :2].max() < 0.01),
        "supported_by_destination_table": bool(
            np.mean(f[verify, 3]) > mass * 9.81 * 0.5
        ),
        "robot_did_not_hit_monitored_environment": bool(
            np.linalg.norm(data["environment_forces"], axis=-1).max() < 1
        ),
        "no_object_attachment_joint": not attachment,
        "object_is_dynamic": bool(
            UsdPhysics.RigidBodyAPI(cup).GetRigidBodyEnabledAttr().Get()
            and not UsdPhysics.RigidBodyAPI(cup).GetKinematicEnabledAttr().Get()
        ),
    }
    if table_b.IsA(UsdGeom.Cylinder):
        radius = float(UsdGeom.Cylinder(table_b).GetRadiusAttr().Get())
        checks["whole_cup_supported"] = bool(
            np.linalg.norm(world[verify, :, :2] - center[:2], axis=-1).max()
            < radius - 0.005
        )
    visual = plan.get("visual_localization")
    visual_error = None
    if visual:
        visual_error = float(
            np.linalg.norm(np.array(visual["cup_bottom_xyz_m"])[:2] - pose[0, :2])
        )
        checks["visual_target_matches_initial_cup"] = visual_error < 0.008
        checks["visual_result_bound_to_execution"] = result.get(
            "visual_localization_sha256"
        ) == plan.get("visual_localization_sha256")
    return {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAILED",
        "scope": "fixed-base two-table manipulation only",
        "checks": checks,
        "metrics": {
            "hold_clearance_min_m": float(bottom[hold].min() - top),
            "placement_xy_error_m": float(np.linalg.norm(pose[-1, :2] - support[:2])),
            "final_tilt_deg": float(tilt[-1]),
            "cup_mass_kg": mass,
            "mean_destination_support_N": float(np.mean(f[verify, 3])),
            "max_robot_table_contact_N": float(
                np.linalg.norm(data["environment_forces"], axis=-1).max()
            ),
            "max_cup_contact_N": f.max(0).tolist(),
            "visual_localization_xy_error_m": visual_error,
        },
        "visual_localization_validated": bool(
            visual
            and checks["visual_target_matches_initial_cup"]
            and checks["visual_result_bound_to_execution"]
        ),
        "visual_validation_scope": "one known-cup synthetic RGB-D view; no generalization claim",
        "monitored_environment_paths": result.get("environment_filters", []),
        "scene_repair_validated": False,
        "autonomous_navigation_validated": False,
        "real_world_validated": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--rollout", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    try:
        report = audit(args.assets, args.rollout)
    except (ValueError, KeyError, OSError, IndexError) as error:
        report = {"schema_version": 1, "status": "FAILED", "error": str(error)}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
