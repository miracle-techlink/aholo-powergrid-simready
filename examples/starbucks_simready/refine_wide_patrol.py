"""Apply recorded local alignment and conservative material fallbacks.

Consumes explicit local alignment evidence; does not infer success from a
render. Source atlases remain intact, with excluded foreground areas recorded.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

from simready.core.artifacts import digest, write_json
from simready.repair.structural_completion import surface_grid
from simready.repair.visible_geometry import VisibleGeometry
from simready.simulation.physical_authoring import bind_contact_material


def level_case_tables(stage, record, reference_to_world):
    """Use fitted table heights with a gravity-aligned tabletop prior.

    Independent reconstruction tilt is not evidence of a sloping cafe table.
    Preserve the fitted center height, flatten the top, and seat all four legs
    on the already leveled floor. Report the actual mesh corrections.
    """
    cache = UsdGeom.XformCache()
    report = []
    for asset in record["assets"]:
        if asset["id"] not in ["Tables/WideNear", "Tables/WideFar"]:
            continue
        root = "/Cafe/Appearance/" + asset["id"]
        center = np.asarray(asset["center"])
        height = (center @ reference_to_world[:3, :3].T + reference_to_world[:3, 3])[2]
        maximum = 0.0
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root)):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            matrix = np.asarray(cache.GetLocalToWorldTransform(prim))
            points = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
            world = points @ matrix[:3, :3] + matrix[3, :3]
            previous = world[:, 2].copy()
            if prim.GetName() == "Top":
                n = len(world) // 2
                world[:n, 2], world[n:, 2] = height, height - 0.065
            else:
                low, high = world[:, 2].min(), world[:, 2].max()
                world[:, 2] = (world[:, 2] - low) / (high - low) * (height - 0.065)
            maximum = max(maximum, float(np.max(abs(world[:, 2] - previous))))
            inverse = np.linalg.inv(matrix)
            points = world @ inverse[:3, :3] + inverse[3, :3]
            mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            normals, offset = [], 0
            for count in mesh.GetFaceVertexCountsAttr().Get():
                face = points[indices[offset : offset + count]]
                normal = np.cross(face[1] - face[0], face[2] - face[0])
                normal /= np.linalg.norm(normal)
                normals.extend([normal] * count)
                offset += count
            mesh.GetNormalsAttr().Set(
                Vt.Vec3fArray.FromNumpy(np.asarray(normals, np.float32))
            )
            mesh.SetNormalsInterpolation("faceVarying")
        report.append(
            {
                "id": asset["id"],
                "world_top_height_m": float(height),
                "max_vertex_height_correction_m": maximum,
                "source": "gravity-aligned planar prior; real slope unmeasured",
            }
        )
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--alignment", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    shutil.copytree(args.reference, args.out)
    record = json.loads((args.out / "appearance_reference.json").read_text())
    correction = json.loads(args.alignment.read_text())
    shutil.copy2(args.alignment, args.out / "wide_local_alignment.json")
    stage = Usd.Stage.Open(str(args.out / "appearance_reference.usda"))
    g = VisibleGeometry(stage, "/Appearance")
    floor = np.asarray(record["floor"])
    for group, key in [("Tables/WideNear", "near"), ("Tables/WideFar", "far")]:
        delta = np.asarray(correction[key]["reference_shift_m"])
        prim = stage.GetPrimAtPath("/Appearance/" + group)
        # Geometry helpers create typeless ancestors. Only an actual Xform
        # participates in USD transform composition and collision extraction.
        UsdGeom.Xform.Define(stage, prim.GetPath()).AddTranslateOp().Set(
            Gf.Vec3d(*delta)
        )
        for asset in record["assets"]:
            if asset["id"] == group:
                asset["center"] = (np.asarray(asset["center"]) + delta).tolist()
                asset["local_alignment"] = correction[key]
    for i in range(14):
        key = "near" if i < 7 else "far"
        delta = np.asarray(correction[key]["reference_shift_m"])
        delta[2] = floor[:2] @ delta[:2]
        UsdGeom.Xform.Define(
            stage, f"/Appearance/Stools/Stool{i:02d}"
        ).AddTranslateOp().Set(Gf.Vec3d(*delta))
    for region in record["regions"]:
        key = {"RefittedNearTableGroup": "near", "FarTableGroup": "far"}.get(
            region["id"]
        )
        if key:
            delta = np.asarray(correction[key]["reference_shift_m"])
            for bound in ["min", "max"]:
                region[bound] = (np.asarray(region[bound]) + delta).tolist()
    paint = g.material("WideEstimatedWall", [0.39, 0.27, 0.19], 0.82)
    base = g.material("WideWindowBase", [0.105, 0.10, 0.085], 0.65)
    # Foreground tables projected onto the uncertain bottom of the window plane
    # are not window observations. Keep the photographed upper part and author
    # an explicitly estimated dark base. This is geometry, not an image filter.
    prim = stage.GetPrimAtPath("/Appearance/Walls/FarWindows")
    mesh = UsdGeom.Mesh(prim)
    pts = np.asarray(mesh.GetPointsAttr().Get()).copy()
    pts[:2, 2] = 0.50
    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts))
    uv = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st")
    uv.Set(Vt.Vec2fArray([(0, 1.2 / 3.35), (1, 1.2 / 3.35), (1, 1), (0, 1)]))
    _, corners = surface_grid(0, [0, 0, -7.025], [[-5.7, 3.22], [-0.7, 0.50]], 4, 4)
    g.mesh("Walls/FarWindowBase", corners, [[0, 1, 2, 3]], base)
    # The aisle plane has insufficient calibrated support for a door texture;
    # preserve its diagnostic atlas but use the declared wall-color estimate.
    UsdShade.MaterialBindingAPI(
        stage.GetPrimAtPath("/Appearance/Walls/EastAisleWall")
    ).Bind(paint)
    UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/Appearance/Walls/FarSouth")).Bind(
        paint
    )
    for name in ["EastAisleWall", "FarSouth"]:
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Appearance/Walls/" + name))
        mesh.GetFaceVertexIndicesAttr().Set([3, 2, 1, 0])
        mesh.GetNormalsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(0, 1, 0)] * 4))
    delta = np.asarray(correction["far"]["reference_shift_m"])
    for name in ["FarWindows", "FarWindowBase", "FarSouth"]:
        UsdGeom.Xformable(
            stage.GetPrimAtPath("/Appearance/Walls/" + name)
        ).AddTranslateOp().Set(Gf.Vec3d(*delta))
    # Additional far floor support shares the fitted visual plane and physics
    # material. The final authoring stage aligns both with the case floor.
    paths = []
    for name, bounds in [
        ("WideSupportWest", [[-7.5, -4.9], [-6.2, 3.39]]),
        ("WideSupportSouth", [[-4.9, 4.99], [-6.2, -3.8]]),
    ]:
        _, points = surface_grid(2, floor, bounds, 4, 4)
        mesh = g.mesh("Floor/" + name, points, [[0, 1, 2, 3]], base)
        mesh.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        paths.append(str(mesh.GetPath()))
    bind_contact_material(
        stage,
        "/Appearance/Materials/WideFloorContact",
        paths,
        {"static_friction": 0.65, "dynamic_friction": 0.5, "restitution": 0},
    )
    for asset in record["assets"]:
        if asset["id"] in ["FarWindows", "EastAisleWall", "FarSouth"]:
            asset["render_limit"] = (
                "Only upper window pixels are rendered; bottom is estimated dark panel"
                if asset["id"] == "FarWindows"
                else "Diagnostic source atlas rejected for rendering; estimated uniform wall material"
            )
    record["wide_patrol_revision"] = 2
    record["wide_patrol"].update(
        local_alignment=correction,
        alignment_sha256=digest(args.alignment),
        far_support_planes=paths,
        estimated_window_base=True,
    )
    stage.GetRootLayer().Save()
    write_json(args.out / "appearance_reference.json", record)


if __name__ == "__main__":
    main()
