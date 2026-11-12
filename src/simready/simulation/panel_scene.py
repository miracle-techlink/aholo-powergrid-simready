"""CPU-only scene contract and isolated USD composition for panel demonstrations.

Importing this module never imports Kit, torch, CUDA, or pxr. USD preparation
must run in a separate system-Python process before the Isaac process starts.
"""

import hashlib
import json
import math
from pathlib import Path

ENV_ROOT = "/World/PanelEnvironment"


def motion_schedule(walk_duration_s):
    if (
        isinstance(walk_duration_s, bool)
        or not isinstance(walk_duration_s, (int, float))
        or not math.isfinite(walk_duration_s)
        or walk_duration_s <= 0
    ):
        raise ValueError("walk_duration_s must be positive and finite")
    phases = []
    start = 0.0
    for name, duration in [
        ("stand", 2.0),
        ("policy_settle", 2.0),
        ("walk", float(walk_duration_s)),
        ("stop", 4.0),
        ("arm_extend", 4.0),
        ("arm_retract", 4.0),
        ("arm_hold", 1.0),
    ]:
        phases.append(
            dict(name=name, start_s=start, end_s=start + duration, duration_s=duration)
        )
        start += duration
    return dict(
        protocol="short_corridor_integration_demo",
        phases=phases,
        total_duration_s=start,
        frozen_flat_10s_acceptance_applies=False,
    )


def phase_at(schedule, t):
    for phase in schedule["phases"]:
        if t < phase["end_s"]:
            return phase["name"]
    return "arm_hold"


def arm_fraction_at(schedule, t):
    phases = {p["name"]: p for p in schedule["phases"]}
    extend = phases["arm_extend"]
    retract = phases["arm_retract"]
    if t < extend["start_s"] or t >= retract["end_s"]:
        return 0.0
    u = (
        (t - extend["start_s"]) / extend["duration_s"]
        if t < extend["end_s"]
        else (retract["end_s"] - t) / retract["duration_s"]
    )
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def render_settings(camera):
    settings = {
        "/isaaclab/video/enabled": True,
        "/rtx/rendermode": camera["renderer"],
        "/rtx/post/aa/op": 3 if camera["renderer"] == "RealTimePathTracing" else 0,
        "/rtx/dlss/frameGeneration": False,
        "/rtx/post/colorcorr/enabled": False,
        "/rtx/post/colorgrad/enabled": False,
    }
    if camera["renderer"] == "PathTracing":
        pt = camera["pathtracing"]
        # Replicator's own AA-Off implementation disables this restriction first.
        # Otherwise RTX coerces unsupported AA modes back to DLSS (3) on render.
        settings["/rtx-transient/post/aa/limitedOps"] = False
        settings.update(
            {
                "/rtx/pathtracing/spp": pt["spp"],
                "/rtx/pathtracing/totalSpp": pt["total_spp"],
                "/rtx/pathtracing/clampSpp": pt["clamp_spp"],
                "/rtx/pathtracing/optixDenoiser/enabled": pt["optix_denoiser"],
            }
        )
    return settings


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def vector(value, n, name):
    if not isinstance(value, list) or len(value) != n:
        raise ValueError(f"{name} must contain {n} numbers")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in value
    ):
        raise ValueError(f"{name} must contain finite numbers")
    return [float(x) for x in value]


def load_config(path, check_files=True):
    path = Path(path).resolve()
    c = json.loads(path.read_text())
    if c.get("schema_version") != 1:
        raise ValueError("Expected panel config schema_version=1")
    s = c["scene"]
    spawn = c["spawn"]
    cam = c["camera"]
    if s.get("scale_status") != "estimated" or s.get("collision_kind") != "proxy":
        raise ValueError(
            "This entry point only permits estimated-scale, proxy-collision demonstrations"
        )
    s.setdefault("visual_reference_path", s["visual_source_path"])
    for key in [
        "usd_path",
        "visual_source_path",
        "visual_reference_path",
        "collision_derived_from_path",
    ]:
        p = Path(s[key])
        s[key] = str(
            (path.parent / p).resolve() if not p.is_absolute() else p.resolve()
        )
        if check_files and not Path(s[key]).is_file():
            raise ValueError(f"Missing {key}: {s[key]}")
    if s["visual_source_path"] != s["collision_derived_from_path"]:
        raise ValueError(
            "Collision provenance must identify the same visual source, not a warehouse registry"
        )
    prim = s.get("prim_path")
    if prim is not None and (
        not isinstance(prim, str) or not prim.startswith("/") or prim == "/"
    ):
        raise ValueError(
            "scene.prim_path must be a specific absolute prim path or null for defaultPrim"
        )
    if not s.get("ground_paths"):
        raise ValueError(
            "At least one environment-relative ground collision path is required"
        )
    s.setdefault("allowed_rigid_body_paths", [])
    s.setdefault("allowed_world_fixed_joint_paths", [])
    for p in (
        s["ground_paths"]
        + s["allowed_rigid_body_paths"]
        + s["allowed_world_fixed_joint_paths"]
    ):
        if (
            not isinstance(p, str)
            or p.startswith("/")
            or any(x in ["", "..", "."] for x in p.split("/"))
        ):
            raise ValueError(
                "ground_paths must be paths relative to the selected environment prim"
            )
    z = s.get("ground_z_m")
    if isinstance(z, bool) or not isinstance(z, (float, int)) or not math.isfinite(z):
        raise ValueError("Invalid ground_z_m")
    spawn["position_m"] = vector(spawn["position_m"], 3, "spawn.position_m")
    if ("yaw_deg" in spawn) == ("quaternion_xyzw" in spawn):
        raise ValueError("Specify exactly one spawn yaw_deg or quaternion_xyzw")
    if "yaw_deg" in spawn:
        yaw = spawn["yaw_deg"]
        if (
            isinstance(yaw, bool)
            or not isinstance(yaw, (int, float))
            or not math.isfinite(yaw)
        ):
            raise ValueError("Invalid yaw_deg")
        angle = math.radians(yaw)
        spawn["quaternion_xyzw"] = [0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2)]
    else:
        q = vector(spawn["quaternion_xyzw"], 4, "spawn.quaternion_xyzw")
        if abs(sum(x * x for x in q) - 1) > 1e-5:
            raise ValueError("Spawn quaternion must be normalized")
        # Flat support adapter: allow arbitrary yaw, reject pitched/rolled spawns.
        if abs(q[0]) + abs(q[1]) > 1e-6:
            raise ValueError("Panel adapter currently requires a level spawn")
        spawn["quaternion_xyzw"] = q
    q = spawn["quaternion_xyzw"]
    spawn["forward_world"] = [1 - 2 * q[2] * q[2], 2 * q[2] * q[3], 0.0]
    spawn["left_world"] = [-spawn["forward_world"][1], spawn["forward_world"][0], 0.0]
    if not 0.30 <= spawn["position_m"][2] - z <= 0.50:
        raise ValueError(
            "Spawn torso must start 0.30–0.50m above configured support plane"
        )
    for key in ["eye_m", "target_m", "up"]:
        cam[key] = vector(cam[key], 3, "camera." + key)
    direction = [b - a for a, b in zip(cam["eye_m"], cam["target_m"], strict=False)]
    cross = [
        direction[1] * cam["up"][2] - direction[2] * cam["up"][1],
        direction[2] * cam["up"][0] - direction[0] * cam["up"][2],
        direction[0] * cam["up"][1] - direction[1] * cam["up"][0],
    ]
    if sum(x * x for x in cross) < 1e-8:
        raise ValueError("Camera look direction and up must be nonzero and nonparallel")
    focal = cam.get("focal_length_mm", 32.0)
    if (
        not isinstance(focal, (float, int))
        or not math.isfinite(focal)
        or not 5 <= focal <= 150
    ):
        raise ValueError("Invalid focal length")
    cam["focal_length_mm"] = focal
    cam.setdefault("supplemental_lighting", False)
    if not isinstance(cam["supplemental_lighting"], bool):
        raise ValueError("supplemental_lighting must be boolean")
    cam.setdefault("renderer", "RealTimePathTracing")
    if cam["renderer"] not in ["RealTimePathTracing", "PathTracing"]:
        raise ValueError(
            "Gaussian video renderer must be RealTimePathTracing or PathTracing"
        )
    if cam["renderer"] == "PathTracing":
        pt = cam.setdefault(
            "pathtracing",
            dict(spp=32, total_spp=128, clamp_spp=128, optix_denoiser=True),
        )
        for key in ["spp", "total_spp", "clamp_spp"]:
            if (
                isinstance(pt[key], bool)
                or not isinstance(pt[key], int)
                or pt[key] <= 0
            ):
                raise ValueError("Invalid pathtracing." + key)
        if min(pt["total_spp"], pt["clamp_spp"]) < pt["spp"]:
            raise ValueError("Path tracing sample limits cannot be lower than spp")
        if not isinstance(pt["optix_denoiser"], bool):
            raise ValueError("optix_denoiser must be boolean")
    # Native 16:9 camera gate; no image resizing or color enhancement is used.
    cam["horizontal_aperture_mm"] = 36.0
    cam["vertical_aperture_mm"] = 20.25
    motion = c.setdefault("motion", {})
    motion.setdefault("walk_duration_s", 5.0)
    c["schedule"] = motion_schedule(motion["walk_duration_s"])
    motion.setdefault("speed_m_s", 0.45)
    if (
        isinstance(motion["speed_m_s"], bool)
        or not isinstance(motion["speed_m_s"], (int, float))
        or not 0 < motion["speed_m_s"] <= 0.8
    ):
        raise ValueError("Forward speed must be within (0,.8]m/s")
    motion.setdefault("nonfoot_contact_abort_N", 15.0)
    if (
        isinstance(motion["nonfoot_contact_abort_N"], bool)
        or not isinstance(motion["nonfoot_contact_abort_N"], (int, float))
        or not 0 < motion["nonfoot_contact_abort_N"] <= 100
    ):
        raise ValueError("Invalid nonfoot contact threshold")
    c["config_sha256"] = sha256(path)
    c["config_path"] = str(path)
    if check_files:
        for pathkey, hashkey in [
            ("usd_path", "usd_sha256"),
            ("visual_source_path", "visual_source_sha256"),
            ("visual_reference_path", "visual_reference_sha256"),
        ]:
            actual = sha256(s[pathkey])
            expected = s.get(hashkey)
            if expected and actual != expected:
                raise ValueError(f"Changed {pathkey}")
            s[hashkey] = actual
    return c


def prepare_scene(config_path, out, rig_report):
    """Offline USD preparation. No simulation is performed or claimed."""
    import numpy as np
    from pxr import Usd, UsdGeom, UsdPhysics

    c = load_config(config_path)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rig = json.loads(Path(rig_report).read_text())
    rig["rig_path"] = str(
        (Path(rig_report).resolve().parent / rig["rig_path"]).resolve()
    )
    source = Usd.Stage.Open(c["scene"]["usd_path"])
    root = (
        source.GetPrimAtPath(c["scene"]["prim_path"])
        if c["scene"].get("prim_path")
        else source.GetDefaultPrim()
    )
    if not root:
        raise ValueError("Selected source environment prim does not exist")
    if (
        UsdGeom.GetStageMetersPerUnit(source) != 1
        or UsdGeom.GetStageUpAxis(source) != "Z"
    ):
        raise ValueError(
            "Environment stage must explicitly use meters and Z-up; estimated visual scaling belongs in its transforms"
        )
    source_rigid = []
    unexpected = []
    allowed = {
        str(root.GetPath()) + "/" + p for p in c["scene"]["allowed_rigid_body_paths"]
    }
    for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if p.HasAPI(UsdPhysics.ArticulationRootAPI):
            raise ValueError(
                "Extra articulation in environment is not supported: "
                + str(p.GetPath())
            )
        if p.HasAPI(UsdPhysics.RigidBodyAPI):
            source_rigid.append(str(p.GetPath()))
            if str(p.GetPath()) not in allowed:
                unexpected.append(str(p.GetPath()))
    if unexpected:
        raise ValueError(
            "Unexpected environment rigid bodies; explicitly list reusable cabinet/button bodies: "
            + str(unexpected)
        )
    visual = Path(c["scene"]["visual_reference_path"]).resolve()
    used = {Path(x.realPath).resolve() for x in source.GetUsedLayers() if x.realPath}
    if visual not in used:
        raise ValueError(
            "Declared visual source is not present in the source scene layer dependencies"
        )
    # Preserve all canonical robot references and topology; never edit the source.
    stage = Usd.Stage.Open(rig["rig_path"])
    from simready.simulation.asset_bundle import copy_layer_with_resolved_paths

    copy_layer_with_resolved_paths(rig["rig_path"], out / "scene.usda")
    stage = Usd.Stage.Open(str(out / "scene.usda"))
    for p in ["/World/Ground", "/Render", "/OmniverseKit_Persp"]:
        if stage.GetPrimAtPath(p):
            stage.RemovePrim(p)
    env = UsdGeom.Xform.Define(stage, ENV_ROOT).GetPrim()
    env.GetReferences().AddReference(c["scene"]["usd_path"], root.GetPath())
    # Referenced physics scenes cannot be deleted by editing the run layer.
    # Explicitly deactivate them; the canonical root physics scene is replaced by Lab.
    for p in list(stage.Traverse()):
        if str(p.GetPath()).startswith(ENV_ROOT + "/") and p.IsA(UsdPhysics.Scene):
            p.SetActive(False)
    bbox = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), ["default", "render", "proxy", "guide"]
    )
    colliders = []
    ground_colliders = []
    grounds = [ENV_ROOT + "/" + p for p in c["scene"]["ground_paths"]]
    for p in Usd.PrimRange(
        stage.GetPrimAtPath(ENV_ROOT), Usd.TraverseInstanceProxies()
    ):
        if (
            p.HasAPI(UsdPhysics.CollisionAPI)
            and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get()
        ):
            b = bbox.ComputeWorldBound(p).ComputeAlignedRange()
            lo = np.array(b.GetMin())
            hi = np.array(b.GetMax())
            if (
                not np.isfinite(lo).all()
                or not np.isfinite(hi).all()
                or np.any(hi < lo)
            ):
                raise ValueError("Invalid collision bounds: " + str(p.GetPath()))
            name = str(p.GetPath())
            record = dict(
                path=name,
                type=p.GetTypeName(),
                world_min_m=lo.tolist(),
                world_max_m=hi.tolist(),
            )
            colliders.append(record)
            if any(name == g or name.startswith(g + "/") for g in grounds):
                ground_colliders.append(record)
    for g in grounds:
        if not any(
            x["path"] == g or x["path"].startswith(g + "/") for x in ground_colliders
        ):
            raise ValueError("Missing enabled ground collision beneath " + g)
    if not ground_colliders:
        raise ValueError(
            "No environment ground colliders; infinite flat fallback is forbidden"
        )
    point = c["spawn"]["position_m"]
    z = c["scene"]["ground_z_m"]
    if not any(
        x["world_min_m"][0] <= point[0] <= x["world_max_m"][0]
        and x["world_min_m"][1] <= point[1] <= x["world_max_m"][1]
        and abs(x["world_max_m"][2] - z) < 0.01
        for x in ground_colliders
    ):
        raise ValueError(
            "Spawn is not above the declared proxy support surface within 1cm"
        )
    c["scene"]["prim_path"] = str(root.GetPath())
    c["scene"]["composed_root"] = ENV_ROOT
    c["scene"]["composed_ground_paths"] = grounds
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(3000)
    stage.GetRootLayer().Save()
    deps = [
        dict(path=str(Path(x.realPath).resolve()), sha256=sha256(x.realPath))
        for x in stage.GetUsedLayers()
        if x.realPath and Path(x.realPath).resolve() != (out / "scene.usda").resolve()
    ]
    report = dict(
        status="cpu_prepared",
        physics_stepped=False,
        gpu_started=False,
        scale_status="estimated",
        collision_kind="proxy",
        schedule=c["schedule"],
        requested_render_settings=render_settings(c["camera"]),
        renderer_readback=None,
        calibrated_substation_pass=False,
        canonical_rig_sha256=sha256(rig["rig_path"]),
        scene_path=str((out / "scene.usda").resolve()),
        scene_sha256=sha256(out / "scene.usda"),
        canonical_flat_ground_removed=not bool(stage.GetPrimAtPath("/World/Ground")),
        collider_count=len(colliders),
        colliders=colliders,
        ground_colliders=ground_colliders,
        dependencies=deps,
        allowed_environment_rigid_bodies=source_rigid,
        same_source_provenance="declared visual and proxy source identity matches; proxy alignment is not metrically certified",
    )
    (out / "resolved_config.json").write_text(json.dumps(c, indent=2) + "\n")
    (out / "scene_preflight.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
