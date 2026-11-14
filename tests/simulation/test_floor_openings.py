import numpy as np
import pytest

from simready.repair.floor_openings import cut_floor_mesh, inside_convex_polygon


def test_transformed_floor_has_real_hole_and_preserves_collision():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    from simready.repair.visible_geometry import VisibleGeometry

    stage = Usd.Stage.CreateInMemory()
    geometry = VisibleGeometry(stage, "/Floor")
    mat = geometry.material("Floor", [0.4, 0.4, 0.4])
    # An affine transform exercises reference-frame cutting rather than just
    # deleting complete mesh faces whose centers happen to lie inside a hole.
    matrix = np.eye(4)
    matrix[:3, 3] = [3, 1, 0.4]
    mesh = geometry.mesh(
        "Surface", [[-2, -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]], [[0, 1, 2, 3]], mat
    )
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    assert cut_floor_mesh(mesh, matrix, [[[2.5, 3.5], [0.5, 1.5]]])
    points = np.asarray(mesh.GetPointsAttr().Get()) + matrix[:3, 3]
    triangles = points[np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)]
    area = (
        np.linalg.norm(
            np.cross(
                triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
            ),
            axis=1,
        ).sum()
        / 2
    )
    assert area == pytest.approx(15)
    for triangle in triangles:
        center = triangle.mean(0)
        assert not (2.5 < center[0] < 3.5 and 0.5 < center[1] < 1.5)
    assert mesh.GetPrim().HasAPI(UsdPhysics.CollisionAPI)
    assert len(mesh.GetNormalsAttr().Get()) == 3 * len(triangles)


def test_polygon_test_handles_winding_and_boundary():
    polygon = [[0, 1], [1, 0], [2, 1], [1, 2]]
    assert inside_convex_polygon([1, 1], polygon)
    assert inside_convex_polygon([1, 1], polygon[::-1])
    assert inside_convex_polygon([0, 1], polygon)
    assert not inside_convex_polygon([0, 0], polygon)


def test_observation_camera_preserves_motion_and_start_end():
    from examples.starbucks_simready.focus_far_left import observation_camera

    rows = [
        {
            "frame": i,
            "time_s": i / 30,
            "robot_position": [i / 1000, 0, 0],
            "yaw": i,
            "eye": [0, 0, 2],
            "target": [0, 0, 1],
        }
        for i in range(900)
    ]
    result = observation_camera(rows, np.eye(4))
    for a, b in zip(rows, result, strict=True):
        assert a["robot_position"] == b["robot_position"] and a["yaw"] == b["yaw"]
        if a["time_s"] <= 6 or a["time_s"] >= 24:
            assert a == b
    assert result[450]["eye"] == [-2.8, 0.3, 2.3]
    eye = np.asarray([r["eye"] for r in result])
    assert np.linalg.norm(np.diff(eye, axis=0), axis=1).max() < 0.04
