"""Fit support geometry in the original-video 3DGS; retain every visual splat.

Floor and table fitting use case-specific search regions. A nominal 0.85 m
table height supplies metric scale, which is explicitly not surveyed evidence.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def plane(points):
    x = np.c_[points[:, :2], np.ones(len(points))]
    coefficients = np.linalg.lstsq(x, points[:, 2], rcond=None)[0]
    for _ in range(8):
        keep = abs(x @ coefficients - points[:, 2]) < 0.025
        if keep.sum() < 100:
            raise ValueError("Insufficient plane inliers")
        coefficients = np.linalg.lstsq(x[keep], points[keep, 2], rcond=None)[0]
    return coefficients


def table_fit(points):
    coefficients = plane(points)
    p = points[
        abs(np.c_[points[:, :2], np.ones(len(points))] @ coefficients - points[:, 2])
        < 0.025,
        :2,
    ]
    center = (np.quantile(p, 0.02, axis=0) + np.quantile(p, 0.98, axis=0)) / 2
    for _ in range(5):
        delta = p - center
        angle = np.arctan2(delta[:, 1], delta[:, 0])
        distance = np.linalg.norm(delta, axis=1)
        edge = []
        for low, high in zip(
            np.linspace(-np.pi, np.pi, 49)[:-1],
            np.linspace(-np.pi, np.pi, 49)[1:],
            strict=True,
        ):
            ids = np.flatnonzero((angle >= low) & (angle < high))
            if len(ids) > 2:
                q = np.quantile(distance[ids], 0.94)
                edge.append(p[ids[np.argmin(abs(distance[ids] - q))]])
        edge = np.array(edge)
        fit = least_squares(
            lambda q, edge=edge: np.linalg.norm(edge - q[:2], axis=1) - q[2],
            np.r_[center, 0.33],
            loss="soft_l1",
            f_scale=0.01,
        )
        center = fit.x[:2]
    radius = float(fit.x[2])
    return {
        "center_old": [*center, float(np.r_[center, 1] @ coefficients)],
        "radius_old": radius,
        "plane_old": coefficients.tolist(),
        "edge_points": len(edge),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--alignment", type=Path, required=True)
    ap.add_argument("--source-manifest", type=Path, required=True)
    ap.add_argument("--proxies", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    a = np.array(json.loads(args.alignment.read_text())["new_to_scene_sim3_column"])
    source = Usd.Stage.Open(str(args.scene))
    field = source.GetPrimAtPath("/World/StarbucksSplats")
    raw = np.array(field.GetAttribute("positions").Get())
    opacity = np.array(field.GetAttribute("opacities").Get())
    old = raw @ a[:3, :3].T + a[:3, 3]
    valid = opacity > 0.5
    floor_points = old[
        valid
        & (old[:, 0] > 2)
        & (old[:, 0] < 4)
        & (old[:, 1] > -1)
        & (old[:, 1] < 2.5)
        & (old[:, 2] > -0.90)
        & (old[:, 2] < -0.60)
    ]
    floor = plane(floor_points)
    fitted = []
    for y0, y1 in [(0.78, 1.55), (-0.05, 0.74)]:
        pts = old[
            valid
            & (old[:, 0] > 3)
            & (old[:, 0] < 3.9)
            & (old[:, 1] > y0)
            & (old[:, 1] < y1)
            & (old[:, 2] > -0.03)
            & (old[:, 2] < 0.12)
        ]
        fitted.append(table_fit(pts))
    centers = np.array([t["center_old"] for t in fitted])
    normal = np.r_[-floor[:2], 1.0]
    normal /= np.linalg.norm(normal)
    level = Rotation.align_vectors([[0, 0, 1]], [normal])[0].as_matrix()
    delta = level @ (centers[1] - centers[0])
    wanted = np.array([-0.25, -0.81])
    yaw = np.arctan2(wanted[1], wanted[0]) - np.arctan2(delta[1], delta[0])
    rotation = Rotation.from_euler("z", yaw).as_matrix() @ level
    floor_below = np.r_[centers[0, :2], np.r_[centers[0, :2], 1.0] @ floor]
    height = (rotation @ (centers[0] - floor_below))[2]
    scale = 0.85 / height
    transform = np.eye(4)
    transform[:3, :3] = scale * rotation
    transform[:3, 3] = np.array([0.65, -0.13, 0.85]) - transform[:3, :3] @ centers[0]
    world = centers @ transform[:3, :3].T + transform[:3, 3]
    destination = world[1].copy()
    direction = world[0, :2] - world[1, :2]
    direction /= np.linalg.norm(direction)
    destination[:2] += 0.20 * direction
    reachable_target = np.array([0.4, -0.7])
    if (
        np.linalg.norm(reachable_target - world[1, :2])
        < fitted[1]["radius_old"] * scale - 0.06
    ):
        destination[:2] = reachable_target
    layout = {
        "schema_version": 1,
        "tables": {},
        "destination": destination.tolist(),
        "source": "fitted source-video Gaussian table circles and floor plane; 0.85 m nominal scale",
        "metric_scale_verified": False,
    }
    for name, fit, center in zip(["Table", "TableB"], fitted, world, strict=True):
        layout["tables"][name] = {
            "xy": center[:2].tolist(),
            "top": float(center[2]),
            "radius": fit["radius_old"] * scale,
        }
    (args.out / "task_layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    shutil.copy2(args.scene, args.out / "original_video_splats.usdc")
    # Geometry stays visible only in the reconstruction; estimated collision
    # proxies are hidden and cannot replace the photographed walls/furniture.
    shutil.copytree(args.proxies, args.out / "proxies")
    stage = Usd.Stage.CreateNew(str(args.out / "scene.usda"))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/Cafe")
    stage.SetDefaultPrim(root.GetPrim())
    visual = UsdGeom.Xform.Define(stage, "/Cafe/OriginalVideo")
    visual.AddTransformOp().Set(Gf.Matrix4d((transform @ a).T.tolist()))
    visual.GetPrim().GetReferences().AddReference(
        "original_video_splats.usdc", "/World"
    )
    proxy = stage.DefinePrim("/Cafe/PhysicsProxies", "Xform")
    proxy.GetReferences().AddReference("proxies/scene.usda")
    xf = UsdGeom.Xformable(proxy)
    xf.ClearXformOpOrder()
    xf.AddTransformOp(opSuffix="aligned").Set(Gf.Matrix4d(transform.T.tolist()))
    UsdGeom.Imageable(proxy).MakeInvisible()
    for name in ["Floor", "Tiles"]:
        stage.OverridePrim("/Cafe/PhysicsProxies/" + name).SetActive(False)
    stage.GetRootLayer().Save()
    provenance = json.loads(args.source_manifest.read_text())["source"]
    contract = {
        "schema_version": 1,
        "appearance": "original_video_reconstruction",
        "render_table_proxies": False,
        "source_video": provenance,
        "source_usd_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
        "visual_gaussians_retained": len(raw),
        "visual_gaussians_removed": 0,
        "fitted_tables": fitted,
        "floor_plane_old": floor.tolist(),
        "old_scene_to_task": transform.tolist(),
        "metric_scale_verified": False,
        "scope": "original full visual reconstruction with estimated hidden contact proxies",
    }
    (args.out / "scene_contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(json.dumps(layout, indent=2))


if __name__ == "__main__":
    main()
