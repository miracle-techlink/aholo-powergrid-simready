"""Compose the trained cafe and document two explicitly estimated table repairs.

The source Gaussians remain in their original coordinate frame. A parent Sim3
transforms both geometry and view-dependent appearance; no SH rotation is baked.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, Vt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--alignment", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = Usd.Stage.Open(str(args.scene))
    align = np.array(json.loads(args.alignment.read_text())["new_to_scene_sim3_column"])
    # Reviewed candidate table locations in the historical cafe coordinate frame.
    old = np.array([[3.4, 1.0, 0.05], [3.4, 0.25, 0.05]])
    new = np.array([[0.65, -0.13, 0.85], [0.40, -0.94, 0.85]])
    scale = np.linalg.norm(new[1, :2] - new[0, :2]) / np.linalg.norm(
        old[1, :2] - old[0, :2]
    )
    delta = new[1] - new[0]
    source_delta = old[1] - old[0]
    angle = np.arctan2(delta[1], delta[0]) - np.arctan2(
        source_delta[1], source_delta[0]
    )
    c, ss = np.cos(angle), np.sin(angle)
    transform = np.eye(4)
    transform[:3, :3] = scale * np.array([[c, -ss, 0], [ss, c, 0], [0, 0, 1]])
    transform[:3, 3] = new[0] - transform[:3, :3] @ old[0]
    if not np.allclose(old @ transform[:3, :3].T + transform[:3, 3], new, atol=1e-8):
        raise ValueError("Both table anchors must align")
    cropped = Usd.Stage.Open(source.Flatten())
    fields = []
    for prim in cropped.Traverse():
        if prim.GetTypeName() != "ParticleField3DGaussianSplat":
            continue
        points = np.array(prim.GetAttribute("positions").Get())
        historic = points @ align[:3, :3].T + align[:3, 3]
        mask = np.zeros(len(points), bool)
        for table in old:
            # The interaction tabletop, its old small-object splats and support
            # are replaced together. This is an authored local reconstruction.
            mask |= (
                (np.linalg.norm(historic[:, :2] - table[:2], axis=1) < 0.29)
                & (historic[:, 2] > -0.73)
                & (historic[:, 2] < 0.3)
            )
        opacity = np.array(prim.GetAttribute("opacities").Get())
        opacity[mask] = 0
        prim.GetAttribute("opacities").Set(Vt.FloatArray.FromNumpy(opacity))
        fields.append(
            {
                "prim": str(prim.GetPath()),
                "points": len(points),
                "removed_task_table_points": int(mask.sum()),
            }
        )
    cropped.GetRootLayer().Export(str(args.out / "cafe_cropped.usdc"))
    # Preserve an unedited reference for paired surveys.
    source.Flatten().Export(str(args.out / "cafe_original.usdc"))
    for name, asset in [
        ("repaired", "cafe_cropped.usdc"),
        ("original", "cafe_original.usdc"),
    ]:
        stage = Usd.Stage.CreateNew(str(args.out / (name + ".usda")))
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.SetStageUpAxis(stage, "Z")
        root = UsdGeom.Xform.Define(stage, "/Cafe")
        stage.SetDefaultPrim(root.GetPrim())
        field = UsdGeom.Xform.Define(stage, "/Cafe/Reconstruction")
        field.AddTransformOp().Set(Gf.Matrix4d((transform @ align).T.tolist()))
        field.GetPrim().GetReferences().AddReference(asset, "/World")
        stage.GetRootLayer().Save()
    report = {
        "schema_version": 1,
        "status": "REQUIRES_RENDER_REVIEW",
        "tables_old": old.tolist(),
        "tables_task": new.tolist(),
        "old_scene_to_task": transform.tolist(),
        "sfm_to_task": (transform @ align).tolist(),
        "fields": fields,
        "metric_scale": "operator-estimated using nominal 0.85 m table height; not physically surveyed",
        "appearance": "original-video-trained 30000-step 3DGS, reused; no generated frame enhancement",
        "scope": "two local table replacements; full-scene repair NOT validated",
        "source_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
        "alignment_sha256": hashlib.sha256(args.alignment.read_bytes()).hexdigest(),
    }
    (args.out / "repair.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
