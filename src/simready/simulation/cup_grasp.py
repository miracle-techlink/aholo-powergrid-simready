"""G2 contact canary: drive actual joints and preserve measured trajectories.

The fixed-base canary uses a known target to isolate control/contact. It is not
autonomous visual localization or a validated real-world cup parameter model.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", type=Path)
    parser.add_argument("--physics-hz", type=int, choices=[240, 480], default=240)
    parser.add_argument(
        "--gripper-close-rad",
        type=float,
        choices=[0.60, 0.65, 0.70, 0.75],
        default=0.70,
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    import faulthandler

    trace = (args.out / "stack.log").open("w")
    faulthandler.dump_traceback_later(120, repeat=True, file=trace)
    started = time.perf_counter()
    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True},
        experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
    )
    result = {
        "status": "FAILED",
        "scope": "fixed-base contact canary; known target; no cafe background",
    }
    try:
        import carb.settings
        import numpy as np
        import omni.physics.tensors as tensors
        import omni.physx
        from omni.physx.scripts.utils import new_memory_stage, release_memory_stage
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics, UsdUtils

        print("LOAD_MEMORY_STAGE", flush=True)
        stage = new_memory_stage(attach_stage=False)
        stage.GetRootLayer().subLayerPaths = [str(args.assets / "task.usda")]
        if args.scene:
            stage.DefinePrim("/World/Background", "Xform").GetReferences().AddReference(
                str(args.scene)
            )
            contract_path = args.scene.parent / "scene_contract.json"
            contract = (
                json.loads(contract_path.read_text()) if contract_path.exists() else {}
            )
            for binding in contract.get("task_support_overrides", []):
                task_support = stage.GetPrimAtPath(binding["task_path"])
                duplicate = stage.GetPrimAtPath(
                    "/World/Background/" + binding["background_path"]
                )
                if not task_support or not duplicate:
                    raise ValueError("Missing declared task support binding")
                UsdPhysics.CollisionAPI(duplicate).CreateCollisionEnabledAttr(False)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.SetStageUpAxis(stage, "Z")
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        ps = PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim())
        ps.CreateEnableGPUDynamicsAttr(False)
        ps.CreateBroadphaseTypeAttr("MBP")
        ps.CreateSolverTypeAttr("TGS")
        for p in stage.Traverse():
            if p.HasAPI(UsdPhysics.RigidBodyAPI):
                PhysxSchema.PhysxContactReportAPI.Apply(p).CreateThresholdAttr(0.0)
                api = PhysxSchema.PhysxRigidBodyAPI.Apply(p)
                api.CreateSolverPositionIterationCountAttr(32)
                api.CreateSolverVelocityIterationCountAttr(8)
                if str(p.GetPath()) == "/World/Cup":
                    api.CreateSleepThresholdAttr(0.0)
            if p.HasAPI(UsdPhysics.CollisionAPI):
                api = PhysxSchema.PhysxCollisionAPI.Apply(p)
                api.CreateContactOffsetAttr(0.001)
                api.CreateRestOffsetAttr(0.0)
        plan = json.loads((args.assets / "plan.json").read_text())
        from simready.core.artifacts import digest

        result["asset_sha256"] = {
            name: digest(args.assets / name)
            for name in ("task.usda", "plan.json", "robot.usdc", "cup.usdc")
        }
        if args.scene:
            relative_scene = args.scene.relative_to(args.assets)
            result["asset_sha256"][str(relative_scene)] = digest(args.scene)
            # Bind nested repair layers and their provenance to this rollout;
            # hashing only the top-level reference file misses scene changes.
            for dependency in sorted(args.scene.parent.rglob("*")):
                if dependency.is_file() and dependency.suffix in {
                    ".usd",
                    ".usda",
                    ".usdc",
                    ".json",
                    ".npy",
                    ".npz",
                    ".png",
                    ".jpg",
                    ".jpeg",
                }:
                    result["asset_sha256"][str(dependency.relative_to(args.assets))] = (
                        digest(dependency)
                    )
        result["worker_sha256"] = digest(Path(__file__))
        result["scope"] = plan["scope"]
        # Resolve the stiff six-joint gripper contact without relaxing the
        # independent bilateral-contact acceptance threshold.
        dt = 1 / args.physics_hz
        result["physics_hz"] = args.physics_hz
        result["gripper_close_rad"] = args.gripper_close_rad
        result["command_interpolation"] = (
            "quintic zero endpoint velocity and acceleration"
        )
        settings = carb.settings.get_settings()
        settings.set_bool("/physics/updateToUsd", True)
        simulation = omni.physx.get_physx_simulation_interface()
        stage_id = UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
        print("ATTACH_STAGE", stage_id, flush=True)
        simulation.attach_stage(stage_id)
        print("FIRST_STEP", flush=True)
        simulation.simulate(dt, 0.0)
        simulation.fetch_results()
        print("CREATE_VIEW", flush=True)
        view = tensors.create_simulation_view("numpy", stage_id=stage_id)
        view.set_subspace_roots("/")
        robot = view.create_articulation_view("/World/G2")
        print("ARTICULATION", robot.count, robot.shared_metatype.dof_names, flush=True)
        names = list(robot.shared_metatype.dof_names)
        indices = np.array([0], dtype=np.int32)
        q = np.array([[plan["initial"].get(n, 0.0) for n in names]], dtype=np.float32)
        robot.set_dof_positions(q, indices)
        robot.set_dof_position_targets(q, indices)
        cup = view.create_rigid_body_view("/World/Cup")
        filters = [
            "/World/G2/gripper_r_left_support_link",
            "/World/G2/gripper_r_right_support_link",
            "/World/Table",
            "/World/TableB",
        ]
        contact = view.create_rigid_contact_view(
            ["/World/Cup"], filter_patterns=[filters], max_contact_data_count=256
        )
        bodies = list(robot.shared_metatype.link_names)
        robot_paths = ["/World/G2/" + name for name in bodies]
        environment_paths = ["/World/Table", "/World/TableB"]
        environment_paths += [
            str(p.GetPath())
            for p in stage.Traverse()
            if str(p.GetPath()).startswith("/World/Background/")
            and p.HasAPI(UsdPhysics.CollisionAPI)
            and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False
            and not str(p.GetPath()).endswith("/Floor")
        ]
        environment = view.create_rigid_contact_view(
            robot_paths,
            filter_patterns=[environment_paths for _ in robot_paths],
            max_contact_data_count=4096,
        )
        print(
            "CONTACT",
            contact.sensor_count,
            contact.filter_count,
            "LINKS",
            len(bodies),
            flush=True,
        )
        arm_i = [names.index(n) for n in plan["arm_names"]]
        grip = {
            "idx71_gripper_r_inner_joint1": -1,
            "idx72_gripper_r_outer_joint1": -1,
            "idx73_gripper_r_left_support_joint": 1,
            "idx74_gripper_r_inner_joint2": 1,
            "idx75_gripper_r_outer_joint2": 1,
            "idx76_gripper_r_right_support_joint": 1,
        }
        waypoints = {w["name"]: np.array(w["q"]) for w in plan["waypoints"]}
        poses = []
        joints = []
        commands = []
        forces = []
        labels = []
        env_forces = []
        closed = args.gripper_close_rad
        phases = [
            ("SETTLE", 1.0, "pregrasp", "pregrasp", 0, 0),
            ("APPROACH", 2.0, "pregrasp", "grasp", 0, 0),
            ("CLOSE", 1.5, "grasp", "grasp", 0, closed),
            ("LIFT", 2.0, "grasp", "lift", closed, closed),
            ("HOLD", 2.0, "lift", "lift", closed, closed),
            ("TRANSFER", 4.0, "lift", "transfer", closed, closed),
            ("PLACE", 4.0, "transfer", "place", closed, closed),
            ("RELEASE", 2.0, "place", "place", closed, 0),
            ("RETREAT", 2.0, "place", "transfer", 0, 0),
            ("VERIFY", 2.0, "transfer", "transfer", 0, 0),
        ]
        for label, duration, qa, qb, ga, gb in phases:
            if label == "PLACE":
                from scipy.spatial.transform import Rotation

                from simready.simulation.articulated_kinematics import UsdJointTree

                tree = UsdJointTree(stage, "/World/G2/base_link")
                palm = robot.get_link_transforms()[
                    0, bodies.index("gripper_r_base_link")
                ].copy()
                held = cup.get_transforms()[0].copy()
                destination = np.array(plan["destination"]["cup_bottom_xyz_m"])
                # Transfer the cup's load to the support before opening.
                # Releasing 2 mm above it lets asymmetric finger contact push
                # the still-unsupported cup sideways during opening.
                end = palm[:3] + destination - held[:3]
                target = np.eye(4)
                target[:3, :3] = Rotation.from_quat(palm[3:]).as_matrix()
                seed = robot.get_dof_positions()[0, arm_i].copy()
                samples = []
                for fraction in np.linspace(0, 1, 81):
                    target[:3, 3] = palm[:3] * (1 - fraction) + end * fraction
                    seed = tree.solve(
                        "/World/G2/gripper_r_base_link", target, plan["arm_names"], seed
                    )
                    samples.append(seed.tolist())
                plan["paths"]["transfer_place"] = samples
                plan["paths"]["place_transfer"] = list(reversed(samples))
                waypoints["place"] = np.array(samples[-1])
                result["placement_feedback"] = {
                    "source": "measured simulator rigid-body poses; privileged canary feedback",
                    "held_cup_xyz_m": held[:3].tolist(),
                    "gripper_destination_xyz_m": end.tolist(),
                    "release_clearance_m": 0.0,
                }
            path = np.array(
                plan["paths"].get(
                    qa + "_" + qb, [waypoints[qa].tolist(), waypoints[qb].tolist()]
                )
            )
            print("PHASE", label, flush=True)
            for step in range(round(duration / dt)):
                t = (step + 1) / round(duration / dt)
                u = t * t * t * (10 - 15 * t + 6 * t * t)
                q[0, arm_i] = [
                    np.interp(u, np.linspace(0, 1, len(path)), path[:, j])
                    for j in range(7)
                ]
                for n, sign in grip.items():
                    q[0, names.index(n)] = sign * (ga * (1 - u) + gb * u)
                robot.set_dof_position_targets(q, indices)
                simulation.simulate(dt, (len(poses) + 1) * dt)
                simulation.fetch_results()
                poses.append(
                    np.concatenate(
                        [robot.get_link_transforms()[0], cup.get_transforms()], axis=0
                    ).copy()
                )
                joints.append(robot.get_dof_positions()[0].copy())
                commands.append(q[0].copy())
                forces.append(contact.get_contact_force_matrix(dt)[0].copy())
                labels.append(label)
                env_forces.append(environment.get_contact_force_matrix(dt).copy())
            print(
                "STATE",
                label,
                "cup",
                cup.get_transforms()[0].tolist(),
                "force",
                forces[-1].tolist(),
                flush=True,
            )
        np.savez_compressed(
            args.out / "rollout.npz",
            poses=np.asarray(poses),
            joints=np.asarray(joints),
            commands=np.asarray(commands),
            forces=np.asarray(forces),
            environment_forces=np.asarray(env_forces),
            phases=np.asarray(labels),
            dt=dt,
        )
        result["rollout_sha256"] = digest(args.out / "rollout.npz")
        cp = np.asarray(poses)[:, -1, :3]
        f = np.linalg.norm(np.asarray(forces), axis=-1)
        hold = np.asarray(labels) == "HOLD"
        # Initial gate, later independent audit also checks release/penetration.
        checks = {
            "lift_above_10cm": bool(np.min(cp[hold, 2]) > 0.95),
            "bilateral_contact_during_hold": bool(
                np.mean(np.all(f[hold, :2] > 0.05, axis=1)) > 0.95
            ),
            "placed_on_second_table": bool(
                np.linalg.norm(
                    cp[-1] - np.array(plan["destination"]["cup_bottom_xyz_m"])
                )
                < 0.015
            ),
            "released": bool(np.max(f[np.asarray(labels) == "VERIFY", :2]) < 0.01),
            "no_robot_environment_contact": bool(
                np.max(np.linalg.norm(env_forces, axis=-1)) < 1.0
            ),
        }
        result.update(
            status="PASS" if all(checks.values()) else "FAILED",
            checks=checks,
            min_hold_height_m=float(np.min(cp[hold, 2]) - 0.85),
            max_height_m=float(cp[:, 2].max() - 0.85),
            bilateral_hold_fraction=float(np.mean(np.all(f[hold, :2] > 0.05, axis=1))),
            max_force_N=f.max(axis=0).tolist(),
            joint_names=names,
            body_names=bodies + ["Cup"],
            contact_filters=filters,
            environment_filters=environment_paths,
            background_scene=str(args.scene),
            frames=len(poses),
            dt_s=dt,
            real_world_validated=False,
            visual_localization=bool(plan.get("visual_localization")),
            visual_localization_sha256=plan.get("visual_localization_sha256"),
            object_pose_overrides_during_episode=0,
            object_attachment_joint=False,
        )
        stage.GetRootLayer().Export(str(args.out / "final_stage.usda"))
        simulation.detach_stage()
        release_memory_stage(stage)
    except Exception as error:
        import traceback

        traceback.print_exc()
        result["error"] = str(error)
    finally:
        result["wall_s"] = time.perf_counter() - started
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
        faulthandler.cancel_dump_traceback_later()
        trace.close()
        app.close(exit_code=0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
