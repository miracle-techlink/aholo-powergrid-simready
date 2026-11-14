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

from simready.assets.appearance.gaussian_asset import (
    decode_vertices,
    read_ply,
    write_appearance,
)
from simready.assets.compilation.collision_geometry import (
    fit_floor_plane as fit_floor_plane,
)
from simready.assets.compilation.collision_geometry import (
    voxel_proxies as voxel_proxies,
)
from simready.assets.compilation.spec_validation import (
    _integer,
    _keys,
    _number,
    _objects,
)
from simready.assets.compilation.usd_layers import (
    _stage,
    _write_collision,
    _write_objects,
)
from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.types import JsonObject, PathLike
from simready.geometry.transforms_validation import finite_array, validate_sim3


def prepare_scene(
    ply: PathLike,
    output: PathLike,
    spec: JsonObject | str | Path | None = None,
) -> JsonObject:
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
