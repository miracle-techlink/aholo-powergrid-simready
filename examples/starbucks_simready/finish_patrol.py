"""Source-backed boundary surfaces for the wider cafe patrol views."""

import json

import numpy as np
from pxr import Usd

from simready.core.artifacts import write_json
from simready.repair.source_texture import SourceTextureBaker
from simready.repair.structural_completion import surface_grid
from simready.repair.visible_geometry import VisibleGeometry


def finish_patrol(args, directory, record):
    if record.get("patrol_boundaries_revision"):
        return record
    sfm = args.sfm_root
    alignment = np.asarray(
        json.loads((sfm / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    inverse = np.linalg.inv(alignment)
    source = Usd.Stage.Open(
        str(args.reference_assets / "scene/original_video_splats.usdc")
    )
    field = next(
        p
        for p in source.Traverse()
        if p.GetTypeName() == "ParticleField3DGaussianSplat"
    )
    raw = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    cameras = json.loads(
        (sfm / "full_sfm/reconstruction/sparse/0/trajectory.json").read_text()
    )["poses"]
    baker = SourceTextureBaker(cameras, sfm / "full_sfm/images", raw[alpha > 0.45][::2])
    stage = Usd.Stage.Open(str(directory / "appearance_reference.usda"))
    geometry = VisibleGeometry(stage, "/Appearance")
    paint = geometry.material("SourceBrownBoundary", [0.40, 0.24, 0.16], 0.7)
    # 7,908 high-confidence source points support this plane. The extent beyond
    # the photographed poster is explicitly estimated material continuation.
    coeff = [0.0006, -0.0092, -2.9486]
    bounds = [[-1.60, 1.32], [0.28, 2.25]]
    grid, corners = surface_grid(1, coeff, bounds, 1536, 1024)
    texture = directory / "appearance_textures/SouthPoster.png"
    evidence = baker.bake(
        grid @ inverse[:3, :3].T + inverse[:3, 3],
        (1024, 1536),
        texture,
        fallback_srgb=(99, 64, 45),
        depth_tolerance=0.20,
    )
    material = geometry.material(
        "SouthPoster",
        [0.4, 0.24, 0.16],
        texture="appearance_textures/SouthPoster.png",
        photo_gain=0.8,
    )
    geometry.mesh(
        "Walls/SouthPoster",
        corners,
        [[3, 2, 1, 0]],
        material,
        [(0, 0), (1, 0), (1, 1), (0, 1)],
    )
    record["assets"].append(
        {
            "id": "SouthPoster",
            "category": "wall",
            "texture": evidence,
            "geometry": "source-plane fit; source pixel projection; missing pixels use declared material color",
        }
    )
    record["regions"].append(
        {
            "id": "SouthPoster",
            "kind": "plane",
            "axis": 1,
            "coefficients": coeff,
            "bounds": bounds,
            "distance": 0.20,
        }
    )
    for name, center, size in [
        ("SouthBacking", [-0.2, -3.03, 0.70], [3.0, 0.10, 3.2]),
        ("EntryReturn", [4.30, -0.39, 0.70], [0.78, 0.22, 3.2]),
        ("UnobservedWestBoundary", [-4.9, 0.0, 0.70], [0.10, 6.5, 3.2]),
    ]:
        geometry.box("Boundaries/" + name, center, size, paint, 0.008)
        record["regions"].append(
            {
                "id": name,
                "kind": "box",
                "min": (np.asarray(center) - np.asarray(size) / 2 - 0.1).tolist(),
                "max": (np.asarray(center) + np.asarray(size) / 2 + 0.1).tolist(),
            }
        )
    stage.GetRootLayer().Save()
    record.update(
        patrol_boundaries_revision=1,
        patrol_boundary_limit="South poster plane is source fitted; boundary thickness and unseen west closure are estimates. Only the checked patrol corridor is task-validated.",
    )
    write_json(directory / "appearance_reference.json", record)
    return record
