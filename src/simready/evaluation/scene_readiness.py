"""Independent structural/source gates; never equate them with photo fidelity."""

import argparse
import json
from pathlib import Path

import numpy as np

from simready.core.artifacts import digest, scene_dependency_hashes, write_json


def audit_scene(assets, probe, route=None, task=None):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    assets = Path(assets)
    scene_dir = assets / "scene"
    contract = json.loads((scene_dir / "scene_contract.json").read_text())
    source = Usd.Stage.Open(str(scene_dir / "original_video_splats.usdc"))
    stage = Usd.Stage.Open(str(scene_dir / "scene.usda"))
    source_fields = [
        p
        for p in source.Traverse()
        if p.GetTypeName() == "ParticleField3DGaussianSplat"
    ]
    fields = [
        p for p in stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    ]
    checks = {
        "source_usd_hash_matches": digest(scene_dir / "original_video_splats.usdc")
        == contract["source_usd_sha256"],
        "completion_hash_matches": digest(scene_dir / "completion.usda")
        == contract["completion_sha256"],
        "one_original_visual_field": len(source_fields) == len(fields) == 1,
        "meters_and_z_up": UsdGeom.GetStageMetersPerUnit(stage) == 1
        and UsdGeom.GetStageUpAxis(stage) == "Z",
    }
    if not checks["one_original_visual_field"]:
        raise ValueError("Unexpected visual field composition")
    original, current = source_fields[0], fields[0]
    checks["source_positions_unchanged"] = np.array_equal(
        original.GetAttribute("positions").Get(),
        current.GetAttribute("positions").Get(),
    )
    alpha = np.array(original.GetAttribute("opacities").Get())
    cleanup = scene_dir / "transient_cleanup.json"
    suppressed = 0
    if cleanup.exists():
        record = json.loads(cleanup.read_text())
        indices = np.load(scene_dir / "person_gaussian_indices.npy", allow_pickle=False)
        checks["cleanup_indices_valid"] = (
            indices.ndim == 1
            and len(np.unique(indices)) == len(indices)
            and indices.dtype.kind in "iu"
            and (indices >= 0).all()
            and (indices < len(alpha)).all()
        )
        checks["cleanup_hashes_match"] = (
            digest(scene_dir / "people_cleanup.usdc") == record["opacity_layer_sha256"]
            and digest(scene_dir / "person_gaussian_indices.npy")
            == record["indices_sha256"]
        )
        if checks["cleanup_indices_valid"]:
            alpha[indices] = 0
            suppressed = len(indices)
    appearance = scene_dir / "appearance_repair.json"
    if appearance.exists():
        record = json.loads(appearance.read_text())
        indices = np.load(
            scene_dir / "appearance_gaussian_indices.npy", allow_pickle=False
        )
        valid = (
            indices.ndim == 1
            and indices.dtype.kind in "iu"
            and len(np.unique(indices)) == len(indices)
            and (indices >= 0).all()
            and (indices < len(alpha)).all()
        )
        checks["appearance_indices_valid"] = bool(valid)
        checks["appearance_hashes_match"] = all(
            digest(scene_dir / filename) == record[key]
            for filename, key in [
                ("appearance_gaussian_indices.npy", "indices_sha256"),
                ("appearance_replacement.usdc", "opacity_layer_sha256"),
                ("appearance_reference.usda", "reference_geometry_sha256"),
                ("appearance_reference.json", "reference_manifest_sha256"),
            ]
        )
        reference = json.loads((scene_dir / "appearance_reference.json").read_text())
        texture_checks = []
        for asset in reference["assets"]:
            if "texture" not in asset:
                continue
            tex = asset["texture"]
            name = asset["id"].split("/")[-1]
            texture_checks.append(
                digest(scene_dir / "appearance_textures" / (name + ".png"))
                == tex["texture_sha256"]
                and not tex["heldout_used_for_selection"]
                and not (
                    set(tex["heldout_indices"])
                    & {x["camera_index"] for x in tex["sources"]}
                )
            )
        checks["texture_integrity_and_heldout_separation"] = bool(
            texture_checks
        ) and all(texture_checks)
        if valid:
            alpha[indices] = 0
            suppressed = int(
                np.count_nonzero(
                    np.asarray(original.GetAttribute("opacities").Get()) != alpha
                )
            )
    checks["only_declared_opacity_changes"] = np.array_equal(
        alpha, np.array(current.GetAttribute("opacities").Get())
    )
    checks["source_scales_unchanged"] = np.array_equal(
        original.GetAttribute("scales").Get(), current.GetAttribute("scales").Get()
    )
    colliders = []
    for p in stage.Traverse():
        if (
            not p.HasAPI(UsdPhysics.CollisionAPI)
            or UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is False
        ):
            continue
        mat, _ = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial("physics")
        material = UsdPhysics.MaterialAPI(mat.GetPrim()) if mat else None
        valid = (
            bool(material)
            and material.GetStaticFrictionAttr().Get()
            >= material.GetDynamicFrictionAttr().Get()
            >= 0
        )
        if p.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(p)
            points = np.asarray(mesh.GetPointsAttr().Get())
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            valid = (
                valid
                and points.ndim == 2
                and np.isfinite(points).all()
                and len(points) > 0
                and (indices >= 0).all()
                and (indices < len(points)).all()
            )
        colliders.append(
            {"path": str(p.GetPath()), "material_and_geometry_valid": bool(valid)}
        )
    checks["finite_colliders_with_contact_materials"] = len(colliders) >= 4 and all(
        c["material_and_geometry_valid"] for c in colliders
    )
    measured = json.loads(Path(probe).read_text())
    if appearance.exists():
        checks["probe_bound_to_all_scene_dependencies"] = measured.get(
            "scene_dependencies"
        ) == scene_dependency_hashes(scene_dir)
    raw = np.load(Path(probe).parent / "trajectories.npz", allow_pickle=False)
    positions = raw["positions"]
    checks["probe_trajectory_hash_and_shape"] = (
        digest(Path(probe).parent / "trajectories.npz") == measured["trajectory_sha256"]
        and positions.shape == (241, measured["probe_count"], 3)
        and np.isfinite(positions).all()
    )
    checks["probe_final_samples_match"] = np.allclose(
        positions[-1], [c["final"] for c in measured["checks"]], atol=1e-8
    )
    checks["actual_scene_probe_passed"] = (
        measured["status"] == "PASS"
        and measured["probe_count"] >= 12
        and not measured["fallback_ground"]
    )
    checks["probe_bound_to_this_completion"] = measured["completion_sha256"] == digest(
        scene_dir / "completion.usda"
    ) and measured["scene_sha256"] == digest(scene_dir / "scene.usda")
    if route:
        route_report = json.loads(Path(route).read_text())
        checks["patrol_geometry_gate"] = (
            route_report["status"] == "PASS"
            and len(route_report.get("frames", [])) == 900
            and all(
                x["overlapping_obstacle_cells"] == 0 for x in route_report["frames"]
            )
        )
        checks["patrol_evidence_bound"] = route_report[
            "scene_obstacles_sha256"
        ] == digest(scene_dir / "obstacles.npz") and route_report[
            "checked_trajectory_sha256"
        ] == digest(Path(route).parent / "trajectory.json")
    if task:
        task_report = json.loads(Path(task).read_text())
        checks["manipulation_task_gate"] = task_report["status"] == "PASS" and all(
            task_report["checks"].values()
        )
    return {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAILED",
        "scope": "static scene contact readiness plus separately supplied task/route gates",
        "checks": {k: bool(v) for k, v in checks.items()},
        "colliders": colliders,
        "suppressed_gaussians": suppressed,
        "source_video_sha256": contract["source_video"]["sha256"],
        "full_photometric_reconstruction_validated": False,
        "real_world_parameters_validated": False,
        "autonomous_navigation_validated": False,
        "limits": [
            "Explicit furniture meshes and any retained occupied-cell proxies are estimates, not measured geometry",
            "Unobserved surfaces and material values are estimates",
            "Source fidelity and unobserved texture completion have not passed a photometric benchmark",
        ],
        "input_hashes": {
            "scene": digest(scene_dir / "scene.usda"),
            "probe": digest(probe),
            **({"route": digest(route)} if route else {}),
            **({"task": digest(task)} if task else {}),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", required=True, type=Path)
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--route", type=Path)
    ap.add_argument("--task", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    result = audit_scene(args.assets, args.probe, args.route, args.task)
    write_json(args.out, result)
    print(
        json.dumps({"status": result["status"], "checks": result["checks"]}), flush=True
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
