"""Isaac lifetime, composed stage and measured sensor access for cup workers.

No task policy or acceptance thresholds live here. Imports requiring Kit happen
only after SimulationApp is created, and partial initialization is cleanable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np


class IsaacCupRuntime:
    def __init__(self, args, result):
        self.args, self.result = args, result
        self.app = self.stage = self.simulation = None
        self.attached = False
        self.release_memory_stage = None
        self.tree = None

    def start(self):
        from isaacsim import SimulationApp

        self.app = SimulationApp(
            {"headless": True},
            experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
        )
        self._initialize()

    def _initialize(self):
        args, result = self.args, self.result
        import carb.settings
        import numpy as np
        import omni.physics.tensors as tensors
        import omni.physx
        from omni.physx.scripts.utils import new_memory_stage, release_memory_stage
        from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics, UsdUtils

        print("LOAD_MEMORY_STAGE", flush=True)
        stage = new_memory_stage(attach_stage=False)
        self.stage = stage
        self.release_memory_stage = release_memory_stage
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
        result["worker_sha256"] = digest(Path(__file__).with_name("cup_grasp.py"))
        randomization_receipt = args.assets / "randomization.json"
        if randomization_receipt.is_file():
            result["asset_sha256"]["randomization.json"] = digest(randomization_receipt)
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
        self.simulation = simulation
        self.attached = True
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
        if randomization_receipt.is_file():
            parameters = json.loads(randomization_receipt.read_text())
            result["randomization_runtime"] = {
                "cup_mass_kg": float(cup.get_masses().reshape(-1)[0]),
                "materials": {},
            }
            for group, binding in parameters["bindings"].items():
                prim = stage.GetPrimAtPath(binding["material"])
                material = UsdPhysics.MaterialAPI(prim)
                physx_material = PhysxSchema.PhysxMaterialAPI(prim)
                result["randomization_runtime"]["materials"][group] = {
                    "static_friction": float(material.GetStaticFrictionAttr().Get()),
                    "dynamic_friction": float(material.GetDynamicFrictionAttr().Get()),
                    "combine": physx_material.GetFrictionCombineModeAttr().Get(),
                }
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

        self.stage, self.robot, self.cup = stage, robot, cup
        self.contact, self.environment = contact, environment
        self.names, self.bodies, self.plan = names, bodies, plan
        self.filters, self.environment_paths = filters, environment_paths
        self.indices, self.dt, self.view = indices, dt, view

    def observe(self):
        links = self.robot.get_link_transforms()[0].copy()
        return {
            "poses": np.concatenate([links, self.cup.get_transforms()], axis=0).copy(),
            "palm": links[self.bodies.index("gripper_r_base_link")].copy(),
            "joints": self.robot.get_dof_positions()[0].copy(),
            "forces": self.contact.get_contact_force_matrix(self.dt)[0].copy(),
            "environment_forces": self.environment.get_contact_force_matrix(
                self.dt
            ).copy(),
        }

    def step(self, command, simulation_time):
        self.robot.set_dof_position_targets(command, self.indices)
        self.simulation.simulate(self.dt, simulation_time)
        self.simulation.fetch_results()

    def _kinematics(self):
        if self.tree is None:
            from simready.simulation.articulated_kinematics import UsdJointTree

            self.tree = UsdJointTree(self.stage, "/World/G2/base_link")
        return self.tree

    def solve(self, target, seed):
        return self._kinematics().solve(
            "/World/G2/gripper_r_base_link", target, self.plan["arm_names"], seed
        )

    def palm_pose(self, joints):
        return self._kinematics().pose(
            "/World/G2/gripper_r_base_link", dict(zip(self.names, joints, strict=True))
        )

    def export(self, path):
        self.stage.GetRootLayer().Export(str(path))

    def close_physics(self):
        errors = []
        interrupted = None
        if self.attached:
            try:
                self.simulation.detach_stage()
            except BaseException as error:
                errors.append(
                    {"resource": "stage_attachment", "error": type(error).__name__}
                )
                if not isinstance(error, Exception):
                    interrupted = error
            self.attached = False
        if self.stage is not None and self.release_memory_stage is not None:
            try:
                self.release_memory_stage(self.stage)
            except BaseException as error:
                errors.append(
                    {"resource": "memory_stage", "error": type(error).__name__}
                )
                if not isinstance(error, Exception) and interrupted is None:
                    interrupted = error
            self.stage = None
        if interrupted is not None:
            raise interrupted
        return errors

    def close_app(self, exit_code):
        if self.app is not None:
            app, self.app = self.app, None
            app.close(exit_code=exit_code)
