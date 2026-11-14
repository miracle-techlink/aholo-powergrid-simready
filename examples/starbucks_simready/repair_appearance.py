"""Source-conditioned visible cafe assets using the substation repair method.

The original field is retained. Only declared replaced surfaces/instances are
suppressed. Cafe layout is case-specific; no arbitrary-video automation claim.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

from simready.core.artifacts import digest, write_json
from simready.repair.source_texture import SourceTextureBaker
from simready.repair.structural_completion import surface_grid
from simready.repair.visible_geometry import VisibleGeometry
from simready.simulation.physical_authoring import bind_contact_material


def transform(points, matrix):
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def chair(g, name, xy, floor, facing, materials, stool=False):
    wood, leather, metal = materials
    yaw = facing
    rot = np.array(
        [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
    )
    origin = np.r_[xy, floor]

    def point(q):
        return np.asarray(q) @ rot.T + origin

    seat = 0.70 if stool else 0.47
    radius = 0.19 if stool else 0.235
    if stool:
        g.cylinder(name + "/Seat", point([0, 0, seat]), radius, 0.055, leather)
    else:
        m = np.eye(4)
        m[:3, :3] = rot
        m[:3, 3] = origin
        g.box(name + "/Seat", [0, 0, seat], [0.46, 0.45, 0.085], leather, 0.025, m)
    for i, (x, y) in enumerate([(-1, -1), (-1, 1), (1, -1), (1, 1)]):
        g.rod(
            name + f"/Leg{i}",
            point([x * radius * 0.78, y * radius * 0.78, 0.035]),
            point([x * radius * 0.62, y * radius * 0.62, seat - 0.03]),
            0.021,
            wood,
        )
        if stool:
            j = point([x * radius * 0.72, y * radius * 0.72, 0.26])
            k = point([-x * radius * 0.72, y * radius * 0.72, 0.26])
            if x < 0:
                g.rod(name + f"/Rung{i}", j, k, 0.012, wood)
    if not stool:
        # Curved timber back matching the photographed chair family.
        angle = np.linspace(np.pi * 0.12, np.pi * 0.88, 33)
        points = []
        for z, rad in [
            (seat + 0.25, radius * 1.03),
            (seat + 0.37, radius * 1.03),
            (seat + 0.25, radius * 1.13),
            (seat + 0.37, radius * 1.13),
        ]:
            points.extend([point([rad * np.cos(t), rad * np.sin(t), z]) for t in angle])
        n = len(angle)
        faces = []
        for i in range(n - 1):
            faces += [
                [i, i + 1, n + i + 1, n + i],
                [2 * n + i, 3 * n + i, 3 * n + i + 1, 2 * n + i + 1],
                [n + i, n + i + 1, 3 * n + i + 1, 3 * n + i],
                [i, 2 * n + i, 2 * n + i + 1, i + 1],
            ]
        faces += [[0, n, 3 * n, 2 * n], [n - 1, 3 * n - 1, 4 * n - 1, 2 * n - 1]]
        g.mesh(name + "/CurvedBack", points, faces, wood)
        for i, x in enumerate([-0.19, 0.19]):
            g.rod(
                name + f"/BackSupport{i}",
                point([x, 0.15, seat - 0.04]),
                point([x, 0.17, seat + 0.29]),
                0.017,
                wood,
            )


def build_reference(args, destination):
    sfm = args.sfm_root
    cameras = json.loads(
        (sfm / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    alignment = np.asarray(
        json.loads((sfm / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    ai = np.linalg.inv(alignment)
    source = Usd.Stage.Open(
        str(args.reference_assets / "scene/original_video_splats.usdc")
    )
    field = [
        p
        for p in source.Traverse()
        if p.GetTypeName() == "ParticleField3DGaussianSplat"
    ][0]
    raw = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    contract = json.loads(
        (args.reference_assets / "scene/scene_contract.json").read_text()
    )
    floor = np.asarray(contract["floor_plane_old"])

    def floor_at(x, y):
        return float(np.r_[x, y, 1] @ floor)

    baker = SourceTextureBaker(cameras, sfm / "full_sfm/images", raw[alpha > 0.45][::2])
    stage = Usd.Stage.CreateNew(str(destination / "appearance_reference.usda"))
    root = UsdGeom.Xform.Define(stage, "/Appearance")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    g = VisibleGeometry(stage, "/Appearance")
    wood = g.material("SourceWarmWood", [0.62, 0.38, 0.18], 0.43)
    leather = g.material("SourceLeather", [0.43, 0.235, 0.125], 0.42)
    dark = g.material("DarkJoinery", [0.12, 0.09, 0.065], 0.52)
    metal = g.material("PedestalMetal", [0.10, 0.09, 0.08], 0.34, 0.45)
    grout = g.material("Grout", [0.32, 0.30, 0.27], 0.88)
    tile = g.material("SourceFloorTile", [0.57, 0.54, 0.48], 0.8)
    records = []
    regions = []
    # True north wall is at y~3.23. The earlier narrow y-window fitted an edge.
    surface_specs = [
        (
            "EastMural",
            0,
            [-0.0047444, 0.0190969, 4.6608284],
            [[-0.22, 3.24], [0.28, 2.45]],
            (18, 37),
        ),
        ("Entry", 0, [0, 0.015, 4.69], [[-2.63, -0.35], [-0.76, 2.45]], (0, 24)),
    ]
    for i, (start, stop) in enumerate(
        zip([-4.65, -2.8, -0.9, 1.0, 2.85], [-2.8, -0.9, 1.0, 2.85, 4.67], strict=True)
    ):
        surface_specs.append(
            (
                f"NorthMural{i}",
                1,
                [0.0054724, 0.0007312, 3.2150441],
                [[start, stop], [0.28, 2.45]],
                (24, 70),
            )
        )
    for name, axis, coeff, bounds, times in surface_specs:
        w = h = 1536
        grid, corners = surface_grid(axis, coeff, bounds, w, h)
        tex = destination / "appearance_textures" / f"{name}.png"
        record = baker.bake(
            transform(grid, ai),
            (h, w),
            tex,
            fallback_srgb=(133, 121, 104),
            depth_tolerance=0.25,
            allowed_times=times,
        )
        mat = g.material(
            name,
            [0.5, 0.5, 0.5],
            texture="appearance_textures/" + tex.name,
            photo_gain=0.8,
        )
        g.mesh(
            "Walls/" + name,
            corners,
            [[0, 1, 2, 3]],
            mat,
            [(0, 0), (1, 0), (1, 1), (0, 1)],
        )
        regions.append(
            {
                "kind": "plane",
                "id": name,
                "axis": axis,
                "coefficients": coeff,
                "bounds": bounds,
                "distance": 0.23,
            }
        )
        records.append(
            {
                "id": name,
                "category": "wall",
                "texture": record,
                "geometry": "source-conditioned planar fit or explicit boundary estimate",
                "source_times": times,
            }
        )
        print(name, "coverage", record["observed_fraction"], flush=True)
    # Floor stays tied to the fitted support plane; tile grid is estimated.
    floor_shear = np.eye(4)
    floor_shear[2, :2] = floor[:2]
    floor_shear[2, 3] = floor[2]
    for ix, x in enumerate(np.arange(-5.1, 4.7, 0.60)):
        for iy, y in enumerate(np.arange(-3.8, 3.25, 0.60)):
            g.box(
                f"Floor/Tile_{ix}_{iy}",
                [x + 0.3, y + 0.3, -0.012],
                [0.596, 0.596, 0.02],
                tile,
                0.001,
                floor_shear,
            )
    grid, corners = surface_grid(2, floor, [[-5.1, 4.99], [-3.8, 3.39]], 8, 8)
    corners[:, 2] -= 0.025
    g.mesh("Floor/Grout", corners, [[0, 1, 2, 3]], grout)
    regions.append(
        {
            "kind": "floor",
            "id": "Floor",
            "coefficients": floor.tolist(),
            "bounds": [[-5.1, 4.99], [-3.8, 3.39]],
            "height": 0.10,
        }
    )
    # Source L-bench: separate base, segmented cushions, backrest and seam gaps.
    for side, start, stop, constant in [
        ("East", -0.20, 3.18, 4.28),
        ("North", -4.65, 4.32, 2.84),
    ]:
        count = int(np.ceil((stop - start) / 0.65))
        length = (stop - start) / count
        for i in range(count):
            v = start + (i + 0.5) * length
            x, y = (constant, v) if side == "East" else (v, constant)
            z = floor_at(x, y)
            size = (
                [0.65, length - 0.006, 0.09]
                if side == "East"
                else [length - 0.006, 0.65, 0.09]
            )
            g.box(
                f"Benches/{side}/Segment{i}/Seat",
                [x, y, z + 0.50],
                size,
                leather,
                0.018,
            )
            g.box(
                f"Benches/{side}/Segment{i}/Base",
                [x, y, z + 0.245],
                [size[0] - 0.03, size[1] - 0.03, 0.44],
                dark,
                0.006,
            )
            bx, by = (x + 0.27, y) if side == "East" else (x, y + 0.27)
            bsize = (
                [0.10, length - 0.006, 0.23]
                if side == "East"
                else [length - 0.006, 0.10, 0.23]
            )
            g.box(
                f"Benches/{side}/Segment{i}/Back",
                [bx, by, z + 0.86],
                bsize,
                leather,
                0.018,
            )
        lo = [3.88, start - 0.05, -1] if side == "East" else [start - 0.05, 2.42, -1]
        hi = [4.68, stop + 0.05, 0.40] if side == "East" else [stop + 0.05, 3.23, 0.40]
        regions.append({"kind": "box", "id": side + "Bench", "min": lo, "max": hi})
    # Remove seated-person remnants only inside the photographed bench strip.
    regions.append(
        {
            "kind": "box",
            "id": "ObservedBenchPeople",
            "min": [-4.7, 2.38, -0.36],
            "max": [2.62, 3.28, 1.05],
            "reason": "source-visible seated people excluded from static environment",
        }
    )
    table_specs = [
        (x["center_old"], x["radius_old"]) for x in contract["fitted_tables"]
    ]
    table_specs += [
        ([3.44, 2.0, 0.03], 0.33),
        ([2.00, 2.02, 0.06], 0.34),
        ([0.91, 2.02, 0.09], 0.31),
        ([-0.12, 1.93, 0.11], 0.31),
        ([-1.18, 1.85, 0.125], 0.34),
        ([-2.65, 2.17, 0.17], 0.30),
        ([-3.6, 2.12, 0.20], 0.31),
    ]
    for i, (center, radius) in enumerate(table_specs):
        x, y, z = center
        name = f"Tables/Table{i:02d}"
        grid, corners = surface_grid(
            2,
            [*floor[:2], z - floor[:2] @ [x, y]],
            [[x - radius, x + radius], [y - radius, y + radius]],
            768,
            768,
        )
        tex = destination / "appearance_textures" / f"Table{i:02d}.png"
        record = baker.bake(
            transform(grid, ai),
            (768, 768),
            tex,
            fallback_srgb=(177, 117, 55),
            depth_tolerance=0.065,
            max_sources=5,
        )
        mat = g.material(
            f"TableTop{i}",
            [0.6, 0.4, 0.2],
            0.42,
            texture="appearance_textures/" + tex.name,
            photo_gain=0.75,
        )
        g.cylinder(
            name + "/Top",
            [x, y, z - 0.018],
            radius,
            0.036,
            mat,
            segments=96,
            texture_uv=True,
        )
        bottom = floor_at(x, y)
        g.cylinder(
            name + "/Stem", [x, y, (z + bottom) / 2], 0.033, z - bottom - 0.04, metal
        )
        g.cylinder(name + "/Foot", [x, y, bottom + 0.025], radius * 0.70, 0.05, metal)
        for part in ["Top", "Foot"]:
            mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Appearance/" + name + "/" + part))
            # Preserve the exact task top center while leveling the surface.
            m = np.eye(4)
            m[2, :2] = floor[:2]
            m[2, 3] = -floor[:2] @ [x, y]
            UsdGeom.Xformable(mesh).AddTransformOp().Set(Gf.Matrix4d(m.T.tolist()))
        regions.append(
            {
                "kind": "cylinder",
                "id": name,
                "xy": [x, y],
                "radius": radius + 0.08,
                "z": [bottom - 0.08, z + 0.025],
            }
        )
        records.append(
            {
                "id": name,
                "category": "table",
                "center": center,
                "radius": radius,
                "texture": record,
                "source": "registered reconstruction plus original frames",
                "metric_verified": False,
            }
        )
        print(name, "coverage", record["observed_fraction"], flush=True)
    chairs = [
        ([2.5, 0.18], np.pi / 2),
        ([2.18, 1.25], np.pi),
        ([0.9, 1.28], np.pi),
        ([-0.12, 1.23], np.pi),
        ([-1.18, 1.08], np.pi),
        ([-2.60, 1.38], np.pi),
        ([-3.65, 1.38], np.pi),
    ]
    for i, (xy, yaw) in enumerate(chairs):
        bottom = floor_at(*xy)
        name = f"Chairs/Chair{i:02d}"
        chair(g, name, xy, bottom, yaw, (wood, leather, metal))
        regions.append(
            {
                "kind": "cylinder",
                "id": name,
                "xy": xy,
                "radius": 0.34,
                "z": [bottom - 0.08, bottom + 1.03],
            }
        )
    # Long shared table and stools, measured layout / estimated hidden joinery.
    g.box("SharedTable/Top", [-0.55, -0.55, 0.43], [3.30, 0.78, 0.07], wood, 0.035)
    for i, x in enumerate([-1.85, 0.75]):
        for j, y in enumerate([-0.83, -0.27]):
            bottom = floor_at(x, y)
            g.box(
                f"SharedTable/Leg{i}_{j}",
                [x, y, (bottom + 0.40) / 2],
                [0.055, 0.055, 0.40 - bottom],
                dark,
                0.006,
            )
    regions.append(
        {
            "kind": "box",
            "id": "SharedTable",
            "min": [-2.27, -1.02, -0.90],
            "max": [1.17, -0.08, 0.49],
        }
    )
    for i, xy in enumerate(
        [
            [-1.5, 0.22],
            [-0.55, 0.22],
            [0.4, 0.22],
            [1.75, 0.25],
            [-1.2, -1.16],
            [0.15, -1.16],
            [1.4, -1.10],
        ]
    ):
        bottom = floor_at(*xy)
        name = f"Stools/Stool{i:02d}"
        chair(g, name, xy, bottom, 0, (wood, leather, metal), True)
        regions.append(
            {
                "kind": "cylinder",
                "id": name,
                "xy": xy,
                "radius": 0.29,
                "z": [bottom - 0.05, bottom + 0.86],
            }
        )
    # Wall/bench records keep estimates separate from observed photo pixels.
    stage.GetRootLayer().Save()
    result = {
        "schema_version": 1,
        "status": "CANDIDATE_REQUIRES_NATIVE_REVIEW",
        "regions": regions,
        "assets": records,
        "floor": floor.tolist(),
        "reference_source_sha256": digest(
            args.reference_assets / "scene/original_video_splats.usdc"
        ),
        "provenance": "substation coherent sharp-source selection and explicit geometry/PBR replacement; source cafe layout retained",
        "source_fidelity_verified": False,
        "metric_verified": False,
        "heldout_used_for_texture_selection": False,
    }
    write_json(destination / "appearance_reference.json", result)
    return result


def replacement_mask(points, regions):
    selected = np.zeros(len(points), bool)
    counts = {}
    for r in regions:
        if r["kind"] == "box":
            mask = ((points >= r["min"]) & (points <= r["max"])).all(1)
        elif r["kind"] == "cylinder":
            mask = (
                (np.linalg.norm(points[:, :2] - r["xy"], axis=1) < r["radius"])
                & (points[:, 2] >= r["z"][0])
                & (points[:, 2] <= r["z"][1])
            )
        elif r["kind"] == "plane":
            axis = r["axis"]
            other = [j for j in range(3) if j != axis]
            residual = abs(
                points[:, axis]
                - np.c_[points[:, other], np.ones(len(points))] @ r["coefficients"]
            )
            mask = residual < r["distance"]
            for j, bounds in zip(other, r["bounds"], strict=True):
                mask &= (points[:, j] >= bounds[0]) & (points[:, j] <= bounds[1])
        else:
            h = (
                points[:, 2]
                - np.c_[points[:, :2], np.ones(len(points))] @ r["coefficients"]
            )
            mask = (h < r["height"]) & (h > -0.3)
            for j, bounds in enumerate(r["bounds"]):
                mask &= (points[:, j] >= bounds[0]) & (points[:, j] <= bounds[1])
        selected |= mask
        counts[r["id"]] = int(mask.sum())
    return selected, counts


def finish_reference(args, directory, record):
    """Suppress residual splat footprints where complete replacement exists."""
    if record.get("appearance_revision") == 2:
        return record
    stage = Usd.Stage.Open(str(directory / "appearance_reference.usda"))
    g = VisibleGeometry(stage, "/Appearance")
    paint = g.material("SourceWallPaint", [0.48, 0.39, 0.30], 0.85)
    for name, axis, coeff, bounds in [
        (
            "EastBase",
            0,
            [-0.0047444, 0.0190969, 4.6608284],
            [[-0.22, 3.24], [-0.85, 0.28]],
        ),
        (
            "NorthBase",
            1,
            [0.0054724, 0.0007312, 3.2150441],
            [[-4.65, 4.67], [-0.85, 0.28]],
        ),
    ]:
        _, corners = surface_grid(axis, coeff, bounds, 4, 4)
        g.mesh("Walls/" + name, corners, [[0, 1, 2, 3]], paint)
    record["regions"] += [
        {
            "kind": "box",
            "id": "CompleteNorthBenchAndWall",
            "min": [-4.8, 2.25, -1.1],
            "max": [4.95, 3.55, 2.65],
        },
        {
            "kind": "box",
            "id": "CompleteEastBenchAndWall",
            "min": [3.82, -0.28, -1.1],
            "max": [4.95, 3.55, 2.65],
        },
    ]
    for region in record["regions"]:
        if region["kind"] == "floor":
            region["height"] = 0.24
        if region["kind"] == "cylinder":
            region["radius"] += 0.09
            if region["id"].startswith("Tables/"):
                region["z"][1] += 0.36
    # Reuse the reconstructed cup asset for the two stationary photographed
    # cups. Their placement is estimated; only the task cup is dynamic.
    props = directory / "appearance_props"
    props.mkdir(exist_ok=True)
    shutil.copy2(args.reference_assets / "cup.usdc", props / "cup.usdc")
    shutil.copytree(
        args.reference_assets / "textures", props / "textures", dirs_exist_ok=True
    )
    floor = np.asarray(record["floor"])
    table = record["assets"][
        next(i for i, a in enumerate(record["assets"]) if a["id"] == "Tables/Table01")
    ]
    center = np.asarray(table["center"])
    for i, xy in enumerate([[3.27, 0.30], [3.52, 0.44]]):
        prim = stage.DefinePrim(f"/Appearance/Props/Cup{i}", "Xform")
        prim.GetReferences().AddReference("appearance_props/cup.usdc")
        UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        z = center[2] + floor[:2] @ (np.asarray(xy) - center[:2])
        xf.AddTranslateOp().Set(Gf.Vec3d(*xy, z))
        xf.AddScaleOp().Set(Gf.Vec3f(1.05))
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            if mesh.GetNormalsInterpolation() == "uniform":
                normals = np.repeat(
                    np.asarray(mesh.GetNormalsAttr().Get()),
                    mesh.GetFaceVertexCountsAttr().Get(),
                    axis=0,
                )
                mesh.GetNormalsAttr().Set(
                    Vt.Vec3fArray.FromNumpy(normals.astype(np.float32))
                )
                mesh.SetNormalsInterpolation("faceVarying")
    stage.GetRootLayer().Save()
    record["appearance_revision"] = 2
    record["static_cups"] = {
        "count": 2,
        "source": "reused reconstructed cup",
        "placement_verified": False,
    }
    write_json(directory / "appearance_reference.json", record)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets", required=True, type=Path)
    p.add_argument("--reference-assets", required=True, type=Path)
    p.add_argument("--sfm-root", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--reuse-reference", type=Path)
    p.add_argument("--reference-to-case", type=Path)
    p.add_argument(
        "--generated-mural",
        type=Path,
        help="Optional generated completion of NorthMural3; requires adjacent JSON provenance",
    )
    p.add_argument(
        "--transient-mask",
        type=Path,
        help="Grounded person mask for the NorthMural3 source atlas; clipped to the 3D transient height band",
    )
    p.add_argument(
        "--editable-patrol-background",
        action="store_true",
        help="Add photographed boundary surfaces and replace the residual Gaussian background for patrol views",
    )
    args = p.parse_args()
    shutil.copytree(args.assets, args.out)
    d = args.out / "scene"
    contract = json.loads((d / "scene_contract.json").read_text())
    t = np.asarray(
        contract.get("old_scene_to_world", contract.get("old_scene_to_task"))
    )
    c = (
        np.eye(4)
        if not args.reference_to_case
        else np.asarray(
            json.loads(args.reference_to_case.read_text())["reference_to_case"]
        )
    )
    if args.reuse_reference:
        for name in ["appearance_reference.usda", "appearance_reference.json"]:
            shutil.copy2(args.reuse_reference / name, d / name)
        shutil.copytree(
            args.reuse_reference / "appearance_textures", d / "appearance_textures"
        )
        if (args.reuse_reference / "appearance_props").exists():
            shutil.copytree(
                args.reuse_reference / "appearance_props", d / "appearance_props"
            )
        record = json.loads((d / "appearance_reference.json").read_text())
    else:
        record = build_reference(args, d)
    record = finish_reference(args, d, record)
    if args.transient_mask:
        shutil.copy2(
            args.transient_mask,
            d / "appearance_textures/NorthMural3_transient_mask.png",
        )
    if args.generated_mural:
        if not args.generated_mural.with_suffix(".json").is_file():
            raise ValueError("Generated mural requires adjacent JSON provenance")
        shutil.copy2(
            args.generated_mural, d / "appearance_textures/NorthMural3_generated.png"
        )
        shutil.copy2(
            args.generated_mural.with_suffix(".json"),
            d / "appearance_textures/NorthMural3_generated.json",
        )
    from finish_materials import finish_materials

    record = finish_materials(d, record)
    if args.editable_patrol_background:
        from finish_patrol import finish_patrol

        record = finish_patrol(args, d, record)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    field = [
        p for p in stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    ][0]
    raw = np.asarray(field.GetAttribute("positions").Get())
    opacity = np.asarray(field.GetAttribute("opacities").Get())
    matrix = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(field))
    world = raw @ matrix[:3, :3] + matrix[3, :3]
    reference = transform(world, np.linalg.inv(t @ c))
    mask, counts = replacement_mask(reference, record["regions"])
    if args.editable_patrol_background:
        mask[:] = True
        UsdGeom.Imageable(field).MakeInvisible()
    indices = np.flatnonzero(mask).astype(np.int32)
    np.save(d / "appearance_gaussian_indices.npy", indices)
    override = Usd.Stage.CreateNew(str(d / "appearance_replacement.usdc"))
    changed = opacity.copy()
    changed[indices] = 0
    override.OverridePrim(field.GetPath()).CreateAttribute(
        "opacities", Sdf.ValueTypeNames.FloatArray
    ).Set(Vt.FloatArray.FromNumpy(changed.astype(np.float32)))
    override.GetRootLayer().Save()
    stage.GetRootLayer().subLayerPaths.insert(0, "appearance_replacement.usdc")
    root = UsdGeom.Xform.Define(stage, "/Cafe/Appearance")
    root.GetPrim().GetReferences().AddReference("appearance_reference.usda")
    root.AddTransformOp().Set(Gf.Matrix4d((t @ c).T.tolist()))
    if not np.allclose(c, np.eye(4)):
        # Align the shared reference floor to this reconstruction's actual
        # support plane; rigid ICP alone can leave it several cm above ground.
        target_floor = np.asarray(contract["floor_plane_old"])
        for prim in list(stage.Traverse()):
            if prim.IsA(UsdGeom.Mesh) and str(prim.GetPath()).startswith(
                "/Cafe/Appearance/Floor/"
            ):
                mesh = UsdGeom.Mesh(prim)
                pts = np.asarray(mesh.GetPointsAttr().Get()).copy()
                old_height = (
                    pts[:, 2] - np.c_[pts[:, :2], np.ones(len(pts))] @ record["floor"]
                )
                case = transform(pts, c)
                case[:, 2] = (
                    np.c_[case[:, :2], np.ones(len(pts))] @ target_floor + old_height
                )
                mesh.GetPointsAttr().Set(
                    Vt.Vec3fArray.FromNumpy(
                        transform(case, np.linalg.inv(c)).astype(np.float32)
                    )
                )
    for name in ["Floor", "WallX", "WallY", "BenchSeat", "BenchBack"]:
        stage.OverridePrim("/Cafe/Completion/Visual/" + name).CreateAttribute(
            "visibility", Sdf.ValueTypeNames.Token
        ).Set("invisible")
    case_table_leveling = []
    if record.get("wide_patrol"):
        from refine_wide_patrol import level_case_tables

        case_table_leveling = level_case_tables(stage, record, t @ c)
    case_floor_openings = []
    if record.get("far_left"):
        from restore_far_left import apply_case_floor_opening

        case_floor_openings = apply_case_floor_opening(stage, contract, record, c, t)
    # New detailed furniture collision is static and shares the visible geometry.
    if __package__:
        from .appearance_collision import author_replacement_collision
    else:
        from appearance_collision import author_replacement_collision

    collision_paths, support_overrides = author_replacement_collision(
        stage, d, contract, t, c, record["regions"], replacement_mask
    )
    physical = json.loads((d / "physical_assets.json").read_text())
    physical["previous_assets"] = physical["assets"]
    physical["assets"] = [a for a in physical["assets"] if a["category"] == "floor"]
    for path in collision_paths:
        name = path.rsplit("/", 1)[-1]
        category = {
            "Tables": "table",
            "Chairs": "chair",
            "Stools": "stool",
            "Benches": "bench",
            "Walls": "wall",
            "Props": "cup",
            "Stairs": "stairwell",
            "Railings": "guardrail",
        }.get(name.split("_")[0], "static_furniture")
        if name == "Props_StairBin":
            category = "bin"
        asset = {
            "id": name,
            "category": category,
            "collision_prim": path,
            "geometry_source": "source-conditioned explicit mesh; unmeasured",
            "physics": {
                "contact": {
                    "static_friction": 0.65,
                    "dynamic_friction": 0.5,
                    "restitution": 0,
                },
                "body_type": "static",
                "parameter_source": "engineering preset",
                "real_world_validated": False,
            },
        }
        if category == "table":
            table = next(
                a for a in record["assets"] if a["id"].replace("/", "_") == name
            )
            asset["world_top_center"] = transform(
                np.asarray(table["center"])[None], t @ c
            )[0].tolist()
        physical["assets"].append(asset)
    write_json(d / "physical_assets.json", physical)
    # Material binding is explicit for the actual task tables as well.
    if (args.out / "task.usda").exists():
        task = Usd.Stage.Open(str(args.out / "task.usda"))
        bind_contact_material(
            task,
            "/World/TableContact",
            ["/World/Table", "/World/TableB"],
            {"static_friction": 0.65, "dynamic_friction": 0.5, "restitution": 0.0},
        )
        task.GetRootLayer().Save()
    stage.GetRootLayer().Save()
    contract.update(
        appearance="source_conditioned_repaired_reconstruction",
        appearance_repair="appearance_repair.json",
        original_visual_preserved=False,
        original_visual_data_preserved=True,
        gaussians_suppressed=int(np.count_nonzero(changed == 0)),
        task_support_overrides=support_overrides,
        scope="source-conditioned visible geometry and texture replacement; candidate pending appearance/physics gates",
    )
    if record.get("wide_patrol"):
        # Independent probes also exercise the newly revealed floor; these
        # sites sit away from the far table, not on unobserved obstacles.
        far_sites = np.array([[-5.4, -4.8], [-4.2, -4.8], [-5.4, -3.4], [-4.2, -3.4]])
        far_sites = np.c_[far_sites, np.c_[far_sites, np.ones(4)] @ record["floor"]]
        contract["additional_support_sites_old"] = transform(far_sites, c)[
            :, :2
        ].tolist()
        for name in ["WideSupportWest", "WideSupportSouth"]:
            physical["assets"].append(
                {
                    "id": name,
                    "category": "floor",
                    "collision_prim": "/Cafe/Appearance/Floor/" + name,
                    "geometry_source": "extension of fitted floor; unmeasured",
                    "physics": {
                        "body_type": "static",
                        "contact": {
                            "static_friction": 0.65,
                            "dynamic_friction": 0.5,
                            "restitution": 0,
                        },
                        "parameter_source": "engineering preset",
                        "real_world_validated": False,
                    },
                }
            )
        write_json(d / "physical_assets.json", physical)
    write_json(d / "scene_contract.json", contract)
    write_json(
        d / "appearance_repair.json",
        {
            "status": "CANDIDATE_REQUIRES_REVIEW",
            "reference_to_case": c.tolist(),
            "counts": counts,
            "suppressed_count": len(indices),
            "indices_sha256": digest(d / "appearance_gaussian_indices.npy"),
            "opacity_layer_sha256": digest(d / "appearance_replacement.usdc"),
            "reference_geometry_sha256": digest(d / "appearance_reference.usda"),
            "reference_manifest_sha256": digest(d / "appearance_reference.json"),
            "collision_prims": collision_paths,
            "new_physics_validation_required": True,
            "source_fidelity_verified": False,
            "residual_gaussian_background_visible": not args.editable_patrol_background,
            "case_table_leveling": case_table_leveling,
            "case_floor_openings": case_floor_openings,
        },
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "replaced_gaussians": len(indices),
                "colliders": len(collision_paths),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
