"""Add source-backed surfaces, contact materials and obstacles to the real cafe.

Case-specific observed regions are explicit. Original USD layers remain intact.
This prepares assets; independent probe/render/task acceptance is still required.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt
from scipy.spatial.transform import Rotation

from simready.repair.structural_completion import (
    bake_observed_texture,
    obstacle_voxels,
    robust_plane,
    surface_grid,
    voxel_boundary,
)
from simready.simulation.physical_authoring import bind_contact_material
from simready.simulation.physical_spec import normalize_physics


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def cloud(stage):
    fields = [
        p for p in stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    ]
    if len(fields) != 1:
        raise ValueError("Expected exactly one original Gaussian field")
    p = fields[0]
    xyz = np.asarray(p.GetAttribute("positions").Get())
    t = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(p))
    return (
        xyz @ t[:3, :3] + t[3, :3],
        np.asarray(p.GetAttribute("opacities").Get()),
        str(p.GetPath()),
    )


def mesh(stage, path, vertices, faces, *, visible=True, collision=True):
    shape = UsdGeom.Mesh.Define(stage, path)
    shape.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(vertices, np.float32)))
    shape.CreateFaceVertexCountsAttr([len(f) for f in faces])
    shape.CreateFaceVertexIndicesAttr(np.asarray(faces, np.int32).ravel().tolist())
    shape.CreateSubdivisionSchemeAttr("none")
    shape.CreateDoubleSidedAttr(True)
    if collision:
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    if not visible:
        shape.MakeInvisible()
    return shape


def texture_material(stage, path, texture, *, matte_color=None):
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.85)
    tex = UsdShade.Shader.Define(stage, path + "/Texture")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture))
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    uv = UsdShade.Shader.Define(stage, path + "/UV")
    uv.CreateIdAttr("UsdPrimvarReader_float2")
    uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    uv.CreateOutput("result", Sdf.ValueTypeNames.Float2)
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
        uv.ConnectableAPI(), "result"
    )
    tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.ConnectableAPI(), "rgb"
    )
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    if matte_color is not None:
        diffuse = shader.GetInput("diffuseColor")
        diffuse.DisconnectSource()
        diffuse.Set(Gf.Vec3f(*map(float, matte_color)))
    return mat


def observed_floor_color(texture, mask):
    """Neutral source-derived backing; avoid seams in unsupported floor texture."""
    from PIL import Image

    rgb = np.asarray(Image.open(texture).convert("RGB"), dtype=float) / 255
    valid = np.asarray(Image.open(mask)) > 0
    saturation = (rgb.max(axis=2) - rgb.min(axis=2)) / np.maximum(
        rgb.max(axis=2), 0.001
    )
    chosen = (
        valid
        & (saturation < 0.35)
        & (rgb.mean(axis=2) > 0.25)
        & (rgb.mean(axis=2) < 0.8)
    )
    if chosen.sum() < 100:
        chosen = valid
    srgb = np.median(rgb[chosen], axis=0)
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    return linear.tolist()


def physics(category):
    friction = {
        "floor": (0.8, 0.6),
        "wall": (0.6, 0.45),
        "table": (0.65, 0.5),
        "unclassified_obstacle": (0.6, 0.45),
    }[category]
    return normalize_physics(
        {
            "motion_type": "static",
            "contact": {
                "static_friction": friction[0],
                "dynamic_friction": friction[1],
                "restitution": 0,
            },
            "provenance": {
                "contact." + key: {
                    "source_kind": "category_prior",
                    "source": f"{category} simulation prior; no measurement",
                    "unit": "1",
                    "interval": [0, 1.2 if key != "restitution" else 0.2],
                }
                for key in ("static_friction", "dynamic_friction", "restitution")
            },
        },
        geometry_type="box",
        fallback_contact={},
        metric_scale_declared=False,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=["grasp", "tour"], required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--sfm-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    a = np.asarray(
        json.loads((args.sfm_root / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    cameras = json.loads(
        (args.sfm_root / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    images = args.sfm_root / "full_sfm/images"
    inverse = np.linalg.inv(a)
    if args.case == "grasp":
        # The output remains a complete prepared manipulation bundle.
        shutil.copytree(args.input, args.out, dirs_exist_ok=True)
        scene_dir = args.out / "scene"
        contract = json.loads((scene_dir / "scene_contract.json").read_text())
        t = np.asarray(contract["old_scene_to_task"])
        original_stage = Usd.Stage.Open(str(scene_dir / "scene.usda"))
        world, opacity, _ = cloud(original_stage)
        ti = np.linalg.inv(t)
        old = world @ ti[:3, :3].T + ti[:3, 3]
        source_path = scene_dir / "original_video_splats.usdc"
        # Remove superseded old proxies from this new composition only.
        original_stage.OverridePrim("/Cafe/PhysicsProxies").SetActive(False)
        original_stage.GetRootLayer().Export(str(scene_dir / "source_scene.usda"))
        floor = np.asarray(contract["floor_plane_old"])
        table_records = contract["fitted_tables"]
    else:
        scene_dir = args.out / "scene"
        scene_dir.mkdir()
        bundle = json.loads((args.input / "bundle.json").read_text())
        s = Usd.Stage.Open(str(args.input / bundle["scene_usd"]))
        s.GetRootLayer().subLayerPaths.insert(
            0, str((args.input / bundle["color_layer"]).resolve())
        )
        # Flatten only the source's color correction; it remains the same captured field.
        s.Flatten().Export(str(scene_dir / "original_video_splats.usdc"))
        source_path = scene_dir / "original_video_splats.usdc"
        old, opacity, _ = cloud(s)
        t = np.eye(4)
        floor_region = (
            (opacity > 0.7)
            & (old[:, 2] > -0.8)
            & (old[:, 2] < -0.4)
            & (old[:, 0] > -2)
            & (old[:, 0] < 4.5)
            & (old[:, 1] > -2)
            & (old[:, 1] < 0)
        )
        floor, _ = robust_plane(old[floor_region])
        normal = np.r_[-floor[:2], 1.0]
        normal /= np.linalg.norm(normal)
        t[:3, :3] = Rotation.align_vectors([[0, 0, 1]], [normal])[0].as_matrix()
        t[2, 3] = -(t[:3, :3] @ [0, 0, floor[2]])[2]
        contract = {
            "schema_version": 1,
            "appearance": "original_video_reconstruction",
            "source_video": {
                "path": bundle["source_video"],
                "sha256": bundle["source_video_sha256"],
            },
            "source_usd_sha256": digest(source_path),
            "visual_gaussians_retained": len(old),
            "visual_gaussians_removed": 0,
            "render_table_proxies": False,
            "metric_scale_verified": False,
            "source_bundle_sha256": digest(args.input / "bundle.json"),
        }
        source = Usd.Stage.CreateNew(str(scene_dir / "source_scene.usda"))
        root = UsdGeom.Xform.Define(source, "/Cafe")
        source.SetDefaultPrim(root.GetPrim())
        visual = UsdGeom.Xform.Define(source, "/Cafe/OriginalVideo")
        visual.GetPrim().GetReferences().AddReference(
            "original_video_splats.usdc", "/World"
        )
        visual.AddTransformOp().Set(Gf.Matrix4d(t.T.tolist()))
        source.GetRootLayer().Save()
        table_records = []
    # Aholo opacity statistics differ materially from the local gsplat model.
    opacity_cutoff = 0.2 if args.case == "tour" else 0.7
    stable = (opacity > opacity_cutoff) & np.isfinite(old).all(axis=1)
    floor_z = np.c_[old[:, :2], np.ones(len(old))] @ floor
    height = old[:, 2] - floor_z
    surfaces = [
        dict(
            name="Floor",
            category="floor",
            axis=2,
            coefficients=floor,
            bounds=[[-4.8, 4.75], [-3.8, 2.95]],
            offset=-0.012,
        )
    ]
    for name, axis, region, bounds in [
        (
            "WallX",
            0,
            (old[:, 0] > 4.45)
            & (old[:, 0] < 4.82)
            & (old[:, 1] > -0.25)
            & (old[:, 1] < 2.9),
            [[-0.25, 2.9], [-0.65, 2.5]],
        ),
        (
            "WallY",
            1,
            (old[:, 1] > 2.65)
            & (old[:, 1] < 2.95)
            & (old[:, 0] > 2.3)
            & (old[:, 0] < 4.75),
            [[2.3, 4.75], [-0.65, 2.5]],
        ),
    ]:
        selected = stable & region & (old[:, 2] > 0.3) & (old[:, 2] < 2.3)
        coefficients, keep = robust_plane(old[selected], axis=axis)
        surfaces.append(
            dict(
                name=name,
                category="wall",
                axis=axis,
                coefficients=coefficients,
                bounds=bounds,
                offset=0.02,
                plane_inliers=int(keep.sum()),
            )
        )
    repair = Usd.Stage.CreateNew(str(scene_dir / "completion.usda"))
    UsdGeom.SetStageMetersPerUnit(repair, 1)
    UsdGeom.SetStageUpAxis(repair, "Z")
    root = UsdGeom.Xform.Define(repair, "/Completion")
    repair.SetDefaultPrim(root.GetPrim())
    root.AddTransformOp().Set(Gf.Matrix4d(t.T.tolist()))
    (scene_dir / "textures").mkdir(exist_ok=True)
    records = []
    task_support_overrides = []
    cloud_sfm = old[stable][::2] @ inverse[:3, :3].T + inverse[:3, 3]
    for spec in surfaces:
        name = spec["name"]
        grid, corners = surface_grid(
            spec["axis"], spec["coefficients"], spec["bounds"], width=1024, height=1024
        )
        sfm = grid @ inverse[:3, :3].T + inverse[:3, 3]
        tex = bake_observed_texture(
            sfm,
            cloud_sfm,
            cameras,
            images,
            width=1024,
            height=1024,
            output=scene_dir / "textures" / (name + ".png"),
        )
        # Offset backing slightly behind the original observed surface.
        visible_corners = corners.copy()
        visible_corners[:, spec["axis"]] += spec["offset"]
        shape = mesh(
            repair,
            "/Completion/Visual/" + name,
            visible_corners,
            [[0, 1, 2, 3]],
            collision=False,
        )
        # Captured appearance already includes room illumination. A backing
        # surface must not add a second wall shadow to the retained splats.
        shape.GetPrim().CreateAttribute(
            "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool
        ).Set(True)
        UsdGeom.PrimvarsAPI(shape).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
        ).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        floor_color = (
            observed_floor_color(
                scene_dir / "textures/Floor.png",
                scene_dir / "textures/Floor_observed.png",
            )
            if name == "Floor"
            else None
        )
        mat = texture_material(
            repair,
            "/Completion/Materials/" + name,
            "textures/" + name + ".png",
            matte_color=floor_color,
        )
        if floor_color is not None:
            tex["rendered_backing"] = (
                "plain source-derived matte material; captured Gaussian floor remains visible"
            )
            tex["linear_diffuse_color"] = floor_color
        UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(mat)
        # A finite-thickness slab is independently queryable and collidable.
        lower = corners.copy()
        lower[:, spec["axis"]] += -0.08 if spec["axis"] == 2 else 0.08
        path = "/Completion/Collision/" + name
        faces = [
            [0, 1, 2, 3],
            [4, 7, 6, 5],
            [0, 4, 5, 1],
            [1, 5, 6, 2],
            [2, 6, 7, 3],
            [3, 7, 4, 0],
        ]
        mesh(repair, path, np.vstack([corners, lower]), faces, visible=False)
        p = physics(spec["category"])
        bind_contact_material(
            repair, "/Completion/Contact/" + name, [path], p["contact"]
        )
        records.append(
            {
                "id": name,
                "category": spec["category"],
                "collision_prim": path,
                "physics": p,
                "fit": {
                    "axis": spec["axis"],
                    "coefficients": spec["coefficients"].tolist(),
                    "bounds": spec["bounds"],
                },
                "texture": tex,
            }
        )
        print("SURFACE", name, tex["observed_fraction"], flush=True)
    # Standalone scene supports; the manipulation worker disables duplicates
    # only where its exact task-owned support collider is present.
    for index, table in enumerate(table_records):
        center = np.asarray(table["center_old"]) @ t[:3, :3].T + t[:3, 3]
        radius = table["radius_old"] * np.linalg.norm(t[:3, 0])
        angle = np.linspace(0, 2 * np.pi, 64, endpoint=False)
        top = np.c_[
            center[0] + radius * np.cos(angle),
            center[1] + radius * np.sin(angle),
            np.full(64, center[2]),
        ]
        bottom = top - [0, 0, 0.06]
        ti = np.linalg.inv(t)
        vertices = (
            np.vstack([top, bottom, center, center - [0, 0, 0.06]]) @ ti[:3, :3].T
            + ti[:3, 3]
        )
        faces = []
        for k in range(64):
            j = (k + 1) % 64
            faces.extend(
                [
                    (128, k, j),
                    (129, 64 + j, 64 + k),
                    (k, 64 + k, j),
                    (j, 64 + k, 64 + j),
                ]
            )
        name = f"Table_{index}"
        path = "/Completion/Collision/" + name
        mesh(repair, path, vertices, faces, visible=False)
        p = physics("table")
        bind_contact_material(
            repair, "/Completion/Contact/" + name, [path], p["contact"]
        )
        records.append(
            {
                "id": name,
                "category": "table",
                "collision_prim": path,
                "physics": p,
                "source": "fitted Gaussian tabletop; flat task support convention",
                "world_top_center": center.tolist(),
                "world_radius_m": float(radius),
            }
        )
        task_support_overrides.append(
            {
                "background_path": "Completion/Collision/" + name,
                "task_path": "/World/" + ("Table" if index == 0 else "TableB"),
            }
        )
    # Sparse Gaussian fog is not a collider. Require dense connected occupied cells.
    obstacles = stable & (height > 0.12) & (height < 2.2)
    for table in table_records:
        xy = np.asarray(table["center_old"][:2])
        obstacles &= ~(
            (np.linalg.norm(old[:, :2] - xy, axis=1) < table["radius_old"] + 0.08)
            & (old[:, 2] < table["center_old"][2] + 0.12)
        )
    occupied, origin, resolution = obstacle_voxels(
        old[obstacles],
        [[-4.8, -3.8, -1], [4.8, 3, 2.5]],
        resolution=0.05 if args.case == "tour" else 0.10,
        minimum_points=1 if args.case == "tour" else 8,
    )
    vertices, faces = voxel_boundary(occupied, origin, resolution)
    path = "/Completion/Collision/ObservedObstacles"
    mesh(repair, path, vertices, faces, visible=False)
    p = physics("unclassified_obstacle")
    bind_contact_material(repair, "/Completion/Contact/Obstacles", [path], p["contact"])
    records.append(
        {
            "id": "ObservedObstacles",
            "category": "unclassified_obstacle",
            "collision_prim": path,
            "physics": p,
            "geometry": "density-filtered occupied cells; conservative, not semantic segmentation",
            "voxel_m_in_source_coordinates": resolution,
            "occupied_cells": int(occupied.sum()),
            "triangles": len(faces),
        }
    )
    np.savez_compressed(
        scene_dir / "obstacles.npz",
        occupied=occupied,
        origin=origin,
        resolution=resolution,
        old_to_world=t,
        floor=floor,
    )
    repair.GetRootLayer().Save()
    scene = (
        Usd.Stage.CreateNew(str(scene_dir / "scene.usda"))
        if args.case == "tour"
        else Usd.Stage.Open(str(scene_dir / "scene.usda"))
    )
    scene.GetRootLayer().Clear()
    UsdGeom.SetStageMetersPerUnit(scene, 1)
    UsdGeom.SetStageUpAxis(scene, "Z")
    root = UsdGeom.Xform.Define(scene, "/Cafe")
    scene.SetDefaultPrim(root.GetPrim())
    scene.GetRootLayer().subLayerPaths = ["source_scene.usda"]
    scene.DefinePrim("/Cafe/Completion", "Xform").GetReferences().AddReference(
        "completion.usda"
    )
    scene.GetRootLayer().Save()
    contract.update(
        completion_layer="completion.usda",
        completion_sha256=digest(scene_dir / "completion.usda"),
        original_visual_preserved=True,
        scope="source-preserving structural completion with static contact proxies; physical and visual gates required",
        repair_validation="PENDING",
        calibrated_materials=False,
        task_support_overrides=task_support_overrides,
        old_scene_to_world=t.tolist(),
        floor_plane_old=floor.tolist(),
    )
    json_write(scene_dir / "scene_contract.json", contract)
    json_write(
        scene_dir / "physical_assets.json",
        {
            "schema_version": 1,
            "metric_scale_verified": False,
            "assets": records,
            "dynamic_task_assets": "separate task bundle"
            if args.case == "grasp"
            else "none",
            "table_supports": table_records,
            "limitations": [
                "Original Gaussian blur and dynamic-person ghosts are retained",
                "Unknown furniture uses coarse conservative collision cells",
                "Texture coverage is a projection proxy, not independent fidelity validation",
                "Mass is not assigned to static colliders; friction remains an unmeasured prior",
            ],
        },
    )
    json_write(
        args.out / "build.json",
        {
            "status": "BUILT_NOT_VALIDATED",
            "case": args.case,
            "source_usd_sha256": digest(source_path),
            "scene": "scene/scene.usda",
            "source_visual_points": len(old),
            "surface_count": len(surfaces),
            "obstacle_cells": int(occupied.sum()),
        },
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "surfaces": len(surfaces),
                "obstacle_cells": int(occupied.sum()),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
