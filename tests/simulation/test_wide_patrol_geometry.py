import numpy as np
import pytest


def test_gravity_leveling_preserves_fitted_height_and_seats_legs():
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    from examples.starbucks_simready.refine_wide_patrol import level_case_tables
    from simready.repair.visible_geometry import VisibleGeometry

    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/Cafe/Appearance")
    transform = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 4))
    root.AddTransformOp().Set(transform)
    geometry = VisibleGeometry(stage, "/Cafe/Appearance")
    material = geometry.material("Wood", [0.5, 0.3, 0.1])
    top = geometry.cylinder(
        "Tables/WideFar/Top", [0, 0, 1 - 0.065 / 2], 0.5, 0.065, material
    )
    legs = [
        geometry.box(
            f"Tables/WideFar/Leg{i}", [x, y, 0.45], [0.05, 0.05, 0.9], material
        )
        for i, (x, y) in enumerate([[-0.3, -0.3], [0.3, 0.3]])
    ]
    matrix = np.asarray(transform).T
    record = {"assets": [{"id": "Tables/WideFar", "center": [0, 0, 1]}]}
    evidence = level_case_tables(stage, record, matrix)
    height = matrix[2, 2]
    cache = UsdGeom.XformCache()

    def world(mesh):
        m = np.asarray(cache.GetLocalToWorldTransform(mesh.GetPrim()))
        return np.asarray(mesh.GetPointsAttr().Get()) @ m[:3, :3] + m[3, :3]

    vertices = world(top)
    n = len(vertices) // 2
    assert np.allclose(vertices[:n, 2], height, atol=1e-7)
    assert np.allclose(vertices[n:, 2], height - 0.065, atol=1e-7)
    for mesh in legs:
        assert np.isclose(world(mesh)[:, 2].min(), 0, atol=1e-7)
        assert np.isclose(world(mesh)[:, 2].max(), height - 0.065, atol=1e-7)
    for mesh in [top, *legs]:
        normals = np.asarray(mesh.GetNormalsAttr().Get())
        assert len(normals) == sum(mesh.GetFaceVertexCountsAttr().Get())
        assert np.isfinite(normals).all()
        assert np.allclose(np.linalg.norm(normals, axis=1), 1)
    assert evidence[0]["max_vertex_height_correction_m"] > 0.03
