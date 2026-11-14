"""Restore the cafe's long patrol views with source-constrained furniture.

This case-specific refinement keeps measured source pixels separate from
estimated geometry. Run repair_appearance.py afterwards to rebuild collision
and the occupancy grid from the structural base, never from stale occupancy.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Usd, UsdShade
from repair_appearance import chair, transform

from simready.core.artifacts import digest, write_json
from simready.repair.source_texture import SourceTextureBaker
from simready.repair.structural_completion import robust_plane, surface_grid
from simready.repair.visible_geometry import VisibleGeometry


def capsule_table(g, name, xy, length, width, plane, floor, material, metal):
    """Closed capsule tabletop and four legs; top follows the fitted plane."""
    radius = width / 2
    half = (length - width) / 2
    right = np.linspace(-np.pi / 2, np.pi / 2, 33)
    left = np.linspace(np.pi / 2, 3 * np.pi / 2, 33)
    ring = (
        np.vstack(
            [
                np.c_[half + radius * np.cos(right), radius * np.sin(right)],
                np.c_[-half + radius * np.cos(left), radius * np.sin(left)],
            ]
        )
        + xy
    )
    height = np.c_[ring, np.ones(len(ring))] @ plane
    n = len(ring)
    points = np.vstack([np.c_[ring, height], np.c_[ring, height - 0.065]])
    faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
    faces += [[i, (i + 1) % n, (i + 1) % n + n, i + n] for i in range(n)]
    uv = np.tile((ring - xy) / [length, width] + 0.5, (2, 1))
    g.mesh(name + "/Top", points, faces, material, uv)
    for i, x in enumerate([xy[0] - length * 0.31, xy[0] + length * 0.31]):
        for j, y in enumerate([xy[1] - width * 0.3, xy[1] + width * 0.3]):
            base = np.array([x, y, 1]) @ floor
            top = np.array([x, y, 1]) @ plane - 0.065
            g.box(
                name + f"/Leg{i}_{j}",
                [x, y, (base + top) / 2],
                [0.065, 0.065, top - base],
                metal,
                0.006,
            )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="Completed appearance scene directory to copy",
    )
    ap.add_argument("--reference-assets", type=Path, required=True)
    ap.add_argument("--sfm-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ["appearance_reference.usda", "appearance_reference.json"]:
        shutil.copy2(args.reference / name, args.out / name)
    for name in ["appearance_textures", "appearance_props"]:
        shutil.copytree(args.reference / name, args.out / name)
    record = json.loads((args.out / "appearance_reference.json").read_text())
    alignment = np.asarray(
        json.loads((args.sfm_root / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    inverse = np.linalg.inv(alignment)
    source_path = args.reference_assets / "scene/original_video_splats.usdc"
    source = Usd.Stage.Open(str(source_path))
    field = next(
        p
        for p in source.Traverse()
        if p.GetTypeName() == "ParticleField3DGaussianSplat"
    )
    raw = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    scales = np.asarray(field.GetAttribute("scales").Get()).max(1)
    points = transform(raw, alignment)
    confident = points[(alpha > 0.25) & (scales < 0.06)]
    cameras = json.loads(
        (args.sfm_root / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    baker = SourceTextureBaker(
        cameras, args.sfm_root / "full_sfm/images", raw[alpha > 0.45][::2]
    )
    stage = Usd.Stage.Open(str(args.out / "appearance_reference.usda"))
    g = VisibleGeometry(stage, "/Appearance")
    for name in ["SharedTable", "Stools", "Boundaries/UnobservedWestBoundary"]:
        stage.RemovePrim("/Appearance/" + name)
    record["regions"] = [
        r
        for r in record["regions"]
        if r["id"] not in ["SharedTable", "UnobservedWestBoundary"]
        and not r["id"].startswith("Stools/")
    ]
    floor = np.asarray(record["floor"])
    wood = UsdShade.Material(
        stage.GetPrimAtPath("/Appearance/Materials/SourceWarmWood")
    )
    leather = UsdShade.Material(
        stage.GetPrimAtPath("/Appearance/Materials/SourceLeather")
    )
    dark = UsdShade.Material(stage.GetPrimAtPath("/Appearance/Materials/DarkJoinery"))
    tile = UsdShade.Material(
        stage.GetPrimAtPath("/Appearance/Materials/SourceFloorTile")
    )
    grout = UsdShade.Material(stage.GetPrimAtPath("/Appearance/Materials/Grout"))
    table_mat = UsdShade.Material(
        stage.GetPrimAtPath("/Appearance/Materials/TableTop0")
    )
    fits = []
    for name, lo, hi, xy, size in [
        (
            "WideNear",
            [-1.4, -1.1, 0.15],
            [2.4, 0.08, 0.60],
            [0.42, -0.43],
            [3.55, 1.02],
        ),
        ("WideFar", [-6.2, -1.2, 0.30], [-2.8, 0.02, 0.8], [-4.55, -0.56], [3.6, 1.08]),
    ]:
        subset = confident[((confident > lo) & (confident < hi)).all(1)]
        plane, inliers = robust_plane(subset, 2, 0.035)
        capsule_table(
            g, "Tables/" + name, np.asarray(xy), *size, plane, floor, table_mat, dark
        )
        center = [*xy, float(np.r_[xy, 1] @ plane)]
        evidence = {
            "id": "Tables/" + name,
            "category": "table",
            "center": center,
            "geometry": "source plane fit; capsule outline and legs are estimates",
            "fit_points": int(inliers.sum()),
            "plane": plane.tolist(),
            "roi_min": lo,
            "roi_max": hi,
            "outline_size_m": size,
        }
        fits.append(evidence)
        record["assets"].append(evidence)
    # Seat locations follow the visible source clusters. These are not measured
    # real-world poses; unlike the previous guesses they preserve the aisle.
    stool_xy = [
        [-0.55, 0.13],
        [0.06, 0.12],
        [0.83, 0.32],
        [1.77, 0.30],
        [-0.95, -0.89],
        [0.60, -0.94],
        [1.55, -1.02],
        [-5.85, 0.20],
        [-5.0, 0.20],
        [-4.2, 0.18],
        [-3.5, 0.20],
        [-5.6, -1.27],
        [-4.8, -1.23],
        [-3.45, -1.32],
    ]
    for i, xy in enumerate(stool_xy):
        chair(
            g,
            f"Stools/Stool{i:02d}",
            xy,
            float(np.r_[xy, 1] @ floor),
            0,
            (wood, leather, dark),
            True,
        )
    # Replace complete near/far table clusters, including their old splat
    # footprints. No obstacle cells outside these explicit regions are removed.
    for name, low, high in [
        ("RefittedNearTableGroup", [-1.55, -1.4, -1.0], [2.55, 0.65, 0.8]),
        ("FarTableGroup", [-6.6, -1.7, -1.0], [-2.4, 0.65, 0.9]),
    ]:
        record["regions"].append({"kind": "box", "id": name, "min": low, "max": high})
    # Source-facing window and wall surfaces extend the old camera's far view.
    surfaces = [
        (
            "FarWindows",
            0,
            [0, 0, -7.03],
            [[-5.7, 3.22], [-0.7, 2.65]],
            (2304, 1152),
            (45, 74),
            (66, 64, 60),
            0.45,
        ),
        (
            "EastAisleWall",
            1,
            [0, 0, -2.42],
            [[1.32, 4.70], [-0.85, 2.45]],
            (1536, 1536),
            (0, 24),
            (112, 84, 61),
            0.3,
        ),
        (
            "SouthReturn",
            0,
            [0, 0, -1.65],
            [[-4.05, -2.95], [-0.85, 2.45]],
            (768, 1536),
            (0, 68),
            (95, 68, 48),
            0.25,
        ),
        (
            "FarSouth",
            1,
            [0, 0, -5.9],
            [[-7.08, -1.65], [-0.65, 2.65]],
            (2048, 1280),
            (45, 72),
            (65, 62, 56),
            0.45,
        ),
    ]
    for name, axis, coeff, bounds, shape, times, fallback, tolerance in surfaces:
        grid, corners = surface_grid(axis, coeff, bounds, *shape)
        texture = args.out / "appearance_textures" / (name + ".png")
        evidence = baker.bake(
            transform(grid, inverse),
            shape[::-1],
            texture,
            fallback_srgb=fallback,
            depth_tolerance=tolerance,
            allowed_times=times,
            max_sources=12,
        )
        material = g.material(
            name,
            [0.4, 0.35, 0.3],
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
                "geometry": "source-constrained estimated planar surface; metric position unverified",
            }
        )
        record["regions"].append(
            {
                "kind": "plane",
                "id": name,
                "axis": axis,
                "coefficients": coeff,
                "bounds": bounds,
                "distance": 0.25,
            }
        )
        print(name, evidence["observed_fraction"], flush=True)
    # Continue the fitted floor into the revealed area, preserving tile spacing.
    shear = np.eye(4)
    shear[2, :2], shear[2, 3] = floor[:2], floor[2]
    for ix, x in enumerate(np.arange(-7.5, 4.7, 0.6)):
        for iy, y in enumerate(np.arange(-6.2, 3.25, 0.6)):
            if x >= -5.1 - 1e-6 and y >= -3.8 - 1e-6:
                continue
            g.box(
                f"Floor/WideTile_{ix}_{iy}",
                [x + 0.3, y + 0.3, -0.012],
                [0.596, 0.596, 0.02],
                tile,
                0.001,
                shear,
            )
    _, corners = surface_grid(2, floor, [[-7.5, 4.99], [-6.2, 3.39]], 4, 4)
    corners[:, 2] -= 0.025
    g.mesh("Floor/WideGrout", corners, [[0, 1, 2, 3]], grout)
    record["wide_patrol_revision"] = 1
    record["wide_patrol"] = {
        "source_sha256": digest(source_path),
        "table_fits": fits,
        "stool_locations_reference": stool_xy,
        "restored_view": "original 30 s camera; west closure removed; far table and photographed window area exposed",
        "limits": "Geometry, scale, hidden textures and physical parameters remain estimates. No autonomous navigation or real-world validation.",
    }
    stage.GetRootLayer().Save()
    write_json(args.out / "appearance_reference.json", record)


if __name__ == "__main__":
    main()
