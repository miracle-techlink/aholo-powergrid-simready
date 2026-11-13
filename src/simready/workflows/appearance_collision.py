"""Keep visible replacements, static collision and route occupancy consistent."""

import numpy as np
from pxr import Gf, UsdGeom, UsdPhysics, Vt
from scipy.ndimage import binary_dilation, binary_fill_holes

from simready.repair.structural_completion import voxel_boundary
from simready.simulation.physical_authoring import bind_contact_material


def triangle_cells(points, counts, indices, origin, resolution, shape):
    """Conservatively sample triangle surfaces, then fill closed components."""
    result = np.zeros(shape, bool)
    offset = 0
    for count in counts:
        face = indices[offset : offset + count]
        offset += count
        for k in range(1, count - 1):
            a, b, c = points[[face[0], face[k], face[k + 1]]]
            n = max(
                1,
                int(
                    np.ceil(
                        max(
                            np.linalg.norm(b - a),
                            np.linalg.norm(c - a),
                            np.linalg.norm(c - b),
                        )
                        / (resolution / 2)
                    )
                ),
            )
            u, v = np.meshgrid(np.arange(n + 1) / n, np.arange(n + 1) / n)
            keep = u + v <= 1.0000001
            xyz = a + u[keep, None] * (b - a) + v[keep, None] * (c - a)
            cell = np.floor((xyz - origin) / resolution).astype(int)
            cell = cell[((cell >= 0) & (cell < shape)).all(1)]
            result[tuple(cell.T)] = True
    return binary_fill_holes(result)


def author_replacement_collision(
    stage, directory, contract, transform, reference_to_case, regions, replacement_mask
):
    data = dict(np.load(directory / "obstacles.npz", allow_pickle=False))
    occupied, origin, res = (
        data["occupied"].copy(),
        data["origin"],
        float(data["resolution"]),
    )
    cells = np.argwhere(occupied)
    centers = (cells + 0.5) * res + origin
    inverse = np.linalg.inv(reference_to_case)
    reference = centers @ inverse[:3, :3].T + inverse[:3, 3]
    remove, _ = replacement_mask(reference, regions)
    occupied[tuple(cells[remove].T)] = False
    # Everything inside a replacement region is represented by its new mesh.
    for name in [
        "ObservedObstacles",
        "WallX",
        "WallY",
        "BenchSeat",
        "BenchBack",
        "Table_0",
        "Table_1",
    ]:
        prim = stage.GetPrimAtPath("/Cafe/Completion/Collision/" + name)
        if prim:
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
    root = UsdGeom.Xform.Define(stage, "/Cafe/AppearanceCollision")
    root.AddTransformOp().Set(Gf.Matrix4d(transform.T.tolist()))
    root.CreateVisibilityAttr("invisible")
    cache = UsdGeom.XformCache()
    world_to_old = np.linalg.inv(transform)
    groups = {}
    for prim in list(stage.Traverse()):
        path = str(prim.GetPath())
        if (
            not prim.IsA(UsdGeom.Mesh)
            or not path.startswith("/Cafe/Appearance/")
            or "/Floor/" in path
        ):
            continue
        relative = path.removeprefix("/Cafe/Appearance/").split("/")
        group = (
            "_".join(relative[:2]) if relative[0] != "SharedTable" else "SharedTable"
        )
        mesh = UsdGeom.Mesh(prim)
        mat = np.asarray(cache.GetLocalToWorldTransform(prim))
        vertices = np.asarray(mesh.GetPointsAttr().Get()) @ mat[:3, :3] + mat[3, :3]
        vertices = vertices @ world_to_old[:3, :3].T + world_to_old[:3, 3]
        parts = groups.setdefault(group, [[], [], []])
        parts[2].extend(
            np.asarray(mesh.GetFaceVertexIndicesAttr().Get()) + len(parts[0])
        )
        parts[0].extend(vertices.tolist())
        parts[1].extend(mesh.GetFaceVertexCountsAttr().Get())
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
    residual = occupied.copy()
    vp, vf = voxel_boundary(residual, origin, res)
    groups["UnreplacedObserved"] = [vp, [3] * len(vf), vf.reshape(-1)]
    paths, overrides = [], []
    for name, (points, counts, indices) in groups.items():
        if not len(points):
            continue
        points, indices = np.asarray(points), np.asarray(indices)
        path = "/Cafe/AppearanceCollision/" + name
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr(indices.tolist())
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDoubleSidedAttr(True)
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        paths.append(path)
        if name != "UnreplacedObserved":
            occupied |= triangle_cells(
                points, counts, indices, origin, res, occupied.shape
            )
        if "old_scene_to_task" in contract and name in [
            "Tables_Table00",
            "Tables_Table01",
        ]:
            overrides.append(
                {
                    "background_path": path.removeprefix("/Cafe/"),
                    "task_path": "/World/Table"
                    if name.endswith("00")
                    else "/World/TableB",
                }
            )
    bind_contact_material(
        stage,
        "/Cafe/AppearanceCollision/Contact",
        paths,
        {"static_friction": 0.65, "dynamic_friction": 0.5, "restitution": 0.0},
    )
    # A one-cell conservative shell prevents thin mesh faces being skipped by
    # the robot-envelope route test. This occupancy is not extra PhysX geometry.
    data["occupied"] = binary_dilation(occupied, iterations=1)
    np.savez_compressed(directory / "obstacles.npz", **data)
    return paths, overrides
