"""Suppress multi-view person-associated Gaussians in a reversible override layer."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from build import cloud, json_write, mesh, physics, texture_material
from PIL import Image
from pxr import Sdf, Usd, UsdGeom, Vt
from scipy import ndimage

from simready.repair.structural_completion import (
    bake_observed_texture,
    obstacle_voxels,
    project,
    robust_plane,
    surface_grid,
    voxel_boundary,
)
from simready.simulation.physical_authoring import bind_contact_material


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assets", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True)
    ap.add_argument("--sfm-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    shutil.copytree(args.assets, args.out)
    d = args.out / "scene"
    contract = json.loads((d / "scene_contract.json").read_text())
    t = np.asarray(
        contract.get("old_scene_to_world", contract.get("old_scene_to_task"))
    )
    inverse = np.linalg.inv(t)
    a = np.asarray(
        json.loads((args.sfm_root / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    ai = np.linalg.inv(a)
    stage = Usd.Stage.Open(str(d / "scene.usda"))
    world, opacity, field_path = cloud(stage)
    old = world @ inverse[:3, :3].T + inverse[:3, 3]
    sfm = old @ ai[:3, :3].T + ai[:3, 3]
    cameras = json.loads(
        (args.sfm_root / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    camera_map = {c["image_name"]: c for c in cameras}
    votes = np.zeros(len(old), dtype=np.int16)
    evidence = []
    for manifest in sorted(args.masks.glob("*/manifest.json")):
        record = json.loads(manifest.read_text())
        name = Path(record["source_image"]["path"]).name
        camera = camera_map[name]
        mask = np.zeros(
            (record["source_image"]["height"], record["source_image"]["width"]),
            dtype=bool,
        )
        for path in sorted((manifest.parent / "masks").glob("person_*.png")):
            mask |= np.asarray(Image.open(path)) > 0
        mask = ndimage.binary_dilation(mask, iterations=4)
        uv, z = project(sfm, camera)
        h, w = mask.shape
        valid = (
            (z > 0.1)
            & (uv[:, 0] >= 0)
            & (uv[:, 0] < w)
            & (uv[:, 1] >= 0)
            & (uv[:, 1] < h)
        )
        ids = np.flatnonzero(valid)
        ij = uv[ids].astype(int)
        inside = mask[ij[:, 1], ij[:, 0]]
        cw, ch = (w + 3) // 4, (h + 3) // 4
        depth = np.full((ch, cw), np.inf)
        trusted = opacity[ids] > 0.3
        np.minimum.at(
            depth, (ij[trusted, 1] // 4, ij[trusted, 0] // 4), z[ids[trusted]]
        )
        depth = ndimage.minimum_filter(depth, size=3)
        visible = abs(z[ids] - depth[ij[:, 1] // 4, ij[:, 0] // 4]) < 0.65
        votes[ids[inside & visible]] += 1
        evidence.append(
            {
                "frame": name,
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "visible_person_candidates": int((inside & visible).sum()),
            }
        )
    required_votes = max(2, int(np.ceil(len(evidence) * 0.6)))
    protected = np.zeros(len(old), dtype=bool)
    existing_physical = json.loads((d / "physical_assets.json").read_text())
    for asset in existing_physical["assets"]:
        if asset["category"] in {"floor", "wall"}:
            fit = asset["fit"]
            other = [i for i in range(3) if i != fit["axis"]]
            residual = abs(
                old[:, fit["axis"]]
                - np.c_[old[:, other], np.ones(len(old))] @ fit["coefficients"]
            )
            protected |= residual < 0.10
    votes[protected] = 0
    suppress = np.flatnonzero(votes >= required_votes)
    if not 10 < len(suppress) < 0.1 * len(old):
        raise ValueError(f"Ambiguous person association count: {len(suppress)}")
    np.save(d / "person_gaussian_indices.npy", suppress.astype(np.int32))
    cleanup = Usd.Stage.CreateNew(str(d / "people_cleanup.usdc"))
    override = cleanup.OverridePrim(field_path)
    changed = opacity.copy()
    changed[suppress] = 0
    override.CreateAttribute("opacities", Sdf.ValueTypeNames.FloatArray).Set(
        Vt.FloatArray.FromNumpy(changed.astype(np.float32))
    )
    cleanup.GetRootLayer().Save()
    stage.GetRootLayer().subLayerPaths.insert(0, "people_cleanup.usdc")
    stage.GetRootLayer().Save()
    # The same observed bench continues behind the seated person. Add backing
    # surfaces there, with projection provenance and explicitly estimated fill.
    repair = Usd.Stage.Open(str(d / "completion.usda"))
    physical = json.loads((d / "physical_assets.json").read_text())
    case = json.loads((args.out / "build.json").read_text())["case"]
    grid_data = np.load(d / "obstacles.npz", allow_pickle=False)
    floor = grid_data["floor"].copy()
    h = old[:, 2] - np.c_[old[:, :2], np.ones(len(old))] @ floor
    obstacles = (
        (opacity > (0.2 if case == "tour" else 0.7))
        & (votes < required_votes)
        & (h > 0.12)
        & (h < 2.2)
    )
    for table in physical["table_supports"]:
        obstacles &= ~(
            (
                np.linalg.norm(old[:, :2] - table["center_old"][:2], axis=1)
                < table["radius_old"] + 0.08
            )
            & (old[:, 2] < table["center_old"][2] + 0.12)
        )
    occupied, origin, resolution = obstacle_voxels(
        old[obstacles],
        [[-4.8, -3.8, -1], [4.8, 3, 2.5]],
        resolution=0.05 if case == "tour" else 0.10,
        minimum_points=1 if case == "tour" else 8,
    )
    vertices, faces = voxel_boundary(occupied, origin, resolution)
    mesh(
        repair,
        "/Completion/Collision/ObservedObstacles",
        vertices,
        faces,
        visible=False,
    )
    np.savez_compressed(
        d / "obstacles.npz",
        occupied=occupied,
        origin=origin,
        resolution=resolution,
        old_to_world=t,
        floor=floor,
    )
    for record in physical["assets"]:
        if record["id"] == "ObservedObstacles":
            record.update(
                occupied_cells=int(occupied.sum()),
                triangles=len(faces),
                transient_exclusion=f"person mask association in >={required_votes} registered views",
            )
    stable = (opacity > 0.3) & (votes < required_votes)
    bench_region = (
        stable
        & (old[:, 0] > 4.0)
        & (old[:, 0] < 4.55)
        & (old[:, 1] > -0.2)
        & (old[:, 1] < 2.7)
        & (old[:, 2] > -0.4)
        & (old[:, 2] < -0.12)
    )
    seat, _ = robust_plane(old[bench_region], tolerance=0.04)
    specs = [
        ("BenchSeat", 2, seat, [[3.93, 4.58], [-0.2, 2.75]], -0.012),
        (
            "BenchBack",
            0,
            np.array([0.0, 0.0, 4.57]),
            [[-0.2, 2.75], [-0.25, 0.22]],
            0.012,
        ),
    ]
    for name, axis, coefficients, bounds, offset in specs:
        grid, corners = surface_grid(axis, coefficients, bounds, width=1024, height=512)
        tex = bake_observed_texture(
            grid @ ai[:3, :3].T + ai[:3, 3],
            sfm[(opacity > 0.3)][::2],
            cameras,
            args.sfm_root / "full_sfm/images",
            width=1024,
            height=512,
            output=d / "textures" / (name + ".png"),
        )
        visual = corners.copy()
        visual[:, axis] += offset
        shape = mesh(
            repair,
            "/Completion/Visual/" + name,
            visual,
            [[0, 1, 2, 3]],
            collision=False,
        )
        shape.GetPrim().CreateAttribute(
            "primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool
        ).Set(True)
        UsdGeom.PrimvarsAPI(shape).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
        ).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        from pxr import UsdShade

        UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(
            texture_material(
                repair, "/Completion/Materials/" + name, "textures/" + name + ".png"
            )
        )
        lower = corners.copy()
        lower[:, axis] += -0.08 if axis == 2 else 0.08
        path = "/Completion/Collision/" + name
        mesh(
            repair,
            path,
            np.vstack([corners, lower]),
            [
                [0, 1, 2, 3],
                [4, 7, 6, 5],
                [0, 4, 5, 1],
                [1, 5, 6, 2],
                [2, 6, 7, 3],
                [3, 7, 4, 0],
            ],
            visible=False,
        )
        p = physics("table")
        bind_contact_material(
            repair, "/Completion/Contact/" + name, [path], p["contact"]
        )
        physical["assets"].append(
            {
                "id": name,
                "category": "bench",
                "collision_prim": path,
                "physics": p,
                "texture": tex,
                "geometry_source": "fitted seat height and explicit estimated bench extent; no seat compliance model",
            }
        )
    repair.GetRootLayer().Save()
    record = {
        "status": "CANDIDATE_REQUIRES_RENDER_REVIEW",
        "method": "DINO/SAM person masks in >=2 registered views, with depth occlusion check",
        "suppressed_count": len(suppress),
        "source_count": len(old),
        "source_gaussians_deleted": 0,
        "required_views": required_votes,
        "available_views": len(evidence),
        "mask_dilation_pixels": 4,
        "depth_tolerance_sfm_units": 0.65,
        "protected_structure_distance_old_units": 0.10,
        "field_path": field_path,
        "evidence": evidence,
        "opacity_layer_sha256": hashlib.sha256(
            (d / "people_cleanup.usdc").read_bytes()
        ).hexdigest(),
        "indices_sha256": hashlib.sha256(
            (d / "person_gaussian_indices.npy").read_bytes()
        ).hexdigest(),
        "limitations": [
            "Adjacent views share reconstruction/registration errors",
            "No claim of perfect instance segmentation",
            "Original source USD remains unchanged",
            "Obstacle mesh rebuilt with the same transient exclusion; remaining cells are uncertain",
        ],
    }
    json_write(d / "transient_cleanup.json", record)
    contract.update(
        gaussians_suppressed=len(suppress),
        transient_cleanup="transient_cleanup.json",
        completion_sha256=hashlib.sha256(
            (d / "completion.usda").read_bytes()
        ).hexdigest(),
    )
    json_write(d / "scene_contract.json", contract)
    json_write(d / "physical_assets.json", physical)
    print(json.dumps({"out": str(args.out), "suppressed": len(suppress)}), flush=True)


if __name__ == "__main__":
    main()
