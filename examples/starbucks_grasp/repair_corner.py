"""Source-image wall textures and explicit estimated furniture for the task corner.

This is a local, case-configured repair, not a fully automatic scene rebuild.
It preserves the raw video and fitted 3DGS alignment as separate evidence.
"""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt


def material(stage, path, color, roughness=0.65, texture=None):
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    if texture:
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
        shader.GetInput("diffuseColor").ConnectToSource(tex.ConnectableAPI(), "rgb")
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def box(stage, path, center, size, mat):
    shape = UsdGeom.Cube.Define(stage, path)
    shape.CreateSizeAttr(1)
    shape.AddTranslateOp().Set(Gf.Vec3d(*center))
    shape.AddScaleOp().Set(Gf.Vec3f(*size))
    UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(mat)
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())


def plane(stage, path, corners, mat):
    shape = UsdGeom.Mesh.Define(stage, path)
    shape.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(corners, np.float32)))
    shape.CreateFaceVertexCountsAttr([4])
    shape.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    shape.CreateSubdivisionSchemeAttr("none")
    shape.CreateDoubleSidedAttr(True)
    UsdGeom.PrimvarsAPI(shape).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
    ).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(mat)


def atlas(image, cam, old_to_sfm, axis, axes, constant, bounds, output):
    width, height = 1536, 1024
    u, v = np.meshgrid(
        np.linspace(*bounds[0], width), np.linspace(bounds[1][1], bounds[1][0], height)
    )
    pts = np.zeros((width * height, 3))
    pts[:, axis] = constant
    pts[:, axes[0]] = u.ravel()
    pts[:, axes[1]] = v.ravel()
    pts = pts @ old_to_sfm[:3, :3].T + old_to_sfm[:3, 3]
    t = np.array(cam["world_to_camera"])
    pts = pts @ t[:3, :3].T + t[:3, 3]
    xy = pts[:, :2] / pts[:, 2:]
    xy *= 1 + cam["camera_params"][-1] * np.sum(xy * xy, axis=1)[:, None]
    k = np.array(cam["K"])
    uv = xy @ k[:2, :2].T + k[:2, 2]
    frame = cv2.imread(str(image))
    valid = (
        (pts[:, 2] > 0)
        & (uv[:, 0] >= 0)
        & (uv[:, 0] < frame.shape[1] - 1)
        & (uv[:, 1] >= 0)
        & (uv[:, 1] < frame.shape[0] - 1)
    )
    warped = cv2.remap(
        frame,
        uv[:, 0].astype(np.float32).reshape(height, width),
        uv[:, 1].astype(np.float32).reshape(height, width),
        cv2.INTER_LINEAR,
    )
    warped[~valid.reshape(height, width)] = [150, 155, 160]
    cv2.imwrite(str(output), warped)
    return float(valid.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cafe", type=Path, required=True)
    ap.add_argument("--sfm-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    mapping = np.array(
        json.loads((args.cafe / "repair.json").read_text())["old_scene_to_task"]
    )
    a = np.array(
        json.loads((args.sfm_root / "integration/full_alignment.json").read_text())[
            "new_to_scene_sim3_column"
        ]
    )
    cameras = {
        c["image_name"]: c
        for c in json.loads(
            (
                args.sfm_root / "full_sfm/reconstruction/sparse/0/trajectory.json"
            ).read_text()
        )["poses"]
    }
    images = args.sfm_root / "full_sfm/images"
    stage = Usd.Stage.CreateNew(str(args.out / "scene.usda"))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/Corner")
    stage.SetDefaultPrim(root.GetPrim())
    root.AddTransformOp().Set(Gf.Matrix4d(mapping.T.tolist()))
    wall = material(stage, "/Corner/Materials/Wall", (0.30, 0.29, 0.25), 0.9)
    leather = material(stage, "/Corner/Materials/Leather", (0.21, 0.075, 0.025), 0.48)
    dark = material(stage, "/Corner/Materials/Wood", (0.065, 0.029, 0.014), 0.5)
    grout = material(stage, "/Corner/Materials/Grout", (0.16, 0.145, 0.12), 0.95)
    floor = -mapping[2, 3] / mapping[2, 2]
    box(stage, "/Corner/Floor", (2.1, 0.1, floor - 0.03), (5.5, 6.0, 0.06), grout)
    rng = np.random.default_rng(41)
    for ix in range(10):
        for iy in range(10):
            color = np.array([0.36, 0.325, 0.27]) * rng.uniform(0.92, 1.08)
            mat = material(stage, f"/Corner/Materials/Tile_{ix}_{iy}", color, 0.8)
            box(
                stage,
                f"/Corner/Tiles/Tile_{ix}_{iy}",
                (-0.8 + ix * 0.6, -2.5 + iy * 0.6, floor - 0.003),
                (0.597, 0.597, 0.006),
                mat,
            )
    box(stage, "/Corner/WallX", (4.7, 1.15, 1.05), (0.10, 3.5, 3.5), wall)
    box(stage, "/Corner/WallY", (3.0, 2.86, 1.05), (3.4, 0.10, 3.5), wall)
    records = []
    specs = [
        (
            "wall_x",
            "frame_000566.png",
            0,
            [1, 2],
            4.64,
            [[-0.35, 2.78], [0.28, 2.32]],
            [
                [4.64, -0.35, 0.28],
                [4.64, 2.78, 0.28],
                [4.64, 2.78, 2.32],
                [4.64, -0.35, 2.32],
            ],
        ),
        (
            "wall_y",
            "frame_000761.png",
            1,
            [0, 2],
            2.80,
            [[2.4, 4.64], [0.28, 2.32]],
            [
                [2.4, 2.80, 0.28],
                [4.64, 2.80, 0.28],
                [4.64, 2.80, 2.32],
                [2.4, 2.80, 2.32],
            ],
        ),
    ]
    for name, frame, axis, axes, constant, bounds, corners in specs:
        source = images / frame
        coverage = atlas(
            source,
            cameras[frame],
            np.linalg.inv(a),
            axis,
            axes,
            constant,
            bounds,
            args.out / (name + ".png"),
        )
        mat = material(
            stage, "/Corner/Materials/" + name, (1, 1, 1), 0.95, name + ".png"
        )
        plane(stage, "/Corner/" + name, corners, mat)
        records.append(
            {
                "asset": name,
                "source_frame": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "image_coverage_fraction": coverage,
            }
        )
    # Sofa dimensions/color are explicit estimates; these are visible static
    # replacement meshes, not claims of recovered leather or contact properties.
    for i in range(6):
        y = -0.30 + i * 0.5
        box(
            stage, f"/Corner/BenchX/Base{i}", (4.25, y, -0.49), (0.72, 0.49, 0.40), dark
        )
        box(
            stage,
            f"/Corner/BenchX/Seat{i}",
            (4.25, y, -0.27),
            (0.75, 0.49, 0.08),
            leather,
        )
        box(
            stage,
            f"/Corner/BenchX/Back{i}",
            (4.57, y, 0.0),
            (0.12, 0.49, 0.46),
            leather,
        )
    for i in range(4):
        x = 2.5 + i * 0.48
        box(
            stage, f"/Corner/BenchY/Base{i}", (x, 2.47, -0.49), (0.47, 0.64, 0.40), dark
        )
        box(
            stage,
            f"/Corner/BenchY/Seat{i}",
            (x, 2.47, -0.27),
            (0.47, 0.67, 0.08),
            leather,
        )
        box(
            stage,
            f"/Corner/BenchY/Back{i}",
            (x, 2.74, 0.0),
            (0.47, 0.12, 0.46),
            leather,
        )
    source = images / "frame_001106.png"
    Image.open(source).crop((90, 1000, 620, 1250)).save(args.out / "table_wood.png")
    records.append(
        {
            "asset": "table_wood",
            "source_frame": str(source),
            "crop_xyxy": [90, 1000, 620, 1250],
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    stage.GetRootLayer().Save()
    (args.out / "repair.json").write_text(
        json.dumps(
            {
                "status": "LOCAL_TASK_CORNER_REVIEW_REQUIRED",
                "source_textures": records,
                "geometry": "operator configured planes, round tables and benches; metric alignment unverified",
                "appearance": "original-video wall projections and wood patch; estimated leather, tiles and roughness",
                "excluded": "remaining cafe, storefront, other movable props; no full-scene completion claim",
                "physics": "static primitive colliders; estimated geometry without calibrated materials",
                "old_scene_to_task": mapping.tolist(),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
