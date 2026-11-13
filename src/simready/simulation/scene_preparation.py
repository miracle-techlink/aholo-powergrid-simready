"""Bounded CPU preparation of a reconstruction, not semantic reconstruction.

``source_to_world`` is a column-vector positive Sim3. World coordinates are Z-up;
without that declaration, automatic floor fitting is disabled. Scale provenance
is supplied by the caller and is never elevated to independent task validation.
Explicit objects use world coordinates and simulation presets unless evidenced.
"""

import argparse
import shutil
import tempfile
from pathlib import Path

import numpy as np

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.simulation.gaussian_asset import (
    decode_vertices,
    finite_array,
    read_ply,
    validate_sim3,
    write_appearance,
)
from simready.simulation.physical_authoring import (
    author_physics_metadata,
    author_rigid_body,
    bind_contact_material,
)
from simready.simulation.physical_spec import normalize_physics


def _number(value, name, lower, upper):
    if (
        type(value) not in (int, float)
        or not np.isfinite(value)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be within {lower}..{upper}")
    return float(value)


def _integer(value, name, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer within {lower}..{upper}")
    return value


def _keys(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"Unknown or malformed {name} fields")


def _bounds(value):
    _keys(value, {"min", "max"}, "bounds_world")
    low = finite_array(value.get("min"), "bounds min", (3,))
    high = finite_array(value.get("max"), "bounds max", (3,))
    if np.any(high <= low) or np.max(np.abs([low, high])) > 1e9:
        raise ValueError("Bounds must have positive extent and magnitude <= 1e9")
    return low, high


def _objects(spec, *, contact, declared_scale):
    from pxr import Sdf

    values = spec.get("objects", [])
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("objects must be a list with at most 256 entries")
    result, seen = [], set()
    common = {
        "id",
        "type",
        "category",
        "evidence",
        "color",
        "roughness",
        "physics",
        "visible",
    }
    for value in values:
        if not isinstance(value, dict) or value.get("type") not in {"box", "button"}:
            raise ValueError("Explicit object type must be box or button")
        kind = value["type"]
        allowed = (
            {"bounds_world"}
            if kind == "box"
            else {
                "position_world",
                "axis",
                "radius",
                "travel",
                "stiffness",
                "damping",
                "mass",
                "max_force",
                "cap_height",
            }
        )
        _keys(value, common | allowed, "object")
        name = value.get("id")
        if (
            not isinstance(name, str)
            or not Sdf.Path.IsValidIdentifier(name)
            or name in seen
        ):
            raise ValueError("Object id must be a unique USD identifier")
        seen.add(name)
        color = finite_array(
            value.get(
                "color", [0.55, 0.58, 0.62] if kind == "box" else [0.8, 0.1, 0.05]
            ),
            "color",
            (3,),
        )
        if np.any(color < 0) or np.any(color > 1):
            raise ValueError("color must be linear RGB within 0..1")
        evidence = value.get("evidence", "")
        category = value.get("category", kind)
        if (
            not isinstance(evidence, str)
            or not isinstance(category, str)
            or not category
        ):
            raise ValueError("Object evidence and category must be strings")
        if type(value.get("visible", True)) is not bool:
            raise ValueError("Object visible must be boolean")
        obj = dict(
            id=name,
            type=kind,
            category=category,
            visible=value.get("visible", True),
            evidence=evidence,
            color=color.tolist(),
            roughness=_number(value.get("roughness", 0.6), "roughness", 0, 1),
            source="explicit_scene_spec",
            estimate_status="user_declared_not_independently_verified",
            physical_parameters_measured=False,
            semantic_function_verified=False,
        )
        if kind == "box":
            low, high = _bounds(value.get("bounds_world"))
        else:
            position = finite_array(value.get("position_world"), "position_world", (3,))
            axis = value.get("axis", "Z")
            if axis not in {"X", "Y", "Z"} or np.max(np.abs(position)) > 1e9:
                raise ValueError("Button axis must be X/Y/Z and position bounded")
            obj.update(position_world=position.tolist(), axis=axis)
            for key, default, lower, upper in [
                ("radius", 0.02, 0.001, 2),
                ("travel", 0.004, 0.0001, 0.5),
                ("cap_height", 0.01, 0.001, 1),
                ("stiffness", 400.0, 0, 1e6),
                ("damping", 4.0, 0, 1e5),
                ("mass", 0.02, 0.0001, 100),
                ("max_force", 8.0, 0.001, 1e6),
            ]:
                obj[key] = _number(value.get(key, default), key, lower, upper)
            extent = np.full(3, obj["radius"] * 1.5)
            extent["XYZ".index(axis)] = obj["travel"] + obj["cap_height"] * 2
            low, high = position - extent, position + extent
            obj.update(
                signal_semantics="unbound physical spring; no automatic device-state transition",
                inward_axis_world=(-np.eye(3)["XYZ".index(axis)]).tolist(),
            )
        obj["bounds_world"] = {"min": low.tolist(), "max": high.tolist()}
        if "physics" in value and not isinstance(value["physics"], dict):
            raise ValueError("object.physics must be a mapping")
        obj["physics"] = normalize_physics(
            value.get("physics"),
            geometry_type=kind,
            fallback_contact=contact,
            metric_scale_declared=declared_scale,
            legacy_mass_kg=obj.get("mass"),
            legacy_mass_explicit="mass" in value,
            contact_explicit=set(spec.get("collision", {})),
        )
        if kind == "button":
            obj["mass"] = obj["physics"]["mass_kg"]
        result.append(obj)
    return result


def fit_floor_plane(points, threshold=0.05, iterations=128):
    """Deterministic near-Z RANSAC on low points; returns an estimate or None."""
    points = finite_array(points, "floor points")
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 20:
        return None
    rng = np.random.default_rng(20261113)
    sampled = points[rng.choice(len(points), min(len(points), 10_000), replace=False)]
    candidates = sampled[sampled[:, 2] <= np.percentile(sampled[:, 2], 40)]
    if len(candidates) < 20:
        return None
    best, best_count = None, 0
    for _ in range(iterations):
        triangle = candidates[rng.choice(len(candidates), 3, replace=False)]
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-10:
            continue
        normal /= norm
        if normal[2] < 0:
            normal = -normal
        if normal[2] < np.cos(np.deg2rad(20)):
            continue
        offset = -float(normal @ triangle[0])
        mask = np.abs(candidates @ normal + offset) <= threshold
        count = int(mask.sum())
        if count > best_count:
            best, best_count = mask, count
    if best is None or best_count < max(20, int(len(candidates) * 0.15)):
        return None
    inliers = candidates[best]
    center = inliers.mean(0)
    _, singular, vectors = np.linalg.svd(inliers - center, full_matrices=False)
    if singular[1] < 1e-8:
        return None
    normal = vectors[-1]
    if normal[2] < 0:
        normal = -normal
    if normal[2] < np.cos(np.deg2rad(20)):
        return None
    offset = -float(normal @ center)
    return {
        "normal": normal.tolist(),
        "offset": offset,
        "source": "low_point_ransac",
        "estimate_status": "estimated_not_reviewed",
        "sample_count": len(candidates),
        "inlier_count": best_count,
        "rmse": float(np.sqrt(np.mean((inliers @ normal + offset) ** 2))),
    }


def voxel_proxies(points, voxel_size=0.25, max_proxies=2000, min_points=2):
    """Occupied voxel boxes with a hard output cap; coarsening is reported."""
    if len(points) == 0:
        return [], voxel_size
    origin = points.min(0)
    size = voxel_size
    for _ in range(64):
        keys, counts = np.unique(
            np.floor((points - origin) / size).astype(np.int64),
            axis=0,
            return_counts=True,
        )
        keys = keys[counts >= min_points]
        if len(keys) <= max_proxies:
            return [
                {
                    "min": (origin + key * size).tolist(),
                    "max": (origin + (key + 1) * size).tolist(),
                }
                for key in keys
            ], size
        size *= 2
    raise ValueError("Cannot satisfy collision proxy resource cap")


def _stage(path, root_path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, root_path)
    stage.SetDefaultPrim(root.GetPrim())
    return stage


def _cube(stage, path, bounds, invisible=False):
    from pxr import Gf, UsdGeom, UsdPhysics

    low, high = _bounds(bounds)
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(*map(float, (low + high) / 2)))
    cube.AddScaleOp().Set(Gf.Vec3f(*map(float, high - low)))
    if invisible:
        cube.CreateVisibilityAttr("invisible")
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    return cube


def _material(stage, path, color, roughness):
    from pxr import Gf, Sdf, UsdShade

    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _bind_contact_material(stage, root, parameters):
    from pxr import Usd, UsdPhysics

    # Background geometry has one preset, scoped to its subtree. It must never
    # replace a semantic object's separately declared material.
    colliders = [
        str(prim.GetPath())
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root))
        if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    bind_contact_material(stage, root + "/ContactPreset", colliders, parameters)


def _write_objects(path, objects, contact):
    from pxr import Gf, UsdGeom, UsdPhysics, UsdShade

    stage = _stage(path, "/Objects")
    # Retain the legacy preset prim for schema-v1 readers. Bind only object-local
    # materials below, after shapes exist; a global pass would overwrite them.
    _bind_contact_material(stage, "/Objects", contact)
    for obj in objects:
        root = "/Objects/" + obj["id"]
        body = UsdGeom.Xform.Define(stage, root)
        body.GetPrim().SetCustomDataByKey("simready:semantic_category", obj["category"])
        body.GetPrim().SetCustomDataByKey("simready:geometry_type", obj["type"])
        body.GetPrim().SetCustomDataByKey("simready:object_id", obj["id"])
        if not obj["visible"]:
            body.CreateVisibilityAttr("invisible")
        author_physics_metadata(body.GetPrim(), obj["physics"])
        material = _material(
            stage, root + "/Looks/Surface", obj["color"], obj["roughness"]
        )
        if obj["type"] == "box":
            low, high = _bounds(obj["bounds_world"])
            center = (low + high) / 2
            body.AddTranslateOp().Set(Gf.Vec3d(*center.tolist()))
            shape = _cube(
                stage,
                root + "/Shape",
                {
                    "min": (low - center).tolist(),
                    "max": (high - center).tolist(),
                },
            )
            author_rigid_body(body.GetPrim(), obj["physics"])
            UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(material)
            obj["prim_path"] = "/World" + root + "/Shape"
            colliders = [root + "/Shape"]
            material_path = bind_contact_material(
                stage,
                root + "/Looks/PhysicsMaterial",
                colliders,
                obj["physics"]["contact"],
            )
            obj["physics_binding"] = {
                "material_prim": "/World" + material_path,
                "collision_prims": ["/World" + p for p in colliders],
                "rigid_body_prims": ["/World" + root]
                if obj["physics"]["motion_type"] != "static"
                else [],
            }
            continue
        position, axis = np.array(obj["position_world"]), obj["axis"]
        vector = np.eye(3)["XYZ".index(axis)]
        base_center = position - vector * (obj["travel"] + obj["cap_height"] * 1.5)
        base = UsdGeom.Xform.Define(stage, root + "/Base")
        base.AddTranslateOp().Set(Gf.Vec3d(*base_center.tolist()))
        UsdPhysics.RigidBodyAPI.Apply(base.GetPrim()).CreateKinematicEnabledAttr(True)
        cap = UsdGeom.Xform.Define(stage, root + "/Cap")
        cap.AddTranslateOp().Set(Gf.Vec3d(*position.tolist()))
        author_rigid_body(cap.GetPrim(), obj["physics"])
        for role, radius, height in [
            ("Base", obj["radius"] * 1.3, obj["cap_height"]),
            ("Cap", obj["radius"], obj["cap_height"]),
        ]:
            shape = UsdGeom.Cylinder.Define(stage, root + "/" + role + "/Shape")
            shape.CreateAxisAttr(axis)
            shape.CreateRadiusAttr(radius)
            shape.CreateHeightAttr(height)
            UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
            UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(material)
        joint = UsdPhysics.PrismaticJoint.Define(stage, root + "/Joint")
        joint.CreateBody0Rel().SetTargets([root + "/Base"])
        joint.CreateBody1Rel().SetTargets([root + "/Cap"])
        joint.CreateAxisAttr(axis)
        joint.CreateLocalPos0Attr(Gf.Vec3f(*(position - base_center).tolist()))
        joint.CreateLocalPos1Attr(Gf.Vec3f(0))
        joint.CreateLocalRot0Attr(Gf.Quatf(1))
        joint.CreateLocalRot1Attr(Gf.Quatf(1))
        joint.CreateLowerLimitAttr(-obj["travel"])
        joint.CreateUpperLimitAttr(0.0)
        joint.CreateCollisionEnabledAttr(False)
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
        drive.CreateTypeAttr("force")
        drive.CreateTargetPositionAttr(0.0)
        drive.CreateStiffnessAttr(obj["stiffness"])
        drive.CreateDampingAttr(obj["damping"])
        drive.CreateMaxForceAttr(obj["max_force"])
        obj.update(
            base_prim="/World" + root + "/Base",
            cap_prim="/World" + root + "/Cap",
            joint_prim="/World" + root + "/Joint",
        )
        colliders = [root + "/Base/Shape", root + "/Cap/Shape"]
        material_path = bind_contact_material(
            stage, root + "/Looks/PhysicsMaterial", colliders, obj["physics"]["contact"]
        )
        obj["physics_binding"] = {
            "material_prim": "/World" + material_path,
            "collision_prims": ["/World" + p for p in colliders],
            "rigid_body_prims": [obj["base_prim"], obj["cap_prim"]],
        }
    stage.GetRootLayer().Save()


def _write_collision(path, bounds, floor, proxies, contact):
    from pxr import UsdGeom, UsdPhysics

    stage = _stage(path, "/Collision")
    if floor:
        low, high = _bounds(bounds)
        normal, offset = np.array(floor["normal"]), floor["offset"]
        vertices = []
        for x, y in [
            (low[0], low[1]),
            (high[0], low[1]),
            (high[0], high[1]),
            (low[0], high[1]),
        ]:
            vertices.append(
                (
                    float(x),
                    float(y),
                    float(-(normal[0] * x + normal[1] * y + offset) / normal[2]),
                )
            )
        mesh = UsdGeom.Mesh.Define(stage, "/Collision/Floor")
        mesh.CreatePointsAttr(vertices)
        mesh.CreateFaceVertexCountsAttr([3, 3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    for index, box in enumerate(proxies):
        _cube(stage, f"/Collision/Voxel_{index:05d}", box, invisible=True)
    _bind_contact_material(stage, "/Collision", contact)
    stage.GetRootLayer().Save()


def prepare_scene(ply, output, spec=None):
    """Write portable layers and evidence; return the preparation.json contents.

    Supported spec keys: schema_version, source_to_world, scale_provenance,
    world_up_axis, floor_plane, collision, objects, camera and render. Textures,
    arbitrary object USD imports and automatic semantic inference are excluded.
    """
    from pxr import Usd, UsdGeom, UsdLux, UsdPhysics

    ply, output = Path(ply).resolve(), Path(output).resolve()
    supplied = (
        read_json(spec)
        if isinstance(spec, (str, Path))
        else (spec if spec is not None else {})
    )
    _keys(
        supplied,
        {
            "schema_version",
            "source_to_world",
            "scale_provenance",
            "world_up_axis",
            "floor_plane",
            "collision",
            "objects",
            "camera",
            "render",
        },
        "scene spec",
    )
    if supplied.get("schema_version", 1) != 1:
        raise ValueError("scene spec schema_version must be 1")
    if output == ply or output in ply.parents:
        raise ValueError("Output may not contain the input PLY")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("Preparation requires a fresh or empty output directory")
    transform = validate_sim3(supplied.get("source_to_world", np.eye(4)))
    provenance = supplied.get("scale_provenance", {})
    _keys(provenance, {"status", "method", "evidence"}, "scale_provenance")
    if any(not isinstance(value, str) for value in provenance.values()):
        raise ValueError("scale_provenance fields must be strings")
    if provenance.get("status", "unverified") not in {"verified", "unverified"}:
        raise ValueError("Scale status must be verified or unverified")
    declared_scale = bool(
        "source_to_world" in supplied
        and provenance.get("status") == "verified"
        and provenance.get("method", "").strip()
        and provenance.get("evidence", "").strip()
    )
    if provenance.get("status") == "verified" and not declared_scale:
        raise ValueError(
            "Verified scale declaration requires explicit Sim3, method and evidence"
        )
    if supplied.get("world_up_axis", "Z") != "Z":
        raise ValueError(
            "World coordinates must be Z-up; register other frames with source_to_world"
        )
    up_declared = "source_to_world" in supplied or supplied.get("world_up_axis") == "Z"
    collision = supplied.get("collision", {})
    _keys(
        collision,
        {
            "enabled",
            "voxel_size",
            "max_proxies",
            "max_points",
            "min_points_per_voxel",
            "floor_threshold",
            "static_friction",
            "dynamic_friction",
            "restitution",
        },
        "collision",
    )
    enabled = collision.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("collision.enabled must be boolean")
    voxel = _number(collision.get("voxel_size", 0.25), "voxel_size", 0.005, 1e6)
    max_proxies = _integer(collision.get("max_proxies", 2000), "max_proxies", 1, 5000)
    max_points = _integer(
        collision.get("max_points", 100_000), "max_points", 20, 200_000
    )
    min_points = _integer(
        collision.get("min_points_per_voxel", 2), "min_points_per_voxel", 1, 1000
    )
    threshold = _number(
        collision.get("floor_threshold", 0.05), "floor_threshold", 0.001, 100
    )
    contact = {
        key: _number(collision.get(key, default), key, 0, upper)
        for key, default, upper in [
            ("static_friction", 0.8, 10),
            ("dynamic_friction", 0.7, 10),
            ("restitution", 0.0, 1),
        ]
    }
    if contact["dynamic_friction"] > contact["static_friction"]:
        raise ValueError("dynamic_friction must not exceed static_friction")
    objects = _objects(supplied, contact=contact, declared_scale=declared_scale)
    source_sha256 = digest(ply)
    data = decode_vertices(read_ply(ply))
    world = data["points"].astype(np.float64) @ transform[:3, :3].T + transform[:3, 3]
    if not np.isfinite(world).all() or np.max(np.abs(world)) > 1e9:
        raise ValueError("Transformed points exceed finite coordinate bounds")
    low, high = world.min(0), world.max(0)
    # Degenerate point clouds retain finite nonzero bounds for a preview camera.
    high = np.maximum(high, low + 1e-4)
    for obj in objects:
        low = np.minimum(low, obj["bounds_world"]["min"])
        high = np.maximum(high, obj["bounds_world"]["max"])
    bounds = {"min": low.tolist(), "max": high.tolist()}
    usable = world if data["kind"] == "rgb_points" else world[data["opacities"] >= 0.1]
    sample = usable[
        np.random.default_rng(20261113).choice(
            len(usable), min(len(usable), max_points), replace=False
        )
    ]
    floor = None
    if "floor_plane" in supplied:
        definition = supplied["floor_plane"]
        _keys(definition, {"normal", "offset", "evidence"}, "floor_plane")
        normal = finite_array(definition.get("normal"), "floor normal", (3,))
        norm = float(np.linalg.norm(normal))
        offset = _number(definition.get("offset"), "floor offset", -1e9, 1e9)
        if norm < 1e-8 or normal[2] / norm < np.cos(np.deg2rad(20)):
            raise ValueError("floor_plane must face within 20 degrees of world +Z")
        if not isinstance(definition.get("evidence", ""), str):
            raise ValueError("Floor evidence must be a string")
        floor = dict(
            normal=(normal / norm).tolist(),
            offset=offset / norm,
            source="explicit_scene_spec",
            evidence=definition.get("evidence", ""),
            estimate_status="user_declared_not_independently_verified",
        )
    elif up_declared and enabled:
        floor = fit_floor_plane(sample, threshold)
    obstacles = sample
    if floor:
        obstacles = obstacles[
            obstacles @ np.array(floor["normal"]) + floor["offset"] > threshold * 2
        ]
    proxies, actual_voxel = (
        voxel_proxies(obstacles, voxel, max_proxies, min_points)
        if enabled
        else ([], voxel)
    )
    # Voxel boxes must not obstruct an explicitly modeled control's travel volume.
    exclusions = [obj["bounds_world"] for obj in objects]
    proxies = [
        box
        for box in proxies
        if not any(
            np.all(np.array(box["max"]) > area["min"])
            and np.all(np.array(box["min"]) < area["max"])
            for area in exclusions
        )
    ]
    if not enabled:
        floor = None
    limitations = [
        "Appearance retains the reconstruction; no watertight visible mesh or UV texture baking is performed.",
        "Voxel occupancy and a fitted floor are conservative geometric estimates, not semantic or contact-validated assets.",
        "Physical parameters are declared hypotheses with source metadata; no independent real-world validation or identification has run.",
        "Per-object contact materials are authored; pairwise effective friction still depends on the runtime material-combination rule.",
        "No task policy, contact event adapter, task audit or independent metric-scale validation has run.",
    ]
    if not declared_scale:
        limitations.append(
            "Metric scale is unverified; numeric dimensions are in declared world units only."
        )
    if not up_declared:
        limitations.append(
            "Source up direction is unverified; no automatic ground plane was generated."
        )
    if not floor:
        limitations.append(
            "No usable ground plane; a walkable support surface is not guaranteed."
        )
    if len(usable) > max_points:
        limitations.append(
            "Collision fitting uses a bounded deterministic sample and may omit thin geometry."
        )
    if exclusions:
        limitations.append(
            "Voxel proxies overlapping explicit object bounds are excluded; inspect the surrounding gaps."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".scene-preparation-", dir=output.parent))
    try:
        appearance = write_appearance(data, temporary / "appearance.usdc", transform)
        _write_collision(temporary / "collision.usda", bounds, floor, proxies, contact)
        _write_objects(temporary / "objects.usda", objects, contact)
        stage = _stage(temporary / "scene.usda", "/World")
        for layer, prim in [
            ("appearance.usdc", "Appearance"),
            ("collision.usda", "Collision"),
            ("objects.usda", "Objects"),
        ]:
            UsdGeom.Xform.Define(
                stage, "/World/" + prim
            ).GetPrim().GetReferences().AddReference("./" + layer)
        physics = UsdPhysics.Scene.Define(stage, "/World/Physics")
        physics.CreateGravityDirectionAttr((0, 0, -1))
        physics.CreateGravityMagnitudeAttr(9.81)
        light = UsdLux.DomeLight.Define(stage, "/World/Light")
        light.CreateIntensityAttr(800.0)
        stage.GetRootLayer().Save()
        loaded = Usd.Stage.Open(str(temporary / "scene.usda"))
        if not loaded or not loaded.GetPrimAtPath("/World/Appearance/Source"):
            raise RuntimeError("Composed appearance did not resolve")
        colliders = [p for p in loaded.Traverse() if p.HasAPI(UsdPhysics.CollisionAPI)]
        for obj in objects:
            if obj["type"] == "button":
                joint = UsdPhysics.PrismaticJoint(
                    loaded.GetPrimAtPath(obj["joint_prim"])
                )
                targets = list(joint.GetBody0Rel().GetTargets()) + list(
                    joint.GetBody1Rel().GetTargets()
                )
                if len(targets) != 2 or any(
                    not loaded.GetPrimAtPath(p).HasAPI(UsdPhysics.RigidBodyAPI)
                    for p in targets
                ):
                    raise RuntimeError("Button joint body targets did not compose")
        catalog = {
            "schema_version": 1,
            "appearance": appearance,
            "objects": objects,
            "automatic_semantic_detection": False,
            "floor": floor,
            "collision": {
                "proxy_count": len(proxies),
                "requested_voxel_size": voxel,
                "actual_voxel_size": actual_voxel,
                "source_point_count": len(usable),
                "sample_count": len(sample),
                "collider_count": len(colliders),
                "estimate_status": "unvalidated_static_occupancy",
                "max_proxies": max_proxies,
                "contact_material": contact,
            },
        }
        write_json(temporary / "asset_catalog.json", catalog)
        if digest(ply) != source_sha256:
            raise ValueError("Input PLY changed during preparation")
        report = {
            "schema_version": 1,
            "scene": "scene.usda",
            "bounds_world": bounds,
            "source_sha256": source_sha256,
            "spec_sha256": object_digest(supplied),
            "source_to_world": transform.tolist(),
            "scale_provenance": provenance,
            "metric_scale_verified": declared_scale,
            "declared_scale_verified": declared_scale,
            "provenance_is_user_supplied": True,
            "independent_scale_validation": False,
            "world_up_declared": up_declared,
            "physics_ready": bool(declared_scale and colliders and floor),
            "physics_configured": bool(colliders),
            "physics_ready_definition": "structural colliders plus support plane and user-declared metric scale; not dynamic/task validation",
            "task_validated": False,
            "preview_ready": True,
            "appearance": appearance,
            "floor": floor,
            "collision": catalog["collision"],
            "explicit_object_count": len(objects),
            "limitations": limitations,
            "files": [
                {"path": p.name, "sha256": digest(p), "bytes": p.stat().st_size}
                for p in sorted(temporary.iterdir())
            ],
        }
        write_json(temporary / "preparation.json", report)
        if output.exists():
            output.rmdir()
        temporary.rename(output)
        return report
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ply", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args(argv)
    prepare_scene(args.ply, args.output, args.spec)
    print(args.output / "preparation.json")


if __name__ == "__main__":
    main()
