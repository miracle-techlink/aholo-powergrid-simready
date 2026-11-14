"""Cut declared rectangular openings from floor meshes, including colliders.

All clipping happens in the supplied reference frame. This prevents a visible
stair opening from retaining an invisible flat collision floor underneath it.
"""

import numpy as np

from simready.geometry.polygons import inside_convex_polygon as inside_convex_polygon


def clip_polygon(vertices, axis, bound, keep_greater):
    """Sutherland–Hodgman clipping against one axis-aligned half plane."""
    result = []
    if not len(vertices):
        return result
    previous = np.asarray(vertices[-1], float)
    previous_in = (
        (previous[axis] >= bound) if keep_greater else (previous[axis] <= bound)
    )
    for current in np.asarray(vertices, float):
        current_in = (
            (current[axis] >= bound) if keep_greater else (current[axis] <= bound)
        )
        if current_in != previous_in:
            fraction = (bound - previous[axis]) / (current[axis] - previous[axis])
            result.append(previous + fraction * (current - previous))
        if current_in:
            result.append(current)
        previous, previous_in = current, current_in
    return result


def outside_rectangle(vertices, bounds):
    """Partition the outside into four non-overlapping convex polygons."""
    (x0, x1), (y0, y1) = np.asarray(bounds, float)
    if not (x0 < x1 and y0 < y1):
        raise ValueError("Floor opening must have positive finite extent")
    middle = clip_polygon(clip_polygon(vertices, 0, x0, True), 0, x1, False)
    return [
        clip_polygon(vertices, 0, x0, False),
        clip_polygon(vertices, 0, x1, True),
        clip_polygon(middle, 1, y0, False),
        clip_polygon(middle, 1, y1, True),
    ]


def cut_floor_mesh(mesh, local_to_reference, openings):
    """Replace geometry with triangles outside openings; preserve prim APIs.

    Caller must supply untextured floor meshes. Authored UVs are rejected rather
    than silently becoming invalid after topology changes.
    """
    from pxr import UsdGeom, Vt

    if UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").HasValue():
        raise ValueError("Floor clipping requires an untextured mesh")
    matrix = np.asarray(local_to_reference, float)
    local = np.asarray(mesh.GetPointsAttr().Get(), float)
    reference = local @ matrix[:3, :3].T + matrix[:3, 3]
    if not any(
        np.all(reference[:, :2].max(0) > np.asarray(b)[:, 0])
        and np.all(reference[:, :2].min(0) < np.asarray(b)[:, 1])
        for b in openings
    ):
        return False
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
    triangles, offset = [], 0
    for count in mesh.GetFaceVertexCountsAttr().Get():
        face = reference[indices[offset : offset + count]]
        offset += count
        for i in range(1, count - 1):
            polygons = [face[[0, i, i + 1]]]
            for bounds in openings:
                polygons = [
                    q
                    for p in polygons
                    for q in outside_rectangle(p, bounds)
                    if len(q) >= 3
                ]
            for polygon in polygons:
                for j in range(1, len(polygon) - 1):
                    tri = np.asarray([polygon[0], polygon[j], polygon[j + 1]])
                    if (
                        np.linalg.norm(np.cross(tri[1] - tri[0], tri[2] - tri[0]))
                        > 1e-10
                    ):
                        triangles.append(tri)
    inverse = np.linalg.inv(matrix)
    if not triangles:
        # An inactive prim contributes neither rendering nor collision.
        mesh.GetPrim().SetActive(False)
        return True
    vertices = np.asarray(triangles).reshape(-1, 3)
    vertices = vertices @ inverse[:3, :3].T + inverse[:3, 3]
    tri = vertices.reshape(-1, 3, 3)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))
    mesh.GetFaceVertexCountsAttr().Set([3] * len(tri))
    mesh.GetFaceVertexIndicesAttr().Set(np.arange(len(vertices)).tolist())
    mesh.GetNormalsAttr().Set(
        Vt.Vec3fArray.FromNumpy(np.repeat(normals, 3, axis=0).astype(np.float32))
    )
    mesh.SetNormalsInterpolation("faceVarying")
    return True
