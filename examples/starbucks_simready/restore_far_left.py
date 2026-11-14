"""Restore source-visible cafe stair enclosure and omitted seating.

This is a recorded case layout, not an automatic unseen-geometry estimator.
Upper rail/wood-wall observations are separate from estimated deeper steps.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdShade

from simready.core.artifacts import digest, write_json
from simready.repair.floor_openings import cut_floor_mesh
from simready.repair.source_texture import SourceTextureBaker
from simready.repair.structural_completion import surface_grid
from simready.repair.visible_geometry import VisibleGeometry


def transform(points, matrix):
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def reject_foreground_projection(stage, record):
    """Render only the rear wall's observed artwork, not projected furniture."""
    g = VisibleGeometry(stage, "/Appearance")
    for name in ["StairWoodWall", "StairBackWall"]:
        base = "/Appearance/Materials/" + name
        shader = UsdShade.Shader(stage.GetPrimAtPath(base + "/Surface"))
        texture_node = UsdShade.Shader(stage.GetPrimAtPath(base + "/Texture"))
        shader.GetInput("emissiveColor").DisconnectSource()
        shader.GetInput("emissiveColor").Set(Gf.Vec3f(0))
        shader.GetInput("diffuseColor").ConnectToSource(
            texture_node.ConnectableAPI(), "rgb"
        )
        texture_node.GetInput("scale").Set(Gf.Vec4f(0.7, 0.7, 0.7, 1))
    gray = g.material("StairEstimatedRearPaint", [0.48, 0.47, 0.43], 0.85)
    rear = stage.GetPrimAtPath("/Appearance/Walls/StairBackWall")
    UsdShade.MaterialBindingAPI(rear).Bind(gray)
    texture = UsdShade.Material(
        stage.GetPrimAtPath("/Appearance/Materials/StairBackWall")
    )
    bounds = [[-5.25, -3.75], [0.8, 2.25]]
    _, corners = surface_grid(1, [0, 0, -6.035], bounds, 4, 4)
    uv = [
        [(x + 7.1) / 5.48, (z + 2.0) / 4.5]
        for x, z in [(-5.25, 0.8), (-3.75, 0.8), (-3.75, 2.25), (-5.25, 2.25)]
    ]
    g.mesh("Walls/StairBackArtwork", corners, [[0, 1, 2, 3]], texture, uv)
    for asset in record["assets"]:
        if asset["id"] == "StairBackWall":
            asset["render_limit"] = (
                "Only upper rear-wall artwork patch is rendered. Foreground railing, chair and side-wall pixels are rejected; remaining rear surface is estimated paint. Raw atlas retained."
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--reference-assets", required=True, type=Path)
    parser.add_argument("--case-assets", required=True, type=Path)
    parser.add_argument("--reference-to-case", required=True, type=Path)
    parser.add_argument("--sfm-root", required=True, type=Path)
    parser.add_argument("--layout-evidence", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    shutil.copytree(args.reference, args.out)
    record = json.loads((args.out / "appearance_reference.json").read_text())
    fits = json.loads(args.layout_evidence.read_text())
    shutil.copy2(args.layout_evidence, args.out / "far_left_layout_fits.json")
    contract = json.loads((args.case_assets / "scene/scene_contract.json").read_text())
    t = np.asarray(contract["old_scene_to_world"])
    c = np.asarray(json.loads(args.reference_to_case.read_text())["reference_to_case"])
    m = t @ c
    mi = np.linalg.inv(m)
    stage = Usd.Stage.Open(str(args.out / "appearance_reference.usda"))
    g = VisibleGeometry(stage, "/Appearance")
    openings = [[[-6.10, -3.02], [-4.35, -3.05]]]
    edited = []
    cache = UsdGeom.XformCache()
    for prim in list(stage.Traverse()):
        if prim.IsA(UsdGeom.Mesh) and str(prim.GetPath()).startswith(
            "/Appearance/Floor/"
        ):
            matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T
            if cut_floor_mesh(UsdGeom.Mesh(prim), matrix, openings):
                edited.append(str(prim.GetPath()))
    # Replace the uniformly painted side wall with the actual observed wood.
    stage.RemovePrim("/Appearance/Walls/SouthReturn")
    stage.RemovePrim("/Appearance/Walls/FarSouth")
    record["assets"] = [
        a for a in record["assets"] if a["id"] not in ["SouthReturn", "FarSouth"]
    ]
    sfm = args.sfm_root
    a = np.asarray(
        json.loads((sfm / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    ai = np.linalg.inv(a)
    src = Usd.Stage.Open(
        str(args.reference_assets / "scene/original_video_splats.usdc")
    )
    field = next(
        p for p in src.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    )
    raw = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    cameras = json.loads(
        (sfm / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    baker = SourceTextureBaker(cameras, sfm / "full_sfm/images", raw[alpha > 0.45][::2])
    for name, axis, coefficients, bounds, size, times in [
        (
            "StairWoodWall",
            0,
            fits["stair_wood_wall"]["coefficients"],
            [[-4.38, -2.96], [-0.65, 2.45]],
            (768, 1536),
            (0, 84),
        ),
        (
            "StairBackWall",
            1,
            [0, 0, -6.05],
            [[-7.1, -1.62], [-2.0, 2.5]],
            (1536, 1280),
            (72, 84),
        ),
    ]:
        grid, corners = surface_grid(axis, coefficients, bounds, *size)
        texture = args.out / "appearance_textures" / (name + ".png")
        evidence = baker.bake(
            transform(grid, ai),
            size[::-1],
            texture,
            fallback_srgb=(105, 99, 88),
            depth_tolerance=0.15,
            allowed_times=times,
            max_sources=8,
        )
        material = g.material(
            name,
            [0.45, 0.3, 0.2],
            texture="appearance_textures/" + texture.name,
            photo_gain=0.8,
        )
        g.mesh(
            "Walls/" + name,
            corners,
            [[0, 1, 2, 3]],
            material,
            [(0, 0), (1, 0), (1, 1), (0, 1)],
        )
        record["assets"].append(
            {
                "id": name,
                "category": "wall",
                "texture": evidence,
                "geometry": "fitted wood side wall; estimated back wall plane",
                "source_times_s": list(times),
                "metric_verified": False,
            }
        )
        print(name, evidence["observed_fraction"], flush=True)

    reject_foreground_projection(stage, record)
    # New components are authored level in the case's gravity frame and mapped
    # back into the shared reference; no table tilt or floating legs is added.
    dark = g.material("StairBlackSteel", [0.065, 0.065, 0.06], 0.45, 0.4)
    chair_wood = g.material("RestoredChairWood", [0.43, 0.25, 0.13], 0.5)
    chair_leather = g.material("RestoredChairLeather", [0.36, 0.18, 0.085], 0.55)
    tabletop = UsdShade.Material(stage.GetPrimAtPath("/Appearance/Materials/TableTop0"))
    tile = g.material("StairStone", [0.43, 0.42, 0.39], 0.85)
    woodrail = g.material("StairOakHandrail", [0.52, 0.30, 0.105], 0.42)
    step_edge = g.material("StairNosing", [0.20, 0.19, 0.17], 0.65)
    near_floor = 0.0

    def world_xy(xy):
        p = transform(np.r_[xy, 0], m)
        return p[:2]

    def group(name):
        p = UsdGeom.Xform.Define(stage, "/Appearance/" + name)
        p.AddTransformOp().Set(Gf.Matrix4d(mi.T.tolist()))
        return VisibleGeometry(stage, "/Appearance/" + name)

    def point(x, y, z):
        return np.r_[world_xy([x, y]), z]

    for name, xy, radius, height in [
        ("CorridorRound", [0.68, -2.50], 0.29, 0.76),
        ("StairRound", [-4.85, -2.45], 0.30, 0.74),
    ]:
        bg = group("Tables/" + name)
        center = point(*xy, height)
        bg.cylinder(
            "Top",
            center - [0, 0, 0.021],
            radius,
            0.042,
            tabletop,
            segments=96,
            texture_uv=True,
        )
        bg.cylinder(
            "Stem", point(*xy, (height - 0.042) / 2), 0.033, height - 0.042, dark
        )
        bg.cylinder("Foot", point(*xy, 0.025), radius * 0.64, 0.05, dark)
        record["assets"].append(
            {
                "id": "Tables/" + name,
                "category": "table",
                "center": transform(center, mi).tolist(),
                "radius": radius,
                "source": "original video visible round table; placement and dimensions estimated",
                "source_times_s": [2, 8, 76, 83],
                "metric_verified": False,
            }
        )
    # Existing chair family is reused, keeping the observed round-table layout.
    from repair_appearance import chair

    for name, xy, facing in [
        ("CorridorWest", [0.10, -2.49], -np.pi / 2),
        ("CorridorEast", [1.25, -2.48], np.pi / 2),
        ("StairNear", [-4.15, -2.41], np.pi / 2),
        ("StairFar", [-5.55, -2.43], -np.pi / 2),
    ]:
        bg = group("Chairs/" + name)
        chair(
            bg,
            "Body",
            world_xy(xy),
            near_floor,
            facing,
            (chair_wood, chair_leather, dark),
        )
        if name.startswith("Stair"):
            rot = np.eye(4)
            rot[:2, :2] = [
                [np.cos(facing), -np.sin(facing)],
                [np.sin(facing), np.cos(facing)],
            ]
            rot[:3, 3] = np.r_[world_xy(xy), near_floor]
            bg.box(
                "Body/PaddedBack",
                [0, 0.18, 0.65],
                [0.41, 0.08, 0.30],
                chair_leather,
                0.025,
                rot,
            )
            for side in [-1, 1]:
                bg.box(
                    f"Body/Arm{side + 1}",
                    [side * 0.23, 0, 0.60],
                    [0.065, 0.39, 0.18],
                    chair_leather,
                    0.025,
                    rot,
                )
        record["assets"].append(
            {
                "id": "Chairs/" + name,
                "category": "chair",
                "source": "source-visible seating group, estimated pose",
                "metric_verified": False,
            }
        )

    bg = group("Stairs/Treads")
    support_sites = []
    n, run, rise = 11, 3.08 / 11, 0.145
    for i in range(n):
        x = -3.02 - (i + 0.5) * run
        center = point(x, -3.70, -(i + 1) * rise)
        # Case axes differ from reference by less than a degree; orient boxes
        # in XY to match that rotation, with horizontal gravity-aligned tops.
        yaw = np.arctan2(m[1, 0], m[0, 0])
        rot = np.eye(4)
        rot[:2, :2] = [[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]]
        rot[:3, 3] = center
        bg.box(
            f"Step{i:02d}", [0, 0, -0.075], [run + 0.002, 1.30, 0.15], tile, 0.003, rot
        )
        bg.box(
            f"Nosing{i:02d}",
            [run / 2 - 0.012, 0, 0.001],
            [0.024, 1.28, 0.006],
            step_edge,
            0.001,
            rot,
        )
        if i in [1, 5, 9]:
            support_sites.append(
                {
                    "id": f"stair_tread_{i:02d}",
                    "surface": "stair_tread",
                    "point": center.tolist(),
                }
            )
    # Closed lower landing bounds the unseen region, explicitly an estimate.
    bg.box(
        "LowerLanding",
        point(-6.35, -3.70, -n * rise - 0.08),
        [0.50, 1.30, 0.16],
        tile,
        0.004,
    )

    def railing(name, start, end):
        bg = group("Railings/" + name)
        start, end = world_xy(start), world_xy(end)
        length = np.linalg.norm(end - start)
        direction = (end - start) / length

        def at(u, z):
            return np.r_[start + direction * u, z]

        for i, u in enumerate(np.linspace(0, length, int(np.ceil(length / 0.85)) + 1)):
            bg.rod(f"Post{i}", at(u, 0.03), at(u, 1.03), 0.022, dark, segments=12)
        bg.rod("TimberCap", at(0, 1.06), at(length, 1.06), 0.035, woodrail, segments=12)
        bg.rod("BottomRail", at(0, 0.10), at(length, 0.10), 0.025, dark, segments=8)
        # Actual open mesh geometry, not an opaque wall texture.
        count = 0
        for slope in [-1.0, 1.0]:
            for intercept in np.arange(-length, length + 1.2, 0.10):
                corners = []
                for u in [0.0, length]:
                    z = slope * u + intercept
                    if 0.14 <= z <= 0.99:
                        corners.append((u, z))
                for z in [0.14, 0.99]:
                    u = (z - intercept) / slope
                    if 0 <= u <= length:
                        corners.append((u, z))
                if len(corners) == 2 and np.linalg.norm(np.subtract(*corners)) > 0.001:
                    bg.rod(
                        f"MeshWire{count:03d}",
                        at(*corners[0]),
                        at(*corners[1]),
                        0.002,
                        dark,
                        segments=6,
                    )
                    count += 1
        record["assets"].append(
            {
                "id": "Railings/" + name,
                "category": "guardrail",
                "source": "observed timber cap and diamond steel mesh; dimensions estimated",
                "height_m": 1.06,
                "metric_verified": False,
            }
        )

    railing("Front", [-6.1, -3.04], [-3.02, -3.04])
    railing("LandingBack", [-3.02, -4.35], [-1.67, -4.35])
    railing("FarReturn", [-6.1, -4.35], [-6.1, -3.04])
    # A descending handrail follows the visible first flight.
    bg = group("Railings/Descending")
    bg.rod(
        "Rail",
        point(-3.02, -4.32, 0.85),
        point(-6.08, -4.32, 0.85 - n * rise),
        0.028,
        woodrail,
    )
    bg = group("Props/StairBin")
    center = point(-1.95, -3.87, 0.43)
    bg.cylinder("Body", center, 0.16, 0.86, dark)
    bg.cylinder("Rim", center + [0, 0, 0.44], 0.12, 0.025, dark)
    record["assets"].extend(
        [
            {
                "id": "Stairs/Treads",
                "category": "stairwell",
                "source": "source-visible descending entrance; deeper steps are estimates",
                "estimated_parameters": {
                    "count": n,
                    "run_m": run,
                    "rise_m": rise,
                    "width_m": 1.30,
                },
                "metric_verified": False,
            },
            {
                "id": "Props/StairBin",
                "category": "bin",
                "source": "source video 0–3 s and 81–84 s; geometry and placement estimated",
                "metric_verified": False,
            },
        ]
    )

    record["regions"].append(
        {
            "id": "RestoredStairwell",
            "kind": "box",
            "min": [-6.55, -4.6, -2.6],
            "max": [-1.55, -2.98, 0.9],
        }
    )
    for name, xy, radius in [
        ("CorridorSeating", [0.68, -2.50], 1.02),
        ("StairSeating", [-4.85, -2.45], 1.04),
    ]:
        record["regions"].append(
            {
                "id": name,
                "kind": "box",
                "min": [xy[0] - radius, xy[1] - 0.45, -0.9],
                "max": [xy[0] + radius, xy[1] + 0.45, 0.65],
            }
        )
    record["far_left"] = {
        "revision": 1,
        "floor_openings_reference": openings,
        "reference_floor_meshes_cut": edited,
        "stair_support_sites_world": support_sites,
        "layout_fits": fits,
        "layout_evidence_sha256": digest(args.layout_evidence),
        "source_observations": "video 0–3 s and 74–84 s: wood side wall, open metal-mesh guardrail, stair entrance, bin and round tables",
        "unobserved_geometry": "11 deeper treads, rise/run, lower landing and backing wall are parameterized estimates; no real stair traversal claim",
        "parameters": {"tread_count": n, "run_m": run, "rise_m": rise, "width_m": 1.30},
        "reference_to_world": m.tolist(),
        "metric_verified": False,
    }
    stage.GetRootLayer().Save()
    write_json(args.out / "appearance_reference.json", record)


def apply_case_floor_opening(stage, contract, record, reference_to_case, old_to_world):
    """Cut original completion colliders and declare matching probe targets."""
    if not record.get("far_left"):
        return []
    if not np.allclose(
        record["far_left"]["reference_to_world"],
        old_to_world @ reference_to_case,
        atol=1e-8,
    ):
        raise ValueError(
            "Far-left gravity-aligned assets belong to another case transform; rebuild them"
        )
    openings = record["far_left"]["floor_openings_reference"]
    to_reference = np.linalg.inv(old_to_world @ reference_to_case)
    cache = UsdGeom.XformCache()
    changed = []
    for prim in list(stage.Traverse()):
        path = str(prim.GetPath())
        if prim.IsA(UsdGeom.Mesh) and (
            path.startswith("/Cafe/Appearance/Floor/")
            or path == "/Cafe/Completion/Collision/Floor"
        ):
            local_to_reference = (
                to_reference @ np.asarray(cache.GetLocalToWorldTransform(prim)).T
            )
            if cut_floor_mesh(UsdGeom.Mesh(prim), local_to_reference, openings):
                changed.append(path)
    polygons = []
    for (x0, x1), (y0, y1) in openings:
        xy = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        points = np.c_[xy, np.c_[xy, np.ones(4)] @ record["floor"]]
        polygons.append(transform(points, reference_to_case)[:, :2].tolist())
    contract["floor_opening_polygons_old"] = polygons
    contract["additional_support_targets_world"] = record["far_left"][
        "stair_support_sites_world"
    ]
    return changed


if __name__ == "__main__":
    main()
