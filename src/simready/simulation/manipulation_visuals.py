"""Appearance-only repairs for rendering an independently measured rollout."""

import numpy as np


def refine_tables(stage, texture=None):
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

    for path in [
        "/World/" + name + suffix
        for name in ("Table", "TableB")
        for suffix in ("", "Stem", "Foot")
    ]:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsA(UsdGeom.Cylinder):
            continue
        cylinder = UsdGeom.Cylinder(prim)
        radius = float(cylinder.GetRadiusAttr().Get())
        height = float(cylinder.GetHeightAttr().Get())
        n = 128
        angle = np.arange(n) * 2 * np.pi / n
        ring = np.c_[radius * np.cos(angle), radius * np.sin(angle)]
        points = np.r_[
            np.c_[ring, np.full(n, height / 2)],
            np.c_[ring, np.full(n, -height / 2)],
            [[0, 0, height / 2], [0, 0, -height / 2]],
        ]
        indices = []
        for i in range(n):
            j = (i + 1) % n
            indices.extend(
                [2 * n, i, j, 2 * n + 1, n + j, n + i, i, n + i, n + j, i, n + j, j]
            )
        mesh = UsdGeom.Mesh.Define(stage, path + "RenderMesh")
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
        mesh.CreateFaceVertexCountsAttr([3] * (len(indices) // 3))
        mesh.CreateFaceVertexIndicesAttr(indices)
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.AddTransformOp().Set(UsdGeom.XformCache().GetLocalToWorldTransform(prim))
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
        ).Set(
            Vt.Vec2fArray.FromNumpy(
                (points[:, :2] / (2 * radius) + 0.5).astype(np.float32)
            )
        )
        mat = UsdShade.Material.Define(stage, path + "RenderMaterial")
        shader = UsdShade.Shader.Define(stage, mat.GetPath().AppendChild("Surface"))
        shader.CreateIdAttr("UsdPreviewSurface")
        tabletop = path.endswith(("Table", "TableB"))
        color = (0.32, 0.16, 0.065) if tabletop else (0.025, 0.022, 0.019)
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*color)
        )
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(
            0.48 if tabletop else 0.6
        )
        if tabletop and texture:
            tex = UsdShade.Shader.Define(stage, mat.GetPath().AppendChild("Texture"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture))
            )
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            uv = UsdShade.Shader.Define(stage, mat.GetPath().AppendChild("UV"))
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
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
        UsdGeom.Imageable(prim).MakeInvisible()


def repair_robot_uv_interpolation(stage, root_path="/World/G2"):
    from pxr import UsdGeom

    fixed = []
    for prim in stage.Traverse():
        if not str(prim.GetPath()).startswith(
            root_path.rstrip("/") + "/"
        ) or not prim.IsA(UsdGeom.Mesh):
            continue
        st = UsdGeom.PrimvarsAPI(prim).GetPrimvar("st")
        if not st or st.IsIndexed() or st.GetInterpolation() != "faceVarying":
            continue
        values = st.Get()
        if values is not None and len(values) == len(
            UsdGeom.Mesh(prim).GetPointsAttr().Get()
        ):
            st.SetInterpolation("vertex")
            fixed.append(str(prim.GetPath()))
    return fixed
