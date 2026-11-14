"""Exercise the composed scene's actual colliders with independent drop probes.

There is no fallback ground plane. Failure to author/load collision geometry
therefore produces falling probes and a failed gate. This is not robot control.
"""

import argparse
import json
import os
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True},
        experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
    )
    status = 2
    try:
        import carb.settings
        import numpy as np
        import omni.physx
        from omni.physx.scripts.utils import new_memory_stage, release_memory_stage
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics, UsdUtils
        from scipy.spatial import cKDTree

        from simready.assets.physics.physical_authoring import bind_contact_material
        from simready.core.artifacts import digest, scene_dependency_hashes, write_json
        from simready.geometry.polygons import inside_convex_polygon

        carb.settings.get_settings().set_bool("/physics/updateToUsd", True)
        carb.settings.get_settings().set_bool("/physics/updateVelocitiesToUsd", True)
        data = np.load(args.assets / "scene/obstacles.npz", allow_pickle=False)
        occupied, origin, res = (
            data["occupied"],
            data["origin"],
            float(data["resolution"]),
        )
        t, floor = data["old_to_world"], data["floor"]
        inverse = np.linalg.inv(t)
        obstacles = (np.argwhere(occupied) + 0.5) * res + origin
        tree = cKDTree(obstacles[:, :2]) if len(obstacles) else None
        stage = new_memory_stage(attach_stage=False)
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        world = UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(world.GetPrim())
        stage.DefinePrim("/World/Background", "Xform").GetReferences().AddReference(
            str(args.assets / "scene/scene.usda")
        )
        scene = UsdPhysics.Scene.Define(stage, "/World/Physics")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        physx_scene = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
        physx_scene.CreateEnableGPUDynamicsAttr(False)
        physx_scene.CreateBroadphaseTypeAttr("MBP")
        physx_scene.CreateSolverTypeAttr("TGS")
        targets = []
        contract = json.loads((args.assets / "scene/scene_contract.json").read_text())
        floor_openings = contract.get("floor_opening_polygons_old", [])
        excluded_floor_sites = []
        for x in np.linspace(-3.8, 4.1, 9):
            for y in [-3.1, -2.5, -1.6, -0.7, 0.3, 1.5, 2.3]:
                if any(
                    inside_convex_polygon([x, y], polygon) for polygon in floor_openings
                ):
                    excluded_floor_sites.append([float(x), float(y)])
                    continue
                if tree is not None and tree.query([x, y])[0] < 0.25:
                    continue
                old = np.array([x, y, np.array([x, y, 1]) @ floor])
                point = old @ t[:3, :3].T + t[:3, 3]
                targets.append(
                    {
                        "id": f"floor_{len(targets):03d}",
                        "surface": "floor",
                        "point": point.tolist(),
                    }
                )
        manifest = json.loads((args.assets / "scene/physical_assets.json").read_text())
        for i, xy in enumerate(contract.get("additional_support_sites_old", [])):
            if len(xy) != 2 or not np.isfinite(xy).all():
                raise ValueError("Invalid additional floor probe site")
            if any(inside_convex_polygon(xy, polygon) for polygon in floor_openings):
                excluded_floor_sites.append(list(xy))
                continue
            old = np.r_[xy, np.r_[xy, 1] @ floor]
            point = old @ t[:3, :3].T + t[:3, 3]
            targets.append(
                {
                    "id": f"additional_floor_{i:03d}",
                    "surface": "floor",
                    "point": point.tolist(),
                }
            )
        for target in contract.get("additional_support_targets_world", []):
            if (
                target.get("surface") != "stair_tread"
                or len(target.get("point", [])) != 3
                or not np.isfinite(target["point"]).all()
                or not target.get("id", "").replace("_", "").isalnum()
            ):
                raise ValueError("Invalid explicitly declared stair probe target")
            targets.append(target)
        for asset in manifest["assets"]:
            if asset["category"] == "table":
                targets.append(
                    {
                        "id": asset["id"],
                        "surface": "table",
                        "point": asset["world_top_center"],
                    }
                )
        if len(targets) < 12:
            raise ValueError("Insufficient free support probe sites")
        probes = []
        for target in targets:
            path = "/World/Probes/" + target["id"]
            sphere = UsdGeom.Sphere.Define(stage, path)
            sphere.CreateRadiusAttr(0.035)
            start = np.asarray(target["point"]) + [0, 0, 0.25]
            if target["surface"] == "stair_tread":
                # Start above the upper floor, so a stale invisible flat floor
                # catches the ball and fails the lower-tread height check.
                start[2] = max(start[2], 0.25)
            sphere.AddTranslateOp().Set(Gf.Vec3d(*start))
            UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
            UsdPhysics.RigidBodyAPI.Apply(sphere.GetPrim())
            UsdPhysics.MassAPI.Apply(sphere.GetPrim()).CreateMassAttr(0.05)
            body = PhysxSchema.PhysxRigidBodyAPI.Apply(sphere.GetPrim())
            body.CreateSolverPositionIterationCountAttr(16)
            body.CreateSolverVelocityIterationCountAttr(4)
            bind_contact_material(
                stage,
                path + "Material",
                [path],
                {"static_friction": 0.8, "dynamic_friction": 0.6, "restitution": 0},
            )
            probes.append(sphere.GetPrim())
        stage.GetRootLayer().Export(str(args.out / "probe_scene.usda"))
        simulation = omni.physx.get_physx_simulation_interface()
        simulation.attach_stage(UsdUtils.StageCache.Get().GetId(stage).ToLongInt())
        positions = []
        dt = 1 / 120
        for step in range(241):
            if step:
                simulation.simulate(dt, (step - 1) * dt)
                simulation.fetch_results()
            cache = UsdGeom.XformCache()
            positions.append(
                [
                    list(cache.GetLocalToWorldTransform(p).ExtractTranslation())
                    for p in probes
                ]
            )
        positions = np.asarray(positions)
        checks = []
        for i, target in enumerate(targets):
            end = positions[-1, i]
            expected = target["point"][2] + 0.035
            if target["surface"] == "floor":
                old = end @ inverse[:3, :3].T + inverse[:3, 3]
                old[2] = np.r_[old[:2], 1] @ floor
                expected = (old @ t[:3, :3].T + t[:3, 3])[2] + 0.035
            error = float(abs(end[2] - expected))
            settled = float(np.linalg.norm(positions[-1, i] - positions[-31, i]))
            checks.append(
                {
                    **target,
                    "final": end.tolist(),
                    "support_height_error_m": error,
                    "last_quarter_second_displacement_m": settled,
                    "passed": error < 0.025 and settled < 0.06,
                }
            )
        valid = np.isfinite(positions).all() and all(c["passed"] for c in checks)
        np.savez_compressed(args.out / "trajectories.npz", positions=positions, dt=dt)
        result = {
            "status": "PASS" if valid else "FAILED",
            "scope": "static support drop probes on the actual composed collision scene",
            "checks": checks,
            "fallback_ground": False,
            "excluded_flat_floor_sites_in_declared_openings": excluded_floor_sites,
            "stair_tread_probes": sum(t["surface"] == "stair_tread" for t in targets),
            "simulated_seconds": 2,
            "probe_count": len(checks),
            "passed_count": sum(c["passed"] for c in checks),
            "collision_prims": [
                str(p.GetPath())
                for p in stage.Traverse()
                if p.HasAPI(UsdPhysics.CollisionAPI)
                and str(p.GetPath()).startswith("/World/Background/")
            ],
            "scene_sha256": digest(args.assets / "scene/scene.usda"),
            "completion_sha256": digest(args.assets / "scene/completion.usda"),
            "scene_dependencies": scene_dependency_hashes(args.assets / "scene"),
            "trajectory_sha256": digest(args.out / "trajectories.npz"),
            "real_world_validated": False,
            "robot_navigation_validated": False,
        }
        write_json(args.out / "probe.json", result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "passed": result["passed_count"],
                    "total": result["probe_count"],
                }
            ),
            flush=True,
        )
        release_memory_stage(stage)
        status = 0 if valid else 2
    finally:
        app.close(exit_code=status)


if __name__ == "__main__":
    main()
