"""Editable source-conditioned geometry, adapted from the cabinet repair.

Meshes have explicit UVs and normals. These are estimated render surfaces;
callers must author and validate collision separately.
"""

import numpy as np
from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt


class VisibleGeometry:
    def __init__(self, stage, root):
        self.stage = stage
        self.root = root.rstrip("/")

    def material(
        self, name, srgb, roughness=0.6, metallic=0.0, texture=None, photo_gain=0.0
    ):
        path = self.root + "/Materials/" + name
        mat = UsdShade.Material.Define(self.stage, path)
        shader = UsdShade.Shader.Define(self.stage, path + "/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        c = np.asarray(srgb, float)
        linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*linear)
        )
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
        if texture:
            uv = UsdShade.Shader.Define(self.stage, path + "/UV")
            uv.CreateIdAttr("UsdPrimvarReader_float2")
            uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            uv.CreateOutput("result", Sdf.ValueTypeNames.Float2)
            tex = UsdShade.Shader.Define(self.stage, path + "/Texture")
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(str(texture))
            )
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                uv.ConnectableAPI(), "result"
            )
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            if photo_gain:
                shader.GetInput("diffuseColor").Set(Gf.Vec3f(0))
                tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(
                    Gf.Vec4f(photo_gain, photo_gain, photo_gain, 1)
                )
                shader.CreateInput(
                    "emissiveColor", Sdf.ValueTypeNames.Color3f
                ).ConnectToSource(tex.ConnectableAPI(), "rgb")
            else:
                shader.GetInput("diffuseColor").ConnectToSource(
                    tex.ConnectableAPI(), "rgb"
                )
        shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return mat

    def mesh(self, name, points, faces, material, uv=None):
        mesh = UsdGeom.Mesh.Define(self.stage, self.root + "/" + name)
        points = np.asarray(points, np.float32)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
        mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
        mesh.CreateFaceVertexIndicesAttr([i for face in faces for i in face])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDoubleSidedAttr(True)
        normals = []
        for face in faces:
            p = points[face]
            n = np.cross(p[1] - p[0], p[2] - p[0])
            length = np.linalg.norm(n)
            if length < 1e-10:
                raise ValueError(f"Degenerate face: {name}")
            normals.extend([n / length] * len(face))
        mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(normals, np.float32)))
        mesh.SetNormalsInterpolation("faceVarying")
        if uv is not None:
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
            ).Set(Vt.Vec2fArray.FromNumpy(np.asarray(uv, np.float32)))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        mesh.GetPrim().CreateAttribute(
            "simready:geometryOrigin", Sdf.ValueTypeNames.String
        ).Set("source_conditioned_estimate")
        return mesh

    def box(self, name, center, size, material, bevel=0.003, transform=None):
        # Beveled box: inset face boundaries plus 12 edge strips and 8 corners.
        size = np.asarray(size, float)
        center = np.asarray(center, float)
        b = min(bevel, float(size.min()) * 0.22)
        points, faces, lookup = [], [], {}

        def point(q):
            key = tuple(np.round(q, 9))
            if key not in lookup:
                lookup[key] = len(points)
                points.append(q)
            return lookup[key]

        # Each large face has four vertices inset along the other two axes.
        for axis in range(3):
            other = [j for j in range(3) if j != axis]
            for sign in [-1, 1]:
                face = []
                for a, c in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
                    q = np.zeros(3)
                    q[axis] = sign * size[axis] / 2
                    q[other[0]] = a * (size[other[0]] / 2 - b)
                    q[other[1]] = c * (size[other[1]] / 2 - b)
                    face.append(point(q))
                faces.append(face)
        # ConvexHull closes bevels consistently and keeps faces manifold.
        from scipy.spatial import ConvexHull

        pts = np.asarray(points)
        hull = ConvexHull(pts)
        faces = []
        for simplex, equation in zip(hull.simplices, hull.equations, strict=True):
            q = pts[simplex]
            if np.dot(np.cross(q[1] - q[0], q[2] - q[0]), equation[:3]) < 0:
                simplex = simplex[::-1]
            faces.append(simplex.tolist())
        pts += center
        if transform is not None:
            pts = pts @ transform[:3, :3].T + transform[:3, 3]
        return self.mesh(name, pts, faces, material)

    def cylinder(
        self, name, center, radius, height, material, *, segments=64, texture_uv=False
    ):
        angle = np.arange(segments) * 2 * np.pi / segments
        ring = np.c_[radius * np.cos(angle), radius * np.sin(angle)]
        points = np.vstack(
            [
                np.c_[ring, np.full(segments, height / 2)],
                np.c_[ring, np.full(segments, -height / 2)],
            ]
        )
        faces = [list(range(segments)), list(range(2 * segments - 1, segments - 1, -1))]
        faces += [
            [i, (i + 1) % segments, (i + 1) % segments + segments, i + segments]
            for i in range(segments)
        ]
        uv = np.tile(ring / (2 * radius) + 0.5, (2, 1)) if texture_uv else None
        return self.mesh(name, points + center, faces, material, uv)

    def rod(self, name, start, end, radius, material, segments=16):
        start, end = np.asarray(start), np.asarray(end)
        direction = end - start
        direction /= np.linalg.norm(direction)
        helper = (
            np.array([0.0, 0.0, 1.0])
            if abs(direction[2]) < 0.9
            else np.array([0.0, 1.0, 0.0])
        )
        u = np.cross(direction, helper)
        u /= np.linalg.norm(u)
        v = np.cross(direction, u)
        angle = np.arange(segments) * 2 * np.pi / segments
        offsets = radius * (np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v)
        points = np.vstack([start + offsets, end + offsets])
        faces = [list(range(segments - 1, -1, -1)), list(range(segments, 2 * segments))]
        faces += [
            [i, (i + 1) % segments, (i + 1) % segments + segments, i + segments]
            for i in range(segments)
        ]
        return self.mesh(name, points, faces, material)
