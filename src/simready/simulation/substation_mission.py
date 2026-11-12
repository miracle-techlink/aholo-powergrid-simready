#!/usr/bin/env python3
"""Known-map Lite3 navigation, RGB-D target localization and physical Z1 press."""

import argparse
import json
import time
import traceback
from pathlib import Path

from simready.core.artifacts import digest, write_json
from simready.simulation.preflight import simulation_lease

MODULE_DIR = Path(__file__).resolve().parent


def run(a, apps):
    import numpy as np
    from isaaclab.app import AppLauncher

    app = AppLauncher(
        headless=True,
        enable_cameras=True,
        limit_cpu_threads=4,
        experience="isaacsim.exp.base.kit",
    ).app
    apps.append(app)
    import carb
    import cv2
    import omni.replicator.core as rep
    import omni.usd
    import onnxruntime as ort
    import torch
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import (
        Articulation,
        ArticulationCfg,
        RigidObject,
        RigidObjectCfg,
    )
    from isaaclab.sim import SimulationCfg, SimulationContext
    from isaaclab_physx.physics import PhysxManager
    from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdLux, UsdPhysics
    from scipy.spatial.transform import Rotation

    from simready.simulation.arm_planner import ArmScenePlanner, advance_joint_target
    from simready.simulation.button import ButtonSample
    from simready.simulation.kinematics import Chain
    from simready.simulation.panel_controls import PanelControl
    from simready.simulation.panel_perception import (
        detect_stop_candidate,
        localize_rgbd,
    )
    from simready.simulation.panel_scene import prepare_scene
    from simready.simulation.scene_contract import mission_config

    cfg = mission_config(a.config)
    manifest_path = Path(cfg["environment_manifest"])
    manifest = json.loads(manifest_path.read_text())
    original_spec = next(
        b for b in manifest["buttons"] if b["id"] == cfg["audit_target_id"]
    )
    # The catalog ID is used only for contact binding and independent checks.
    # Target coordinates used by IK are supplied by RGB-D observations below.
    spec = dict(original_spec)
    for key in ["cap_prim", "base_prim", "joint_prim"]:
        spec[key] = spec[key].replace("/World/", "/World/PanelEnvironment/", 1)
    contract_path = Path(cfg["rig_report"])
    contract = json.loads(contract_path.read_text())
    contract["rig_path"] = str((contract_path.parent / contract["rig_path"]).resolve())
    scene_config = json.loads((Path(cfg["scene_config"])).read_text())
    for key in (
        "usd_path",
        "visual_source_path",
        "visual_reference_path",
        "collision_derived_from_path",
    ):
        if key in scene_config["scene"]:
            scene_config["scene"][key] = str(
                (
                    Path(cfg["scene_config"]).parent / scene_config["scene"][key]
                ).resolve()
            )
    scene_config["spawn"]["position_m"] = cfg["spawn"]
    write_json(a.out / "scene_config.json", scene_config)
    prepare_scene(a.out / "scene_config.json", a.out, contract_path)
    omni.usd.get_context().open_stage(str(a.out / "scene.usda"))
    stage = omni.usd.get_context().get_stage()
    for _ in range(8):
        app.update()
    arm_joint = UsdPhysics.RevoluteJoint(
        stage.GetPrimAtPath("/World/FrankaArm/Physics/joint6")
    )
    tool_body = str(arm_joint.GetBody1Rel().GetTargets()[0])
    tip_offset = np.array(cfg["tool"]["flange_offset_m"])
    tip_radius = cfg["tool"]["tip_radius_m"]
    flange_body = tool_body
    tool_info = None
    if cfg["tool"].get("kind") == "spring_plunger":
        from simready.simulation.press_tool import build_spring_plunger

        tool_info = build_spring_plunger(stage, flange_body, cfg["tool"])
        tool_body = tool_info["contact_body"]
        write_json(a.out / "press_tool.json", tool_info)
    else:
        tip = UsdGeom.Sphere.Define(stage, tool_body + "/TaskPressTip")
        tip.CreateRadiusAttr(tip_radius)
        tip.AddTranslateOp().Set(Gf.Vec3d(*tip_offset))
        tip.CreateDisplayColorAttr([Gf.Vec3f(0.02, 0.25, 0.8)])
        UsdPhysics.CollisionAPI.Apply(tip.GetPrim())
        shaft = UsdGeom.Capsule.Define(stage, tool_body + "/TaskPressShaft")
        shaft.CreateRadiusAttr(0.002)
        shaft.CreateHeightAttr(float(tip_offset[0]))
        shaft.CreateAxisAttr("X")
        shaft.AddTranslateOp().Set(Gf.Vec3d(float(tip_offset[0] / 2), 0, 0))
        shaft.CreateDisplayColorAttr([Gf.Vec3f(0.3, 0.35, 0.4)])
        UsdPhysics.CollisionAPI.Apply(shaft.GetPrim())
    body_paths = {}
    for p in stage.Traverse():
        if p.HasAPI(UsdPhysics.RigidBodyAPI):
            PhysxSchema.PhysxContactReportAPI.Apply(p).CreateThresholdAttr(0.0)
            if str(p.GetPath()).startswith(("/World/Lite3/", "/World/FrankaArm/")):
                assert not UsdPhysics.RigidBodyAPI(p).GetKinematicEnabledAttr().Get()
                body_paths[p.GetName()] = str(p.GetPath())
        if p.IsA(UsdPhysics.FixedJoint):
            j = UsdPhysics.FixedJoint(p)
            ends = list(j.GetBody0Rel().GetTargets()) + list(
                j.GetBody1Rel().GetTargets()
            )
            if any(
                str(x).startswith(("/World/Lite3/", "/World/FrankaArm/")) for x in ends
            ):
                assert len(j.GetBody0Rel().GetTargets()) and len(
                    j.GetBody1Rel().GetTargets()
                ), "Robot world fixture forbidden"
    art_api = PhysxSchema.PhysxArticulationAPI.Apply(
        stage.GetPrimAtPath("/World/Lite3/TORSO")
    )
    art_api.CreateSolverPositionIterationCountAttr(24)
    art_api.CreateSolverVelocityIterationCountAttr(8)
    load = stage.DefinePrim("/World/TaskState/RightFarSimulatedLoad", "Xform")
    load.CreateAttribute("task:state", Sdf.ValueTypeNames.String).Set("RUNNING")
    load.CreateAttribute("task:realElectricalCommand", Sdf.ValueTypeNames.Bool).Set(
        False
    )
    dt = 0.002
    sim = SimulationContext(
        SimulationCfg(
            dt=dt,
            device="cuda:0",
            physics_prim_path="/World/PhysicsScene",
            render_interval=20,
        )
    )
    legs, arms = contract["groups"]["legs"], contract["groups"]["arm"]
    home = {n: j["home"] for n, j in contract["joints"].items()}
    for n in legs:
        home[n] = 0.0 if "HipX" in n else (-0.65 if "HipY" in n else 1.3)
    actuators = {
        "legs": ImplicitActuatorCfg(
            joint_names_expr=legs,
            stiffness=0.0,
            damping=0.0,
            effort_limit_sim=40.0,
            velocity_limit_sim=26.2,
        ),
        "arm": ImplicitActuatorCfg(
            joint_names_expr=arms,
            stiffness=80.0,
            damping=7.0,
            effort_limit_sim=40.0,
            velocity_limit_sim=1.0,
        ),
    }
    if tool_info:
        home[tool_info["joint_name"]] = 0.0
        actuators["passive_tool"] = ImplicitActuatorCfg(
            joint_names_expr=[tool_info["joint_name"]],
            stiffness=cfg["tool"]["spring_N_m"],
            damping=cfg["tool"]["damping_N_s_m"],
            effort_limit_sim=20.0,
            velocity_limit_sim=0.15,
        )
    robot = Articulation(
        ArticulationCfg(
            prim_path="/World/Lite3",
            spawn=None,
            init_state=ArticulationCfg.InitialStateCfg(joint_pos=home),
            actuators=actuators,
        )
    )
    caps = RigidObject(RigidObjectCfg(prim_path=spec["cap_prim"], spawn=None))
    bases = RigidObject(RigidObjectCfg(prim_path=spec["base_prim"], spawn=None))
    sim.reset()
    robot.update(dt)
    caps.update(dt)
    bases.update(dt)

    def cpu(x):
        if hasattr(x, "torch"):
            x = x.torch
        return x.detach().cpu().numpy() if hasattr(x, "detach") else x.numpy()

    names, bodies = list(robot.joint_names), list(robot.body_names)
    li, ai = [names.index(n) for n in legs], [names.index(n) for n in arms]
    tool_i = bodies.index(tool_body.rsplit("/", 1)[-1])
    spring_i = names.index(tool_info["joint_name"]) if tool_info else None
    q0 = torch.tensor([[home[n] for n in names]], device=robot.device)
    robot.write_root_pose_to_sim(
        torch.tensor([[*cfg["spawn"], 0.0, 0.0, 0.0, 1.0]], device=robot.device)
    )
    robot.write_root_velocity_to_sim(torch.zeros((1, 6), device=robot.device))
    robot.write_joint_state_to_sim(q0, torch.zeros_like(q0))
    robot.reset()
    target = q0.clone()
    last_action = np.zeros(12, np.float32)
    defaults = np.array([home[n] for n in legs], np.float32)
    scales = np.array([0.125, 0.25, 0.25] * 4, np.float32)
    limits = torch.tensor(
        [[36.0 if "Knee" in n else (24.0 if n in legs else 40.0) for n in names]],
        device=robot.device,
    )
    policy_path = Path(cfg["locomotion_policy"])
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    policy = ort.InferenceSession(
        str(policy_path), opts, providers=["CPUExecutionProvider"]
    )
    contact_view = PhysxManager.get_physics_sim_view().create_rigid_contact_view(
        [spec["cap_prim"]], filter_patterns=[[tool_body]], max_contact_data_count=128
    )
    robot_paths = [body_paths[n] for n in bodies]
    environment_actors = set()
    for p in stage.Traverse():
        if str(p.GetPath()).startswith("/World/PanelEnvironment/") and p.HasAPI(
            UsdPhysics.CollisionAPI
        ):
            if UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is False:
                continue
            owner = p
            while owner and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                owner = owner.GetParent()
            environment_actors.add(str(owner.GetPath() if owner else p.GetPath()))
    environment_actors = sorted(environment_actors)
    environment_view = PhysxManager.get_physics_sim_view().create_rigid_contact_view(
        robot_paths,
        filter_patterns=[environment_actors for _ in bodies],
        max_contact_data_count=8192,
    )
    assert environment_view.sensor_count == len(
        bodies
    ) and environment_view.filter_count == len(environment_actors)
    sensor_order = [list(environment_view.sensor_paths).index(p) for p in robot_paths]
    assert contact_view.sensor_count == 1 and contact_view.filter_count == 1
    adapter = PanelControl(
        spec,
        tool_body,
        lambda value: (_ for _ in ()).throw(
            RuntimeError("Momentary target must not command cap")
        ),
    )
    chain = Chain.from_usd(contract["rig_path"])
    planner = ArmScenePlanner(stage, chain, spec["cap_prim"])
    tool_transform = np.eye(4)
    tool_transform[:3, 3] = tip_offset

    def body_frames():
        positions = cpu(robot.data.body_pos_w)[0]
        quats = cpu(robot.data.body_quat_w)[0]
        result = {}
        for name, position, quat in zip(bodies, positions, quats, strict=False):
            frame = np.eye(4)
            frame[:3, 3] = position
            frame[:3, :3] = Rotation.from_quat(quat).as_matrix()
            result[name] = frame
        return result

    def solve_point(point, seed):
        frames = body_frames()
        goal = np.eye(4)
        goal[:3, 3] = point
        goal[:3, :3] = Rotation.from_euler("z", -90, degrees=True).as_matrix()
        desired = np.linalg.inv(frames["link00"]) @ goal @ np.linalg.inv(tool_transform)
        solution = chain.solve(
            desired,
            np.clip(seed, chain.lower + 1e-7, chain.upper - 1e-7),
            position_tolerance=0.003,
            rotation_tolerance=0.03,
        )
        return np.array([solution["joints"][n] for n in arms]), frames

    settings = carb.settings.get_settings()
    settings.set("/isaaclab/video/enabled", True)
    settings.set("/rtx/rendermode", "RealTimePathTracing")
    settings.set("/rtx/post/aa/op", 4)
    settings.set("/rtx/dlss/frameGeneration", False)
    settings.set("/rtx/post/colorcorr/enabled", False)
    dome = UsdLux.DomeLight.Define(stage, "/World/TaskDome")
    dome.CreateIntensityAttr(700.0)
    lamp = UsdLux.RectLight.Define(stage, "/World/TaskFill")
    lamp.CreateIntensityAttr(1400.0)
    lamp.CreateWidthAttr(1.0)
    lamp.CreateHeightAttr(0.5)
    lamp.AddTransformOp().Set(
        Gf.Matrix4d()
        .SetLookAt(Gf.Vec3d(3.8, 0.2, 1.4), Gf.Vec3d(3.9, -0.6, 0.8), Gf.Vec3d(0, 0, 1))
        .GetInverse()
    )
    width, height = cfg["camera"]["resolution"]
    camera = UsdGeom.Camera.Define(stage, "/World/TaskRGBCamera")
    camera.CreateFocalLengthAttr(cfg["camera"]["focal_length_mm"])
    camera.CreateHorizontalApertureAttr(cfg["camera"]["horizontal_aperture_mm"])
    camera.CreateVerticalApertureAttr(
        cfg["camera"]["horizontal_aperture_mm"] * height / width
    )
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, 30))
    camera_op = camera.AddTransformOp()
    product = rep.create.render_product(str(camera.GetPath()), (width, height))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([product])
    depth = rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
    depth.attach([product])
    focal_px = (
        cfg["camera"]["focal_length_mm"]
        / cfg["camera"]["horizontal_aperture_mm"]
        * width
    )
    observations = []

    def acquire(label, yaw_degrees, pitch_degrees):
        root = cpu(robot.data.root_state_w)[0]
        rotation = Rotation.from_quat(root[3:7]).as_matrix()
        eye = root[:3] + rotation @ np.array(cfg["camera"]["head_offset_m"])
        yaw, pitch = np.deg2rad([yaw_degrees, pitch_degrees])
        direction = rotation @ np.array(
            [np.cos(pitch) * np.cos(yaw), np.cos(pitch) * np.sin(yaw), np.sin(pitch)]
        )
        transform = (
            Gf.Matrix4d()
            .SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*(eye + direction)), Gf.Vec3d(0, 0, 1))
            .GetInverse()
        )
        camera_op.Set(transform)
        # Drain render latency after every changed view before reading RGB-D.
        for _ in range(12):
            sim.render()
        for _ in range(60):
            sim.render()
            pixels = np.asarray(rgb.get_data())[..., :3].copy()
            z = np.asarray(depth.get_data()).squeeze().copy()
            if pixels.shape == (height, width, 3) and z.shape == (height, width):
                break
        assert pixels.shape == (height, width, 3) and z.shape == (height, width), (
            pixels.shape,
            z.shape,
        )
        detection = detect_stop_candidate(pixels)
        cv2.imwrite(
            str(a.out / (label + ".png")), cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
        )
        np.savez_compressed(
            a.out / (label + "_rgbd.npz"),
            rgb=pixels,
            depth=z,
            world_from_camera=np.array(transform).T,
            focal_px=focal_px,
        )
        record = dict(
            label=label,
            camera_yaw_deg=yaw_degrees,
            camera_pitch_deg=pitch_degrees,
            detection=detection,
        )
        if detection["found"]:
            record["localization"] = localize_rgbd(
                detection, z, np.array(transform).T, focal_px
            )
        observations.append(record)
        write_json(a.out / "perception.json", observations)
        print(
            json.dumps(
                dict(
                    perception=label,
                    found=detection["found"],
                    candidates=len(detection["candidates"]),
                )
            ),
            flush=True,
        )
        return record

    def scan(label, views):
        for index, (yaw, pitch) in enumerate(views):
            record = acquire(f"{label}_{index}", yaw, pitch)
            if record["detection"]["found"]:
                return np.array(record["localization"]["surface_world_m"])
        raise RuntimeError("RGB target not uniquely recognized: " + label)

    stage.GetRootLayer().Save()
    write_json(
        a.out / "metadata.json",
        dict(
            config=cfg,
            config_sha256=digest(a.config),
            manifest_sha256=digest(manifest_path),
            policy_sha256=digest(policy_path),
            runtime_sha256=digest(MODULE_DIR / "panel_controls.py"),
            detector_sha256=digest(MODULE_DIR / "panel_perception.py"),
            runner_sha256=digest(__file__),
            planner_sha256=digest(MODULE_DIR / "arm_planner.py"),
            robot_body_paths=robot_paths,
            joint_names=names,
            body_names=bodies,
            target_contact_pair=[spec["cap_prim"], tool_body],
            contact_sensor_paths=list(environment_view.sensor_paths),
            environment_contact_actors=environment_actors,
            tool_offset=tip_offset.tolist(),
            press_tool=tool_info,
            root_writes_after_initialization=0,
            button_pose_writes=0,
            target_pose_used_for_control=False,
            odometry="PhysX torso state",
            navigation="known semantic map",
            self_collision_enabled=art_api.GetEnabledSelfCollisionsAttr().Get(),
            camera_mount="simulated body-mounted pan/tilt RGB-D",
            real_electrical_function_verified=False,
            natural_language_policy=False,
        ),
    )
    import shutil

    (a.out / "code_used").mkdir(exist_ok=True)
    for source in [
        Path(__file__),
        MODULE_DIR / "panel_perception.py",
        MODULE_DIR / "arm_planner.py",
        MODULE_DIR / "panel_controls.py",
        MODULE_DIR / "press_tool.py",
    ]:
        shutil.copy2(source, a.out / "code_used" / source.name)
    phase = "STAND"
    phase_start = 0.0
    transitions = []
    stable_since = None
    freeze_legs = None
    surface = None
    press_distance = 0.0
    event_time = None
    events = []
    failure = None
    arm_goal = cpu(target)[0, ai].copy()
    arm_home = arm_goal.copy()
    rows = []
    replays = []
    reach_waypoints = []
    reach_waypoint_index = 1
    retract_waypoints = []
    retract_waypoint_index = 1
    plans = []
    motion_checks = 0
    start = time.monotonic()
    max_force = 0.0
    max_depth = 0.0
    ground_positive = False
    max_unwanted = 0.0

    def transition(name, t):
        nonlocal phase, phase_start, stable_since
        transitions.append(dict(previous=phase, phase=name, t=t))
        phase = name
        phase_start = t
        stable_since = None
        print(json.dumps(transitions[-1]), flush=True)

    try:
        with (a.out / "sensors.jsonl").open("w") as log:
            for step in range(round(a.max_seconds / dt)):
                t = step * dt
                root = cpu(robot.data.root_state_w)[0]
                rpy = Rotation.from_quat(root[3:7]).as_euler("xyz")
                cmd = np.zeros(3, np.float32)
                if phase == "STAND" and t >= 2.0:
                    surface = scan(
                        "initial", [(-30, 18), (-40, 22), (-22, 18), (-50, 25)]
                    )
                    if a.scan_only:
                        transition("SCAN_COMPLETE", t)
                        break
                    transition("NAVIGATE", t)
                if phase == "NAVIGATE":
                    delta = np.array(cfg["navigation"]["map_dock_xy"]) - root[:2]
                    yaw = rpy[2]
                    c, s = np.cos(yaw), np.sin(yaw)
                    body_delta = np.array([[c, s], [-s, c]]) @ delta
                    cmd[:2] = np.clip(body_delta * 1.7, [-0.15, -0.12], [0.4, 0.12])
                    # This locomotion policy stands still for small commands.
                    # Continue a slow step until the measured dock tolerance,
                    # then issue zero velocity and wait for actual settling.
                    if abs(body_delta[0]) > 0.025 and abs(cmd[0]) < 0.22:
                        cmd[0] = np.sign(body_delta[0]) * 0.22
                    cmd[2] = np.clip(-yaw * 1.5, -0.3, 0.3)
                    if np.linalg.norm(delta) < 0.025 and abs(yaw) < 0.04:
                        transition("SETTLE", t)
                if phase == "SETTLE":
                    stable = (
                        np.linalg.norm(root[7:10]) < 0.03
                        and np.linalg.norm(root[10:13]) < 0.10
                    )
                    if stable:
                        stable_since = t if stable_since is None else stable_since
                    else:
                        stable_since = None
                    if stable_since is not None and t - stable_since > 0.6:
                        refined = scan(
                            "dock", [(-130, 32), (-115, 30), (-145, 30), (-100, 35)]
                        )
                        if np.linalg.norm(refined - surface) > 0.035:
                            raise RuntimeError(
                                "Initial and dock images disagree on target identity/position"
                            )
                        surface = refined
                        freeze_legs = cpu(robot.data.joint_pos)[0, li].copy()
                        current = cpu(robot.data.joint_pos)[0, ai]
                        goal, frames = solve_point(
                            surface
                            + np.array(
                                [
                                    0.0,
                                    tip_radius + cfg["contact"]["prepress_clearance_m"],
                                    0.0,
                                ]
                            ),
                            current,
                        )
                        plan = planner.plan(current, goal, frames["link00"], frames)
                        plan.update(phase="REACH", t=t)
                        plans.append(plan)
                        write_json(a.out / "motion_plans.json", plans)
                        reach_waypoints = [np.array(x) for x in plan["waypoints"]]
                        transition("REACH", t)
                if phase in ["REACH", "PRESS", "WITHDRAW"] and step % 10 == 0:
                    point = surface.copy()
                    inward = np.array(
                        [0.0, -1.0, 0.0]
                    )  # known cabinet plane orientation, not target position
                    if phase == "REACH":
                        point -= inward * (
                            tip_radius + cfg["contact"]["prepress_clearance_m"]
                        )
                    elif phase == "PRESS":
                        press_distance = min(
                            cfg["contact"]["prepress_clearance_m"]
                            + cfg["contact"].get("max_penetration_m", 0.0036),
                            (t - phase_start) * cfg["contact"]["approach_speed_m_s"],
                        )
                        point -= inward * (
                            tip_radius
                            + cfg["contact"]["prepress_clearance_m"]
                            - press_distance
                        )
                    else:
                        point -= inward * (tip_radius + 0.065)
                    if (
                        phase == "REACH"
                        and reach_waypoint_index < len(reach_waypoints) - 1
                    ):
                        arm_goal = reach_waypoints[reach_waypoint_index]
                        if (
                            np.max(abs(cpu(robot.data.joint_pos)[0, ai] - arm_goal))
                            < 0.03
                        ):
                            reach_waypoint_index += 1
                    else:
                        arm_goal, _ = solve_point(point, arm_goal)
                if phase == "RETRACT":
                    arm_goal = retract_waypoints[retract_waypoint_index]
                    if (
                        retract_waypoint_index < len(retract_waypoints) - 1
                        and np.max(abs(cpu(robot.data.joint_pos)[0, ai] - arm_goal))
                        < 0.015
                    ):
                        retract_waypoint_index += 1
                        arm_goal = retract_waypoints[retract_waypoint_index]
                if phase != "STAND" and freeze_legs is None and step % 6 == 0:
                    obs = np.concatenate(
                        [
                            cpu(robot.data.root_ang_vel_b)[0] * 0.25,
                            cpu(robot.data.projected_gravity_b)[0],
                            cmd,
                            cpu(robot.data.joint_pos)[0, li] - defaults,
                            cpu(robot.data.joint_vel)[0, li] * 0.05,
                            last_action,
                        ]
                    ).astype(np.float32)
                    last_action = policy.run(None, {"obs": obs[None, :]})[0][0]
                    target[:, li] = torch.as_tensor(
                        defaults + last_action * scales, device=robot.device
                    )
                if freeze_legs is not None:
                    target[:, li] = torch.as_tensor(freeze_legs, device=robot.device)
                qtarget = advance_joint_target(cpu(target)[0, ai], arm_goal, dt, 0.5)
                if freeze_legs is not None and step % 10 == 0:
                    frames = body_frames()
                    check = planner.check_segment(
                        cpu(robot.data.joint_pos)[0, ai],
                        qtarget,
                        frames["link00"],
                        frames,
                        allow_tip_contact=phase in ["PRESS", "WITHDRAW"],
                    )
                    motion_checks += 1
                    if not check["passed"]:
                        write_json(
                            a.out / "motion_check_failure.json",
                            dict(phase=phase, t=t, check=check),
                        )
                        raise RuntimeError(
                            "Collision check rejected the next arm command"
                        )
                target[:, ai] = torch.as_tensor(
                    qtarget, device=robot.device, dtype=target.dtype
                )
                kp = (
                    100.0
                    if phase == "STAND"
                    else (65.0 if freeze_legs is not None else 30.0)
                )
                kd = 3.0 if phase == "STAND" or freeze_legs is not None else 1.0
                effort = torch.zeros_like(target)
                effort[:, li] = (
                    kp * (target[:, li] - robot.data.joint_pos.torch[:, li])
                    - kd * robot.data.joint_vel.torch[:, li]
                )
                gravity = cpu(robot.root_physx_view.get_gravity_compensation_forces())[
                    0, -len(names) :
                ]
                # Apply only Z1 joint gravity torques; no root force/pose writes.
                effort[:, ai] = torch.as_tensor(gravity[ai], device=robot.device)
                robot.set_joint_position_target(target)
                robot.set_joint_effort_target(torch.clamp(effort, -limits, limits))
                robot.write_data_to_sim()
                sim.step(render=False)
                robot.update(dt)
                caps.update(dt)
                bases.update(dt)
                root = cpu(robot.data.root_state_w)[0]
                rpy = Rotation.from_quat(root[3:7]).as_euler("xyz")
                positions = cpu(robot.data.body_pos_w)[0]
                quats = cpu(robot.data.body_quat_w)[0]
                actual_tip = (
                    positions[tool_i].copy()
                    if tool_info
                    else positions[tool_i]
                    + Rotation.from_quat(quats[tool_i]).apply(tip_offset)
                )
                measured_depth = float(
                    np.dot(
                        cpu(caps.data.root_pos_w)[0] - cpu(bases.data.root_pos_w)[0],
                        spec["inward_axis_world"],
                    )
                    + spec["rest_offset_m"]
                )
                measured_force = float(
                    np.linalg.norm(cpu(contact_view.get_contact_force_matrix(dt)))
                )
                max_force = max(max_force, measured_force)
                max_depth = max(max_depth, measured_depth)
                feedback = adapter.observe(
                    ButtonSample(
                        step,
                        (step + 1) * dt,
                        spec["cap_prim"],
                        tool_body,
                        measured_depth,
                        measured_force,
                    )
                )
                if feedback["event"]:
                    assert phase == "PRESS", (
                        "Unexpected target actuation before authorized pressing phase"
                    )
                    assert load.GetAttribute("task:state").Get() == "RUNNING", (
                        "Repeated stop event"
                    )
                    load.GetAttribute("task:state").Set("STOPPED")
                    events.append(feedback["event"])
                    event_time = t
                # Explicit body/actor pairs prevent cancellation between contacts.
                env_matrix = cpu(environment_view.get_contact_force_matrix(dt)).reshape(
                    len(bodies), -1, 3
                )[sensor_order]
                magnitudes = np.linalg.norm(env_matrix, axis=2)
                foot_ids = [i for i, n in enumerate(bodies) if n.endswith("_FOOT")]
                ground_cols = [
                    i
                    for i, p in enumerate(environment_actors)
                    if "/Collision/Ground" in p
                ]
                ground_positive = ground_positive or bool(
                    magnitudes[np.ix_(foot_ids, ground_cols)].max() > 2.0
                )
                magnitudes[np.ix_(foot_ids, ground_cols)] = 0.0
                magnitudes[tool_i, environment_actors.index(spec["cap_prim"])] = 0.0
                unexpected = float(magnitudes.max())
                max_unwanted = max(max_unwanted, unexpected)
                if phase == "REACH":
                    prepress = surface + np.array(
                        [0.0, tip_radius + cfg["contact"]["prepress_clearance_m"], 0.0]
                    )
                    if (
                        np.linalg.norm(actual_tip - prepress) < 0.003
                        and np.linalg.norm(cpu(robot.data.joint_vel)[0, ai]) < 0.08
                    ):
                        stable_since = t if stable_since is None else stable_since
                        if t - stable_since > 0.25:
                            # Pre-check the full Cartesian approach/press line.
                            seed = cpu(robot.data.joint_pos)[0, ai]
                            checks = []
                            for distance in np.linspace(
                                0,
                                cfg["contact"]["prepress_clearance_m"]
                                + cfg["contact"].get("max_penetration_m", 0.0036),
                                40,
                            ):
                                point = surface + np.array(
                                    [
                                        0.0,
                                        tip_radius
                                        + cfg["contact"]["prepress_clearance_m"]
                                        - distance,
                                        0.0,
                                    ]
                                )
                                goal, frames = solve_point(point, seed)
                                checked = planner.check_segment(
                                    seed,
                                    goal,
                                    frames["link00"],
                                    frames,
                                    allow_tip_contact=True,
                                )
                                if not checked["passed"]:
                                    raise RuntimeError(
                                        "Cartesian press path blocked: " + str(checked)
                                    )
                                checks.append(checked)
                                seed = goal
                            plans.append(
                                dict(
                                    phase="PRESS",
                                    t=t,
                                    passed=True,
                                    method="40 RGB-D-target Cartesian samples + bounded IK",
                                    checks=checks,
                                )
                            )
                            write_json(a.out / "motion_plans.json", plans)
                            transition("PRESS", t)
                    else:
                        stable_since = None
                if (
                    phase == "PRESS"
                    and event_time is not None
                    and t - event_time > cfg["contact"]["hold_s"]
                ):
                    transition("WITHDRAW", t)
                if (
                    phase == "WITHDRAW"
                    and np.linalg.norm(
                        actual_tip
                        - (surface + np.array([0.0, tip_radius + 0.065, 0.0]))
                    )
                    < 0.004
                    and measured_force < 0.05
                ):
                    frames = body_frames()
                    plan = planner.plan(
                        cpu(robot.data.joint_pos)[0, ai],
                        arm_home,
                        frames["link00"],
                        frames,
                    )
                    plan.update(phase="RETRACT", t=t)
                    plans.append(plan)
                    write_json(a.out / "motion_plans.json", plans)
                    retract_waypoints = [np.array(x) for x in plan["waypoints"]]
                    transition("RETRACT", t)
                if (
                    phase == "RETRACT"
                    and np.max(abs(cpu(robot.data.joint_pos)[0, ai] - arm_home)) < 0.025
                    and measured_depth < spec["release_depth_m"]
                ):
                    transition("SUCCEEDED", t)
                row = dict(
                    step=step,
                    t=(step + 1) * dt,
                    phase=phase,
                    root=root.tolist(),
                    rpy=rpy.tolist(),
                    command=cmd.tolist(),
                    q=cpu(robot.data.joint_pos)[0].tolist(),
                    qd=cpu(robot.data.joint_vel)[0].tolist(),
                    tip_world=actual_tip.tolist(),
                    arm_gravity_compensation_Nm=gravity[ai].tolist(),
                    target_depth_m=measured_depth,
                    target_contact_N=measured_force,
                    unwanted_environment_contact_N=unexpected,
                    load_state=load.GetAttribute("task:state").Get(),
                    target_surface_from_rgbd=surface.tolist()
                    if surface is not None
                    else None,
                )
                if tool_info:
                    compression = -float(cpu(robot.data.joint_pos)[0, spring_i])
                    row["tool_compression_m"] = compression
                    row["tool_spring_force_N"] = cfg["tool"]["spring_N_m"] * compression
                log.write(json.dumps(row, separators=(",", ":")) + "\n")
                rows.append(row)
                if step % 20 == 0:
                    replays.append(
                        dict(
                            step=step,
                            body_poses=np.c_[positions, quats],
                            cap_pose=np.r_[
                                cpu(caps.data.root_pos_w)[0],
                                cpu(caps.data.root_quat_w)[0],
                            ],
                        )
                    )
                if step % 500 == 0:
                    log.flush()
                    print(
                        json.dumps(
                            dict(
                                t=t,
                                phase=phase,
                                xy=root[:2].tolist(),
                                depth=measured_depth,
                                force=measured_force,
                                tip=actual_tip.tolist(),
                                wall=time.monotonic() - start,
                            )
                        ),
                        flush=True,
                    )
                if (
                    not np.isfinite(root).all()
                    or root[2] < 0.19
                    or max(abs(rpy[:2])) > 0.35
                ):
                    raise RuntimeError("Base fall or excessive tilt")
                if measured_force > cfg["contact"]["max_force_N"]:
                    raise RuntimeError("Target force limit exceeded")
                if tool_info and compression > cfg["tool"]["travel_m"] * 0.98:
                    raise RuntimeError("Tool spring nearly bottomed out")
                if unexpected > 15.0:
                    raise RuntimeError("Unexpected nonfoot environment contact")
                if (a.out / "STOP_REQUESTED").exists():
                    raise RuntimeError("Run stopped by explicit request")
                if t - phase_start > 28.0:
                    raise RuntimeError("Phase timeout: " + phase)
                if phase == "SUCCEEDED":
                    break
    except Exception:
        failure = traceback.format_exc()
        print(failure, flush=True)
    if phase not in ["SUCCEEDED", "SCAN_COMPLETE"] and failure is None:
        failure = "Mission time limit"
    np.savez_compressed(
        a.out / "measured_replay.npz",
        steps=np.array([r["step"] for r in replays]),
        body_poses=np.array([r["body_poses"] for r in replays]),
        body_paths=np.array(robot_paths),
        cap_poses=np.array([r["cap_pose"] for r in replays]),
        cap_path=spec["cap_prim"],
        dt=dt,
    )
    result = dict(
        passed=phase == "SUCCEEDED" and ground_positive,
        phase=phase,
        failure=failure,
        transitions=transitions,
        press_events=events,
        ground_contact_positive_control=ground_positive,
        max_target_depth_m=max_depth,
        max_target_force_N=max_force,
        max_unwanted_environment_contact_N=max_unwanted,
        initial_xy=cfg["spawn"][:2],
        final_xy=root[:2].tolist(),
        root_writes_after_initialization=0,
        button_pose_writes=0,
        physical_z1_contact=bool(events),
        sampled_motion_plans=len(plans),
        online_collision_checks=motion_checks,
        load_state=load.GetAttribute("task:state").Get(),
        target_localization="image RGB-D",
        real_electrical_function_verified=False,
        full_scene_collision_certified=False,
        original_source_instance_identity_verified=False,
        config_sha256=digest(a.config),
        wall_s=time.monotonic() - start,
    )
    write_json(a.out / "result.json", result)
    sim.stop()
    return 0 if result["passed"] or a.scan_only and phase == "SCAN_COMPLETE" else 2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--scan-only", action="store_true")
    p.add_argument("--max-seconds", type=float, default=60.0)
    a = p.parse_args()
    a.config = a.config.resolve()
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=False)
    apps = []
    code = 2
    try:
        with simulation_lease():
            code = run(a, apps)
    except BaseException:
        traceback.print_exc()
        write_json(
            a.out / "failure.json", dict(passed=False, error=traceback.format_exc())
        )
    finally:
        if apps:
            apps[-1].close(wait_for_replicator=False, exit_code=code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
